"""Graph nodes 2-3: Core risk precheck, then the human approval interrupt for yellow/red actions."""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

from planner import (
    CARD_CHANNEL_NAMES,
    UNDO_KINDS,
    GREEN_INTENTS,
    RED_INTENTS,
    YELLOW_INTENTS,
    Intent,
)
from security import audit_event
from bank_agent import runtime
from bank_agent.state import (
    BankingAgentState,
)
from bank_agent.runtime import (
    error_message,
    response_json,
    interrupt,
)
from bank_agent.verification import (
    finite_number,
)


_CARD_TAIL_RE = r"(?:尾号|末四位|后四位)\s*(?:为|是)?\s*([0-9]{4})(?![0-9])"
# Money-moving intents that settle against one of the user's cards.  With a
# single active card the core picks it; with several it refuses to guess and
# the caller must name one.
_PAYING_CARD_INTENTS = {Intent.TRANSFER.value, Intent.SCHEDULED_TRANSFER.value, Intent.WEALTH_PURCHASE.value}


def _card_by_tail(state: BankingAgentState, text: str) -> tuple[Optional[str], Optional[str], bool]:
    """Resolve a spoken card tail ("尾号3082") to exactly one of the user's cards.

    Returns ``(card_id, message, ask_again)``.  ``ask_again`` separates a gap the
    user can close by answering (no such tail, or two cards share it) from a
    failure they cannot (the core did not answer), so only the first becomes a
    clarification instead of a dead-end error.
    """

    tail = re.search(_CARD_TAIL_RE, text)
    if not tail:
        return None, None, False
    response = runtime._http("GET", f"cards/{state['user_id']}", state=state, headers={"X-User-Id": state["user_id"]})
    if response.status_code >= 400:
        return None, f"暂时无法核对卡片尾号：{error_message(response)}", False
    rows = response_json(response).get("data")
    if not isinstance(rows, list):
        return None, "核心返回的卡片列表无效，请稍后再试。", False
    matches = [card for card in rows if isinstance(card, dict) and str(card.get("last4") or str(card.get("number_masked") or "")[-4:]) == tail.group(1)]
    if not matches:
        return None, f"没有找到尾号{tail.group(1)}的卡片，请提供明确的卡片编号（例如 CARD-001），我不会替您选卡。", True
    if len(matches) > 1 or not matches[0].get("card_id"):
        return None, f"尾号{tail.group(1)}对应多张卡片，请提供明确的卡片编号（例如 CARD-001），我不会替您选卡。", True
    return str(matches[0]["card_id"]), None, False


def _pending_slot_clarification(
    state: BankingAgentState,
    intent: str,
    message: str,
    missing: list[str],
    *,
    drop_target: bool = False,
    drop_amount: bool = False,
) -> dict[str, Any]:
    """Ask for one more slot while keeping everything the user already gave.

    The pending payload is what lets the next turn's bare answer ("用尾号3082
    的卡") fold back into this intent instead of starting over.
    """

    payload = {key: state.get(key) for key in (
        "intent", "target", "amount", "date_scope", "extra_info", "participants",
        "product_ids", "slots", "scheduled_at", "event_at",
    )}
    context = dict(state.get("context") or {})
    if drop_target:
        payload["target"] = None
        context["target"] = None
    if drop_amount:
        payload["amount"] = None
        context["amount"] = None
    context["pending_clarification"] = {"intent": intent, "missing": missing, "confidence": None, "payload": payload}
    return {
        "risk_level": "UNKNOWN", "clarification_required": True,
        "final_response": message, "auth_token": None,
        "action_requirements": [], "context": context,
        **({"target": None} if drop_target else {}),
        **({"amount": None} if drop_amount else {}),
    }


# Core refusals the user can fix by naming someone (something) else or another
# amount. The task stays open with everything else kept, so "李四" after
# "不允许向本人账户转账" continues the same transfer instead of starting over.
_TARGET_RETRY_CODES = {"TARGET_NOT_FOUND", "SELF_TRANSFER", "CONTACT_TARGET_AMBIGUOUS", "TARGET_INACTIVE", "PRODUCT_NOT_FOUND", "HOLDING_NOT_FOUND"}
_AMOUNT_RETRY_CODES = {"INSUFFICIENT_FUNDS", "AMOUNT_LIMIT_EXCEEDED", "INVALID_AMOUNT", "AMOUNT_NOT_ALLOWED", "MIN_AMOUNT_NOT_MET", "CARD_LIMIT_EXCEEDED"}
_PAYEE_INTENTS = {Intent.TRANSFER.value, Intent.SCHEDULED_TRANSFER.value}


