"""Conversation evidence and the boundary between an inquiry and a new action."""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Mapping, Optional

from planner import GREEN_INTENTS, Intent
from security import redact
from banking_knowledge import DESCRIPTIONS, TOPICS
from intent_language import action_request_clause, execution_constraint, finite_limit_change


_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("intent_understanding_context", default={})
_CASE = re.compile(r"(?i)(?<![A-Za-z0-9-])CASE-[a-z0-9]{4,32}(?![A-Za-z0-9-])")
_DISPUTE = re.compile(r"争议|工单|案件|CASE-", re.I)
_SUSPICIOUS = re.compile(r"可疑|异常交易|异常消费|盗刷")
_READ = re.compile(r"查询|查看|查(?:一?下|下|看|查)?|明细|记录|详情|哪几笔|哪些|分析|解释|怎么回事|什么情况")
_HELP = re.compile(r"解释|说明|为什么|为何|怎么|如何|多久|多长时间|什么时候|何时|多少.{0,8}结果|进度|流程|步骤|时效|规则|影响|费用|有什么|是什么|是否|是不是|需要(?:什么|哪些)|(?:需要|能|会|还能|可以).{0,16}[吗么？?]|能不能|能否")
_DIRECT = re.compile(r"(?<!申)(?:帮我|替我|给我|请|麻烦)(?:先|现在|立即|马上|直接|把|将|这张|这笔|我的|\s){0,8}(?:冻结|解冻|挂失|转账|转给|取消|申购|赎回|创建|提交|发起|办理)")
_BANKING = re.compile(r"转账|到账|交易|消费|扣款|预约|卡|冻结|挂失|解冻|理财|申购|赎回|订阅|代扣|工单|争议|案件|验证|测评")
DIALOGUE_ACTS = {"QUERY", "EXPLAIN", "ACTION", "SLOT_REPLY", "UNSUPPORTED", "UNCLEAR", "SOCIAL"}
READ_INTENTS = GREEN_INTENTS - {Intent.WEALTH_RISK_ASSESSMENT.value}
HELP_TOPICS = TOPICS


INTENT_DESCRIPTIONS = DESCRIPTIONS


def help_topic(text: str) -> str:
    from bank_agent.query_focus import contact_question

    if contact_question(text):
        return "contacts"
    if re.search(r"生日|跨场景|订花|订.*花|预订.*花|鲜花|蛋糕", text):
        return "cross_scene"
    if re.search(r"限额|额度|上限", text) and not re.search(r"验证|为什么|原因", text):
        return "limits"
    if re.search(r"核对|确认|查证|核实|本人", text) and re.search(r"消费|交易|商户|扣款", text):
        return "dispute"
    for topic, pattern in (("dispute", _DISPUTE), ("transfer", re.compile(r"转账|到账|预约")),
                           ("card", re.compile(r"卡|挂失|冻结|解冻")), ("wealth", re.compile(r"理财|申购|赎回|测评")),
                           ("subscription", re.compile(r"订阅|代扣|续费|会员")), ("security", re.compile(r"验证|确认|审批|密码|口令|安全")),
                           ("tasks", re.compile(r"待办|任务页|人工处理"))):
        if pattern.search(text):
            return topic
    return "general"


