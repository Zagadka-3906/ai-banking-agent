"""Graph node 1: parse the user's turn into a validated intent (or a clarification); ``workflow_next`` feeds a compound request's next step back through it."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Optional, cast


from planner import (
    GREEN_INTENTS,
    Intent,
    build_task_plan,
    missing_slots,
    needs_clarification,
    validate_intent_payload,
)
from security import InjectionError, audit_event, sanitize_text
from bank_agent.understanding import enforce_inquiry, understanding_context
from intent_language import business_text
from bank_agent import runtime
from bank_agent.state import (
    BankingAgentState,
    empty_state,
)


def _payee_names(state: BankingAgentState) -> list[str]:
    """Up to five payee names the caller can use; best effort, empty on any failure."""

    user_id = state.get("user_id")
    if not user_id:
        return []
    names: list[str] = []
    for path in (f"contacts/{user_id}/book", f"contacts/{user_id}"):
        try:
            response = runtime._http("GET", path, state=state, headers={"X-User-Id": user_id})
            rows = response.json().get("data") if response.status_code < 400 else None
        except Exception:
            continue
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and (row.get('account_id') == user_id or row.get('contact_id') == user_id):
                continue
            name = (row.get("name") or row.get("display_name")) if isinstance(row, dict) else None
            if isinstance(name, str) and 0 < len(name.strip()) <= 20 and name.strip() not in names:
                names.append(name.strip())
    return names[:5]


_RISK_LEVEL_QUERY = re.compile(r"风险(?:等级|类型|档案|评级|承受能力)|测评结果|(?:我是|属于)(?:什么|哪种|哪类)(?:投资者|风险)")
_RISK_QUIZ_START = re.compile(r"重新|重做|再做|再测|开始|我想做|做一下|测一下|去测评")


def _risk_level_answer(state: BankingAgentState, prior_context: dict[str, Any]) -> dict[str, Any]:
    """“我的风险等级是什么”: the stored level with its meaning, not a product list."""

    profile = (fetch_risk_profile(state) or {}).get("data")
    base = {"request_id": state.get("request_id") or f"gw_{uuid.uuid4().hex}", "context": prior_context,
            "intent": Intent.WEALTH_QUERY.value, "risk_level": "GREEN"}
    if isinstance(profile, dict) and profile.get("risk_profile"):
        grade = str(profile["risk_profile"])
        label = f"{grade} {profile.get('risk_type') or ''}".strip()
        return {**empty_state(final_response=f"您当前的风险等级为 **{label}**，详情如下："), **base,
                "business_status": "SUCCEEDED", "clarification_required": False,
                "profile_result": {"grade": grade, "type": str(profile.get("risk_type") or ""),
                                   "traits": str(profile.get("traits") or ""),
                                   "matching_levels": str(profile.get("matching_levels") or "")},
                "suggestions": ["推荐理财产品", "查看我的持仓", "重新做风险测评"]}
    return {**empty_state(final_response="您还没有完成风险测评。说“我想做风险测评”即可开始（共 8 题）。"), **base,
            "business_status": "SUCCEEDED", "clarification_required": False, "suggestions": ["我想做风险测评"]}


_PAYEE_REFUSAL_CODES ={"TARGET_NOT_FOUND", "SELF_TRANSFER", "CONTACT_TARGET_AMBIGUOUS", "TARGET_INACTIVE"}


def _payee_refusal(state: BankingAgentState, target: str) -> Optional[str]:
    """Core's reason a named payee cannot be paid, or None (also when unsure)."""

    user_id = state.get("user_id")
    if not user_id or not target:
        return None
    try:
        response = runtime._http("POST", "payees/check", state=state,
                                 json_body={"user_id": user_id, "target": target}, headers={"X-User-Id": user_id})
        if response.status_code < 400:
            return None
        detail = response.json().get("detail")
    except Exception:
        return None
    if isinstance(detail, dict) and detail.get("code") in _PAYEE_REFUSAL_CODES:
        return str(detail.get("message") or "").rstrip("。") or None
    return None
