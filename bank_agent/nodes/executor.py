"""Graph node 4: run green reads, or settle an approved write through Core."""

from __future__ import annotations

import contextvars
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional
from urllib.parse import urlencode

import requests

from planner import (
    GREEN_INTENTS,
    Intent,
    PlanExecutor,
    analyze_transactions,
    filter_transactions,
)
from security import audit_event
from bank_agent import runtime
from bank_agent.analysis_codegen import answer_bill_question
from bank_agent.understanding import referenced_case, referenced_transactions
from bank_agent.state import (
    BankingAgentState,
)
from bank_agent.runtime import (
    error_message,
    response_json,
)
from bank_agent.verification import (
    core_success,
    response_validation_failure,
    valid_text_field,
)
from bank_agent.risk_quiz import (
    fetch_risk_profile,
)
from bank_agent.settlement import (
    advance_plan,
    consumed_task_ids,
    plan_from_state,
    settle_failure_response,
    settle_unknown_response,
    settle_with_retry,
)
from bank_agent.dialogue import (
    advance_multi_plan,
)
from bank_agent.nodes.risk import core_operation
from bank_agent.nodes.responder import (
    responder_node,
)


def _execute_green(state: BankingAgentState, intent: str) -> dict[str, Any]:
    user_id = state["user_id"]
    if intent == Intent.BANKING_HELP.value:
        from bank_agent.consultation import consult

        return consult(state)
    if intent in {Intent.DISPUTE_QUERY.value, Intent.BANKING_HELP.value}:
        if intent == Intent.DISPUTE_QUERY.value:
            case_id = referenced_case(state)
            path = "disputes" + ("?" + urlencode({"case_id": case_id}) if case_id else "")
        response = runtime._http("GET", path, state=state, headers={"X-User-Id": user_id})
        if response.status_code >= 400:
            raise RuntimeError(error_message(response))
        return {"type": intent, "data": response_json(response)}
    if intent == Intent.BALANCE_QUERY.value:
        response = runtime._http("GET", f"accounts/{user_id}/balance", state=state, headers={"X-User-Id": user_id})
        if response.status_code >= 400:
            raise RuntimeError(error_message(response))
        return {"type": "BALANCE", "data": response_json(response)}
    if intent in {Intent.BILL_QUERY.value, Intent.BILL_ANALYSIS.value}:
        response = runtime._http("GET", f"accounts/{user_id}/transactions", state=state, headers={"X-User-Id": user_id})
        if response.status_code >= 400:
            raise RuntimeError(error_message(response))
        rows = response_json(response).get("data", [])
        rows = filter_transactions(rows, state.get("date_scope") or "ALL", date_range=state.get("date_range"))
        date_scoped_rows = list(rows)
        slots = state.get("slots")
        if not isinstance(slots, dict):
            slots = {}
        category = slots.get("category")
        question = str(state.get("user_input") or "")
        suspicious_only = slots.get("suspicious_only") is True
        reference_ids, reference_kind = referenced_transactions(state)
        if slots.get("transaction_id"):
            reference_ids, reference_kind = [str(slots["transaction_id"])], "TRANSACTION_REFERENCE"
        if reference_ids:
            rows = [row for row in rows if row.get("tx_id") in reference_ids]
            category = None
            suspicious_only = reference_kind == "SUSPICIOUS_TRANSACTIONS"
        specific_overseas = (
            any(word in question for word in ("境外", "海外", "国外"))
            and any(word in question for word in ("那笔", "这笔", "该笔", "怎么回事", "什么情况", "是什么消费", "是什么交易"))
        )
        # Overseas transactions may be classified as travel/shopping rather
        # than a literal "境外消费" category.  Use recorded merchant/category
        # evidence, within the already authorized account and date scope.
        if specific_overseas:
            rows = [row for row in rows if any(
                word in str(row.get(field) or "")
                for field in ("merchant", "category") for word in ("境外", "海外", "国外")
            )]
            if category in {"境外", "海外", "国外", "境外消费", "海外消费", "国外消费"}:
                category = None
        if suspicious_only:
            category = None
            references: list[Any] = []
            if re.search(r"那|这|刚才|上面|其中", question) and state.get("memory_principal") == user_id:
                for observation in reversed(list((state.get("observations") or {}).values())):
                    if observation.get("intent") in {Intent.BILL_QUERY.value, Intent.BILL_ANALYSIS.value}:
                        references = observation.get("suspicious_transaction_ids") or []
                        break
            if not references:
                candidates = analyze_transactions(rows, user_id=user_id, request_id=state.get("request_id"))
                references = [item.get("tx_id") for item in candidates.get("anomalies") or []]
            rows = [row for row in rows if row.get("tx_id") in references]
        if category is not None:
            if not valid_text_field(category, max_length=120):
                raise RuntimeError("交易分类无效，请明确需要查询的类别")
            category = category.strip()
            # Apply only the requested classification after the date filter;
            # a merchant name is not evidence of a transaction's category.
            rows = [row for row in rows if str(row.get("category") or "其他").strip() == category]
        facts = analyze_transactions(rows, user_id=user_id, request_id=state.get("request_id"))
        result = {"type": intent, "date_scope": state.get("date_scope") or "ALL", "records": rows, "facts": facts}
        if state.get("date_range"):
            result["date_range"] = state["date_range"]
        if category is not None:
            result["category"] = category
        if suspicious_only:
            result["specific_query"] = "SUSPICIOUS_TRANSACTIONS"
            count = re.search(r"([0-9]{1,3})\s*笔", question)
            if count:
                result["mentioned_count"] = int(count.group(1))
        elif reference_kind:
            result["specific_query"] = reference_kind
        elif specific_overseas:
            result["specific_query"] = "OVERSEAS_TRANSACTION"
        else:
            # Open questions ("哪个月花得最多") get an answer computed by
            # generated code in the read-only sandbox over these same rows.
            custom = answer_bill_question(question, rows, all_rows=date_scoped_rows,
                                          user_id=user_id, request_id=state.get("request_id"))
            if custom is not None:
                result["custom_analysis"] = custom
        return result
    if intent == Intent.WEALTH_HOLDINGS.value:
        response = runtime._http("GET", f"wealth/holdings/{user_id}", state=state, headers={"X-User-Id": user_id})
        if response.status_code >= 400:
            raise RuntimeError(error_message(response))
        return {"type": intent, "data": response_json(response)}
    if intent in {Intent.WEALTH_QUERY.value, Intent.WEALTH_RECOMMEND.value}:
        path = f"wealth/recommend?user_id={user_id}" if intent == Intent.WEALTH_RECOMMEND.value else f"wealth/products?user_id={user_id}"
        if intent == Intent.WEALTH_RECOMMEND.value:
            profile = ((fetch_risk_profile(state) or {}).get("data") or {}).get("risk_profile")
            if profile not in {"CONSERVATIVE", "BALANCED", "AGGRESSIVE", "C1", "C2", "C3", "C4", "C5"}:
                raise RuntimeError("暂时无法读取您的风险档案，请稍后重试")
            path += f"&risk_profile={profile}"
        response = runtime._http("GET", path, state=state, headers={"X-User-Id": user_id})
        if response.status_code >= 400:
            raise RuntimeError(error_message(response))
        return {"type": intent, "data": response_json(response)}
    if intent == Intent.WEALTH_RISK_ASSESSMENT.value:
        slots = state.get("slots")
        if not isinstance(slots, dict):
            slots = {}
        # Do not let a missing/invalid questionnaire answer turn into a
        # silently guessed LOW/NONE/one-year assessment.  The planner normally
        # routes this to clarification; this defensive check also protects the
        # executor when a caller constructs state directly or a model returns
        # an unexpected shape.
        tolerance = str(slots.get("loss_tolerance") or "").upper().strip()
        experience = str(slots.get("experience") or "").upper().strip()
        horizon_raw = slots.get("horizon_years")
        if tolerance not in {"LOW", "MEDIUM", "HIGH"} or experience not in {"NONE", "BASIC", "PROFESSIONAL"}:
            return {"final_response": "风险评估问卷尚未完整填写，请回答风险承受程度和投资经验后再继续。", "clarification_required": True}
        try:
            if isinstance(horizon_raw, bool):
                raise ValueError
            horizon_years = int(horizon_raw)  # type: ignore[arg-type]  # None/garbage is rejected below
        except (TypeError, ValueError):
            return {"final_response": "风险评估还需要明确的投资期限（例如 3 年），我不会替您猜测。", "clarification_required": True}
        if horizon_years < 0 or horizon_years > 100:
            return {"final_response": "投资期限需在 0 到 100 年之间，请重新填写。", "clarification_required": True}
        response = runtime._http(
            "POST",
            "wealth/risk-assessment",
            state=state,
            json_body={
                "user_id": user_id,
                "horizon_years": horizon_years,
                "loss_tolerance": tolerance,
                "experience": experience,
            },
            headers={"X-User-Id": user_id},
        )
        if response.status_code >= 400:
            raise RuntimeError(error_message(response))
        return {"type": intent, "data": response_json(response)}
    if intent == Intent.WEALTH_COMPARE.value:
        product_ids = list(state.get("product_ids") or [])
        query = urlencode({"user_id": user_id, "product_ids": ",".join(product_ids)})
        response = runtime._http("GET", f"wealth/compare?{query}", state=state, headers={"X-User-Id": user_id})
        if response.status_code >= 400:
            raise RuntimeError(error_message(response))
        return {"type": intent, "data": response_json(response)}
    if intent == Intent.CARD_QUERY.value:
        response = runtime._http("GET", f"cards/{user_id}", state=state, headers={"X-User-Id": user_id})
        if response.status_code >= 400:
            raise RuntimeError(error_message(response))
        return {"type": intent, "data": response_json(response)}
    raise RuntimeError("不支持的查询类型")