def inquiry_route(text: str) -> Optional[tuple[str, dict[str, Any]]]:
    """Recognize information requests without treating every question as an action.

    Polite directives such as "帮我冻结卡片可以吗" still propose an action.
    Existing records, procedures and time estimates never imply a new write.
    """
    directive = bool(_DIRECT.search(text))
    if help_topic(text) == "contacts" and not directive and not action_request_clause(text):
        return Intent.BANKING_HELP.value, {"help_topic": "contacts"}
    if re.search(r"扣款失败|续费失败|扣费失败", text) and not directive:
        return Intent.BANKING_HELP.value, {"help_topic": "subscription"}
    if (not directive and not action_request_clause(text)
            and re.search(r"(?:可用|能用|可支配)(?:余额|资金|的钱)?(?:还)?(?:有|是)?(?:多少钱|多少)", text)):
        return Intent.BALANCE_QUERY.value, {}
    if re.search(r"(?:取消|解除|去掉|关闭).{0,6}(?:限额|额度上限)|(?:无限|不限额)", text) and not finite_limit_change(text):
        return Intent.BANKING_HELP.value, {"help_topic": "limits"}
    if help_topic(text) == "cross_scene" and (_HELP.search(text) or re.search(r"预订.{0,6}花|订一束|买一束", text)):
        return Intent.BANKING_HELP.value, {"help_topic": "cross_scene"}
    if _DISPUTE.search(text) and re.search(r"撤销|撤回|取消|删除", text):
        return Intent.BANKING_HELP.value, {"help_topic": "dispute"}
    if _DISPUTE.search(text) and re.search(r"(?:怎么|如何).{0,6}(?:创建|提交|发起|申请)|(?:创建|提交|发起|申请).{0,8}(?:流程|步骤|方法)", text) and not directive:
        return Intent.BANKING_HELP.value, {"help_topic": "dispute"}
    if _DISPUTE.search(text) and not directive and not re.search(r"(?:创建|提交|发起|申请).{0,8}(?:争议|工单|案件)", text):
        slots: dict[str, Any] = {}
        case = _CASE.search(text)
        if case:
            slots["case_id"] = case.group().upper()
        slots["dispute_timing"] = bool(re.search(r"多久|多长|何时|什么时候|多少.{0,8}结果|时效", text))
        return Intent.DISPUTE_QUERY.value, slots
    if _SUSPICIOUS.search(text) and (_READ.search(text) or re.search(r"是否|是不是|[吗么？?]", text)) and not directive and not fraud_action_requested(text):
        return Intent.BILL_QUERY.value, {"suspicious_only": True}
    if (_READ.search(text) and re.search(r"交易|消费|流水|转账记录", text) and not directive
            and not re.search(r"流程|步骤|多久|何时|时效|进度|影响|规则|费用", text)):
        return Intent.BILL_QUERY.value, {}
    if _HELP.search(text) and _BANKING.search(text) and not directive and not action_request_clause(text) and not finite_limit_change(text):
        return Intent.BANKING_HELP.value, {"help_topic": help_topic(text)}
    if _READ.search(text) and not directive:
        if "余额" in text:
            return Intent.BALANCE_QUERY.value, {}
        if re.search(r"卡片|银行卡|我的卡|这张卡|那张卡", text):
            return Intent.CARD_QUERY.value, {}
        if re.search(r"交易|账单|流水", text):
            return Intent.BILL_QUERY.value, {}
    return None


def fraud_action_requested(text: str) -> bool:
    if re.search(r"是否|是不是|怎么|如何|有没有|进度|多久|什么时候|[吗么？?]", text) and not _DIRECT.search(text):
        return False
    return bool(re.search(r"不是我的|不是本人|非本人|被盗刷|被盗用|有人拿我的卡|(?:冻结|止付|保护).{0,8}(?:卡|交易)|(?:创建|提交|发起|申请).{0,8}(?:争议|工单)", text))