def _retry_after_refusal(state: BankingAgentState, intent: str, response: requests.Response) -> Optional[dict[str, Any]]:
    # A step of a multi-step request stops the chain instead (see compound workflows).
    if (state.get("context") or {}).get("compound_workflow"):
        return None
    detail = response_json(response).get("detail")
    code = detail.get("code") if isinstance(detail, dict) else None
    reason = error_message(response).rstrip("。")
    amount = state.get("amount")
    kept_amount = f"金额 ¥{float(amount):,.2f}" if isinstance(amount, (int, float)) and amount > 0 else None
    if code in _TARGET_RETRY_CODES and state.get("target"):
        if intent in _PAYEE_INTENTS:
            ask = "请告诉我要转给哪位（联系人姓名或手机号）" + (f"，{kept_amount}会保留。" if kept_amount else "。")
        elif intent.startswith("WEALTH_"):
            ask = "请重新说明理财产品编号（例如 LX00118）" + (f"，{kept_amount}会保留。" if kept_amount else "。")
        else:
            return None
        return _pending_slot_clarification(state, intent, f"本次操作未发起，资金没有变动。{reason}。{ask}", ["target"], drop_target=True)
    if code in _AMOUNT_RETRY_CODES and kept_amount:
        target = state.get("target")
        keep = f"，{'收款人' if intent in _PAYEE_INTENTS else '其他信息'}{' ' + str(target) if target and intent in _PAYEE_INTENTS else ''}会保留"
        return _pending_slot_clarification(state, intent, f"本次操作未发起，资金没有变动。{reason}。请说一个新的金额{keep}。", ["amount"], drop_amount=True)
    return None


def _redeem_all_principal(state: BankingAgentState) -> tuple[Optional[float], Optional[str]]:
    """Total principal the user actually holds in the product to redeem fully."""

    product_id = str(state.get("target") or "").upper()
    response = runtime._http("GET", f"wealth/holdings/{state['user_id']}", state=state, headers={"X-User-Id": state["user_id"]})
    if response.status_code >= 400:
        return None, f"暂时无法读取您的持仓：{error_message(response)}"
    rows = response_json(response).get("data")
    if not isinstance(rows, list):
        return None, "核心返回的持仓数据无效，请稍后再试。"
    total = 0.0
    for row in rows:
        if not isinstance(row, dict) or str(row.get("product_id") or "").upper() != product_id:
            continue
        principal = row.get("principal")
        if isinstance(principal, bool) or not isinstance(principal, (int, float)) or not math.isfinite(float(principal)):
            continue
        total += float(principal)
    total = round(total, 2)
    if total <= 0:
        return None, f"您当前没有 {product_id or '该产品'} 的持仓，无法赎回。"
    return total, None


def core_operation(intent: str) -> str:
    """Core's operation name for a generic write intent."""

    return {Intent.FRAUD_DISPUTE.value: "FRAUD_PROTECT", Intent.UNDO_OPERATION.value: "UNDO_TASK"}.get(intent, intent)


def _resolve_undo_target(state: BankingAgentState) -> dict[str, Any]:
    """Pick the reservation to undo from Core's own list of undoable tasks."""

    kind = str((state.get("slots") or {}).get("undo_kind") or "")
    if kind == "SETTLED_TRANSFER":
        return {"risk_level": "UNKNOWN", "business_status": "NEEDS_INPUT", "final_response": "已完成的转账无法撤销或退回，系统不会发起反向付款。如需追回，请联系收款人，或在任务页请求人工处理。仍未执行的预约转账、未付款的 AA 请求和资金锁定可以撤销。"}
    response = runtime._http("GET", "tasks/undoable", state=state, headers={"X-User-Id": state["user_id"]})
    if response.status_code >= 400:
        raise RuntimeError(error_message(response))
    rows = [row for row in (response_json(response).get("data") or []) if isinstance(row, dict)]
    match = next((row for row in rows if kind == "LAST" or row.get("task_type") == kind), None)
    if match is None:
        label = UNDO_KINDS.get(kind, "操作")
        return {"risk_level": "UNKNOWN", "business_status": "NEEDS_INPUT", "final_response": f"当前没有可撤销的{label}。已执行的转账、申购等资金操作不能撤销；可在任务页查看全部安排。"}
    return {"task_id": str(match["task_id"])}


