"""Conversation evidence and the boundary between an inquiry and a new action."""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Mapping, Optional

from planner import GREEN_INTENTS, Intent
from security import redact


_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("intent_understanding_context", default={})
_CASE = re.compile(r"(?i)(?<![A-Za-z0-9-])CASE-[a-z0-9]{4,32}(?![A-Za-z0-9-])")
_DISPUTE = re.compile(r"争议|工单|案件|CASE-", re.I)
_SUSPICIOUS = re.compile(r"可疑|异常交易|异常消费|盗刷")
_READ = re.compile(r"查询|查看|查(?:一?下|下|看|查)?|明细|记录|详情|哪几笔|哪些|分析|解释|怎么回事|什么情况")
_HELP = re.compile(r"怎么|如何|多久|多长时间|什么时候|何时|多少.{0,8}结果|进度|流程|步骤|时效|规则|影响|费用|有什么|是什么|是否|是不是|需要(?:什么|哪些)|(?:会|还能|可以).{0,16}[吗么]|能不能|能否")
_DIRECT = re.compile(r"(?<!申)(?:帮我|替我|给我|请|麻烦)(?:先|现在|立即|马上|直接|把|将|这张|这笔|我的|\s){0,8}(?:冻结|解冻|挂失|转账|转给|取消|申购|赎回|创建|提交|发起|办理)")
_BANKING = re.compile(r"转账|到账|交易|消费|扣款|预约|卡|冻结|挂失|解冻|理财|申购|赎回|订阅|代扣|工单|争议|案件|验证|测评")
DIALOGUE_ACTS = {"QUERY", "EXPLAIN", "ACTION", "SLOT_REPLY", "UNSUPPORTED", "UNCLEAR", "SOCIAL"}
READ_INTENTS = GREEN_INTENTS - {Intent.WEALTH_RISK_ASSESSMENT.value}
HELP_TOPICS = {"general", "dispute", "transfer", "card", "wealth", "subscription", "security"}


INTENT_DESCRIPTIONS = {
    "BALANCE_QUERY": "只读账户余额", "BILL_QUERY": "只读交易明细，包括查询可疑交易",
    "BILL_ANALYSIS": "只读账单统计、异常线索分析，不确认盗刷", "DISPUTE_QUERY": "只读已有争议工单、进度及处理时效，不冻结或建单",
    "BANKING_HELP": "回答业务流程、规则、影响、时效或如何操作，只读说明",
    "TRANSFER": "提出一笔转账", "SCHEDULED_TRANSFER": "新建预约转账", "SPLIT_BILL": "新建AA收款请求",
    "CROSS_SCENE_LOCK": "锁定生日等用途资金并安排提醒", "WEALTH_QUERY": "浏览理财产品",
    "WEALTH_HOLDINGS": "查询持仓", "WEALTH_RECOMMEND": "请求匹配风险等级的理财推荐",
    "WEALTH_RISK_ASSESSMENT": "实际开始风险测评", "WEALTH_COMPARE": "比较明确产品",
    "WEALTH_PURCHASE": "实际申购", "WEALTH_REDEEM": "实际赎回", "CARD_QUERY": "只读卡片状态",
    "CARD_APPLY": "实际申请演示虚拟卡，不支持信用卡申请、还款或贷款", "CARD_LIMIT": "实际修改限额", "CARD_FREEZE": "实际冻结",
    "CARD_UNFREEZE": "实际解冻", "CARD_REPORT_LOST": "实际挂失或报告卡片丢失", "CARD_UNREPORT_LOST": "实际解除挂失",
    "FRAUD_DISPUTE": "用户明确否认消费或请求反诈保护；经确认强验证后冻结卡并创建/关联争议工单，不能用于查询",
    "PASSWORD_CHANGE": "实际修改密码", "CARD_CHANNEL": "实际开通或关闭交易渠道",
    "UNDO_OPERATION": "实际撤销未执行安排", "SUBSCRIPTION_QUERY": "查询订阅，仍需黄区确认",
    "SUBSCRIPTION_CANCEL": "实际取消明确订阅", "UNKNOWN": "能力不支持或语义不足，应澄清，不能选相近的写操作",
}