def enforce_inquiry(parsed: dict[str, Any], text: str) -> dict[str, Any]:
    route = inquiry_route(text)
    result = dict(parsed)
    act = (result.get("parse_meta") or {}).get("dialogue_act")
    constraint = execution_constraint(text)
    if constraint:
        result["parse_meta"] = {**(result.get("parse_meta") or {}), "execution_constraint": constraint}
        if result.get("intent") not in READ_INTENTS and act not in {"SOCIAL", "UNSUPPORTED"}:
            result.update(intent=Intent.BANKING_HELP.value, target=None, amount=None, participants=[], product_ids=[], confidence=None)
            result["slots"] = {"help_topic": help_topic(text)}
            result["parse_meta"]["dialogue_act"] = "EXPLAIN"
            act = "EXPLAIN"
    if act == "UNSUPPORTED" and route and route[1].get("help_topic") in {"cross_scene", "limits", "contacts"}:
        # Explain the genuine nearby capability without opening a write flow.
        result["parse_meta"] = {**(result.get("parse_meta") or {}), "dialogue_act": "EXPLAIN"}
        act = "EXPLAIN"
    if (result.get("parse_meta") or {}).get("scope") == "OUT_OF_SCOPE":
        result.update(intent=Intent.UNKNOWN.value, target=None, amount=None)
        result["parse_meta"] = {**(result.get("parse_meta") or {}), "dialogue_act": "UNSUPPORTED"}
        return result
    if act in {"UNSUPPORTED", "UNCLEAR", "SOCIAL"}:
        result.update(intent=Intent.UNKNOWN.value, target=None, amount=None)
        return result
    if act in {"QUERY", "EXPLAIN"} and result.get("intent") not in READ_INTENTS | {Intent.SUBSCRIPTION_QUERY.value}:
        result.update(intent=Intent.BANKING_HELP.value, target=None, amount=None, participants=[], product_ids=[], confidence=None)
        result["slots"] = {"help_topic": help_topic(text)}
    if route:
        intent, slots = route
        if intent == Intent.BANKING_HELP.value:
            selected = (result.get("slots") or {}).get("help_topic")
            mandatory_topic = slots.get("help_topic") == "contacts" or bool(re.search(r"取消.{0,6}(?:限额|额度)|无限|不限额|(?:撤销|撤回|取消|删除).{0,8}(?:工单|争议)|扣款失败|续费失败|扣费失败", text))
            if (selected in TOPICS and selected != "general" and
                    (slots.get("help_topic") == "general" or act in {"QUERY", "EXPLAIN"} and not mandatory_topic)):
                slots = {**slots, "help_topic": selected}
        previous = result.get("intent")
        # A generic procedure keyword must not replace a model's grounded
        # request for actual card/account/product state with a help brochure.
        read_understood = act in {"QUERY", "EXPLAIN"} or act is None and bool(re.search(r"为什么|为何", text))
        if (intent == Intent.BANKING_HELP.value and read_understood and previous in (READ_INTENTS | {Intent.SUBSCRIPTION_QUERY.value}) - {Intent.BANKING_HELP.value}
                and slots.get("help_topic") != "contacts"
                and not re.search(r"扣款失败|续费失败|扣费失败", text)
                and not (_DISPUTE.search(text) and re.search(r"撤销|撤回|取消|删除", text))):
            return result
        if intent == Intent.BILL_QUERY.value and previous in {Intent.BILL_QUERY.value, Intent.BILL_ANALYSIS.value} and not slots:
            return result
        result.update(intent=intent, target=None, amount=None, participants=[], product_ids=[])
        if intent == Intent.BANKING_HELP.value:
            result["confidence"] = None
        result["slots"] = {**(result.get("slots") or {}), **slots}
        if previous != intent:
            result["parse_meta"] = {**(result.get("parse_meta") or {}), "source": "rules_guard",
                                    "resolution": "read_only_inquiry", "reason_codes": ["READ_ONLY_INQUIRY"],
                                    "model_intent": previous, "fast_path_eligible": False}
    elif result.get("intent") == Intent.FRAUD_DISPUTE.value and not fraud_action_requested(text):
        result["slots"] = {**(result.get("slots") or {}), "request_ambiguous": True}
        result["parse_meta"] = {**(result.get("parse_meta") or {}), "reason_codes": ["PROTECTIVE_ACTION_NOT_REQUESTED"]}
    return result


