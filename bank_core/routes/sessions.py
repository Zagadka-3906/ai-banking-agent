"""Login, session resolution, renewal and logout."""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from bank_core.access import (
    _raise_security,
    _record_failure_and_raise,
    _request_id,
)
from bank_core.ledger import (
    ACCOUNTS,
)
from bank_core.models import (
    LoginRequest,
)
from security import (
    SecurityError,
    audit_event,
    ensure_circuit_closed,
    issue_session_token,
    record_security_success,
    renew_session,
    revoke_session,
    sanitize_text,
    verify_login_otp,
    verify_password,
)

router = APIRouter()


def _login_otp_required() -> bool:
    """Whether a login must also present an SMS code.

    Optional by design: a password-only login is the demo default, and a
    deployment that wants the second factor turns it on here.
    """

    return os.getenv("BANK_LOGIN_REQUIRE_OTP", "0").lower() in {"1", "true", "yes", "on"}


@router.post("/api/v1/auth/login")
def login(req: LoginRequest, x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """Authenticate a subject with a password, optionally plus an SMS code.

    Failures answer uniformly: an unknown account, a disabled account and a
    wrong password all return 401 ``INVALID_CREDENTIALS``, so the endpoint is not
    an account-enumeration oracle.  Every failure is attributed to the presented
    subject and counts towards that account's breaker, which blunts online
    guessing regardless of whether the account exists.
    """

    request_id = _request_id(x_request_id)
    principal = sanitize_text(req.user_id, field="用户标识", max_length=128)
    if not principal:
        raise HTTPException(status_code=400, detail={"code": "INVALID_SUBJECT", "message": "缺少用户标识"})
    try:
        ensure_circuit_closed(principal)
    except SecurityError as exc:
        _raise_security(exc)

    account = ACCOUNTS.get(principal)
    if account is None or account.get("status") != "ACTIVE":
        _record_failure_and_raise(
            SecurityError("账号或口令不正确", 401, "INVALID_CREDENTIALS"),
            user_id=principal,
            action="LOGIN",
            request_id=request_id,
        )
    if not verify_password(req.password, account.get("password_hash")):
        _record_failure_and_raise(
            SecurityError("账号或口令不正确", 401, "INVALID_CREDENTIALS"),
            user_id=principal,
            action="LOGIN",
            request_id=request_id,
        )
    if _login_otp_required() and not verify_login_otp(req.otp):
        _record_failure_and_raise(
            SecurityError("短信验证码不正确", 401, "INVALID_LOGIN_OTP"),
            user_id=principal,
            action="LOGIN",
            request_id=request_id,
        )

    record_security_success(principal)
    result = issue_session_token(principal, request_id=request_id)
    audit_event(
        "LOGIN",
        user_id=principal,
        risk_level="GREEN",
        decision="ALLOW",
        outcome="SUCCESS",
        request_id=request_id,
        metadata={"otp_required": _login_otp_required()},
    )
    return {"code": 200, **result}


@router.get("/api/v1/auth/session")
def resolve_session_endpoint(
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
):
    """Resolve a presented session credential to its subject.

    A channel gateway must not keep its own copy of the session registry: a
    credential is only meaningful to the authority that issued it, and a second
    copy would drift - or worse, keep honouring a token the core has already
    dropped.  The gateway therefore asks this endpoint instead.

    The middleware has already validated the token and replaced ``X-User-Id``
    with its subject before the request reaches here, so an invalid or expired
    session never gets this far.
    """

    if not x_session_token:
        raise HTTPException(status_code=401, detail={"code": "SESSION_REQUIRED", "message": "缺少会话凭证"})
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    return {"code": 200, "user_id": x_user_id}


@router.post("/api/v1/auth/session/renew")
def renew_session_endpoint(
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
):
    """Hand a still-signed-in client a successor credential.

    The session window is short on purpose, but an absolute expiry signs out a
    user who is mid-form.  The middleware has already proved the presented
    token is live, so this can only ever slide an active session forward; an
    expired one is refused before it reaches here and must log in again.
    """

    request_id = _request_id(x_request_id)
    try:
        result = renew_session(x_session_token, request_id=request_id)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=x_user_id, action="SESSION_RENEW", request_id=request_id)
    return {"code": 200, **result}


@router.post("/api/v1/auth/logout")
def logout(
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
):
    """Revoke this login's credentials, including its renewed tokens.

    A client signing out must be able to kill the credential it holds on the
    server as well; wiping local storage only would leave a copied token valid
    until it expires. The scope is this login and its renewals; independent
    logins remain active. Credential rotation revokes every login instead.
    """

    request_id = _request_id(x_request_id)
    try:
        result = revoke_session(x_session_token, request_id=request_id)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=x_user_id, action="LOGOUT", request_id=request_id)
    return {"code": 200, "status": "LOGGED_OUT", "user_id": result["user_id"]}