def help_topic(text: str) -> str:
    for topic, pattern in (("dispute", _DISPUTE), ("transfer", re.compile(r"转账|到账|预约")),
                           ("card", re.compile(r"卡|挂失|冻结|解冻")), ("wealth", re.compile(r"理财|申购|赎回|测评")),
                           ("subscription", re.compile(r"订阅|代扣|续费")), ("security", re.compile(r"验证|确认|审批|密码|口令|安全"))):
        if pattern.search(text):
            return topic
    return "general"


def inquiry_route(text: str) -> Optional[tuple[str, dict[str, Any]]]:
    """Recognize information requests without treating every question as an action.

    Polite directives such as "帮我冻结卡片可以吗" still propose an action.
    Existing records, procedures and time estimates never imply a new write.
    """
    directive = bool(_DIRECT.search(text))
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
    if _HELP.search(text) and _BANKING.search(text) and not directive:
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
    if act in {"UNSUPPORTED", "UNCLEAR", "SOCIAL"}:
        result.update(intent=Intent.UNKNOWN.value, target=None, amount=None)
        return result
    if act in {"QUERY", "EXPLAIN"} and result.get("intent") not in READ_INTENTS | {Intent.SUBSCRIPTION_QUERY.value}:
        result.update(intent=Intent.BANKING_HELP.value, target=None, amount=None, participants=[], product_ids=[])
        result["slots"] = {"help_topic": help_topic(text)}
    if route:
        intent, slots = route
        previous = result.get("intent")
        # A generic procedure keyword must not replace a model's grounded
        # request for actual card/account/product state with a help brochure.
        if (intent == Intent.BANKING_HELP.value and act == "QUERY" and previous in READ_INTENTS - {Intent.BANKING_HELP.value}
                and not (_DISPUTE.search(text) and re.search(r"撤销|撤回|取消|删除", text))):
            return result
        if intent == Intent.BILL_QUERY.value and previous in {Intent.BILL_QUERY.value, Intent.BILL_ANALYSIS.value} and not slots:
            return result
        result.update(intent=intent, target=None, amount=None, participants=[], product_ids=[])
        result["slots"] = {**(result.get("slots") or {}), **slots}
        if previous != intent:
            result["parse_meta"] = {**(result.get("parse_meta") or {}), "source": "rules_guard",
                                    "resolution": "read_only_inquiry", "reason_codes": ["READ_ONLY_INQUIRY"],
                                    "model_intent": previous, "fast_path_eligible": False}
    elif result.get("intent") == Intent.FRAUD_DISPUTE.value and not fraud_action_requested(text):
        result["slots"] = {**(result.get("slots") or {}), "request_ambiguous": True}
        result["parse_meta"] = {**(result.get("parse_meta") or {}), "reason_codes": ["PROTECTIVE_ACTION_NOT_REQUESTED"]}
    return result


def _safe_text(value: Any, limit: int) -> str:
    text = str(redact(str(value or "")))[:limit]
    text = re.sub(r"(?i)(密码|验证码|password|otp)\s*(?:[:：=]|是|为)?\s*[A-Za-z0-9!@#$%^&*_.-]{4,}", r"\1：[已隐藏]", text)
    return re.sub(r"[A-Za-z0-9_\-.~+/=]{32,}", "[凭据已隐藏]", text)


@contextmanager
def understanding_context(state: Mapping[str, Any]) -> Iterator[None]:
    same_user = state.get("memory_principal") == state.get("user_id")
    history = (state.get("dialogue_history") or []) if same_user else []
    observations = (state.get("observations") or {}) if same_user else {}
    pending = (state.get("context") or {}).get("pending_clarification") or {}
    value = {"recent_turns": history[-6:], "recent_observations": list(observations.values())[-4:],
             "pending_question": {"intent": pending.get("intent"), "missing": pending.get("missing") or []}}
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
    same_user = state.get("memory_principal") == state.get("user_id")
    history = list(state.get("dialogue_history") or [])[-5:] if same_user else []
    history.append({"user": _safe_text(state.get("user_input"), 500), "assistant": _safe_text(response["final_response"], 1000)})
    remembered: dict[str, Any] = {**response, "dialogue_history": history, "memory_principal": state.get("user_id"),
                                  "observations": dict(state.get("observations") or {}) if same_user else {}}
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