def _execute_subscription_query(state: BankingAgentState) -> dict[str, Any]:
    """YELLOW read: Core releases subscription data only for a confirmed nonce."""

    intent = Intent.SUBSCRIPTION_QUERY.value
    user_id = state["user_id"]
    token = state.get("auth_token")
    if not token:
        return {"final_response": "订阅查询授权已失效，请重新发起查询。"}
    try:
        response = settle_with_retry(
            state,
            method="POST",
            path="operations/execute",
            json_body={"user_id": user_id, "operation": intent, "target": state.get("target"), "auth_token": token, "user_confirmed": bool(state.get("user_confirmed")), "reason": state.get("extra_info") or "智能银行操作"},
            headers={"X-User-Id": user_id, "X-Auth-Token": token, "X-User-Confirmation": "true" if state.get("user_confirmed") else "false"},
            idempotency_key=_settle_idempotency_key(state, intent),
        )
    except requests.RequestException:
        return settle_unknown_response(state)
    if response.status_code >= 400:
        return settle_failure_response(state, intent, error_message(response), compensation=_settle_compensation(state, intent))
    envelope = response_json(response)
    data = envelope.get("data") if isinstance(envelope, dict) else None
    if not core_success(envelope, nested_status="QUERIED", required=("subscriptions", "candidates", "renewal_reminders")) or not isinstance(data, dict):
        return response_validation_failure(state, "SUBSCRIPTION_QUERY_RECEIPT_INVALID")
    slots = state.get("slots") or {}
    result: dict[str, Any]
    if slots.get("renewal_reminder") is True:
        # Reminders are created only after the confirmed query succeeded.
        params = {"days_before": slots.get("days_before", 2)}
        if state.get("target"):
            params["merchant"] = state["target"]
        response = runtime._http("POST", f"subscriptions/{user_id}/reminders?{urlencode(params)}", state=state, headers={"X-User-Id": user_id})
        if response.status_code >= 400:
            return settle_failure_response(state, intent, error_message(response))
        result = {"type": "SUBSCRIPTION_REMINDER", "data": response_json(response)}
    elif slots.get("detect_subscriptions") is True:
        result = {"type": intent, "data": {"code": 200, "data": data["candidates"], "renewal_reminders": data["renewal_reminders"]}}
    else:
        result = {"type": intent, "data": {"code": 200, "data": data["subscriptions"]}}
    if result.get("type") == intent:
        from bank_agent.query_focus import subscription_view
        from bank_agent.verification import valid_subscription_collection

        if not valid_subscription_collection(result["data"]):
            return response_validation_failure(state, "READ_COLLECTION_INVALID")
        result["data"] = subscription_view(result["data"], state)
    return {"execution_result": result, "plan": advance_plan(state, intent, outcome="SUCCESS", data={"status": "QUERIED"})}