from bank_agent.runtime import (
    INJECTION_LOCKED_NOTICE,
    report_security_signal,
)
from bank_agent.risk_quiz import (
    RISK_QUIZ_INTENTS,
    fetch_risk_profile,
    handle_risk_quiz,
    is_quiz_reply,
    remind_risk_quiz,
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
    bind_pending_amount_reference,
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
    # Language constraints apply to executable proposals after understanding;
    # they must not short-circuit consultation or erase an unfinished task.
    # 问卷进行中：本回合输入按答题处理，不走常规意图解析。
    prior_context = state.get("context")
    if not isinstance(prior_context, dict):
        prior_context = {}
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
    if active_quiz is None and not compound and _RISK_LEVEL_QUERY.search(text) and not _RISK_QUIZ_START.search(text):
        return _risk_level_answer(state, prior_context)
    new_request =(local_parse(text).get("intent") if active_quiz is not None and not compound
                   and not is_quiz_reply(text, active_quiz) else Intent.UNKNOWN.value)
    if new_request in RISK_QUIZ_INTENTS:
        # Buying, redeeming or a recommendation needs the finished questionnaire:
        # keep the answers so far and say so, rather than restarting it.
        answered = len((active_quiz or {}).get("answers") or {})
        if new_request == Intent.WEALTH_RISK_ASSESSMENT.value:
            return remind_risk_quiz(state, active_quiz or {}, f"风险测评正在进行（已记录 {answered}/8 题），请继续作答，或回复“退出测评”。")
        return remind_risk_quiz(state, active_quiz or {}, f"风险测评还没完成（已记录 {answered}/8 题），完成后就能办理这项业务。请继续作答，或回复“退出测评”。")
    if new_request != Intent.UNKNOWN.value:
        # A clear new request ("我的持仓", "赎回…") leaves the questionnaire instead
        # of being read as an answer; the user is never trapped in it.
        audit_event("RISK_QUIZ", user_id=state.get("user_id"), decision="ALLOW", outcome="LEFT_FOR_REQUEST", request_id=state.get("request_id"))
        left = intent_parser_node(cast(BankingAgentState, {**state, "context": {**prior_context, "risk_quiz": None}}))
        left["risk_quiz"] = {"active": False}
        if isinstance(left.get("final_response"), str) and left["final_response"]:
            left["final_response"] = "已退出未完成的风险测评（本次答案未保存）。\n\n" + left["final_response"]
        return left
    if active_quiz is not None:
        quiz_result = handle_risk_quiz(state, text, active_quiz)
        if compound and quiz_result.get("profile_result"):
            context = {**prior_context, "risk_quiz": None, "compound_workflow": compound}
            if compound["steps"][compound["index"]]["payload"]["intent"] == Intent.WEALTH_RISK_ASSESSMENT.value:
                resumed = workflow_next_node(cast(BankingAgentState, {**state, **quiz_result, "context": context}))
            else:
                resumed = intent_parser_node(cast(BankingAgentState, {**state, **quiz_result, "context": context, "continue_workflow": True,
                                               "user_input": compound["steps"][compound["index"]]["text"], "final_response": None}))
            resumed.update({"profile_result": quiz_result["profile_result"], "risk_quiz": {"active": False}})
            return resumed
        return quiz_result
    if prior_context.get("intent") == Intent.CROSS_SCENE_LOCK.value and re.search(r"(?:花|蛋糕).*(?:买好|买了吗|下单|订好|已经买)|^(?:已经|都|那)?(?:买好|买了吗|订好|下单)[了了吗呢？?]*$", text.strip()):
        return {**empty_state(final_response="尚未购买鲜花或蛋糕。这里只会预留资金、建立提醒和待确认的订单草稿，不会替您向商家付款下单。"),
                "context": prior_context, "request_id": state.get("request_id") or f"gw_{uuid.uuid4().hex}"}
    try:
        with understanding_context(state):
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
    from bank_agent.dialogue_state import resume_task

    restored = resume_task(state, parsed, text)
    if restored:
        prior_context = {**prior_context, "pending_clarification": restored}
        state = {**state, "context": prior_context}
    pending = prior_context.get("pending_clarification")
    if isinstance(pending, dict) and pending.get("intent") == Intent.WEALTH_COMPARE.value and "product_ids" in (pending.get("missing") or []):
        previous = pending.get("payload")
        if not isinstance(previous, dict):
            previous = {}
        previous_ids = list(previous.get("product_ids") or [])
        explicit_ids = local_parse(text).get("product_ids") or []
        if len(previous_ids) == 1 and len(explicit_ids) == 1 and text.strip().upper() == explicit_ids[0]:
            # Only a bare, recognized product ID answers this slot. A full new
            # comparison replaces the pair through the regular merge below;
            # card/transaction IDs and duplicate products cannot fill it.
            parsed["intent"] = Intent.WEALTH_COMPARE.value
            parsed["product_ids"] = list(dict.fromkeys(previous_ids + explicit_ids))
    parsed = bind_pending_amount_reference(parsed, state, text)
    # A reply that only answers the previous question belongs to that request.
    understood_meta = dict(parsed.get("parse_meta") or {})
    if understood_meta.get("dialogue_act") not in {"SOCIAL", "UNSUPPORTED", "UNCLEAR"}:
        parsed = merge_pending_clarification(parsed, state, text)
    if understood_meta.get("dialogue_act"):
        parsed["parse_meta"] = {**(parsed.get("parse_meta") or {}), **understood_meta}
    # Explicit back-references ("再转一次", "还是上次那个") reuse the previous
    # turn's request; the operation is re-authorized from scratch.
    prior = state.get("context")
    if not isinstance(prior, dict):
        prior = {}
    parsed = validate_intent_payload(resolve_ellipsis(parsed, prior, text, state.get("execution_result")))
    parsed = validate_intent_payload(enforce_inquiry(parsed, business_text(text)))
    if (parsed.get("parse_meta") or {}).get("execution_constraint"):
        audit_event("INTENT_WITHHELD", user_id=state.get("user_id"), request_id=state["request_id"],
                    decision="READ_ONLY", outcome="CONSULTATION", reason="NON_EXECUTING_LANGUAGE")
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
    consultation = (parsed.get("intent") == Intent.BANKING_HELP.value
                    and (parsed.get("parse_meta") or {}).get("dialogue_act") in {"QUERY", "EXPLAIN"})
    semantic_read = (parsed.get("intent") in GREEN_INTENTS - RISK_QUIZ_INTENTS
                     and (parsed.get("parse_meta") or {}).get("dialogue_act") in {"QUERY", "EXPLAIN"})
    if consultation:
        sub_requests = []
    if len(split_parts(text)) > 1 and not compound and not sub_requests and not consultation and not semantic_read:
        return {**empty_state(final_response="这条消息包含多项业务，其中有尚未明确的请求。请逐项补充业务、对象和金额，或分开办理。"), "context": {}, "request_id": state["request_id"]}
    # A compound request that also contains a mutating part is never split and
    # never silently half-answered: the user is asked to handle it in steps.
    mixed_write = has_mutating_part(text) and not sub_requests and not compound and not consultation
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
            act = (parsed.get("parse_meta") or {}).get("dialogue_act")
            if act == "SOCIAL":
                response = {"greeting": "你好，我在。想查账户情况，还是了解某项业务？",
                            "thanks": "不客气。还有要核对的记录，继续告诉我就好。", "goodbye": "好的，有需要再来找我。",
                            "capabilities": "可以查账户、账单、卡片和争议工单，也可以先问业务规则；实际办理会单独请您确认。"}.get(
                                str((parsed.get("parse_meta") or {}).get("social_kind")), "我在，有需要可以继续告诉我。")
            elif act == "UNSUPPORTED":
                from bank_agent.understanding import unsupported_reply
                response = unsupported_reply(parsed.get("parse_meta") or {})
            elif act == "UNCLEAR":
                response = "我还没有确定这次的问题。您希望解释刚才的结果、查询已有记录，还是办理一项新操作？"
            elif any(phrase in text for phrase in ELLIPSIS_REPEAT + ELLIPSIS_SAME_TARGET + ELLIPSIS_SAME_PRODUCT + ELLIPSIS_CANCEL_IT):
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
                response = "请至少提供两个理财产品编号（例如：比较 LX00118 和 HQ30060）。"
        elif intent == Intent.WEALTH_RISK_ASSESSMENT.value:
            labels = {"loss_tolerance": "风险承受程度（低/中/高）", "experience": "投资经验（新手/基础/专业）", "horizon_years": "预计投资期限（几年）"}
            if missing_fields:
                response = "风险评估需要您明确回答：" + "、".join(labels[item] for item in missing_fields) + "。我不会替您填写问卷。"
        elif intent in {Intent.TRANSFER.value, Intent.SCHEDULED_TRANSFER.value} and "target" in missing_fields:
            # Ask who first - before the amount - and offer the real payees by name.
            names = _payee_names(state)
            if pending_clarification is not None:
                pending_clarification['choices'] = {'target': names}
            examples = f"，例如：{'、'.join(names)}" if names else ""
            response = f"要转给哪位收款人？请说出联系人姓名或手机号{examples}。我不会替您猜测收款账户。"
        elif intent in {Intent.WEALTH_PURCHASE.value, Intent.WEALTH_REDEEM.value} and "target" in missing_fields:
            response = "请补充理财产品编号（例如 LX00118），我不会猜测产品。"
        elif intent == Intent.CARD_LIMIT.value and "amount" in missing_fields:
            response = "请补充希望设置的卡片额度（例如 5000 元）。"
        elif intent == Intent.SUBSCRIPTION_CANCEL.value and "target" in missing_fields:
            response = "请说明要取消哪个订阅（例如：取消 Apple 订阅）。查看现有订阅可以说“我有哪些订阅”。"
        elif (intent in {Intent.TRANSFER.value, Intent.SCHEDULED_TRANSFER.value} and "amount" in missing_fields
              and parsed.get("target") and (refusal := _payee_refusal(state, str(parsed["target"])))):
            # Say "that is you" / "no such payee" now, not after the amount is given.
            response = f"{refusal}。请告诉我要转给哪位（联系人姓名或手机号）。"
            parsed["target"] = None
            if pending_clarification is not None:
                pending_clarification["payload"]["target"] = None
                pending_clarification["missing"] = ["target"] + [item for item in missing_fields if item != "target"]
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
    # A read-only detour answers the question without discarding a simple
    # unfinished request. A subsequent bare amount/name can still fill its slot.
    if (pending_clarification is None and isinstance(pending, dict) and not prior_context.get("compound_workflow")
            and (parsed.get("intent") in GREEN_INTENTS - RISK_QUIZ_INTENTS
                 or understood_meta.get("dialogue_act") in {"SOCIAL", "UNSUPPORTED", "UNCLEAR"})):
        pending_clarification = pending
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
    audit_meta = {key: value for key, value in (parsed.get("parse_meta") or {}).items() if key != "questions"}
    audit_event("INTENT_PARSE", user_id=state.get("user_id"), decision="CLARIFY" if clarification else "ALLOW", outcome="PARSED", request_id=result["request_id"], metadata={"intent": parsed["intent"], "confidence": parsed.get("confidence"), "parse_meta": audit_meta, "sub_intents": [item.get("intent") for item in sub_requests] or None})
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
