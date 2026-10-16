"""Plan bookkeeping for write steps: settle with one idempotent retry, compensation and unknown outcomes."""

from __future__ import annotations

from typing import Any, Optional

import requests

from planner import (
    Intent,
    PlanExecutor,
    TaskPlan,
)
from security import audit_event
from bank_agent import runtime
from bank_agent.state import (
    BankingAgentState,
)
from bank_agent.runtime import (
    error_message,
    response_json,
)
from bank_agent.verification import (
    response_validation_failure,
    valid_balance_payload,
)


# Task ids consumed by one graph run per intent, in dependency order.  The
# last entry is the step that settles on the core ledger; downstream tasks
# that are owned by the core task center (reminders, order drafts, participant
# notifications) are deliberately left PENDING with an honest note.
_SETTLE_ORDER: dict[str, list[str]] = {
    Intent.BALANCE_QUERY.value: ["fetch_balance"],
    Intent.BILL_QUERY.value: ["fetch_transactions", "compute_facts"],
    Intent.BILL_ANALYSIS.value: ["fetch_transactions", "compute_facts"],
    Intent.WEALTH_QUERY.value: ["fetch_products"],
    Intent.WEALTH_HOLDINGS.value: ["fetch_holdings"],
    Intent.WEALTH_RECOMMEND.value: ["fetch_products"],
    Intent.WEALTH_RISK_ASSESSMENT.value: ["risk_assessment"],
    Intent.WEALTH_COMPARE.value: ["compare_products"],
    Intent.CARD_QUERY.value: ["fetch_cards"],
    Intent.SUBSCRIPTION_QUERY.value: ["subscription_operation"],
    Intent.TRANSFER.value: ["validate_transfer", "execute_transfer"],
    Intent.SCHEDULED_TRANSFER.value: ["validate_transfer", "schedule_transfer"],
    Intent.SPLIT_BILL.value: ["validate_split", "create_split_request"],
    Intent.CROSS_SCENE_LOCK.value: ["validate_event", "reserve_funds"],
    Intent.WEALTH_PURCHASE.value: ["risk_assessment", "mfa_and_confirm", "settle_wealth"],
    Intent.WEALTH_REDEEM.value: ["risk_assessment", "mfa_and_confirm", "settle_wealth"],
    Intent.PASSWORD_CHANGE.value: ["password_change"],
    Intent.FRAUD_DISPUTE.value: [
        "verify_transaction",
        "explain_risk",
        "mfa_confirm",
        "freeze_card",
        "create_dispute_case",
    ],
}


def plan_from_state(state: BankingAgentState) -> Optional[TaskPlan]:
    raw = state.get("plan")
    if not isinstance(raw, dict):
        return None
    try:
        return TaskPlan.from_dict(raw)
    except Exception:
        return None


def consumed_task_ids(plan: TaskPlan, intent: str) -> tuple[list[str], Optional[str]]:
    """Return the task ids one graph run consumes, and the settle task id."""

    if intent in _SETTLE_ORDER:
        ordered = list(_SETTLE_ORDER[intent])
    elif intent.startswith("CARD_") or intent == Intent.SUBSCRIPTION_CANCEL.value:
        ordered = ["card_operation"] if intent.startswith("CARD_") else ["subscription_operation"]
    else:
        return [], None
    available = {task.task_id for task in plan.tasks}
    consumed = [task_id for task_id in ordered if task_id in available]
    settle = consumed[-1] if consumed else None
    return consumed, settle


def advance_plan(state: BankingAgentState, intent: Optional[str], *, outcome: str, data: Optional[dict[str, Any]] = None, error: Optional[str] = None, pending_notes: Optional[dict[str, str]] = None) -> Optional[dict[str, Any]]:
    """Advance the plan ledger truthfully after a run step.

    ``SUCCESS`` marks every consumed step (validation, MFA gate, settle)
    SUCCEEDED and leaves downstream core-owned tasks PENDING with notes.
    ``FAILURE`` marks the settle step FAILED and keeps its compensation action
    readable for the caller.
    """

    plan = plan_from_state(state)
    if plan is None:
        return None
    consumed, settle_id = consumed_task_ids(plan, intent or "")
    if not settle_id:
        return plan.to_dict()
    notes = pending_notes or {}
    if outcome == "SUCCESS":
        for task_id in consumed:
            PlanExecutor.mark_succeeded(plan, task_id)
        settle_task = PlanExecutor.find(plan, settle_id)
        if isinstance(data, dict):
            settle_task.details["result"] = data
        for task in plan.tasks:
            if task.task_id in consumed:
                continue
            PlanExecutor.keep_pending(plan, task.task_id, note=notes.get(task.task_id) or "该步骤由核心任务中心接管（到期保持 WAITING_USER，不自动执行）")
    else:
        compensation = PlanExecutor.mark_failed(plan, settle_id, error=error or "EXECUTION_FAILED")
        # Validation/MFA steps before the settle step were genuinely performed.
        for task_id in consumed[:-1]:
            PlanExecutor.mark_succeeded(plan, task_id)
        settle_task = PlanExecutor.find(plan, settle_id)
        if compensation:
            settle_task.details["compensation_action"] = compensation
    return plan.to_dict()


