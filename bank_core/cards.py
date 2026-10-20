"""Card collections, the mock PAN vault and the card selection/state-transition rules."""

from __future__ import annotations

import os
import re
import secrets
import uuid
from typing import Any, Mapping, Optional

from bank_core.ledger import register_collections
from security import (
    SecurityError,
)

# 每用户卡片列表（支持一户多卡）。卡号是虚构演示号段，接口只回传脱敏形式；
# 卡余额与所属账户共享（借记卡语义），转账日限额按卡生效。
CARDS: dict[str, list[dict[str, Any]]] = {
    "user_zhang": [
        {"card_id": "CARD-001", "pan_ref": "MOCK-PAN-001", "number_masked": "**** **** **** 3082", "last4": "3082", "type": "借记卡", "status": "ACTIVE", "daily_limit": 5000.0, "label": "工资卡"}
    ],
    "user_li": [{"card_id": "CARD-002", "number_masked": "**** **** **** 0022", "last4": "0022", "type": "借记卡", "status": "ACTIVE", "daily_limit": 5000.0, "label": "演示借记卡"}],
    "user_wife": [{"card_id": "CARD-003", "number_masked": "**** **** **** 0033", "last4": "0033", "type": "借记卡", "status": "ACTIVE", "daily_limit": 5000.0, "label": "演示借记卡"}],
}
# Mock PANs live in a separate, read-only vault.  The ledger only stores the
# opaque reference and a masked projection; never put a full PAN or PIN here.
MOCK_CARD_VAULT: dict[str, dict[str, str]] = {
    "MOCK-PAN-001": {"card_number": "6222020000023082", "owner_user_id": "user_zhang", "owner_name": "张三", "issuer_name": "安言演示银行", "pin_hash": "$argon2id$v=19$m=65536,t=3,p=4$tUBB40MvKGEJJke2Q8yCIA$P2uj5W+fw/Y1HHA2wuOHFeo6i3FyX+NNLfkAOASpwkU"},
    "MOCK-PAN-002": {"card_number": "4111111111111111", "owner_user_id": "user_zhang", "owner_name": "张三", "issuer_name": "安言演示银行", "pin_hash": "$argon2id$v=19$m=65536,t=3,p=4$tUBB40MvKGEJJke2Q8yCIA$P2uj5W+fw/Y1HHA2wuOHFeo6i3FyX+NNLfkAOASpwkU"},
    "MOCK-PAN-003": {"card_number": "5555555555554444", "owner_user_id": "user_zhang", "owner_name": "张三", "issuer_name": "安言演示银行", "pin_hash": "$argon2id$v=19$m=65536,t=3,p=4$tUBB40MvKGEJJke2Q8yCIA$P2uj5W+fw/Y1HHA2wuOHFeo6i3FyX+NNLfkAOASpwkU"},
}
CARD_DISCLOSURE_FAILURES: dict[tuple[str, str], dict[str, Any]] = {}
# Self-service issuance needs a ceiling like every other collection here.  Cards
# are part of the persisted ledger and the snapshot is rewritten in full on each
# mutation, so an un-capped add is both unbounded growth and quadratic disk work
# - and each card carries its own daily spending limit.
CARD_MAX_PER_USER = max(1, int(os.getenv("BANK_MAX_CARDS_PER_USER", "10")))
# A newly issued card starts at this daily payment ceiling.
CARD_DEFAULT_DAILY_LIMIT = 5000.0
# ...and CARD_LIMIT may move it, but only within a sane band.  The transfer
# per-transaction ceiling is not a card-limit policy: it let one MFA approval
# raise a 5,000-yuan daily limit to 1,000,000.
CARD_MAX_DAILY_LIMIT = max(
    CARD_DEFAULT_DAILY_LIMIT,
    float(os.getenv("BANK_MAX_CARD_DAILY_LIMIT", "200000")),
)


def user_cards(user_id: str) -> list[dict[str, Any]]:
    return CARDS.get(user_id, [])


def _first_active_card(user_id: str) -> Optional[dict[str, Any]]:
    cards = user_cards(user_id)
    return next((card for card in cards if card.get("status") == "ACTIVE"), None)


def find_card(user_id: str, card_id: Optional[str]) -> Optional[dict[str, Any]]:
    """按卡号找卡；未给卡号时回退第一张 ACTIVE 卡（演示单卡语义兼容）。"""

    cards = user_cards(user_id)
    if card_id:
        card_id = str(card_id)
        return next((card for card in cards if str(card.get("card_id")) == card_id), None)
    return _first_active_card(user_id) or (cards[0] if cards else None)


def select_card(user_id: str, card_id: Optional[str], *, allowed_states: Optional[set[str]] = None) -> dict[str, Any]:
    """Resolve the card one operation applies to, refusing to guess.

    Two ways this used to pick the wrong card, both reachable from the app:

    * an **omitted** id fell back to "the first ACTIVE card", so a multi-card user
      who pointed at their second card could have the first one frozen;
    * a **wrong** id fell back the same way (``find_card(...) or
      _first_active_card(...)``), so a typo - or another account's id - silently
      retargeted the operation at whatever card came first.

    Ambiguity is therefore judged against the states this operation can actually
    act on: un-reporting a lost card considers lost cards, freezing considers
    active ones.  A named id that does not resolve is always an error, never a
    fallback.
    """

    cards = user_cards(user_id)
    if not cards:
        raise SecurityError("当前用户没有可操作的卡片", 404, "CARD_NOT_FOUND")
    if card_id:
        found = find_card(user_id, card_id)
        if found is None:
            raise SecurityError("卡片不存在或不属于当前用户", 404, "CARD_NOT_FOUND")
        return found
    candidates = [item for item in cards if allowed_states is None or str(item.get("status")) in allowed_states]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise SecurityError("卡片当前状态不允许该操作", 409, "INVALID_CARD_STATE")
    names = "、".join(str(item.get("card_id")) for item in candidates[:5])
    raise SecurityError(f"您有多张可选的卡片（{names}），请指定要操作的那一张", 409, "CARD_ID_REQUIRED")