def risk_checker_node(state: BankingAgentState) -> dict[str, Any]:
    if state.get("final_response") or state.get("clarification_required"):
        return {"risk_level": "UNKNOWN"}
    from bank_agent.understanding import READ_INTENTS, fraud_action_requested, inquiry_route, unresolved_transaction_reference
    from intent_language import business_text, execution_constraint

    spoken = str(state.get("user_input") or state.get("extra_info") or "")
    flow = (state.get("context") or {}).get("compound_workflow")
    if isinstance(flow, dict) and isinstance(flow.get("steps"), list):
        index = flow.get("index", 0)
        if isinstance(index, int) and 0 <= index < len(flow["steps"]):
            spoken = str(flow["steps"][index].get("text") or spoken)
    spoken = business_text(spoken)
    intent = state.get("intent", Intent.UNKNOWN.value)
    if unresolved_transaction_reference({**state, "user_input": spoken}):
        return {"risk_level": "UNKNOWN", "auth_token": None, "action_requirements": [], "business_status": "NEEDS_INPUT",
                "clarification_required": True, "final_response": "我还没找到这几笔所指的已核验交易。请提供流水号，或先查询/分析账单后指定其中的记录；我不会用全部流水替代。"}
    nonaction = (state.get("parse_meta") or {}).get("dialogue_act") in {"QUERY", "EXPLAIN", "UNSUPPORTED", "UNCLEAR", "SOCIAL"}
    if ((execution_constraint(spoken) and intent not in READ_INTENTS) or
            (nonaction and intent not in READ_INTENTS | {Intent.SUBSCRIPTION_QUERY.value}) or
            intent not in GREEN_INTENTS | {Intent.SUBSCRIPTION_QUERY.value} and (inquiry_route(spoken) or (intent == Intent.FRAUD_DISPUTE.value and not fraud_action_requested(spoken)))):
        return {"risk_level": "UNKNOWN", "auth_token": None, "action_requirements": [], "business_status": "NEEDS_INPUT",
                "clarification_required": True, "final_response": "当前是在询问或查询，我没有发起冻结、建单或其他变更。请说明需要查看的记录或想了解的规则。"}
    sub_requests = state.get("sub_requests") or []
    if len(sub_requests) > 1 and all(str(item.get("intent")) in GREEN_INTENTS for item in sub_requests):
        # Every sub-request is read-only (validated at parse time), so the whole
        # compound request stays green and needs no authorization nonce.
        audit_event("POLICY_DECISION", user_id=state.get("user_id"), risk_level="GREEN", decision="ALLOW", outcome="AUTO_EXECUTE", request_id=state.get("request_id"), metadata={"sub_intents": [item.get("intent") for item in sub_requests]})
        return {"risk_level": "GREEN", "auth_token": None, "action_requirements": []}
    intent = state.get("intent", Intent.UNKNOWN.value)
    if intent in GREEN_INTENTS:
        audit_event("POLICY_DECISION", user_id=state.get("user_id"), risk_level="GREEN", decision="ALLOW", outcome="AUTO_EXECUTE", request_id=state.get("request_id"), metadata={"intent": intent})
        return {"risk_level": "GREEN", "auth_token": None, "action_requirements": []}
    if intent in RED_INTENTS or intent in (YELLOW_INTENTS - {Intent.TRANSFER.value, Intent.CROSS_SCENE_LOCK.value}):
        # All mutating scenarios obtain a server-issued operation nonce.  The
        # browser/agent cannot manufacture this token or elevate a yellow
        # operation to red (the core owns the policy table).
        try:
            def clarify_card(message: str) -> dict[str, Any]:
                return _pending_slot_clarification(state, intent, message, ["target"], drop_target=True)

            if intent == Intent.UNDO_OPERATION.value:
                undo_target = _resolve_undo_target(state)
                if "task_id" not in undo_target:
                    return undo_target
                state = {**state, "target": undo_target["task_id"]}
            slots = dict(state.get("slots") or {})
            amount = state.get("amount")
            spoken = str(state.get("user_input") or state.get("extra_info") or "")
            if intent.startswith("CARD_") and intent != Intent.CARD_APPLY.value:
                card_id, failure, ask_again = _card_by_tail(state, spoken)
                if failure:
                    return clarify_card(failure) if ask_again else {"risk_level": "ERROR", "final_response": failure}
                if card_id:
                    if state.get("target") and state["target"] != card_id:
                        return clarify_card("您提供的卡片编号与尾号不一致，请核对并明确要操作的卡片编号。")
                    state = {**state, "target": card_id, "context": {**(state.get("context") or {}), "target": card_id}}
            # A transfer's payee and its paying card are different slots: the
            # card must never overwrite the recipient the user named.
            paying_card_id = slots.get("paying_card_id")
            if intent in _PAYING_CARD_INTENTS and not paying_card_id:
                paying_card_id, failure, ask_again = _card_by_tail(state, spoken)
                if failure:
                    if not ask_again:
                        return {"risk_level": "ERROR", "final_response": failure}
                    return _pending_slot_clarification(state, intent, failure, ["paying_card"])
                if paying_card_id:
                    slots = {**slots, "paying_card_id": paying_card_id}
            if intent == Intent.WEALTH_REDEEM.value and slots.get("redeem_all") is True and amount in (None, 0):
                amount, failure = _redeem_all_principal(state)
                if failure:
                    return {"risk_level": "UNKNOWN", "final_response": failure}
            response = runtime._http("POST", "risk/precheck/operation", state=state, json_body={"user_id": state["user_id"], "operation": core_operation(intent), "amount": amount or 0.0, "target": state.get("target"), "product_id": state.get("target") if intent.startswith("WEALTH_") else None, "card_id": state.get("target") if intent.startswith("CARD_") else paying_card_id, "transaction_id": slots.get("transaction_id"), "merchant": state.get("target") if intent == Intent.SUBSCRIPTION_CANCEL.value else None, "participants": state.get("participants") or [], "product_ids": state.get("product_ids") or [], "scheduled_at": state.get("scheduled_at"), "new_limit": amount if intent == Intent.CARD_LIMIT.value else None, "channel": slots.get("card_channel") if intent == Intent.CARD_CHANNEL.value else None, "enable": slots.get("channel_enabled") if intent == Intent.CARD_CHANNEL.value else None, "reason": state.get("extra_info") or "智能银行操作"}, headers={"X-User-Id": state["user_id"]})
            if response.status_code >= 400:
                detail = response_json(response).get("detail")
                code = detail.get("code") if isinstance(detail, dict) else None
                if code in {"CARD_ID_REQUIRED", "CARD_NOT_FOUND"}:
                    if intent.startswith("CARD_") and intent != Intent.CARD_APPLY.value:
                        return clarify_card(f"{error_message(response)}。请回复要操作的卡片编号（例如 CARD-001）。")
                    if intent in _PAYING_CARD_INTENTS:
                        return _pending_slot_clarification(
                            state, intent,
                            f"{error_message(response)}。可以直接说卡号，也可以用尾号指定（例如：用尾号3082的卡）。",
                            ["paying_card"],
                        )
                retry = _retry_after_refusal(state, intent, response)
                if retry is not None:
                    return retry
                return {"risk_level": "ERROR", "final_response": f"本次操作未发起，资金没有变动。原因：{error_message(response)}"}
            data = response_json(response)
            target = data.get("target") or state.get("target")
            if target == f"__{data.get('operation') or intent}__":
                # Core's internal binding placeholder for a target-less action;
                # Core derives it again at execution, so keep the user's slot.
                target = state.get("target")
            resolved = {"risk_level": data.get("risk_level", "ERROR"), "auth_token": data.get("auth_token"), "action_requirements": data.get("required_actions", []), "target": target, "context": {**(state.get("context") or {}), "target": target}, "transaction": data.get("transaction"), "risk_reasons": data.get("risk_reasons")}
            if isinstance(data.get("undo"), dict):
                # Core describes the reservation from its own record; the
                # approval card shows that, not the user's paraphrase.
                resolved["context"]["undo_summary"] = str(data["undo"].get("summary") or "")[:200]
            if paying_card_id:
                resolved["slots"] = slots
            if amount != state.get("amount"):
                resolved["amount"] = amount
            return resolved
        except (requests.RequestException, RuntimeError) as exc:
            return {"risk_level": "ERROR", "final_response": f"暂时无法连接核心风控服务：{exc}"}
    if intent == Intent.CROSS_SCENE_LOCK.value:
        try:
            response = runtime._http("POST", "risk/precheck/lock", state=state, json_body={"user_id": state["user_id"], "amount": state.get("amount"), "event_at": state.get("scheduled_at"), "reason": state.get("extra_info") or "跨场景联动储备"}, headers={"X-User-Id": state["user_id"]})
            if response.status_code >= 400:
                return {"risk_level": "ERROR", "final_response": f"资金预检未通过：{error_message(response)}"}
            data = response_json(response)
            return {
                "risk_level": data.get("risk_level", "ERROR"),
                "auth_token": data.get("auth_token"),
                "action_requirements": data.get("required_actions", []),
                "event_at": data.get("event_at") or state.get("scheduled_at"),
                "reminder_at": data.get("reminder_at"),
            }
        except (requests.RequestException, RuntimeError) as exc:
            return {"risk_level": "ERROR", "final_response": f"暂时无法连接核心风控服务：{exc}"}
    # Immediate transfers use the transfer-specific precheck because the Core
    # resolves a payee and computes the daily total there.  Scheduled and AA
    # requests are handled by the generic operation gateway above (which signs
    # their schedule/participant context); they must never fall through to a
    # yellow flow without a server-issued token.
    if intent == Intent.TRANSFER.value:
        target = state.get("target")
        amount = state.get("amount")
        if not target or amount is None:
            return {"risk_level": "UNKNOWN", "final_response": "请补充收款人和金额后再继续。"}
        slots = dict(state.get("slots") or {})
        paying_card_id = slots.get("paying_card_id")
        try:
            if not paying_card_id:
                paying_card_id, failure, ask_again = _card_by_tail(state, str(state.get("user_input") or state.get("extra_info") or ""))
                if failure:
                    if not ask_again:
                        return {"risk_level": "ERROR", "final_response": failure}
                    return _pending_slot_clarification(state, intent, failure, ["paying_card"])
                if paying_card_id:
                    slots = {**slots, "paying_card_id": paying_card_id}
            response = runtime._http("POST", "risk/precheck", state=state, json_body={"from_user": state["user_id"], "target_name_or_id": target, "amount": amount, "card_id": paying_card_id, "remark": state.get("extra_info") or "智能转账"}, headers={"X-User-Id": state["user_id"]})
            if response.status_code >= 400:
                detail = response_json(response).get("detail")
                if isinstance(detail, dict) and detail.get("code") in {"CARD_ID_REQUIRED", "CARD_NOT_FOUND"}:
                    return _pending_slot_clarification(
                        state, intent,
                        f"{error_message(response)}。可以直接说卡号，也可以用尾号指定（例如：用尾号3082的卡）。",
                        ["paying_card"],
                    )
                retry = _retry_after_refusal(state, intent, response)
                if retry is not None:
                    return retry
                return {"risk_level": "ERROR", "final_response": f"本次操作未发起，资金没有变动。原因：{error_message(response)}"}
            data = response_json(response)
            return {
                "risk_level": data.get("risk_level", "ERROR"),
                "auth_token": data.get("auth_token"),
                "action_requirements": data.get("required_actions", []),
                **({"slots": slots} if paying_card_id else {}),
                "context": {
                    **(state.get("context") or {}),
                    # Names, phone numbers and saved aliases resolve at the
                    # core. Bind those facts to this turn, never a later one.
                    "transfer_receipt_payee": {
                        "request_id": state.get("request_id"),
                        "requested_target": target,
                        "target_id": data.get("target_id"),
                        "target_name": data.get("target_name"),
                    },
                },
            }
        except (requests.RequestException, RuntimeError) as exc:
            return {"risk_level": "ERROR", "final_response": f"暂时无法连接核心风控服务：{exc}"}
    return {"risk_level": "UNKNOWN", "final_response": "未能理解您的银行业务需求，请重新描述。"}