def unsupported_reply(meta: Mapping[str, Any], *, pending: bool = False) -> str:
    if meta.get("scope") == "OUT_OF_SCOPE":
        reply = "我专注于银行业务咨询和办理，不能完成这类非银行请求。可以继续问账户、转账、卡片或其他银行问题。"
    else:
        reply = "这项能力目前没有接入。当前可以查询账户和交易、了解银行业务规则，或办理已支持的转账、理财、卡片与订阅业务。"
    return reply + ("原待确认操作保持不变。" if pending else "")


def _safe_text(value: Any, limit: int) -> str:
    text = str(redact(str(value or "")))[:limit]
    text = re.sub(r"(?i)(密码|验证码|password|otp)\s*(?:[:：=]|是|为)?\s*[A-Za-z0-9!@#$%^&*_.-]{4,}", r"\1：[已隐藏]", text)
    return re.sub(r"[A-Za-z0-9_\-.~+/=]{32,}", "[凭据已隐藏]", text)


@contextmanager
def understanding_context(state: Mapping[str, Any]) -> Iterator[None]:
    from bank_agent.dialogue_state import dialogue_view

    same_user = state.get("memory_principal") == state.get("user_id")
    history = (state.get("dialogue_history") or []) if same_user else []
    observations = (state.get("observations") or {}) if same_user else {}
    pending = (state.get("context") or {}).get("pending_clarification") or {}
    value: dict[str, Any] = {"recent_turns": history[-6:], "recent_observations": list(observations.values())[-4:],
             "pending_question": {"intent": pending.get("intent"), "missing": pending.get("missing") or []}}
    view = dialogue_view(state)
    value["discussion"] = {"active_topic": view.get("active_topic"), "tasks": [
        {"id": task.get("id"), "intent": task.get("intent"), "missing": (task.get("pending") or {}).get("missing")}
        for task in view["tasks"]]}
    next_input = view.get("next_input")
    if (isinstance(next_input, dict) and next_input.get("intent") == "CARD_LIMIT" and next_input.get("field") == "amount"
            and set(next_input) <= {"intent", "field", "target"}):
        value["discussion"]["next_input"] = dict(next_input)
    operation = state.get("pending_operation")
    if isinstance(operation, dict):
        value["pending_operation"] = {"intent": operation.get("intent"), "risk_level": operation.get("risk_level"),
                                      "summary": _safe_text(operation.get("summary"), 300),
                                      "required_actions": [value for value in operation.get("required_actions") or []
                                                           if value in {"USER_CLICK_CONFIRM", "SMS_OTP", "FACE_RECOGNITION"}]}
    token = _CONTEXT.set(value)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def model_context() -> dict[str, Any]:
    return _CONTEXT.get()


def referenced_case(state: Mapping[str, Any]) -> Optional[str]:
    text = str(state.get("user_input") or "")
    literal = _CASE.search(text)
    if literal:
        return literal.group().upper()
    if state.get("memory_principal") != state.get("user_id") or not re.search(r"这|那|该|刚才|上次|刚刚", text):
        return None
    observations = state.get("observations") or {}
    for observation in reversed(list(observations.values())):
        cases = observation.get("case_ids") or []
        if cases:
            return cases[0] if len(cases) == 1 else None
    return None


