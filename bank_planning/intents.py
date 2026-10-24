"""Intent allow-list, risk tiers and validation of parsed (rule or model) intents."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import math
from typing import Any, Mapping, Optional

try:  # security.py is available in the application, but keep this module reusable.
    from security import InjectionError, sanitize_text
except Exception:  # pragma: no cover - only used when imported in isolation
    class InjectionError(ValueError):  # type: ignore[no-redef]
        pass

    def sanitize_text(value: Any, *, field: str = "文本", max_length: int = 2000) -> str:
        text = str(value or "").strip()
        if len(text) > max_length:
            raise InjectionError(f"{field}长度超过安全限制")
        return text



class Intent(str, Enum):
    BALANCE_QUERY = "BALANCE_QUERY"
    BILL_QUERY = "BILL_QUERY"
    BILL_ANALYSIS = "BILL_ANALYSIS"
    DISPUTE_QUERY = "DISPUTE_QUERY"
    BANKING_HELP = "BANKING_HELP"
    TRANSFER = "TRANSFER"
    SCHEDULED_TRANSFER = "SCHEDULED_TRANSFER"
    SPLIT_BILL = "SPLIT_BILL"
    CROSS_SCENE_LOCK = "CROSS_SCENE_LOCK"
    WEALTH_QUERY = "WEALTH_QUERY"
    WEALTH_HOLDINGS = "WEALTH_HOLDINGS"
    WEALTH_RECOMMEND = "WEALTH_RECOMMEND"
    WEALTH_RISK_ASSESSMENT = "WEALTH_RISK_ASSESSMENT"
    WEALTH_COMPARE = "WEALTH_COMPARE"
    WEALTH_PURCHASE = "WEALTH_PURCHASE"
    WEALTH_REDEEM = "WEALTH_REDEEM"
    CARD_APPLY = "CARD_APPLY"
    CARD_QUERY = "CARD_QUERY"
    CARD_LIMIT = "CARD_LIMIT"
    CARD_FREEZE = "CARD_FREEZE"
    FRAUD_DISPUTE = "FRAUD_DISPUTE"
    CARD_UNFREEZE = "CARD_UNFREEZE"
    CARD_REPORT_LOST = "CARD_REPORT_LOST"
    CARD_UNREPORT_LOST = "CARD_UNREPORT_LOST"
    PASSWORD_CHANGE = "PASSWORD_CHANGE"
    # Switch a card payment channel (online / overseas) on or off.
    CARD_CHANNEL = "CARD_CHANNEL"
    # Withdraw a reservation that has not moved money (schedule, AA, fund lock).
    UNDO_OPERATION = "UNDO_OPERATION"
    SUBSCRIPTION_QUERY = "SUBSCRIPTION_QUERY"
    SUBSCRIPTION_CANCEL = "SUBSCRIPTION_CANCEL"
    UNKNOWN = "UNKNOWN"


GREEN_INTENTS = {
    Intent.DISPUTE_QUERY.value,
    Intent.BANKING_HELP.value,
    Intent.BALANCE_QUERY.value,
    Intent.BILL_QUERY.value,
    Intent.BILL_ANALYSIS.value,
    Intent.WEALTH_QUERY.value,
    Intent.WEALTH_HOLDINGS.value,
    Intent.WEALTH_RECOMMEND.value,
    Intent.WEALTH_RISK_ASSESSMENT.value,
    Intent.WEALTH_COMPARE.value,
    Intent.CARD_QUERY.value,
}
YELLOW_INTENTS = {
    Intent.TRANSFER.value,
    Intent.SCHEDULED_TRANSFER.value,
    Intent.SPLIT_BILL.value,
    Intent.CROSS_SCENE_LOCK.value,
    Intent.SUBSCRIPTION_QUERY.value,
    Intent.SUBSCRIPTION_CANCEL.value,
    Intent.CARD_APPLY.value,
    Intent.UNDO_OPERATION.value,
}
RED_INTENTS = {
    Intent.WEALTH_PURCHASE.value,
    Intent.WEALTH_REDEEM.value,
    Intent.CARD_LIMIT.value,
    Intent.CARD_FREEZE.value,
    Intent.CARD_UNFREEZE.value,
    Intent.CARD_REPORT_LOST.value,
    Intent.CARD_UNREPORT_LOST.value,
    Intent.PASSWORD_CHANGE.value,
    Intent.FRAUD_DISPUTE.value,
    # Preview only: Core lowers switching a channel *off* to YELLOW.
    Intent.CARD_CHANNEL.value,
}
CARD_CHANNEL_NAMES = {"ONLINE": "网上支付", "OVERSEAS": "境外交易"}
UNDO_KINDS = {"SCHEDULED_TRANSFER": "预约转账", "SPLIT_BILL": "AA 收款请求", "FUND_RESERVATION": "资金锁定",
              "LAST": "最近一笔可撤销的操作", "SETTLED_TRANSFER": "已完成的转账"}
ALL_INTENTS = {item.value for item in Intent}


def risk_for_intent(intent: str, *, amount: Any = 0, daily_total: Any = 0) -> str:
    """Return the server-side baseline risk class for an allow-listed intent.

    Transfer risk is cumulative: a transfer is yellow while the resulting
    same-day total is at most 1,000 yuan and red above that threshold.  The
    core API remains the final authority; this function is for planning/UI
    previews and must never replace the API precheck.
    """

    normalized = str(intent or Intent.UNKNOWN.value).upper()
    if normalized in GREEN_INTENTS:
        return "GREEN"
    if normalized in RED_INTENTS:
        return "RED"
    if normalized in {Intent.TRANSFER.value, Intent.SCHEDULED_TRANSFER.value, Intent.SPLIT_BILL.value}:
        try:
            total = Decimal(str(daily_total)) + Decimal(str(amount))
        except (InvalidOperation, ValueError, TypeError):
            return "RED"
        return "YELLOW" if total <= Decimal("1000") else "RED"
    if normalized in YELLOW_INTENTS:
        return "YELLOW"
    return "UNKNOWN"


def parse_amount(value: Any, *, allow_zero: bool = False) -> Optional[float]:
    """Parse a finite yuan amount and normalize it to cents precision."""

    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value).replace(",", "").replace("￥", "").replace("¥", "").strip())
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("金额格式无效")
    if not amount.is_finite() or (amount < 0 if allow_zero else amount <= 0):
        raise ValueError("金额必须为有限的正数")
    amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount > Decimal("100000000"):
        raise ValueError("金额超过安全上限")
    return float(amount)


def validate_intent_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a model-produced intent object.

    Unknown keys are discarded, unknown intents become ``UNKNOWN`` and
    missing high-risk slots are left empty so the UI can ask a clarification
    question.  No fallback recipient is ever invented.
    """

    if not isinstance(payload, Mapping):
        raise ValueError("意图结果必须是 JSON 对象")
    raw_intent = str(payload.get("intent") or Intent.UNKNOWN.value).upper().strip()
    intent = raw_intent if raw_intent in ALL_INTENTS else Intent.UNKNOWN.value
    target = payload.get("target")
    if target is not None:
        target = sanitize_text(target, field="收款人", max_length=120)
        target = target or None
    extra = payload.get("extra_info")
    if extra is not None:
        extra = sanitize_text(extra, field="补充说明", max_length=500)
        extra = extra or None
    scope = str(payload.get("date_scope") or "ALL").upper().strip()
    aliases = {"今天": "TODAY", "今日": "TODAY", "本月": "MONTH", "上月": "LAST_MONTH", "今年": "YEAR", "年度": "YEAR", "全年": "YEAR"}
    scope = aliases.get(scope, scope)
    if scope not in {"TODAY", "MONTH", "LAST_MONTH", "YEAR", "ALL", "CUSTOM"}:
        scope = "ALL"
    amount = parse_amount(payload.get("amount"), allow_zero=True)
    confidence = payload.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(confidence))) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None
    slots = payload.get("slots")
    if not isinstance(slots, Mapping):
        slots = {}
    # Keep only JSON-friendly scalar slot values; large/model-controlled
    # objects must not be passed into an execution tool.
    safe_slots: dict[str, Any] = {}
    for key, value in slots.items():
        if not isinstance(key, str) or not isinstance(value, (str, int, float, bool, type(None))):
            continue
        if isinstance(value, float) and not math.isfinite(value):
            continue
        if isinstance(value, str):
            try:
                value = sanitize_text(value, field="意图槽位", max_length=500)
            except InjectionError:
                # A suspicious optional slot should be dropped rather than
                # allowed to influence a later prompt or tool call.
                continue
        safe_slots[key] = value
    date_range = payload.get("date_range")
    normalized_range = None
    if scope == "CUSTOM":
        try:
            if not isinstance(date_range, Mapping):
                raise ValueError("missing date range")
            start, end = date.fromisoformat(date_range["start"]), date.fromisoformat(date_range["end"])
            if start > end or (end - start).days > 3660:
                raise ValueError("invalid date range")
            normalized_range = {"start": start.isoformat(), "end": end.isoformat()}
        except (ValueError, TypeError, KeyError):
            safe_slots["date_range_ambiguous"] = True
    meta = payload.get("parse_meta")
    parse_meta = None
    if isinstance(meta, Mapping):
        parse_meta = {"source": str(meta.get("source") or "unknown")[:32],
                      "resolution": str(meta.get("resolution") or "unknown")[:32],
                      "reason_codes": [str(value)[:64] for value in (meta.get("reason_codes") or [])[:8] if isinstance(value, str)],
                      "fast_path_eligible": meta.get("fast_path_eligible") is True}
        if meta.get("model_intent") in ALL_INTENTS:
            parse_meta["model_intent"] = str(meta["model_intent"])
        if meta.get("dialogue_act") in {"QUERY", "EXPLAIN", "ACTION", "SLOT_REPLY", "UNSUPPORTED", "UNCLEAR", "SOCIAL"}:
            parse_meta["dialogue_act"] = str(meta["dialogue_act"])
        if meta.get("social_kind") in {"greeting", "thanks", "goodbye", "capabilities"}:
            parse_meta["social_kind"] = str(meta["social_kind"])
        if meta.get("scope") in {"IN_SCOPE", "MIXED", "OUT_OF_SCOPE"}:
            parse_meta["scope"] = str(meta["scope"])
        if meta.get("topic_relation") in {"NEW", "CONTINUE"}:
            parse_meta["topic_relation"] = str(meta["topic_relation"])
        if meta.get("resume_task_id") in ALL_INTENTS:
            parse_meta["resume_task_id"] = str(meta["resume_task_id"])
        if isinstance(meta.get("questions"), list):
            parse_meta["questions"] = [str(value)[:300] for value in meta["questions"][:5] if isinstance(value, str)]
    # Renewal scheduling is opt-in and only meaningful for subscription reads.
    # Never let truthy strings or unrestricted day offsets create reminders.
    if intent == Intent.SUBSCRIPTION_QUERY.value and slots.get("renewal_reminder") is True:
        safe_slots["renewal_reminder"] = True
        days_before = slots.get("days_before", 2)
        if isinstance(days_before, int) and not isinstance(days_before, bool) and 0 <= days_before <= 365:
            safe_slots["days_before"] = days_before
        else:
            raise ValueError("续费提醒需指定 0 到 365 天内的提前天数")
    else:
        safe_slots.pop("renewal_reminder", None)
        safe_slots.pop("days_before", None)
    # Switch and undo slots are allow-listed values only; anything else is
    # dropped so the request falls back to a clarification question.
    channel = str(slots.get("card_channel") or "").upper()
    if intent == Intent.CARD_CHANNEL.value and channel in CARD_CHANNEL_NAMES and isinstance(slots.get("channel_enabled"), bool):
        safe_slots["card_channel"], safe_slots["channel_enabled"] = channel, slots["channel_enabled"]
    else:
        safe_slots.pop("card_channel", None)
        safe_slots.pop("channel_enabled", None)
    undo_kind = str(slots.get("undo_kind") or "").upper()
    if intent == Intent.UNDO_OPERATION.value and undo_kind in UNDO_KINDS:
        safe_slots["undo_kind"] = undo_kind
    else:
        safe_slots.pop("undo_kind", None)
    raw_participants = payload.get("participants")
    if not isinstance(raw_participants, list):
        raw_participants = []
    participants = []
    for value in raw_participants[:20]:
        if isinstance(value, (str, int)):
            try:
                item = sanitize_text(value, field="分摊参与人", max_length=120)
            except InjectionError:
                continue
            if item and item not in participants:
                participants.append(item)
    raw_product_ids = payload.get("product_ids")
    if not isinstance(raw_product_ids, list):
        raw_product_ids = []
    product_ids = []
    for value in raw_product_ids[:10]:
        if isinstance(value, (str, int)):
            try:
                item = sanitize_text(value, field="理财产品编号", max_length=64)
            except InjectionError:
                continue
            item = item.upper()
            if item and item not in product_ids:
                product_ids.append(item)
    scheduled_at = payload.get("scheduled_at")
    if scheduled_at is not None:
        try:
            scheduled_at = sanitize_text(scheduled_at, field="计划时间", max_length=80) or None
        except InjectionError:
            scheduled_at = None
    event_at = payload.get("event_at")
    if event_at is not None:
        try:
            event_at = sanitize_text(event_at, field="事件时间", max_length=80) or None
        except InjectionError:
            event_at = None
    # The model may call the cross-scene birthday slot ``event_at`` while the
    # lightweight scheduler historically used ``scheduled_at``. Keep both
    # representations in sync for downstream policy checks.
    if event_at and not scheduled_at and intent == Intent.CROSS_SCENE_LOCK.value:
        scheduled_at = event_at
    if scheduled_at and not event_at and intent == Intent.CROSS_SCENE_LOCK.value:
        event_at = scheduled_at
    return {
        "intent": intent,
        "target": target,
        "amount": amount,
        "date_scope": scope,
        "date_range": normalized_range,
        "parse_meta": parse_meta,
        "extra_info": extra,
        "confidence": confidence,
        "slots": safe_slots,
        "participants": participants,
        "product_ids": product_ids,
        "scheduled_at": scheduled_at,
        "event_at": event_at,
    }


