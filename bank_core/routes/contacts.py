"""Contact-book endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header
from pydantic import BaseModel

from bank_core.access import (
    get_actor,
    raise_security,
    record_failure_and_raise,
    server_request_id,
)
from bank_core.contacts import (
    ContactAddRequest,
    contact_public_view,
    resolve_contact_account,
)
from bank_core.idempotency import (
    idempotency_abort,
    idempotency_begin,
    idempotency_complete,
)
from bank_core.ledger import (
    ACCOUNTS,
    CONTACT_BIRTHDAY_RE,
    CONTACT_MAX_PER_USER,
    CONTACT_PHONE_RE,
    CONTACT_RELATIONSHIP_LABELS,
    CONTACT_RELATIONSHIPS,
    CONTACTS,
    USER_CONTACTS,
    ledger_transaction,
)
from bank_core.payments import (
    ensure_account,
    resolve_payee,
)
from bank_core.persistence import (
    audit_ledger_event as audit_event,
    durable_ledger_write,
    persist_ledger,
)
from security import (
    STATE_LOCK,
    SecurityError,
    sanitize_text,
)

router = APIRouter()


class PayeeCheckRequest(BaseModel):
    user_id: str
    target: str


@router.post("/api/v1/payees/check")
def check_payee(req: PayeeCheckRequest, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """Whether a payee name/phone resolves for the caller - no amount, no token.

    The same resolution the transfer precheck performs, exposed so the
    conversation can say "张三 is you" or "no such payee" before asking for an
    amount. It names nothing the precheck would not, and only for the caller.
    """

    request_id = server_request_id(x_request_id)
    actor = get_actor(req.user_id, x_user_id, action="PAYEE_CHECK", request_id=request_id)
    ensure_account(req.user_id)
    try:
        _, receiver = resolve_payee(req.target, from_user=actor)
    except SecurityError as exc:
        audit_event("PAYEE_CHECK", user_id=actor, risk_level="GREEN", decision="DENY", outcome="FAILURE", request_id=request_id, metadata={"code": exc.code})
        raise_security(exc)
    audit_event("PAYEE_CHECK", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id)
    return {"code": 200, "data": {"target_name": receiver.get("name")}}


@router.get("/api/v1/contacts/{user_id}")
def get_contacts(user_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = server_request_id(x_request_id)
    actor = get_actor(user_id, x_user_id, action="CONTACT_QUERY", request_id=request_id)
    ensure_account(user_id)
    # Only expose the caller's allow-listed contacts; phone numbers are
    # masked so this endpoint cannot become an account-enumeration oracle.
    rows = []
    seen = set()
    for alias, target_id in CONTACTS.items():
        if target_id in seen:
            continue
        receiver = ACCOUNTS.get(target_id)
        if not receiver:
            continue
        phone = str(receiver.get("phone", ""))
        rows.append({"contact_id": target_id, "display_name": receiver.get("name"), "phone_masked": phone[:3] + "****" + phone[-4:] if len(phone) >= 7 else "****"})
        seen.add(target_id)
    audit_event("CONTACT_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"count": len(rows)})
    return {"code": 200, "data": rows}


@router.get("/api/v1/contacts/{user_id}/book")
def list_contact_book(user_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """The caller's own contact book (distinct from the built-in payee allow-list)."""

    request_id = server_request_id(x_request_id)
    actor = get_actor(user_id, x_user_id, action="CONTACT_QUERY", request_id=request_id)
    ensure_account(user_id)
    with STATE_LOCK:
        rows = [contact_public_view(dict(row), caller_id=user_id) for row in USER_CONTACTS.get(user_id, [])]
    audit_event("CONTACT_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"count": len(rows), "source": "contact_book"})
    return {"code": 200, "data": rows}


@router.post("/api/v1/contacts/{user_id}/book")
@durable_ledger_write
def add_contact(user_id: str, req: ContactAddRequest, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    """Add one contact (name, phone, relationship, birthday) for the caller.

    The contact becomes a first-class payee: by name or phone it resolves in
    ``resolve_payee`` like any built-in contact.  Everything is validated at
    the boundary - the relationship against a small allow-list, the phone as
    digits, the birthday as a real (not future) calendar date.
    """

    request_id = server_request_id(x_request_id)
    actor = get_actor(user_id, x_user_id, action="CONTACT_ADD", request_id=request_id)
    idem_payload = {"user_id": user_id, "name": req.name, "phone": req.phone, "relationship": req.relationship, "birthday": req.birthday}
    cached = idempotency_begin(actor, "contact_add", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    try:
        ensure_account(user_id)
        name = sanitize_text(req.name, field="联系人姓名", max_length=120)
        if not name:
            raise SecurityError("联系人姓名不能为空", 400, "CONTACT_NAME_REQUIRED")
        phone = sanitize_text(req.phone, field="联系电话", max_length=20)
        if not CONTACT_PHONE_RE.fullmatch(phone or ""):
            raise SecurityError("联系电话格式无效", 400, "INVALID_CONTACT_PHONE")
        relationship_raw = sanitize_text(req.relationship, field="与联系人关系", max_length=20)
        # Accept the Chinese label ("家人") or the canonical code ("FAMILY");
        # anything else is refused rather than stored as free text.
        relationship = CONTACT_RELATIONSHIPS.get(relationship_raw)
        if relationship is None and relationship_raw.upper() in CONTACT_RELATIONSHIP_LABELS:
            relationship = relationship_raw.upper()
        if relationship is None:
            raise SecurityError("关系仅支持：家人/朋友/同事/其他", 400, "INVALID_RELATIONSHIP")
        birthday: Optional[str] = None
        if req.birthday:
            birthday_value = sanitize_text(req.birthday, field="生日", max_length=10)
            if not CONTACT_BIRTHDAY_RE.fullmatch(birthday_value or ""):
                raise SecurityError("生日格式应为 YYYY-MM-DD", 400, "INVALID_BIRTHDAY")
            try:
                parsed = datetime.strptime(birthday_value, "%Y-%m-%d").date()
            except ValueError as exc:
                raise SecurityError("生日不是有效日期", 400, "INVALID_BIRTHDAY") from exc
            if parsed > datetime.now().date():
                raise SecurityError("生日不能是未来日期", 400, "INVALID_BIRTHDAY")
            birthday = birthday_value
    except SecurityError as exc:
        idempotency_abort(actor, "contact_add", idempotency_key)
        record_failure_and_raise(exc, user_id=actor, action="CONTACT_ADD", request_id=request_id)

    with STATE_LOCK:
        with ledger_transaction():
            existing = USER_CONTACTS.get(user_id, [])
            if any(row.get("name") == name and row.get("phone") == phone for row in existing):
                raise SecurityError("该联系人已存在", 409, "CONTACT_EXISTS")
            if len(existing) >= CONTACT_MAX_PER_USER:
                raise SecurityError(
                    f"联系人最多 {CONTACT_MAX_PER_USER} 位，请先删除不再使用的联系人",
                    409,
                    "CONTACT_LIMIT_REACHED",
                )
            account_id, linked_existing, link_verification = resolve_contact_account(name, phone, caller_id=user_id)
            row = {
                "contact_id": f"CTT{uuid.uuid4().hex[:10].upper()}",
                "owner_id": user_id,
                "name": name,
                "phone": phone,
                "relationship": relationship,
                "birthday": birthday,
                "account_id": account_id,
                "linked_existing": linked_existing,
                "link_verification": link_verification,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            USER_CONTACTS.setdefault(user_id, []).append(row)
    persist_ledger()
    response = {"code": 200, "status": "CREATED", "data": contact_public_view(dict(row), caller_id=user_id)}
    idempotency_complete(actor, "contact_add", idempotency_key, idem_payload, response)
    audit_event(
        "CONTACT_ADD",
        user_id=actor,
        risk_level="GREEN",
        decision="ALLOW",
        outcome="SUCCESS",
        request_id=request_id,
        metadata={"contact_id": row["contact_id"], "name": name, "phone_masked": phone[:3] + "****" + phone[-4:] if len(phone) >= 7 else "****", "relationship": relationship, "linked_existing": linked_existing},
    )
    return response


@router.post("/api/v1/contacts/{user_id}/book/{contact_id}/remove")
@durable_ledger_write
def remove_contact(user_id: str, contact_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """Remove one contact from the caller's book.

    Only the book entry goes; a provisioned shadow account stays so past
    transfers and its received funds remain reconcilable.
    """

    request_id = server_request_id(x_request_id)
    actor = get_actor(user_id, x_user_id, action="CONTACT_DELETE", request_id=request_id)
    ensure_account(user_id)
    safe_contact_id = sanitize_text(contact_id, field="联系人编号", max_length=64)
    with STATE_LOCK:
        with ledger_transaction():
            rows = USER_CONTACTS.get(user_id, [])
            index = next((i for i, row in enumerate(rows) if row.get("contact_id") == safe_contact_id), None)
            if index is None:
                raise SecurityError("联系人不存在", 404, "CONTACT_NOT_FOUND")
            removed = rows.pop(index)
    persist_ledger()
    audit_event(
        "CONTACT_DELETE",
        user_id=actor,
        risk_level="GREEN",
        decision="ALLOW",
        outcome="SUCCESS",
        request_id=request_id,
        metadata={"contact_id": safe_contact_id, "name": str(removed.get("name") or "")},
    )
    return {"code": 200, "status": "REMOVED", "data": {"contact_id": safe_contact_id}}


@router.post("/api/v1/contacts/{user_id}/book/{contact_id}/update")
@durable_ledger_write
def update_contact(
    user_id: str,
    contact_id: str,
    req: ContactAddRequest,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
):
    """Update one contact (name, phone, relationship, birthday) in the caller's book."""

    request_id = server_request_id(x_request_id)
    actor = get_actor(user_id, x_user_id, action="CONTACT_UPDATE", request_id=request_id)
    try:
        ensure_account(user_id)
        safe_contact_id = sanitize_text(contact_id, field="联系人编号", max_length=64)
        name = sanitize_text(req.name, field="联系人姓名", max_length=120)
        if not name:
            raise SecurityError("联系人姓名不能为空", 400, "CONTACT_NAME_REQUIRED")
        phone = sanitize_text(req.phone, field="联系电话", max_length=20)
        if not CONTACT_PHONE_RE.fullmatch(phone or ""):
            raise SecurityError("联系电话格式无效", 400, "INVALID_CONTACT_PHONE")
        relationship_raw = sanitize_text(req.relationship, field="与联系人关系", max_length=20)
        relationship = CONTACT_RELATIONSHIPS.get(relationship_raw)
        if relationship is None and relationship_raw.upper() in CONTACT_RELATIONSHIP_LABELS:
            relationship = relationship_raw.upper()
        if relationship is None:
            raise SecurityError("关系仅支持：家人/朋友/同事/其他", 400, "INVALID_RELATIONSHIP")
        birthday: Optional[str] = None
        if req.birthday:
            birthday_value = sanitize_text(req.birthday, field="生日", max_length=10)
            if not CONTACT_BIRTHDAY_RE.fullmatch(birthday_value or ""):
                raise SecurityError("生日格式应为 YYYY-MM-DD", 400, "INVALID_BIRTHDAY")
            try:
                parsed = datetime.strptime(birthday_value, "%Y-%m-%d").date()
            except ValueError as exc:
                raise SecurityError("生日不是有效日期", 400, "INVALID_BIRTHDAY") from exc
            if parsed > datetime.now().date():
                raise SecurityError("生日不能是未来日期", 400, "INVALID_BIRTHDAY")
            birthday = birthday_value
    except SecurityError as exc:
        record_failure_and_raise(exc, user_id=actor, action="CONTACT_UPDATE", request_id=request_id)

    with STATE_LOCK:
        with ledger_transaction():
            rows = USER_CONTACTS.get(user_id, [])
            target = next((row for row in rows if row.get("contact_id") == safe_contact_id), None)
            if target is None:
                raise SecurityError("联系人不存在", 404, "CONTACT_NOT_FOUND")
            if any(row.get("contact_id") != safe_contact_id and row.get("name") == name and row.get("phone") == phone for row in rows):
                raise SecurityError("该联系人已存在", 409, "CONTACT_EXISTS")
            account_id, linked_existing, link_verification = resolve_contact_account(name, phone, caller_id=user_id)
            target["name"] = name
            target["phone"] = phone
            target["relationship"] = relationship
            target["birthday"] = birthday
            target["account_id"] = account_id
            target["linked_existing"] = linked_existing
            target["link_verification"] = link_verification
            target["updated_at"] = datetime.now(timezone.utc).isoformat()
    persist_ledger()
    response = {"code": 200, "status": "UPDATED", "data": contact_public_view(dict(target), caller_id=user_id)}
    audit_event(
        "CONTACT_UPDATE",
        user_id=actor,
        risk_level="GREEN",
        decision="ALLOW",
        outcome="SUCCESS",
        request_id=request_id,
        metadata={
            "contact_id": safe_contact_id,
            "name": name,
            "phone_masked": phone[:3] + "****" + phone[-4:] if len(phone) >= 7 else "****",
            "relationship": relationship,
            "linked_existing": linked_existing,
        },
    )
    return response
