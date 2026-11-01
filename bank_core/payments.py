"""Payee resolution, daily totals, AA shares and card spend limits for money movements."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Optional

from bank_core.cards import (
    CARD_CHANNELS,
    card_channels,
    CARD_DAILY_SPENT,
    CARD_DAILY_SPENT_DATE,
    find_card,
    user_cards,
)
from bank_core.ledger import (
    ACCOUNTS,
    CONTACT_RELATION_SYNONYMS,
    CONTACT_RELATIONSHIP_LABELS,
    CONTACTS,
    DAILY_TRANSFER_DATE,
    DAILY_TRANSFERS,
    SPLIT_REQUESTS,
    USER_CONTACTS,
)
from security import (
    STATE_LOCK,
    SecurityError,
    amount_to_cents,
    sanitize_text,
)


def resolve_payee(value: Optional[str], *, from_user: Optional[str] = None) -> tuple[str, dict[str, Any]]:
    safe_value = sanitize_text(value, field="收款人", max_length=128)
    # The caller's own contact book is the first payee allow-list.  A name or
    # phone resolves directly; a kinship word ("爱人"/"儿子"/"兄弟"…) resolves
    # to the contact carrying that relationship - exactly one, otherwise the
    # ambiguity is named instead of guessing.
    if from_user:
        contact: Optional[dict[str, Any]] = None
        with STATE_LOCK:
            rows = USER_CONTACTS.get(from_user, [])
            matches = [row for row in rows if safe_value in {str(row.get("name") or ""), str(row.get("phone") or "")}]
            if len({row.get('account_id') for row in matches}) > 1:
                raise SecurityError('有多位同名联系人，请提供具体手机号或账户编号', 409, 'CONTACT_TARGET_AMBIGUOUS')
            contact = matches[0] if matches else None
            if contact is None:
                category = CONTACT_RELATION_SYNONYMS.get(safe_value) or (safe_value if safe_value in CONTACT_RELATIONSHIP_LABELS else None)
                if category:
                    matches = [row for row in rows if row.get("relationship") == category]
                    if len(matches) > 1:
                        names = "、".join(str(row.get("name")) for row in matches[:5])
                        label = CONTACT_RELATIONSHIP_LABELS.get(category, category)
                        raise SecurityError(
                            f"有多个关系为“{label}”的联系人（{names}），请说明具体姓名",
                            409,
                            "CONTACT_TARGET_AMBIGUOUS",
                        )
                    contact = matches[0] if matches else None
        if contact and contact.get("account_id"):
            receiver = ACCOUNTS.get(str(contact["account_id"]))
            if receiver and receiver.get("status") == "ACTIVE":
                if str(contact["account_id"]) == from_user:
                    raise SecurityError("不允许向本人账户转账", 400, "SELF_TRANSFER")
                return str(contact["account_id"]), receiver
    target_id = CONTACTS.get(safe_value, safe_value)
    receiver = ACCOUNTS.get(target_id)
    if not receiver:
        if safe_value in CONTACT_RELATION_SYNONYMS or safe_value in CONTACT_RELATIONSHIP_LABELS:
            raise SecurityError(
                f"联系人簿中没有关系为“{safe_value}”的联系人，请先添加或直接说明姓名",
                404,
                "TARGET_NOT_FOUND",
            )
        known: list[str] = []
        with STATE_LOCK:
            if from_user:
                known.extend(str(row.get("name")) for row in USER_CONTACTS.get(from_user, []) if row.get("name"))
            # Built-in payees resolve too, so they belong in the hint.
            for account_id in dict.fromkeys(CONTACTS.values()):
                name = (ACCOUNTS.get(account_id) or {}).get("name")
                if name and account_id != from_user:
                    known.append(str(name))
        known = list(dict.fromkeys(known))[:5]
        hint = f"可以转给：{'、'.join(known)}。" if known else "您还没有可用的收款人，可先在“联系人”页面添加。"
        raise SecurityError(
            f"没有找到收款人“{safe_value}”：它不是您的联系人姓名或手机号，也不是本行登记的收款账户。{hint}请说出准确的姓名或手机号",
            404,
            "TARGET_NOT_FOUND",
        )
    if from_user and target_id == from_user:
        raise SecurityError("不允许向本人账户转账", 400, "SELF_TRANSFER")
    if receiver.get("status") != "ACTIVE":
        raise SecurityError(f"收款人“{receiver.get('name') or safe_value}”的账户当前不可用（已冻结或停用），本次没有转账", 409, "TARGET_INACTIVE")
    return target_id, receiver


def inbound_transaction(*, account_id: str, payer_name: str, amount: float, remark: str = "",
                        category: str = "转账收入", merchant: Optional[str] = None, payer_id: Optional[str] = None) -> dict[str, Any]:
    """The payee-side statement row for money received.

    Before this, a transfer changed the payee's balance with no row in their
    statement, so a bill query could not explain where the money came from.
    ``direction = "IN"`` marks a credit: bill totals and the analysis sandbox
    leave it out of spending.
    """

    return {
        "tx_id": f"TXN{uuid.uuid4().hex[:12].upper()}",
        "account_id": account_id,
        "merchant": merchant or f"来自 {payer_name} 的转账",
        "category": category,
        "amount": amount,
        "direction": "IN",
        "remark": remark,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **({'counterparty_id': payer_id} if payer_id else {}),
    }


def ensure_account(user_id: str) -> dict[str, Any]:
    account = ACCOUNTS.get(user_id)
    if not account:
        raise SecurityError("账户不存在", 404, "ACCOUNT_NOT_FOUND")
    if account.get("status") != "ACTIVE":
        raise SecurityError("账户已被安全锁定或不可用", 423, "ACCOUNT_LOCKED")
    return account


def daily_spent(user_id: str) -> float:
    today = datetime.now().strftime("%Y-%m-%d")
    return _spent_on_day(DAILY_TRANSFERS, DAILY_TRANSFER_DATE, user_id, today)


def _spent_on_day(counters: dict[str, float], dates: dict[str, str], key: str, today: str) -> float:
    # A missing date retains intentionally seeded demo counters. Reading an
    # expired bucket projects zero without changing any ledger collection.
    return float(counters.get(key, 0.0)) if dates.get(key) in {None, today} else 0.0


def record_transfer_spend(user_id: str, amount: float) -> None:
    """Write amount and day together inside the caller's ledger transaction."""
    today = datetime.now().strftime("%Y-%m-%d")
    DAILY_TRANSFERS[user_id] = round(_spent_on_day(DAILY_TRANSFERS, DAILY_TRANSFER_DATE, user_id, today) + amount, 2)
    DAILY_TRANSFER_DATE[user_id] = today