def settle_with_retry(state: BankingAgentState, *, method: str, path: str, json_body: dict[str, Any], headers: dict[str, str], idempotency_key: Optional[str] = None) -> requests.Response:
    """Send a settle request; retry exactly once on transport errors.

    The retry reuses the same Idempotency-Key, so a response lost after the
    core settled replays the cached outcome instead of double-settling.  HTTP
    error responses (policy rejections) are never retried.
    """

    endpoint = {"transfers/execute": "transfer_execute", "accounts/lock_fund": "lock_fund", "operations/execute": "operation_execute"}.get(path)
    state["reconciliation"] = {"operation_id": idempotency_key, "endpoint": endpoint,
                               "result_type": {"transfer_execute": "TRANSFER_SUCCESS", "lock_fund": "LOCK_SUCCESS"}.get(endpoint or "") or state.get("intent")}
    attempt = 0
    while True:
        try:
            outgoing = {**headers, "Idempotency-Key": idempotency_key} if idempotency_key else dict(headers)
            return runtime._http(method, path, state=state, json_body=json_body, headers=outgoing)
        except requests.RequestException:
            if attempt >= 1 or not idempotency_key:
                raise
            attempt += 1
            audit_event("EXECUTION_RETRY", user_id=state.get("user_id"), decision="RETRY", outcome="RETRY", reason="settle transport failure, idempotent retry", request_id=state.get("request_id"))


def _run_compensation(state: BankingAgentState, compensation: Optional[str]) -> Optional[dict[str, Any]]:
    """Execute the compensation action for a failed settle step.

    The core ledger is atomic, so a rejected settle call leaves no mutation
    and the compensation is a reconciliation pass, not a reverse transfer:
    confirm with the core that no reservation was created, otherwise escalate
    to human takeover.  A genuine reverse operation (UNLOCK_FUND) is executed
    by the core's own rollback/cancel flows and stays user-authorized.
    """

    if compensation == "UNLOCK_FUND":
        user_id = state.get("user_id")
        try:
            response = runtime._http("GET", f"accounts/{user_id}/balance", state=state, headers={"X-User-Id": user_id})
            if response.status_code >= 400:
                raise RuntimeError(error_message(response))
            data = response_json(response)
            if valid_balance_payload(data) and float(data.get("locked_amount") or 0) <= 0:
                return {"action": compensation, "outcome": "VERIFIED_NO_MUTATION", "message": "已核对核心账本：锁定金额为 0，无需反向解锁（核心原子回滚确认）。"}
            return {"action": compensation, "outcome": "ESCALATE_HUMAN", "message": "检测到未释放的锁定金额，请人工接管复核。"}
        except Exception as exc:
            return {"action": compensation, "outcome": "ESCALATE_HUMAN", "message": f"补偿核对失败（{exc}），请人工接管复核。"}
    if compensation == "ROLLBACK_WEALTH":
        return {"action": compensation, "outcome": "ESCALATE_HUMAN", "message": "理财结算由核心原子回滚；如持仓核对异常请人工接管。"}
    return None


def settle_failure_response(state: BankingAgentState, intent: str, message: str, *, compensation: Optional[str] = None) -> dict[str, Any]:
    """Build the safe refusal + failed plan ledger + compensation result."""

    plan = advance_plan(state, intent, outcome="FAILURE", error=message)
    audit_event("EXECUTION", user_id=state.get("user_id"), decision="ERROR", outcome="FAILURE", reason=message[:200], request_id=state.get("request_id"))
    compensation_result = _run_compensation(state, compensation)
    reply = f"{_BUSINESS_NAMES.get(intent, '本次操作')}未完成：{str(message).rstrip('。')}。"
    if compensation_result:
        reply += f"处理情况：{compensation_result['message']}"
    return {"final_response": reply, "plan": plan, "business_status": "FAILED"}


_BUSINESS_NAMES = {
    "TRANSFER": "转账", "SCHEDULED_TRANSFER": "预约转账", "SPLIT_BILL": "AA 收款",
    "CROSS_SCENE_LOCK": "资金锁定", "WEALTH_PURCHASE": "理财申购", "WEALTH_REDEEM": "理财赎回",
    "CARD_APPLY": "开卡", "CARD_LIMIT": "调整卡片限额", "CARD_FREEZE": "冻结卡片", "CARD_UNFREEZE": "解冻卡片",
    "CARD_REPORT_LOST": "挂失卡片", "CARD_UNREPORT_LOST": "解除挂失", "CARD_CHANNEL": "调整交易渠道",
    "SUBSCRIPTION_CANCEL": "取消订阅", "FRAUD_DISPUTE": "交易争议处理", "PASSWORD_CHANGE": "修改密码",
    "UNDO_OPERATION": "撤销",
}


def settle_unknown_response(state: BankingAgentState) -> dict[str, Any]:
    # A response can be lost after the core commits. Neither success, failure
    # nor rollback is known, even after our one idempotent retry is exhausted.
    audit_event("EXECUTION", user_id=state.get("user_id"), request_id=state.get("request_id"),
                decision="RECONCILE", outcome="UNKNOWN", reason="SETTLEMENT_RECEIPT_UNAVAILABLE")
    response = response_validation_failure(state, "SETTLEMENT_RECEIPT_UNAVAILABLE")
    response.update({"final_response": "本次操作已提交，暂未取得可核验回执，正在核对结果。请勿重复办理。",
                     "business_status": "PENDING_RECONCILIATION", "reconciliation": state.get("reconciliation"), "plan": state.get("plan")})
    return response
