"""Graph node 1: parse the user's turn into a validated intent (or a clarification); ``workflow_next`` feeds a compound request's next step back through it."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Optional


from planner import (
    GREEN_INTENTS,
    Intent,
    build_task_plan,
    missing_slots,
    needs_clarification,
    validate_intent_payload,
)
from security import InjectionError, audit_event, sanitize_text
from intent_language import business_text, nonexecuting_action
from bank_agent.state import (
    BankingAgentState,
    empty_state,
)
from bank_agent.runtime import (
    INJECTION_LOCKED_NOTICE,
    report_security_signal,
)
from bank_agent.risk_quiz import (
    RISK_QUIZ_INTENTS,
    fetch_risk_profile,
    handle_risk_quiz,
    start_risk_quiz,
)
from bank_agent.parsing import (
    local_parse,
    model_parse,
)
from bank_agent.dialogue import (
    ELLIPSIS_CANCEL_IT,
    ELLIPSIS_REPEAT,
    ELLIPSIS_SAME_PRODUCT,
    ELLIPSIS_SAME_TARGET,
    build_multi_read_plan,
    has_mutating_part,
    merge_pending_clarification,
    resolve_ellipsis,
    split_parts,
    split_read_requests,
)


def intent_parser_node(state: BankingAgentState) -> dict[str, Any]:
    state = {**state, "request_id": state.get("request_id") or f"agent_{uuid.uuid4().hex}"}
    raw = state.get("user_input", "")
    try:
        text = sanitize_text(raw, field="用户指令", max_length=2000)
    except InjectionError as exc:
        audit_event("PROMPT_INJECTION_BLOCKED", user_id=state.get("user_id"), request_id=state["request_id"], decision="BLOCK", outcome="FAILURE", reason=exc.code)
        reply = "检测到疑似提示词注入或不安全控制指令，已阻止本次操作。请直接描述银行业务。"
        if report_security_signal(exc.code, request_id=state["request_id"]):
            reply += INJECTION_LOCKED_NOTICE
        return {**empty_state(final_response=reply), "request_id": state["request_id"]}
    # The action words inside a negation, quotation or hypothetical are data,
    # not a request to issue an actionable approval. This guard also constrains
    # a model that extracts a confident financial intent from quoted prose.
    negated_action = re.search(
        r"(?:不要|别|不用|不必)(?:再|帮我|替我|现在|立即|马上|真的|实际|先)*"
        r"[^，,。；;!?！？]{0,12}(?:执行|操作|转账|给.{1,8}转|申购|赎回|买入|冻结|解冻|挂失|解挂|申请.{0,4}卡|取消.{0,8}订阅|锁定)",
        business_text(text),
    )
    quoted_action = re.search(r"(?:解释|翻译|分析).{0,12}(?:引文|示例|这句话)", business_text(text))
    hypothetical_action = nonexecuting_action(text)
    if negated_action or quoted_action or hypothetical_action:
        request_id = state.get("request_id") or f"gw_{uuid.uuid4().hex}"
        audit_event("INTENT_WITHHELD", user_id=state.get("user_id"), request_id=request_id,
                    decision="BLOCK", outcome="CLARIFICATION", reason="NON_EXECUTING_LANGUAGE")
        return {**empty_state(final_response="这是对操作的否定、引用或假设，我没有发起执行或审批。如需实际办理，请单独明确业务、对象和金额。"),
                "request_id": request_id, "context": {}}
    # 问卷进行中：本回合输入按答题处理，不走常规意图解析。
    prior_context = state.get("context") if isinstance(state.get("context"), dict) else {}
    compound = prior_context.get("compound_workflow") if state.get("continue_workflow") or prior_context.get("pending_clarification") or prior_context.get("risk_quiz") else None
    if compound and compound["steps"][compound["index"]].get("status") in {"SUCCEEDED", "FAILED", "CANCELLED"}:
        compound = None
    parts = split_parts(text)
    if len(parts) > 8:
        return {**empty_state(final_response="一次最多办理 8 项任务，请分成两条消息。"), "context": {}, "request_id": state["request_id"]}
    if 2 <= len(parts) <= 8:
        payloads = [validate_intent_payload(local_parse(part)) for part in parts]
        if all(item.get("intent") != Intent.UNKNOWN.value for item in payloads) and any(item["intent"] not in GREEN_INTENTS or item["intent"] in RISK_QUIZ_INTENTS for item in payloads):
            compound = {"id": f"flow_{uuid.uuid4().hex[:12]}", "index": 0,
                        "steps": [{"text": part, "payload": item, "status": "PENDING"} for part, item in zip(parts, payloads)],
                        "results": [], "summaries": []}
            text = parts[0]
    active_quiz = prior_context.get("risk_quiz") if isinstance(prior_context.get("risk_quiz"), dict) else None
    if active_quiz is not None:
        quiz_result = handle_risk_quiz(state, text, active_quiz)
        if compound and quiz_result.get("profile_result"):
            context = {**prior_context, "risk_quiz": None, "compound_workflow": compound}
            if compound["steps"][compound["index"]]["payload"]["intent"] == Intent.WEALTH_RISK_ASSESSMENT.value:
                resumed = workflow_next_node({**state, **quiz_result, "context": context})
            else:
                resumed = intent_parser_node({**state, **quiz_result, "context": context, "continue_workflow": True,
                                               "user_input": compound["steps"][compound["index"]]["text"], "final_response": None})
            resumed.update({"profile_result": quiz_result["profile_result"], "risk_quiz": {"active": False}})
            return resumed
        return quiz_result
    if prior_context.get("intent") == Intent.CROSS_SCENE_LOCK.value and re.search(r"(?:花|蛋糕).*(?:买好|买了吗|下单|订好|已经买)|^(?:已经|都|那)?(?:买好|买了吗|订好|下单)[了了吗呢？?]*$", text.strip()):
        return {**empty_state(final_response="尚未购买鲜花或蛋糕。这里只会预留资金、建立提醒和待确认的订单草稿，不会替您向商家付款下单。"),
                "context": prior_context, "request_id": state.get("request_id") or f"gw_{uuid.uuid4().hex}"}
    try:
        parsed = validate_intent_payload(model_parse(text))
    except Exception as exc:
        audit_event("INTENT_PARSE", user_id=state.get("user_id"), request_id=state["request_id"], decision="BLOCK", outcome="FAILURE", reason="INVALID_MODEL_OUTPUT", metadata={"error_type": type(exc).__name__})
        return {**empty_state(final_response="暂时无法安全解析这条指令，请补充业务类型、收款人或金额后重试。"), "request_id": state["request_id"]}
    # A comparison with one explicit product has a known business intent; only
    # its second product is missing, even without the word "理财".
    product_ids = list(parsed.get("product_ids") or [])
    if parsed.get("intent") == Intent.UNKNOWN.value and len(product_ids) == 1 and re.search(r"比较|对比", text):
        parsed["intent"] = Intent.WEALTH_COMPARE.value
        parsed["confidence"] = None
    pending = prior_context.get("pending_clarification")
    if isinstance(pending, dict) and pending.get("intent") == Intent.WEALTH_COMPARE.value and "product_ids" in (pending.get("missing") or []):
        previous = pending.get("payload") if isinstance(pending.get("payload"), dict) else {}
        previous_ids = list(previous.get("product_ids") or [])
        explicit_ids = local_parse(text).get("product_ids") or []
        if len(previous_ids) == 1 and len(explicit_ids) == 1 and text.strip().upper() == explicit_ids[0]:
            # Only a bare, recognized product ID answers this slot. A full new
            # comparison replaces the pair through the regular merge below;
            # card/transaction IDs and duplicate products cannot fill it.
            parsed["intent"] = Intent.WEALTH_COMPARE.value
            parsed["product_ids"] = list(dict.fromkeys(previous_ids + explicit_ids))
    # A reply that only answers the previous question belongs to that request.
    parsed = merge_pending_clarification(parsed, state, text)
    # Explicit back-references ("再转一次", "还是上次那个") reuse the previous
    # turn's request; the operation is re-authorized from scratch.
    prior = state.get("context") if isinstance(state.get("context"), dict) else {}
    parsed = validate_intent_payload(resolve_ellipsis(parsed, prior, text, state.get("execution_result")))
    if compound:
        step = compound["steps"][compound["index"]]
        if parsed.get("intent") != step["payload"].get("intent"):
            compound = None
        else:
            compound = json.loads(json.dumps(compound))
            compound["steps"][compound["index"]]["payload"] = dict(parsed)
    # 首次咨询理财（推荐/申购/赎回）或显式要求测评且尚无风险档案：
    # 触发 8 题问卷（C1-C5），完成后再继续办理；浏览类查询不强制。
    if parsed.get("intent") in RISK_QUIZ_INTENTS and not any((parsed.get("slots") or {}).get(f"{field}_ambiguous") for field in ("amount", "target", "date_range", "request")):
        profile_response = fetch_risk_profile(state)
        has_profile = bool(profile_response and profile_response.get("data"))
        if parsed.get("intent") == Intent.WEALTH_RISK_ASSESSMENT.value or not has_profile:
            # 重做（已有档案）与首测话术不同；重做前先看余量，避免出卷后交卷失败。
            attempts = (profile_response or {}).get("attempts") or {}
            if has_profile and attempts.get("allowed") is False:
                return empty_state(final_response=attempts.get("message") or "风险测评次数已达上限，请稍后再试。")
            return start_risk_quiz({**state, "context": {**prior_context, "compound_workflow": compound}}, retake=has_profile)
    # Several read-only asks in one sentence become one ordered sub-task DAG.
    sub_requests = split_read_requests(text)
    if len(split_parts(text)) > 1 and not compound and not sub_requests:
        return {**empty_state(final_response="这条消息包含多项业务，其中有尚未明确的请求。请逐项补充业务、对象和金额，或分开办理。"), "context": {}, "request_id": state["request_id"]}
    # A compound request that also contains a mutating part is never split and
    # never silently half-answered: the user is asked to handle it in steps.
    mixed_write = has_mutating_part(text) and not sub_requests and not compound
    # Resolve only explicit conversational references.  A prior recipient is
    # never silently used for a fresh request; the user must say “同一位/他”
    # and the operation remains behind the normal approval gate.
    if parsed.get("intent") in {Intent.TRANSFER.value, Intent.SCHEDULED_TRANSFER.value, Intent.SPLIT_BILL.value}:
        if not parsed.get("target") and prior.get("target") and any(word in text for word in ("他", "她", "同一位", "刚才", "继续")):
            parsed["target"] = prior.get("target")
        if parsed.get("amount") in (None, 0) and prior.get("amount") and any(word in text for word in ("同样金额", "一样多", "刚才金额")):
            parsed["amount"] = prior.get("amount")
    parsed["extra_info"] = parsed.get("extra_info") or text
    if sub_requests:
        # One utterance, several read-only asks: compile them into a single
        # dependency-ordered DAG and answer them in sequence.
        plan = build_multi_read_plan(sub_requests, plan_id=f"plan_{uuid.uuid4().hex[:12]}", sequential=bool(re.search(r"然后|接着|之后|再", text)))
        parsed = {**sub_requests[0], "extra_info": text}
    else:
        plan = build_task_plan(parsed, plan_id=f"plan_{uuid.uuid4().hex[:12]}")
    clarification = needs_clarification(parsed) if not sub_requests else False
    if mixed_write:
        clarification = True
    # Remember exactly what is still missing so a bare answer on the next turn
    # can be folded back into this intent (see merge_pending_clarification).
    missing_fields = missing_slots(parsed)
    pending_clarification: Optional[dict[str, Any]] = None
    if clarification and parsed.get("intent") != Intent.UNKNOWN.value:
        pending_clarification = {
            "intent": parsed.get("intent"),
            "missing": missing_fields,
            "confidence": parsed.get("confidence"),
            "payload": {key: parsed.get(key) for key in ("intent", "target", "amount", "date_scope", "date_range", "extra_info", "participants", "product_ids", "slots", "scheduled_at", "event_at")},
        }
    response = None
    ambiguous_fields = [field for field in ("amount", "target", "date_range", "request") if (parsed.get("slots") or {}).get(f"{field}_ambiguous")]
    if ambiguous_fields:
        labels = {"amount": "最终金额（只提供一个明确数值）", "target": "最终收款人或业务对象（只选一位）", "date_range": "查询的起止日期（例如 2026-09-01 到 2026-09-15）", "request": "各项业务（例如“查询余额，同时查看本月账单”）"}
        if parsed["intent"].startswith("CARD_"):
            labels["target"] = "要操作的卡片（只选一张）"
        elif parsed["intent"] == Intent.SUBSCRIPTION_CANCEL.value:
            labels["target"] = "要取消的订阅商户（只选一家）"
        elif parsed["intent"] in {Intent.WEALTH_PURCHASE.value, Intent.WEALTH_REDEEM.value}:
            labels["target"] = "要操作的理财产品（只选一款）"
        response = "这条指令存在歧义，请确认" + "、".join(labels[field] for field in ambiguous_fields) + "。"
    elif mixed_write:
        response = "这条消息里既有查询也有需要确认的变更操作。为避免误操作，请分开逐条办理：我还没有执行任何变更。"
    elif clarification:
        intent = parsed.get("intent")
        if intent == Intent.UNKNOWN.value:
            if any(phrase in text for phrase in ELLIPSIS_REPEAT + ELLIPSIS_SAME_TARGET + ELLIPSIS_SAME_PRODUCT + ELLIPSIS_CANCEL_IT):
                response = "我没有找到可以沿用的上一次操作，请直接说明要办理的业务、对象和金额。"
            else:
                response = "我可以帮您查询余额/账单、转账、分析消费、管理理财、卡片或订阅。请告诉我具体要办理什么。"
        elif intent == Intent.SPLIT_BILL.value and missing_fields:
            labels = {"amount": "分摊总金额", "participants": "至少一位参与人（姓名或手机号）"}
            response = "请补充" + "和".join(labels[item] for item in missing_fields) + "。"
        elif intent == Intent.CROSS_SCENE_LOCK.value and missing_fields:
            labels = {"target": "生日对象（姓名或关系称呼）", "amount": "需要锁定的金额", "scheduled_at": "生日日期（例如 10月5号 或 2026-10-05）"}
            response = "请补充" + "、".join(labels[item] for item in missing_fields) + "。我不会替您猜测。"
        elif intent == Intent.WEALTH_COMPARE.value and "product_ids" in missing_fields:
            if parsed.get("product_ids"):
                response = "请再提供一个用于比较的理财产品编号。"
            else:
                response = "请至少提供两个理财产品编号（例如：比较 CJ01002 和 AY90204）。"
        elif intent == Intent.WEALTH_RISK_ASSESSMENT.value:
            labels = {"loss_tolerance": "风险承受程度（低/中/高）", "experience": "投资经验（新手/基础/专业）", "horizon_years": "预计投资期限（几年）"}
            if missing_fields:
                response = "风险评估需要您明确回答：" + "、".join(labels[item] for item in missing_fields) + "。我不会替您填写问卷。"
        elif intent in {Intent.TRANSFER.value, Intent.SCHEDULED_TRANSFER.value} and "target" in missing_fields:
            response = "请告诉我收款人姓名或手机号，我不会替您猜测收款账户。"
        elif intent in {Intent.WEALTH_PURCHASE.value, Intent.WEALTH_REDEEM.value} and "target" in missing_fields:
            response = "请补充理财产品编号（例如 CJ01002），我不会猜测产品。"
        elif intent == Intent.CARD_LIMIT.value and "amount" in missing_fields:
            response = "请补充希望设置的卡片额度（例如 5000 元）。"
        elif intent == Intent.SUBSCRIPTION_CANCEL.value and "target" in missing_fields:
            response = "请说明要取消哪个订阅（例如：取消 Apple 订阅）。查看现有订阅可以说“我有哪些订阅”。"
        elif "amount" in missing_fields and intent in {Intent.TRANSFER.value, Intent.SCHEDULED_TRANSFER.value, Intent.WEALTH_PURCHASE.value, Intent.WEALTH_REDEEM.value}:
            response = "请补充明确的金额（例如 300 元）。"
        if response is None:
            # A clarification must always carry a question: derive a targeted
            # prompt from the missing slots instead of returning nothing.
            labels = {
                "amount": "金额",
                "target": "对象（收款人/产品/订阅）",
                "scheduled_at": "日期（例如 明天上午十点 或 10月5号）",
                "event_at": "日期（例如 明天上午十点 或 10月5号）",
                "participants": "参与人",
                "product_ids": "产品编号",
                "loss_tolerance": "风险承受程度",
                "experience": "投资经验",
                "horizon_years": "投资期限",
                "card_channel": "要开通或关闭的交易渠道（网上支付或境外交易）",
                "undo_kind": "要撤销的操作（预约转账、AA 收款请求或资金锁定）",
            }
            missing_names = [labels.get(item, item) for item in missing_fields]
            response = ("请补充：" + "、".join(missing_names) + "。") if missing_names else "请确认这次要办理的具体业务和操作要求。"
    result = {
        **parsed,
        "request_id": state.get("request_id") or f"agent_{uuid.uuid4().hex}",
        "risk_level": None,
        "auth_token": None,
        "mfa_proof": None,
        "user_confirmed": False,
        "action_requirements": [],
        "execution_result": None,
        "execution_results": None,
        "business_status": "NEEDS_INPUT" if clarification else None,
        "reconciliation": None,
        "parallel_reads": not bool(re.search(r"然后|接着|之后|再", text)),
        "completed_workflow": None,
        "continue_workflow": False,
        "chart_data": None,
        "todo_card": None,
        "final_response": response,
        "clarification_required": clarification,
        "plan": plan.to_dict(),
        # Keep only normalized, non-secret conversational slots.  This lets a
        # follow-up such as “给同一位再转同样金额” be resolved explicitly,
        # while never carrying tokens, OTPs or free-form model instructions
        # into the next turn.
        "context": {
            "intent": parsed.get("intent"),
            "target": parsed.get("target"),
            "amount": parsed.get("amount"),
            "participants": list(parsed.get("participants") or []),
            "product_ids": list(parsed.get("product_ids") or []),
            "slots": dict(parsed.get("slots") or {}),
            "scheduled_at": parsed.get("scheduled_at"),
            "event_at": parsed.get("event_at"),
            "date_scope": parsed.get("date_scope"),
            "date_range": parsed.get("date_range"),
            "pending_clarification": pending_clarification,
            "risk_quiz": None,
            "compound_workflow": compound,
        },
        "event_at": parsed.get("event_at"),
        "reminder_at": None,
        "sub_requests": sub_requests or None,
    }
    audit_event("INTENT_PARSE", user_id=state.get("user_id"), decision="CLARIFY" if clarification else "ALLOW", outcome="PARSED", request_id=result["request_id"], metadata={"intent": parsed["intent"], "confidence": parsed.get("confidence"), "parse_meta": parsed.get("parse_meta"), "sub_intents": [item.get("intent") for item in sub_requests] or None})
    return result


def workflow_next_node(state: BankingAgentState) -> dict[str, Any]:
    flow = (state.get("context") or {}).get("compound_workflow")
    if not flow:
        return {}
    flow = json.loads(json.dumps(flow))
    index = flow["index"]
    status = state.get("business_status") or ("SUCCEEDED" if state.get("execution_result") else "FAILED")
    flow["steps"][index]["status"] = status
    context = {**(state.get("context") or {}), "compound_workflow": flow}
    if status != "SUCCEEDED":
        return {"context": context}
    result = state.get("execution_result")
    if result and result not in flow["results"]:
        flow["results"].append(result)
        flow["summaries"].append(state.get("final_response") or "")
    if index + 1 >= len(flow["steps"]):
        return {"context": context, "final_response": "\n\n".join(flow["summaries"]), "business_status": "SUCCEEDED"}
    flow["index"] += 1
    next_text = flow["steps"][flow["index"]]["text"]
    return intent_parser_node({**state, "context": context, "user_input": next_text,
                               "request_id": f"agent_{uuid.uuid4().hex}", "final_response": None, "continue_workflow": True})