def missing_slots(parsed: Mapping[str, Any]) -> list[str]:
    """Slot names a request still needs before it can be executed.

    This mirrors :func:`needs_clarification` but names the gap, so the
    conversational layer can ask a targeted question and, on the next turn,
    fold a bare answer ("下周六", "500 元") back into the intent that is still
    waiting for it instead of starting from UNKNOWN.
    """

    intent = str(parsed.get("intent") or Intent.UNKNOWN.value)
    missing: list[str] = []
    slots = parsed.get("slots")
    if not isinstance(slots, Mapping):
        slots = {}
    for slot_name in ("amount", "target", "date_range", "request"):
        if slots.get(f"{slot_name}_ambiguous") is True:
            missing.append(slot_name)
    if missing:
        return missing
    if intent == Intent.WEALTH_RISK_ASSESSMENT.value:
        slots = parsed.get("slots")
        if not isinstance(slots, Mapping):
            slots = {}
        tolerance = str(slots.get("loss_tolerance") or "").upper().strip()
        experience = str(slots.get("experience") or "").upper().strip()
        horizon = slots.get("horizon_years")
        try:
            horizon_value = int(horizon) if horizon is not None and not isinstance(horizon, bool) else None
        except (TypeError, ValueError):
            horizon_value = None
        if tolerance not in {"LOW", "MEDIUM", "HIGH"}:
            missing.append("loss_tolerance")
        if experience not in {"NONE", "BASIC", "PROFESSIONAL"}:
            missing.append("experience")
        if horizon_value is None or horizon_value < 0 or horizon_value > 100:
            missing.append("horizon_years")
        return missing
    if intent == Intent.SPLIT_BILL.value:
        # AA 收款不是向一个既定收款账户转账。它必须明确总金额和至少一位
        # 参与人；强制要求 ``target`` 会让正常的“和 A、B AA 300 元”永远
        # 停在澄清分支。
        if parsed.get("amount") in (None, 0):
            missing.append("amount")
        participants = parsed.get("participants")
        if not isinstance(participants, list) or not participants:
            missing.append("participants")
        return missing
    if intent == Intent.CROSS_SCENE_LOCK.value:
        # A reservation that promises a birthday reminder must carry an
        # explicit, parseable event date; never silently assume “next week”.
        if not parsed.get("target"):
            missing.append("target")
        if parsed.get("amount") in (None, 0):
            missing.append("amount")
        if not (parsed.get("scheduled_at") or parsed.get("event_at")):
            missing.append("scheduled_at")
        return missing
    if intent == Intent.SCHEDULED_TRANSFER.value and not parsed.get("scheduled_at"):
        missing.append("scheduled_at")
    if intent == Intent.CARD_CHANNEL.value:
        if "card_channel" not in slots:
            missing.append("card_channel")
        return missing
    if intent == Intent.UNDO_OPERATION.value:
        if "undo_kind" not in slots:
            missing.append("undo_kind")
        return missing
    if intent == Intent.WEALTH_COMPARE.value:
        product_ids = parsed.get("product_ids")
        if not isinstance(product_ids, list) or len(product_ids) < 2:
            missing.append("product_ids")
        return missing
    if intent in {
        Intent.TRANSFER.value,
        Intent.SCHEDULED_TRANSFER.value,
        Intent.SUBSCRIPTION_CANCEL.value,
        Intent.WEALTH_PURCHASE.value,
        Intent.WEALTH_REDEEM.value,
        Intent.CARD_LIMIT.value,
        Intent.CROSS_SCENE_LOCK.value,
    }:
        if intent not in {Intent.CROSS_SCENE_LOCK.value, Intent.CARD_LIMIT.value} and not parsed.get("target"):
            missing.append("target")
        if intent not in {Intent.SUBSCRIPTION_CANCEL.value} and parsed.get("amount") in (None, 0):
            # Some operations (card/report-lost) do not have an amount.  A
            # full redemption states its amount as "全部" and is resolved
            # against the real holding before the precheck.
            slots = parsed.get("slots")
            if not isinstance(slots, Mapping):
                slots = {}
            if not (intent == Intent.WEALTH_REDEEM.value and slots.get("redeem_all") is True):
                missing.append("amount")
    return missing


def needs_clarification(parsed: Mapping[str, Any]) -> bool:
    """Whether a request lacks a required slot or is too uncertain to execute."""

    intent = str(parsed.get("intent") or Intent.UNKNOWN.value)
    confidence = parsed.get("confidence")
    if intent == Intent.UNKNOWN.value:
        return True
    if confidence is not None:
        try:
            if float(confidence) < 0.65:
                return True
        except (TypeError, ValueError):
            return True
    return bool(missing_slots(parsed))
