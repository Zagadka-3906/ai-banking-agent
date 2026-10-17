"""Caller identity, request ids and the security-failure path shared by every endpoint."""

from __future__ import annotations

import uuid
from typing import NoReturn, Optional

from fastapi import HTTPException, Request

from security import (
    SecurityError,
    audit_event,
    record_security_failure,
    sanitize_text,
    validate_actor,
)


def request_session_token(request: Optional[Request]) -> Optional[str]:
    """The raw session credential this request presented, if any.

    Set by ``request_safety_middleware`` after it verified the token, so an
    approval can be bound to the session that asked for it.
    """

    if request is None:
        return None
    value = getattr(request.state, "session_token", None)
    return str(value) if value else None


def server_request_id(value: Optional[str]) -> str:
    if value:
        try:
            return sanitize_text(value, field="请求编号", max_length=80)
        except SecurityError:
            pass
    return f"REQ{uuid.uuid4().hex}"


def raise_security(exc: SecurityError) -> NoReturn:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.detail},
        headers={"Retry-After": "30"} if exc.status_code == 423 else None,
    )


def record_failure_and_raise(exc: SecurityError, *, user_id: Optional[str], action: str, request_id: str) -> NoReturn:
    security_codes = {
        "TOKEN_REQUIRED", "INVALID_TOKEN", "INVALID_TOKEN_SIGNATURE", "TOKEN_NOT_FOUND", "TOKEN_REPLAY", "TOKEN_EXPIRED",
        "TOKEN_BINDING_MISMATCH", "TOKEN_CONTEXT_MISMATCH", "TOKEN_INVALID", "TOKEN_OPERATION_MISMATCH", "MFA_PROOF_INVALID",
        "MFA_PROOF_REPLAY", "MFA_PROOF_MISMATCH", "MFA_PROOF_EXPIRED", "MFA_REQUIRED", "MFA_PROOF_REQUIRED",
        "IDENTITY_MISMATCH", "PROMPT_INJECTION_BLOCKED", "CIRCUIT_OPEN",
        # Online password guessing counts towards the same per-account breaker as
        # a faulty nonce: the point of the breaker is to stop repetition.
        "INVALID_CREDENTIALS", "INVALID_LOGIN_OTP",
    }
    # MFA_FAILED is deliberately absent: the MFA check records its failed factor
    # itself, and counting it again here tripped the breaker after three attempts.
    if exc.code in security_codes:
        record_security_failure(user_id, exc.code, request_id=request_id)
    audit_event(action, user_id=user_id, decision="BLOCK", outcome="FAILURE", reason=exc.code, request_id=request_id, metadata={"message": exc.detail})
    raise_security(exc)


_WRITE_ACTIONS_REQUIRING_HEADER = {
    "RISK_PRECHECK",
    "LOCK_PRECHECK",
    "UNLOCK_PRECHECK",
    "OPERATION_PRECHECK",
    "TRANSFER_EXECUTE",
    "LOCK_FUND",
    "UNLOCK_FUND",
    "OPERATION_EXECUTE",
    # Risk assessment persists a suitability profile.  Although the resulting
    # profile is a green/read-style fact, the questionnaire write must still
    # be attributed to an authenticated principal so another account cannot be
    # overwritten by a forged body user id.
    "WEALTH_RISK_ASSESSMENT",
    # The contact book is per-user data that feeds the payee allow-list: adding
    # or removing entries without a presented principal would let an anonymous
    # caller seed (or clear) somebody else's payee list.
    "CONTACT_ADD",
    "CONTACT_DELETE",
    "CARD_ADD",
    "CARD_DETAIL_REVEAL",
}


def get_actor(requested_user: Optional[str], presented_user: Optional[str], *, action: str, request_id: str) -> str:
    # A body/path user id is an object selector, not authentication.  Require
    # a presented principal for every operation that can mint a nonce or
    # mutate state; otherwise a caller could deliberately attribute repeated
    # bad-token failures to another account while demo compatibility remains
    # enabled for read-only queries.
    if action in _WRITE_ACTIONS_REQUIRING_HEADER and not (presented_user or "").strip():
        exc = SecurityError("写操作需要登录身份", 401, "AUTHENTICATION_REQUIRED")
        record_failure_and_raise(exc, user_id=None, action=action, request_id=request_id)
        raise AssertionError("unreachable")
    try:
        return validate_actor(requested_user, presented_user)
    except SecurityError as exc:
        # Attribute an authentication/identity failure to the presented
        # principal (or anonymous), never to the account named in an
        # untrusted request body/path.  Otherwise an attacker could repeatedly
        # submit ``requested_user=victim`` with a mismatching header and open
        # the victim's circuit breaker as a denial-of-service primitive.
        failure_user = presented_user or (None if exc.code == "AUTHENTICATION_REQUIRED" else requested_user)
        record_failure_and_raise(exc, user_id=failure_user, action=action, request_id=request_id)
        raise AssertionError("unreachable")
