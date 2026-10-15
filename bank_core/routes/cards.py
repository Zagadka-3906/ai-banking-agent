"""Card listing, linking and one-time card number disclosure."""

from __future__ import annotations

import hashlib
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Header, Request
from pydantic import StrictBool

from bank_core.access import (
    _get_actor,
    _presented_session,
    _record_failure_and_raise,
    _request_id,
)
from bank_core.cards import (
    CARD_DISCLOSURE_FAILURES,
    CARD_MAX_PER_USER,
    MOCK_CARD_VAULT,
    _card_mask,
    _card_summary,
    _find_card,
    _issue_card_locked,
    _normalize_card_number,
    _user_cards,
)
from bank_core.idempotency import (
    _idempotency_abort,
    _idempotency_begin,
    _idempotency_complete,
)
from bank_core.ledger import (
    _ledger_transaction,
)
from bank_core.models import (
    StrictModel,
)
from bank_core.payments import (
    _card_daily_spent,
    _ensure_account,
)
from bank_core.persistence import (
    persist_ledger,
)
from security import (
    STATE_LOCK,
    SecurityError,
    audit_event,
    sanitize_text,
    verify_password,
)

router = APIRouter()


@router.get("/api/v1/cards/{user_id}")
def get_cards(user_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(user_id, x_user_id, action="CARD_QUERY", request_id=request_id)
    account = _ensure_account(user_id)
    available = round(float(account["balance"]) - float(account.get("locked_amount", 0.0)), 2)
    data: list[dict[str, Any]] = []
    for card in _user_cards(user_id):
        enriched = _card_summary(card)
        card_id = str(card.get("card_id") or "")
        if card_id:
            spent = _card_daily_spent(card_id)
            enriched["daily_spent_today"] = spent
            try:
                enriched["daily_remaining"] = round(max(0.0, float(card.get("daily_limit") or 0.0) - spent), 2)
            except (TypeError, ValueError):
                enriched["daily_remaining"] = None
        # 借记卡语义：卡余额 = 所属账户可用余额。
        enriched["available_balance"] = available
        data.append(enriched)
    audit_event("CARD_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"count": len(data)})
    return {"code": 200, "data": data}


class CardAddRequest(StrictModel):
    card_type: str = "借记卡"
    label: Optional[str] = None
    card_number: str
    source: str
    user_confirmed: StrictBool


@router.post("/api/v1/cards/{user_id}/add")
def add_card(
    user_id: str,
    req: CardAddRequest,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """自行添加一张银行卡（演示：开立即生效，虚构卡号仅回传脱敏形式）。"""

    request_id = _request_id(x_request_id)
    actor = _get_actor(user_id, x_user_id, action="CARD_ADD", request_id=request_id)
    try:
        normalized_number = _normalize_card_number(req.card_number)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=actor, action="CARD_ADD", request_id=request_id)
    idem_payload = {"user_id": user_id, "card_type": req.card_type, "label": req.label, "card_fingerprint": hashlib.sha256(normalized_number.encode("utf-8")).hexdigest(), "source": req.source, "user_confirmed": req.user_confirmed}
    cached = _idempotency_begin(actor, "card_add", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    try:
        _ensure_account(user_id)
        card_type_raw = sanitize_text(req.card_type or "借记卡", field="卡片类型", max_length=20)
        card_type = {"借记卡": "借记卡", "虚拟卡": "虚拟卡"}.get(card_type_raw, card_type_raw.upper() if card_type_raw.upper() in {"DEBIT", "VIRTUAL"} else None)
        if card_type is None:
            raise SecurityError("卡片类型仅支持：借记卡/虚拟卡", 400, "INVALID_CARD_TYPE")
        label = sanitize_text(req.label, field="卡片别名", max_length=40) if req.label else None
        source = str(req.source or "").strip().upper()
        if source not in {"MANUAL", "OCR"}:
            raise SecurityError("银行卡信息无法核验", 400, "CARD_BINDING_REJECTED")
        if not req.user_confirmed:
            raise SecurityError("请确认银行卡信息后继续", 400, "CARD_CONFIRMATION_REQUIRED")
        if len(_user_cards(user_id)) >= CARD_MAX_PER_USER:
            raise SecurityError(
                f"每人最多持有 {CARD_MAX_PER_USER} 张卡片，请先注销不再使用的卡",
                409,
                "CARD_LIMIT_REACHED",
            )
        pan_ref, vault = next(((ref, item) for ref, item in MOCK_CARD_VAULT.items() if item["card_number"] == normalized_number), (None, None))
        if vault is None or vault.get("owner_user_id") != user_id:
            raise SecurityError("银行卡信息无法核验", 400, "CARD_BINDING_REJECTED")
    except SecurityError as exc:
        _idempotency_abort(actor, "card_add", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="CARD_ADD", request_id=request_id)

    try:
        with STATE_LOCK:
            with _ledger_transaction():
                # Re-check the opaque reference while holding the same lock as
                # issuance. This closes the concurrent identical-bind race.
                if any(item.get("pan_ref") == pan_ref for item in _user_cards(user_id)):
                    raise SecurityError("银行卡已绑定", 409, "CARD_BINDING_REJECTED")
                card = _issue_card_locked(user_id, card_type=card_type, label=label, pan_ref=pan_ref, number_masked=_card_mask(normalized_number), last4=normalized_number[-4:])
    except SecurityError as exc:
        _idempotency_abort(actor, "card_add", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="CARD_ADD", request_id=request_id)
    persist_ledger()
    response = {"code": 200, "status": "ISSUED", "data": _card_summary(card)}
    _idempotency_complete(actor, "card_add", idempotency_key, idem_payload, response)
    audit_event("CARD_ADD", user_id=actor, risk_level="YELLOW", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"card_id": card["card_id"], "last4": card["last4"], "source": source})
    return response


class CardRevealRequest(StrictModel):
    pin: str


@router.post("/api/v1/cards/{user_id}/{card_id}/reveal")
def reveal_card(user_id: str, card_id: str, req: CardRevealRequest, request: Request = None, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(user_id, x_user_id, action="CARD_DETAIL_REVEAL", request_id=request_id)
    session = _presented_session(request)
    if not session:
        exc = SecurityError("需要登录会话后查看卡片信息", 401, "CARD_DISCLOSURE_DENIED")
        _record_failure_and_raise(exc, user_id=actor, action="CARD_DETAIL_REVEAL", request_id=request_id)
    card = _find_card(user_id, card_id)
    vault = MOCK_CARD_VAULT.get(str(card.get("pan_ref"))) if card else None
    key = (user_id, card_id)
    failure = CARD_DISCLOSURE_FAILURES.get(key, {})
    now = time.time()
    if float(failure.get("locked_until", 0)) > now:
        exc = SecurityError("卡片查看已锁定，请稍后重试", 423, "CARD_DISCLOSURE_LOCKED")
        _record_failure_and_raise(exc, user_id=actor, action="CARD_DETAIL_REVEAL", request_id=request_id)
    valid_target = bool(card and vault and card.get("status") != "LOST" and vault.get("owner_user_id") == actor)
    if not valid_target or not verify_password(req.pin, vault.get("pin_hash") if vault else None):
        failed = int(failure.get("failed", 0)) + 1
        if failed >= 5:
            CARD_DISCLOSURE_FAILURES[key] = {"failed": failed, "locked_until": now + 300}
            exc = SecurityError("卡片查看已锁定，请稍后重试", 423, "CARD_DISCLOSURE_LOCKED")
        else:
            CARD_DISCLOSURE_FAILURES[key] = {"failed": failed, "locked_until": 0}
            exc = SecurityError("卡片查看验证失败", 401, "CARD_DISCLOSURE_DENIED")
        _record_failure_and_raise(exc, user_id=actor, action="CARD_DETAIL_REVEAL", request_id=request_id)
    CARD_DISCLOSURE_FAILURES.pop(key, None)
    disclosure_id = f"DISC-{uuid.uuid4().hex[:12].upper()}"
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    audit_event("CARD_DETAIL_REVEAL", user_id=actor, risk_level="RED", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"card_id": card_id, "last4": card.get("last4"), "disclosure_id": disclosure_id, "expires_at": expires_at})
    return {"code": 200, "data": {"owner_name": vault["owner_name"], "card_number": vault["card_number"], "issuer_name": vault["issuer_name"], "expires_at": expires_at, "disclosure_id": disclosure_id}}