def _missing_required_factors(required_actions: Any, *, confirmed: bool, mfa_proof: Any) -> list[str]:
    """Return required-action labels not yet satisfied by the resume payload.

    A server-issued MFA proof implies the core validated every red-zone factor
    (SMS OTP and the signed face attestation), so its presence satisfies all
    non-confirmation actions.  This agent-side gate is defense in depth: the
    core still re-verifies everything before settling.
    """

    satisfied: set[str] = set()
    if confirmed:
        satisfied.add("USER_CLICK_CONFIRM")
    if isinstance(mfa_proof, str) and mfa_proof:
        satisfied.update({"SMS_OTP", "FACE_RECOGNITION"})
    required = {str(action) for action in (required_actions or []) if action}
    return sorted(required - satisfied)


def _human_readable_action(state: BankingAgentState) -> str:
    """Describe a pending action in business language, never as an intent code."""

    intent = str(state.get("intent") or "")
    target = state.get("target")
    try:
        amount = float(state.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if intent == Intent.CROSS_SCENE_LOCK.value:
        event_at = state.get("event_at") or state.get("scheduled_at") or "待定日期"
        event_label = str(event_at)
        try:
            event = datetime.fromisoformat(event_label.replace("Z", "+00:00"))
            local_zone = timezone(timedelta(hours=8))
            event = event.replace(tzinfo=local_zone) if event.tzinfo is None else event.astimezone(local_zone)
            event_label = event.strftime("%Y-%m-%d %H:%M") + "，北京时间"
        except ValueError:
            pass
        # Format only the approval text; keep the core's canonical timestamp.
        return f"为{target or '指定对象'}的生日（{event_label}）锁定活期资金 ¥{amount:,.2f}，创建提醒和订单草稿，尚未购买鲜花或蛋糕"
    if intent == Intent.PASSWORD_CHANGE.value:
        return "修改登录密码（演示仅轮换凭证版本，不保存明文密码）"
    if intent == Intent.CARD_LIMIT.value:
        return f"把卡片 {target or '默认卡片'} 的日限额调整为 ¥{amount:,.2f}"
    if intent == Intent.CARD_FREEZE.value:
        return f"冻结卡片 {target or ''} 的交易".strip()
    if intent == Intent.FRAUD_DISPUTE.value:
        return "核验可疑境外交易并冻结关联卡片、创建争议工单"
    if intent == Intent.CARD_UNFREEZE.value:
        return f"解冻卡片 {target or ''} 的交易".strip()
    if intent == Intent.CARD_REPORT_LOST.value:
        return f"挂失卡片 {target or ''}".strip()
    if intent == Intent.CARD_UNREPORT_LOST.value:
        return f"解除卡片 {target or ''} 的挂失".strip()
    if intent == Intent.CARD_APPLY.value:
        return "申请一张虚拟卡"
    if intent == Intent.UNDO_OPERATION.value:
        return (state.get("context") or {}).get("undo_summary") or "撤销一笔尚未执行的安排"
    if intent == Intent.CARD_CHANNEL.value:
        slots = state.get("slots") or {}
        action = "开通" if slots.get("channel_enabled") else "关闭"
        return f"{action}卡片 {target or ''} 的{CARD_CHANNEL_NAMES.get(str(slots.get('card_channel')), '交易渠道')}".replace("卡片  的", "卡片的")
    if intent == Intent.SUBSCRIPTION_QUERY.value:
        slots = state.get("slots") or {}
        if slots.get("renewal_reminder") is True:
            return f"查询{target or '全部'}订阅并在扣款前 {slots.get('days_before', 2)} 天建立续费提醒"
        if slots.get("detect_subscriptions") is True:
            return "查询已登记的订阅代扣，并从账单识别疑似周期扣费"
        return f"查询{target or '全部'}订阅与代扣记录"
    if intent == Intent.SUBSCRIPTION_CANCEL.value:
        return f"取消 {target or '该'} 的自动扣费"
    if intent == Intent.WEALTH_PURCHASE.value:
        return f"申购理财产品 {target or ''} ¥{amount:,.2f}".strip()
    if intent == Intent.WEALTH_REDEEM.value:
        return f"赎回理财产品 {target or ''} ¥{amount:,.2f}".strip()
    if intent == Intent.SCHEDULED_TRANSFER.value:
        description = f"预约于 {state.get('scheduled_at') or '指定时间'} 向 {target or '指定收款人'} 转账 ¥{amount:,.2f}"
        note = re.search(r"备注\s*[:：为是]?\s*([^，,；;。]+)", str(state.get("extra_info") or ""))
        return description + (f"，备注：{note.group(1).strip()}" if note else "")
    if intent == Intent.SPLIT_BILL.value:
        participants = "、".join(str(item) for item in (state.get("participants") or []))
        return f"发起 AA 分摊 ¥{amount:,.2f}（参与人：{participants or '待确认'}）"
    description = f"向 {target or '指定收款人'} 转账 ¥{amount:,.2f}"
    note = re.search(r"备注\s*[:：为是]?\s*([^，,；;。]+)", str(state.get("extra_info") or ""))
    return description + (f"，备注：{note.group(1).strip()}" if note else "")


def human_review_node(state: BankingAgentState) -> dict[str, Any]:
    risk = state.get("risk_level")
    if risk not in {"YELLOW", "RED"} or state.get("final_response"):
        return {"user_confirmed": bool(state.get("user_confirmed", False))}
    intent = state.get("intent")
    desc = _human_readable_action(state)
    payload: dict[str, Any] = {
        "message": f"您正准备进行：{desc}",
        "risk_level": risk,
        "intent": intent,
        "target": state.get("target"),
        "amount": state.get("amount"),
        "auth_token": state.get("auth_token"),
        "required_actions": state.get("action_requirements", []),
        "request_id": state.get("request_id"),
        "plan": state.get("plan"),
    }
    if intent == Intent.FRAUD_DISPUTE.value:
        payload["transaction"] = state.get("transaction")
        payload["risk_reasons"] = state.get("risk_reasons") or ["境外交易", "可疑交易", "用户否认消费"]
        transaction = payload["transaction"] if isinstance(payload["transaction"], dict) else {}
        reasons = "、".join(str(item) for item in payload["risk_reasons"])
        amount = transaction.get("amount")
        amount_text = f"¥{float(amount):,.2f}" if finite_number(amount, nonnegative=True) else "金额待核验"
        payload["message"] = (
            f"已核验交易 {transaction.get('tx_id') or '未知编号'}："
            f"{transaction.get('merchant') or '未知商户'}，{amount_text}，"
            f"时间 {transaction.get('time') or '未知'}。风险依据：{reasons}。"
            "继续后将强验证并冻结关联卡片、创建人工争议工单；不会自动退款。"
        )
    if interrupt is None:
        return {"final_response": "当前运行环境未启用人机确认组件，操作已安全暂停。"}
    decision = interrupt(payload)
    # UI may resume with True for legacy yellow flows, or with a dict carrying
    # server-issued MFA proof.  No token/OTP from the model is trusted here.
    if isinstance(decision, dict):
        confirmed = bool(decision.get("confirmed", decision.get("resume", False)))
        proof = decision.get("mfa_proof")
        if not confirmed:
            return {"user_confirmed": False, "mfa_proof": proof, "final_response": "操作已由您主动取消，核心银行未做任何资金扣减。"}
        missing = _missing_required_factors(state.get("action_requirements") or [], confirmed=True, mfa_proof=proof)
        if missing:
            audit_event(
                "MFA_FACTOR_GATE",
                user_id=state.get("user_id"),
                risk_level=str(state.get("risk_level") or "UNKNOWN"),
                decision="BLOCK",
                outcome="FAILURE",
                reason=f"缺少验证因子: {','.join(missing)}",
                request_id=state.get("request_id"),
            )
            return {"user_confirmed": False, "final_response": f"多因子验证未完成（缺少 {'、'.join(missing)}），操作已安全中止，未执行任何资金变更。"}
        return {"user_confirmed": True, "mfa_proof": proof}
    if not decision:
        return {"user_confirmed": False, "final_response": "操作已由您主动取消，核心银行未做任何资金扣减。"}
    missing = _missing_required_factors(state.get("action_requirements") or [], confirmed=True, mfa_proof=None)
    if missing:
        audit_event(
            "MFA_FACTOR_GATE",
            user_id=state.get("user_id"),
            risk_level=str(state.get("risk_level") or "UNKNOWN"),
            decision="BLOCK",
            outcome="FAILURE",
            reason=f"缺少验证因子: {','.join(missing)}",
            request_id=state.get("request_id"),
        )
        return {"user_confirmed": False, "final_response": f"多因子验证未完成（缺少 {'、'.join(missing)}），操作已安全中止，未执行任何资金变更。"}
    return {"user_confirmed": True}