def _settle_compensation(state: BankingAgentState, intent: str) -> Optional[str]:
    plan = plan_from_state(state)
    if plan is None:
        return None
    _, settle_id = consumed_task_ids(plan, intent)
    if not settle_id:
        return None
    try:
        return PlanExecutor.find(plan, settle_id).compensation
    except KeyError:
        return None


def _settle_idempotency_key(state: BankingAgentState, intent: str) -> Optional[str]:
    plan = plan_from_state(state)
    if plan is None:
        return None
    _, settle_id = consumed_task_ids(plan, intent)
    if not settle_id:
        return None
    try:
        return PlanExecutor.find(plan, settle_id).idempotency_key
    except KeyError:
        return None


def executor_node(state: BankingAgentState) -> dict[str, Any]:
    if state.get("final_response"):
        return {"final_response": state.get("final_response")}
    intent = state.get("intent", Intent.UNKNOWN.value)
    sub_requests = state.get("sub_requests") or []
    if len(sub_requests) > 1:
        def read_one(index_item):
            index, item = index_item
            sub_intent = str(item.get("intent") or "")
            sub_state = {**state, **item, "user_input": item.get("extra_info") or "", "final_response": None, "execution_results": None}
            try:
                result = _execute_green(sub_state, sub_intent)
                if result.get("clarification_required") or result.get("final_response"):
                    return {"type": sub_intent, "business_status": "NEEDS_INPUT", "error": result.get("final_response"), "sub_index": index}
                verified = responder_node({**sub_state, "execution_result": result})
                if verified.get("business_status") == "FAILED":
                    return {"type": sub_intent, "business_status": "FAILED", "error": "查询回执未通过核验", "sub_index": index}
                return {**result, "business_status": "SUCCEEDED", "summary": verified.get("final_response"), "sub_index": index}
            except (requests.RequestException, RuntimeError, ValueError):
                return {"type": sub_intent, "business_status": "FAILED", "error": "查询暂时不可用", "sub_index": index}

        indexed = list(enumerate(sub_requests))
        if state.get("parallel_reads"):
            with ThreadPoolExecutor(max_workers=min(4, len(indexed)), thread_name_prefix="bank-read") as pool:
                futures = [pool.submit(contextvars.copy_context().run, read_one, item) for item in indexed]
                results = [future.result() for future in futures]
        else:
            results = [read_one(item) for item in indexed]
        plan_data = advance_multi_plan(state, sub_requests)
        if plan_data:
            for task in plan_data["tasks"]:
                index = int(task["task_id"].split("_", 1)[0])
                if results[index]["business_status"] != "SUCCEEDED":
                    task["status"] = "FAILED"
                    task["error"] = results[index].get("error")
        chart = next((item.get("records") for item in results if item.get("type") == Intent.BILL_ANALYSIS.value and item.get("business_status") == "SUCCEEDED"), None)
        return {"execution_result": None, "execution_results": results, "chart_data": chart, "plan": plan_data}
    try:
        if intent in GREEN_INTENTS:
            result = _execute_green(state, intent)
            if result.get("clarification_required") or result.get("final_response"):
                # Never mark the plan completed for an unperformed query.
                plan = plan_from_state(state)
                return {"final_response": result.get("final_response"), "clarification_required": True, "plan": plan.to_dict() if plan else None}
            return {"execution_result": result, "chart_data": result.get("records") if intent == Intent.BILL_ANALYSIS.value else None, "plan": advance_plan(state, intent, outcome="SUCCESS", data=result.get("facts") if isinstance(result.get("facts"), dict) else None)}
        user_id = state["user_id"]
        if intent == Intent.CROSS_SCENE_LOCK.value:
            token = state.get("auth_token")
            if not token:
                return {"final_response": "资金锁定授权已失效，请重新发起该任务。"}
            try:
                response = settle_with_retry(
                    state,
                    method="POST",
                    path="accounts/lock_fund",
                    json_body={"user_id": user_id, "amount": state.get("amount"), "event_at": state.get("event_at") or state.get("scheduled_at"), "reminder_at": state.get("reminder_at"), "reason": state.get("extra_info") or "跨场景联动储备", "auth_token": token, "mfa_proof": state.get("mfa_proof"), "user_confirmed": bool(state.get("user_confirmed"))},
                    headers={"X-User-Id": user_id, "X-Auth-Token": token, "X-MFA-Proof": state.get("mfa_proof") or "", "X-User-Confirmation": "true"},
                    idempotency_key=_settle_idempotency_key(state, intent),
                )
            except requests.RequestException:
                return settle_unknown_response(state)
            if response.status_code >= 400:
                return settle_failure_response(state, intent, error_message(response), compensation=_settle_compensation(state, intent))
            data = response_json(response)
            pending_notes: dict[str, str] = {"schedule_orders": f"提醒与订单草稿已挂载核心任务中心（{data.get('reminder_task_id')} / {', '.join(map(str, data.get('order_task_ids') or []))}），到期保持 WAITING_USER，不自动下单"}
            return {"execution_result": {"type": "LOCK_SUCCESS", "data": data}, "todo_card": {"title": "🎂 爱人生日提醒与代订备忘录", "locked_fund": f"¥{float(state.get('amount') or 1000):,.2f}", "schedule": "生日倒计时 2 天自动提醒订购鲜花与蛋糕", "status": "已创建（订单草稿待到期确认）", "task_id": data.get("task_id"), "reminder_task_id": data.get("reminder_task_id"), "order_task_ids": data.get("order_task_ids", [])}, "plan": advance_plan(state, intent, outcome="SUCCESS", data={"lock_id": data.get("lock_id"), "task_id": data.get("task_id"), "reminder_task_id": data.get("reminder_task_id"), "order_task_ids": data.get("order_task_ids")}, pending_notes=pending_notes)}
        if intent == Intent.TRANSFER.value:
            token = state.get("auth_token")
            if not token:
                return {"final_response": "转账授权已失效，请重新发起转账。"}
            remark = state.get("extra_info") or "智能转账"
            headers = {"X-User-Id": user_id, "X-Auth-Token": token, "X-User-Confirmation": "true" if state.get("user_confirmed") else "false"}
            mfa_proof = state.get("mfa_proof")
            if mfa_proof:
                headers["X-MFA-Proof"] = mfa_proof
            try:
                response = settle_with_retry(
                    state,
                    method="POST",
                    path="transfers/execute",
                    json_body={"from_user": user_id, "target_name_or_id": state.get("target"), "amount": state.get("amount"), "card_id": (state.get("slots") or {}).get("paying_card_id"), "remark": remark, "mfa_proof": state.get("mfa_proof"), "user_confirmed": bool(state.get("user_confirmed"))},
                    headers=headers,
                    idempotency_key=_settle_idempotency_key(state, intent),
                )
            except requests.RequestException:
                return settle_unknown_response(state)
            if response.status_code >= 400:
                return settle_failure_response(state, intent, error_message(response), compensation=_settle_compensation(state, intent))
            data = response_json(response)
            return {"execution_result": {"type": "TRANSFER_SUCCESS", "data": data}, "plan": advance_plan(state, intent, outcome="SUCCESS", data={"transaction_id": data.get("transaction_id"), "transferred_amount": data.get("transferred_amount")})}
        if intent == Intent.SUBSCRIPTION_QUERY.value:
            return _execute_subscription_query(state)
        # The remaining write scenarios use explicit mock endpoints.  If an
        # endpoint is not enabled, report a safe refusal rather than claiming
        # completion.
        if intent in {Intent.SCHEDULED_TRANSFER.value, Intent.SPLIT_BILL.value, Intent.SUBSCRIPTION_CANCEL.value, Intent.CARD_APPLY.value, Intent.CARD_LIMIT.value, Intent.CARD_FREEZE.value, Intent.CARD_UNFREEZE.value, Intent.CARD_REPORT_LOST.value, Intent.CARD_UNREPORT_LOST.value, Intent.PASSWORD_CHANGE.value, Intent.WEALTH_PURCHASE.value, Intent.WEALTH_REDEEM.value, Intent.FRAUD_DISPUTE.value, Intent.CARD_CHANNEL.value, Intent.UNDO_OPERATION.value}:
            try:
                response = settle_with_retry(
                    state,
                    method="POST",
                    path="operations/execute",
                    json_body={"user_id": user_id, "operation": core_operation(intent), "target": state.get("target"), "amount": state.get("amount"), "product_id": state.get("target") if intent.startswith("WEALTH_") else None, "card_id": state.get("target") if intent.startswith("CARD_") else (state.get("slots") or {}).get("paying_card_id"), "transaction_id": (state.get("slots") or {}).get("transaction_id"), "new_limit": state.get("amount") if intent == Intent.CARD_LIMIT.value else None, "merchant": state.get("target") if intent == Intent.SUBSCRIPTION_CANCEL.value else None, "participants": state.get("participants") or [], "product_ids": state.get("product_ids") or [], "scheduled_at": state.get("scheduled_at"), "channel": (state.get("slots") or {}).get("card_channel") if intent == Intent.CARD_CHANNEL.value else None, "enable": (state.get("slots") or {}).get("channel_enabled") if intent == Intent.CARD_CHANNEL.value else None, "auth_token": state.get("auth_token"), "mfa_proof": state.get("mfa_proof"), "user_confirmed": bool(state.get("user_confirmed")), "reason": state.get("extra_info") or "智能银行操作"},
                    headers={"X-User-Id": user_id, "X-Auth-Token": state.get("auth_token") or "", "X-MFA-Proof": state.get("mfa_proof") or "", "X-User-Confirmation": "true" if state.get("user_confirmed") else "false"},
                    idempotency_key=_settle_idempotency_key(state, intent),
                )
            except requests.RequestException:
                return settle_unknown_response(state)
            if response.status_code >= 400:
                return settle_failure_response(state, intent, error_message(response), compensation=_settle_compensation(state, intent))
            data = response_json(response)
            pending_notes = {}
            if intent == Intent.SPLIT_BILL.value:
                pending_notes["notify_participants"] = f"分摊请求已登记（{data.get('task_id')}），参与人通知为扩展项，保持待办"
            return {"execution_result": {"type": intent, "data": data}, "plan": advance_plan(state, intent, outcome="SUCCESS", data={"status": data.get("status"), "task_id": data.get("task_id")}, pending_notes=pending_notes)}
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        audit_event("EXECUTION", user_id=state.get("user_id"), decision="ERROR", outcome="FAILURE", reason=type(exc).__name__, request_id=state.get("request_id"))
        return {"final_response": "核心服务暂时不可用，本次查询未完成，请稍后重试。", "business_status": "FAILED", "plan": advance_plan(state, intent, outcome="FAILURE", error=type(exc).__name__)}
    return {"execution_result": {"type": "UNKNOWN"}, "plan": advance_plan(state, intent, outcome="FAILURE", error="UNKNOWN_OPERATION")}