def referenced_transactions(state: Mapping[str, Any]) -> tuple[list[str], str]:
    """Resolve a deictic group against verified observations, never its count alone."""
    text = str(state.get("user_input") or "")
    if state.get("memory_principal") != state.get("user_id") or not re.search(r"这|那|刚才|上述|上面|其中", text):
        return [], ""
    count = re.search(r"([0-9]{1,3})\s*笔", text)
    reference = (state.get("slots") or {}).get("read_reference")
    for observation in reversed(list((state.get("observations") or {}).values())):
        ids = observation.get("transaction_ids") or []
        anomalies = observation.get("suspicious_transaction_ids") or []
        if not ids and not anomalies:
            continue
        if observation.get("intent") in {Intent.TRANSFER.value, Intent.SCHEDULED_TRANSFER.value} and re.search(r"这笔|那笔|刚才.{0,8}转账", text):
            return (ids, "TRANSACTION_REFERENCE") if len(ids) == 1 else ([], "")
        if len(ids) == 1 and re.search(r"这笔|那笔|该笔", text):
            return ids, "TRANSACTION_REFERENCE"
        if anomalies and (reference == "anomalies" or _SUSPICIOUS.search(text) or count and int(count.group(1)) == len(anomalies) != len(ids)):
            return anomalies, "SUSPICIOUS_TRANSACTIONS"
        if ids and (reference == "listed_transactions" or count and int(count.group(1)) == len(ids)):
            return ids, "TRANSACTION_REFERENCE"
        break
    return [], ""


def unresolved_transaction_reference(state: Mapping[str, Any]) -> bool:
    text = str(state.get("user_input") or "")
    if (state.get("intent") not in {Intent.BILL_QUERY.value, Intent.BILL_ANALYSIS.value}
            or (state.get("slots") or {}).get("transaction_id")
            or _SUSPICIOUS.search(text) or re.search(r"境外|海外|国外", text)):
        return False
    deictic = re.search(r"(?:这|那|上述|上面)(?:[0-9]{1,3}|[一二两三四五六七八九十]+|几)?笔|刚才.{0,8}(?:转账|交易|流水)", text)
    return bool(deictic and not referenced_transactions(state)[0])


def remember_reply(state: Mapping[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    """Keep bounded, credential-free dialogue and entities from verified results."""
    if not response.get("final_response"):
        return response
    from bank_agent.dialogue_state import remember_discussion

    same_user = state.get("memory_principal") == state.get("user_id")
    history = list(state.get("dialogue_history") or [])[-5:] if same_user else []
    history.append({"user": _safe_text(state.get("user_input"), 500), "assistant": _safe_text(response["final_response"], 1000)})
    remembered: dict[str, Any] = {**response, "dialogue_history": history, "memory_principal": state.get("user_id"),
                                  "observations": dict(state.get("observations") or {}) if same_user else {},
                                  "dialogue_state": remember_discussion(state, response)}
    result = state.get("execution_result")
    if not isinstance(result, dict) or response.get("business_status") == "FAILED" or state.get("business_status") == "FAILED":
        return remembered
    observation: dict[str, Any] = {"intent": state.get("intent"), "date_scope": state.get("date_scope"),
                                   "date_range": state.get("date_range")}
    facts = result.get("facts") or {}
    observation["suspicious_transaction_ids"] = [row["tx_id"] for row in facts.get("anomalies", [])
                                                if isinstance(row, dict) and isinstance(row.get("tx_id"), str)][:50]
    observation["transaction_ids"] = [row["tx_id"] for row in result.get("records", [])
                                      if isinstance(row, dict) and isinstance(row.get("tx_id"), str)][:50]
    envelope = result.get("data") or {}
    if isinstance(envelope, dict):
        observation["help_topic"] = envelope.get("topic")
        data = envelope.get("data")
        if result.get("type") == "TRANSFER_SUCCESS" and isinstance(envelope.get("transaction_id"), str):
            observation["transaction_ids"] = [envelope["transaction_id"]]
        cases = data if isinstance(data, list) and result.get("type") == Intent.DISPUTE_QUERY.value else []
        if isinstance(data, dict) and isinstance(data.get("dispute_case"), dict):
            cases = [data["dispute_case"]]
        observation["case_ids"] = [row["case_id"] for row in cases if isinstance(row, dict) and isinstance(row.get("case_id"), str)][:20]
    observations = dict(state.get("observations") or {}) if same_user else {}
    key = str(state.get("intent") or "UNKNOWN")
    observations.pop(key, None)
    observations[key] = observation
    remembered["observations"] = dict(list(observations.items())[-8:])
    return remembered