def _split_shares(total_cents: int, participant_count: int) -> tuple[int, int]:
    """Split cents so the parts always add up exactly (no rounding leakage).

    Participants pay an equal floor share; the initiator bears the remainder,
    so ``participant_share * n + initiator_share == total`` for every amount
    (for example 100.00 / 3 -> 33.33 + 33.33 + 33.34).
    """

    divisor = participant_count + 1
    participant_share = total_cents // divisor
    initiator_share = total_cents - participant_share * participant_count
    return participant_share, initiator_share


def prepare_split_shares(user_id: str, total: float, participants: list[str], target: Optional[str]) -> dict[str, Any]:
    """Create payment requests; only each participant can authorize a debit."""

    total_cents = int(round(total * 100))
    participant_share_cents, initiator_share_cents = _split_shares(total_cents, len(participants))
    ensure_account(user_id)
    rows: list[dict[str, Any]] = []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for name in participants:
        target_id, account = resolve_payee(name, from_user=user_id)
        share = participant_share_cents / 100.0
        rows.append({"name": name, "account_id": target_id, "display_name": account.get("name"), "share": share, "status": "PENDING", "reason": None})
    outstanding = participant_share_cents * len(participants) / 100.0
    return {
        "collection": rows,
        "collected_amount": 0.0,
        "outstanding_amount": outstanding,
        "participant_share": participant_share_cents / 100.0,
        "initiator_share": initiator_share_cents / 100.0,
        "status": "PENDING",
        "created_at": timestamp,
        "target": target,
    }


def record_split_payment(*, split_id: str, payer_id: str, recipient_id: str, amount: float, transaction_id: str) -> dict[str, Any]:
    """Reconcile one independently authorized transfer with an AA request."""

    split = next((row for row in SPLIT_REQUESTS if row.get("split_id") == split_id), None)
    if not split or split.get("user_id") != recipient_id:
        raise SecurityError("AA 收款请求不存在或收款人不匹配", 404, "SPLIT_NOT_FOUND")
    participant = next((row for row in split["collection"] if row.get("account_id") == payer_id), None)
    if not participant or participant.get("status") != "PENDING":
        raise SecurityError("该 AA 份额不存在或已处理", 409, "SPLIT_SHARE_NOT_PENDING")
    if amount_to_cents(participant["share"]) != amount_to_cents(amount):
        raise SecurityError("AA 付款金额与待付份额不符", 409, "SPLIT_AMOUNT_MISMATCH")
    participant.update(status="COLLECTED", transaction_id=transaction_id, collected_at=datetime.now().isoformat())
    split["collected_amount"] = round(float(split["collected_amount"]) + amount, 2)
    split["outstanding_amount"] = round(float(split["outstanding_amount"]) - amount, 2)
    refresh_split_status(split)
    split["collected_at"] = datetime.now().isoformat()
    return split


