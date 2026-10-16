"""Verification of Core responses before anything is shown: envelopes, collections and amounts."""

from __future__ import annotations

import math
import re
from typing import Any, TypeGuard


from planner import (
    ALL_INTENTS,
    Intent,
)
from security import audit_event
from bank_agent.state import (
    BankingAgentState,
)


def response_validation_failure(state: BankingAgentState, reason: str) -> dict[str, Any]:
    """Return a safe response when Core data cannot be verified.

    A transport-level 200 is not evidence that a banking operation succeeded.
    Keeping this guard in the final responder prevents a malformed, stale, or
    adversarial upstream payload from becoming a fabricated account statement.
    """

    audit_event(
        "RESULT_REJECTED",
        user_id=state.get("user_id"),
        decision="BLOCK",
        outcome="UNVERIFIED_RESULT",
        reason=reason,
        request_id=state.get("request_id"),
        metadata={
            "intent": state.get("intent") if state.get("intent") in ALL_INTENTS else Intent.UNKNOWN.value,
            "error_type": reason,
        },
    )
    # A refusal in prose is insufficient if the channel still renders the
    # rejected receipt, chart or already-completed plan as a result card.
    return {"final_response": "核心服务返回字段不完整或数值不可核验，没有声称操作成功。请先查询交易记录或账户状态核对结果，再决定是否重新提交。",
            "business_status": "FAILED",
            "execution_result": None, "execution_results": None, "chart_data": None,
            "todo_card": None, "plan": None}


def finite_number(value: Any, *, nonnegative: bool = False) -> TypeGuard[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and (not nonnegative or number >= 0)


def valid_balance_payload(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    required = ("total_balance", "locked_amount", "available_balance")
    if any(key not in data or not finite_number(data[key], nonnegative=True) for key in required):
        return False
    total = float(data["total_balance"])
    locked = float(data["locked_amount"])
    available = float(data["available_balance"])
    return locked <= total and abs((total - locked) - available) <= 0.01


def _valid_collection_envelope(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("data"), list)


def _valid_product_item(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    if not all(valid_text_field(item.get(key), max_length=200) for key in ("product_id", "name", "risk_level", "liquidity")):
        return False
    risk = str(item.get("risk_level"))
    if not re.fullmatch(r"R[1-5]", risk):
        return False
    if not finite_number(item.get("min_amount"), nonnegative=True):
        return False
    yield_value = item.get("annualized_yield")
    return yield_value is None or finite_number(yield_value, nonnegative=True)


def valid_wealth_collection(data: Any) -> bool:
    if not _valid_collection_envelope(data):
        return False
    return all(_valid_product_item(item) for item in data.get("data", []))


def valid_holding_collection(data: Any) -> bool:
    if not _valid_collection_envelope(data):
        return False
    for item in data.get("data", []):
        if not isinstance(item, dict):
            return False
        if not valid_text_field(item.get("holding_id"), max_length=128) or not valid_text_field(item.get("product_id"), max_length=64):
            return False
        if not finite_number(item.get("principal"), nonnegative=True):
            return False
        if item.get("estimated_value") is not None and not finite_number(item.get("estimated_value"), nonnegative=True):
            return False
    return True


def valid_subscription_collection(data: Any) -> bool:
    if not _valid_collection_envelope(data):
        return False
    for item in data.get("data", []):
        if not isinstance(item, dict):
            return False
        if not valid_text_field(item.get("subscription_id"), max_length=128) or not valid_text_field(item.get("merchant"), max_length=200):
            return False
        if not finite_number(item.get("amount"), nonnegative=True) or not valid_text_field(item.get("next_charge"), max_length=80):
            return False
        if str(item.get("status")) not in {"ACTIVE", "CANCELLED", "CANDIDATE"}:
            return False
    return True


def valid_card_collection(data: Any) -> bool:
    if not _valid_collection_envelope(data):
        return False
    for item in data.get("data", []):
        if not isinstance(item, dict) or not valid_text_field(item.get("card_id"), max_length=128):
            return False
        if str(item.get("status")) not in {"ACTIVE", "FROZEN", "LOST"}:
            return False
        if not valid_text_field(item.get("type"), max_length=40) or not finite_number(item.get("daily_limit"), nonnegative=True):
            return False
    return True


def valid_risk_assessment(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and str(data.get("risk_profile")) in {"CONSERVATIVE", "BALANCED", "AGGRESSIVE"}
        and valid_text_field(data.get("disclaimer"), max_length=300)
    )


def valid_text_field(value: Any, *, max_length: int = 256) -> bool:
    """Check a Core-returned display/id field before rendering it."""

    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        return False
    return not any(ord(char) < 32 and char not in "\t\n\r" for char in value)


def core_success(data: Any, *, nested_status: Any = None, required: tuple[str, ...] = ()) -> bool:
    """Validate the envelope and (optionally) a domain result status.

    ``nested_status`` accepts a single status or a collection of acceptable
    statuses (for example an AA request that settled fully or only partially).
    """

    if not isinstance(data, dict) or str(data.get("status", "")).upper() != "SUCCESS":
        return False
    payload = data.get("data")
    if nested_status is not None:
        accepted = (nested_status,) if isinstance(nested_status, str) else tuple(nested_status)
        accepted = tuple(str(item).upper() for item in accepted)
        if not isinstance(payload, dict) or str(payload.get("status", "")).upper() not in accepted:
            return False
        if any(key not in payload for key in required):
            return False
    return True


def amount_matches_expected(actual: Any, expected: Any) -> bool:
    """Compare Core and intent amounts without trusting non-finite values."""

    if not finite_number(actual, nonnegative=True):
        return False
    if expected is None:
        return True
    if not finite_number(expected, nonnegative=True):
        return False
    return abs(float(actual) - float(expected)) <= 0.01


def valid_bill_facts(facts: Any) -> bool:
    if not isinstance(facts, dict) or not finite_number(facts.get("total_spent"), nonnegative=True):
        return False
    count = facts.get("record_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return False
    source_ids = facts.get("source_tx_ids")
    if not isinstance(source_ids, list) or any(not valid_text_field(item, max_length=128) for item in source_ids):
        return False
    breakdown = facts.get("category_breakdown") or []
    if not isinstance(breakdown, list):
        return False
    for item in breakdown:
        if not isinstance(item, dict) or not valid_text_field(item.get("category"), max_length=120):
            return False
        if not finite_number(item.get("amount"), nonnegative=True) or not finite_number(item.get("share"), nonnegative=True):
            return False
        if float(item["share"]) > 1.0 + 1e-9:
            return False
    anomalies = facts.get("anomalies") or []
    if not isinstance(anomalies, list):
        return False
    for item in anomalies:
        if not isinstance(item, dict) or not finite_number(item.get("amount"), nonnegative=True):
            return False
        if item.get("tx_id") is not None and not valid_text_field(item.get("tx_id"), max_length=128):
            return False
    return True