# The single source of truth for what each card operation may act on, as
# source-state -> next-state.  It used to exist as three near-copies (a precheck
# map, an executor map and the selector's implicit "ACTIVE only"), which is how a
# card could be validated against one table and executed against another.
CARD_TRANSITIONS: dict[str, dict[str, str]] = {
    "CARD_FREEZE": {"ACTIVE": "FROZEN"},
    "CARD_UNFREEZE": {"FROZEN": "ACTIVE"},
    "CARD_REPORT_LOST": {"ACTIVE": "LOST", "FROZEN": "LOST"},
    "CARD_UNREPORT_LOST": {"LOST": "ACTIVE"},
}


# Payment channels a cardholder can switch off. Absent = enabled, so cards
# stored before this field existed keep working.
CARD_CHANNELS = {"ONLINE": "网上支付", "OVERSEAS": "境外交易"}


def card_source_states(operation: str) -> Optional[set[str]]:
    """States ``operation`` can act on, or None when it has no state restriction."""

    if operation == "CARD_CHANNEL":
        return {"ACTIVE"}
    transitions = CARD_TRANSITIONS.get(operation)
    return set(transitions) if transitions else None


def card_channels(card: Mapping[str, Any]) -> dict[str, bool]:
    stored = card.get("channels")
    if not isinstance(stored, dict):
        stored = {}
    return {channel: stored.get(channel) is not False for channel in CARD_CHANNELS}


def _mock_card_masked() -> str:
    """虚构借记卡的脱敏卡号（62 开头 16 位，仅回传掩码形式）。"""

    digits = "622202" + "".join(str(secrets.randbelow(10)) for _ in range(10))
    return "**** **** **** " + digits[-4:]


def issue_card_locked(user_id: str, *, card_type: str, label: Optional[str] = None, pan_ref: Optional[str] = None, number_masked: Optional[str] = None, last4: Optional[str] = None) -> dict[str, Any]:
    """Append one card to ``user_id``'s wallet, enforcing the per-user ceiling.

    Every issuance path funnels through here.  ``CARD_APPLY`` (the agent's
    "申请一张虚拟卡" flow) used to append directly, so the ceiling that
    ``/cards/{user_id}/add`` enforced could be bypassed simply by asking the
    assistant instead - and each card is ledger state whose snapshot is
    rewritten in full, which is what the ceiling exists to bound.

    Caller must hold ``STATE_LOCK``.
    """

    held = CARDS.setdefault(user_id, [])
    if len(held) >= CARD_MAX_PER_USER:
        raise SecurityError(
            f"每人最多持有 {CARD_MAX_PER_USER} 张卡片，请先注销不再使用的卡",
            409,
            "CARD_LIMIT_REACHED",
        )
    safe_masked = number_masked or _mock_card_masked()
    card = {
        "card_id": f"CARD-{uuid.uuid4().hex[:8].upper()}",
        **({"pan_ref": pan_ref} if pan_ref else {}),
        "number_masked": safe_masked,
        "last4": last4 or re.sub(r"\D", "", safe_masked)[-4:],
        "type": card_type,
        "status": "ACTIVE",
        "daily_limit": CARD_DEFAULT_DAILY_LIMIT,
        "label": label,
    }
    held.append(card)
    return card


def card_summary(card: Mapping[str, Any], *, available_balance: Optional[float] = None) -> dict[str, Any]:
    """Explicitly safe card projection; private vault references never escape."""

    allowed = {"card_id", "last4", "number_masked", "type", "status", "daily_limit", "label"}
    result = {key: card[key] for key in allowed if key in card}
    if "last4" not in result:
        masked = str(card.get("number_masked") or "")
        result["last4"] = re.sub(r"\D", "", masked)[-4:] if len(re.sub(r"\D", "", masked)) >= 4 else ""
    result["channels"] = card_channels(card)
    if available_balance is not None:
        result["available_balance"] = available_balance
    return result


def normalize_card_number(value: Any) -> str:
    number = re.sub(r"[\s-]", "", str(value or ""))
    if not re.fullmatch(r"\d{12,19}", number):
        raise SecurityError("银行卡信息无法核验", 400, "CARD_BINDING_REJECTED")
    checksum = 0
    for index, digit in enumerate(reversed(number)):
        value_int = int(digit)
        if index % 2 == 1:
            value_int *= 2
            if value_int > 9:
                value_int -= 9
        checksum += value_int
    if checksum % 10:
        raise SecurityError("银行卡信息无法核验", 400, "CARD_BINDING_REJECTED")
    return number


def card_mask(number: str) -> str:
    return f"**** **** **** {number[-4:]}"
# Per-card, per-day settled spend.  The card's ``daily_limit`` is enforced on
# settlement, so lowering a limit (CARD_LIMIT) immediately constrains further
# payments instead of only being a display field.
CARD_DAILY_SPENT: dict[str, float] = {}
CARD_DAILY_SPENT_DATE: dict[str, str] = {}
register_collections(CARDS=CARDS, CARD_DAILY_SPENT=CARD_DAILY_SPENT, CARD_DAILY_SPENT_DATE=CARD_DAILY_SPENT_DATE)