def refresh_split_status(split: dict[str, Any]) -> None:
    shares = split.get("collection", [])
    if all(item.get("status") == "COLLECTED" for item in shares):
        split["status"] = "SETTLED"
    elif float(split.get("collected_amount", 0)) > 0:
        split["status"] = "PARTIAL"
    elif any(item.get("status") == "PENDING" for item in shares):
        split["status"] = "PENDING"
    else:
        split["status"] = "DECLINED"


def split_id_from_remark(remark: str) -> Optional[str]:
    match = re.fullmatch(r"AA:(SPL[A-Z0-9]+)", remark, re.IGNORECASE)
    if not match:
        match = re.search(r"(?:备注|附言)\s*[:：为是]?\s*AA:(SPL[A-Z0-9]+)\s*$", remark, re.IGNORECASE)
    return match.group(1).upper() if match else None


def card_daily_spent(card_id: str) -> float:
    today = datetime.now().strftime("%Y-%m-%d")
    return _spent_on_day(CARD_DAILY_SPENT, CARD_DAILY_SPENT_DATE, card_id, today)


def payment_card(user_id: str, card_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    """The single card a payment settles against, validated for status.

    One resolution for both halves of the check, because enforcement used to
    read ``_first_active_card()`` (the limit) while recording read ``cards[0]``
    (the counter) - so with one frozen and one active card the transfer was
    authorised against the active card's limit and *recorded* on the frozen
    one, leaving both cards' displayed figures wrong.

    ``card_id`` names the card explicitly (an operator choosing on the app);
    otherwise the only ACTIVE card is used and ambiguity is an error rather
    than a guess.
    """

    cards = user_cards(user_id)
    if not cards:
        return None
    if card_id:
        card = find_card(user_id, card_id)
        if card is None:
            raise SecurityError("卡片不存在或不属于当前用户", 404, "CARD_NOT_FOUND")
        if str(card.get("status")) != "ACTIVE":
            raise SecurityError("该卡片当前不可用于交易", 423, "CARD_BLOCKED")
        return card
    active = [card for card in cards if str(card.get("status")) == "ACTIVE"]
    if not active:
        raise SecurityError("当前没有可用于交易的卡片，请先解冻或解除挂失", 423, "CARD_BLOCKED")
    if len(active) > 1:
        names = "、".join(str(card.get("card_id")) for card in active[:5])
        raise SecurityError(f"您有多张可用的卡片（{names}），请指定用于支付的卡片", 409, "CARD_ID_REQUIRED")
    return active[0]


def enforce_card_limit(user_id: str, amount: float, *, card: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """Refuse a settlement that would exceed the paying card's daily limit.

    Returns the card that was checked, which the caller must hand to
    ``record_card_spend`` so both halves use one card.
    """

    resolved = card if card is not None else payment_card(user_id)
    if not resolved:
        return None
    limit = resolved.get("daily_limit")
    if limit is None:
        return resolved
    try:
        limit_value = float(limit)
    except (TypeError, ValueError):
        return resolved
    spent = card_daily_spent(str(resolved.get("card_id") or ""))
    if round(spent + amount, 2) > round(limit_value, 2):
        remaining = max(limit_value - spent, 0.0)
        raise SecurityError(
            f"超出卡片 {resolved.get('card_id') or ''} 的当日支付限额：每日 ¥{limit_value:,.2f}，今日还可用 ¥{remaining:,.2f}。"
            "可以降低金额，或先调整卡片限额（例如“把限额调到 20000”）",
            400, "CARD_LIMIT_EXCEEDED",
        )
    return resolved


def record_card_spend(user_id: str, amount: float, *, card: Optional[dict[str, Any]] = None) -> None:
    resolved = card if card is not None else payment_card(user_id)
    if not resolved:
        return
    card_id = str(resolved.get("card_id") or "")
    if not card_id:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    CARD_DAILY_SPENT[card_id] = round(_spent_on_day(CARD_DAILY_SPENT, CARD_DAILY_SPENT_DATE, card_id, today) + amount, 2)
    CARD_DAILY_SPENT_DATE[card_id] = today


def charge_card_and_balance(user_id: str, amount: float, *, card_id: Optional[str] = None, channel: Optional[str] = None) -> dict[str, Any]:
    """Enforce the paying card's limit and record the spend, consistently.

    Used by every path that actually moves money out of the account (transfer,
    wealth purchase), so ``daily_limit`` cannot be enforced on one outflow and
    silently ignored on another. ``channel`` names a card payment channel the
    cardholder can switch off (``ONLINE`` for merchant orders).
    """

    card = payment_card(user_id, card_id)
    if card is None:
        raise SecurityError("付款卡片当前不可用于交易", 423, "CARD_BLOCKED")
    if channel and not card_channels(card)[channel]:
        raise SecurityError(f"该卡已关闭{CARD_CHANNELS[channel]}，请先开通或换用其他卡", 423, "CARD_CHANNEL_DISABLED")
    checked = enforce_card_limit(user_id, amount, card=card)
    return checked or card
