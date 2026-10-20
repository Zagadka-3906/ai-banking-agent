"""Health, security status, channel security signals and audit endpoints."""

from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request

from bank_core.access import (
    request_session_token,
    raise_security,
    server_request_id,
)
from bank_core.http_edge import (
    API_VERSION,
)
from bank_core.models import (
    StrictModel,
)
from bank_core.payments import (
    ensure_account,
)
from bank_core.persistence import (
    ledger_store_status,
)
from bank_core.task_scheduler import scheduler_status
from sandbox import sandbox_health
from security import (
    SecurityError,
    audit_event,
    behavior_status,
    circuit_status,
    get_audit_logs,
    get_audit_trace,
    record_security_failure,
    security_health,
    validate_actor,
    verify_audit_chain,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Operational observability
# ---------------------------------------------------------------------------
@router.get("/healthz")
def healthz():
    """Liveness probe that does not disclose account or credential data."""

    return {"status": "ok", "service": "mock-banking-core", "version": API_VERSION}


@router.get("/api/v1/security/health")
def security_health_endpoint():
    return {"code": 200, **security_health(), "sandbox": sandbox_health(), "ledger_persistence": ledger_store_status(), "task_scheduler": scheduler_status()}


class SecuritySignalRequest(StrictModel):
    signal: str
    source: str = "AGENT"


# Blocks a channel decides before any Core call. They count against the same
# per-account breaker as a bad nonce or password.
_CHANNEL_SECURITY_SIGNALS = {"PROMPT_INJECTION_BLOCKED"}
_CHANNEL_SIGNAL_SOURCES = {"AGENT", "FEISHU"}


@router.post("/api/v1/security/signals")
def report_security_signal(
    req: SecuritySignalRequest,
    request: Request,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
):
    """Count a Gateway-side block against the caller's own circuit breaker.

    The Gateway rejects injected instructions before reaching Core, so repeated
    attempts never touched the breaker. Only a verified session principal can
    be charged: a self-asserted ``X-User-Id`` could otherwise lock another
    account on purpose.
    """

    request_id = server_request_id(x_request_id)
    if not request_session_token(request) or not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "SESSION_REQUIRED", "message": "上报安全事件需要会话凭证"})
    signal = str(req.signal or "").strip().upper()
    source = str(req.source or "").strip().upper()
    if signal not in _CHANNEL_SECURITY_SIGNALS or source not in _CHANNEL_SIGNAL_SOURCES:
        raise HTTPException(status_code=400, detail={"code": "SIGNAL_NOT_ALLOWED", "message": "不支持的安全事件类型"})
    status = record_security_failure(x_user_id, signal, request_id=request_id)
    audit_event("SECURITY_SIGNAL", user_id=x_user_id, decision="BLOCK", outcome="LOCKED" if status["locked"] else "RECORDED",
                reason=signal, request_id=request_id, metadata={"source": source, "failure_count": status["failure_count"]})
    return {"code": 200, "locked": status["locked"], "failure_count": status["failure_count"]}


@router.get("/api/v1/security/circuit/{user_id}")
def security_circuit_endpoint(user_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id")):
    if not x_user_id or x_user_id != user_id:
        raise HTTPException(status_code=403, detail={"code": "AUTHORIZATION_REQUIRED", "message": "只能查询当前用户的安全状态"})
    return {"code": 200, **circuit_status(user_id)}


@router.get("/api/v1/security/behavior/{user_id}")
def security_behavior_endpoint(user_id: str, x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"), x_user_id: Optional[str] = Header(None, alias="X-User-Id")):
    """Explain the behaviour-risk score behind a combination lockout."""

    expected = os.getenv("BANK_ADMIN_AUDIT_TOKEN")
    admin_ok = bool(expected and x_admin_token and hmac.compare_digest(expected, x_admin_token))
    if not admin_ok and (not x_user_id or x_user_id != user_id):
        raise HTTPException(status_code=403, detail={"code": "AUTHORIZATION_REQUIRED", "message": "只能查询当前用户的行为风险状态"})
    return {"code": 200, **behavior_status(user_id)}


@router.get("/api/v1/security/audit")
@router.get("/api/v1/audit/logs")
def security_audit_endpoint(limit: int = 100, user_id: Optional[str] = None, x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"), x_user_id: Optional[str] = Header(None, alias="X-User-Id")):
    expected = os.getenv("BANK_ADMIN_AUDIT_TOKEN")
    admin_ok = bool(expected and x_admin_token and hmac.compare_digest(expected, x_admin_token))
    # A normal user may inspect only their own redacted events; cross-user or
    # unscoped access remains admin-only.
    if not admin_ok and (not x_user_id or (user_id and user_id != x_user_id)):
        raise HTTPException(status_code=403, detail={"code": "ADMIN_AUTH_REQUIRED", "message": "需要审计管理员凭证"})
    if not admin_ok:
        # Bind the self-service view to a known account and the presented
        # principal.  Merely choosing ``?user_id=...`` is never sufficient.
        try:
            validate_actor(x_user_id or "", x_user_id)
            ensure_account(x_user_id or "")
        except SecurityError as exc:
            raise_security(exc)
        user_id = x_user_id
    return {"code": 200, "data": get_audit_logs(limit=limit, user_id=user_id)}


@router.get("/api/v1/security/audit/verify")
def security_audit_verify(x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token")):
    expected = os.getenv("BANK_ADMIN_AUDIT_TOKEN")
    if not expected or not x_admin_token or not hmac.compare_digest(expected, x_admin_token):
        raise HTTPException(status_code=403, detail={"code": "ADMIN_AUTH_REQUIRED", "message": "需要审计管理员凭证"})
    return {"code": 200, **verify_audit_chain()}


@router.get("/api/v1/security/audit/trace/{request_id}")
def security_audit_trace(request_id: str, x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"), x_user_id: Optional[str] = Header(None, alias="X-User-Id")):
    """Return the full decision chain (precheck → confirm → MFA → settle) of one request.

    A user may trace only their own requests; an audit administrator holding
    ``BANK_ADMIN_AUDIT_TOKEN`` may trace any request.  For a normal user an
    unknown request id is indistinguishable from someone else's request (no
    enumeration oracle), while the administrator sees an empty chain.
    """

    expected = os.getenv("BANK_ADMIN_AUDIT_TOKEN")
    admin_ok = bool(expected and x_admin_token and hmac.compare_digest(expected, x_admin_token))
    trace = get_audit_trace(request_id)
    principal = trace.get("principal")
    if not admin_ok:
        if not x_user_id or not principal or x_user_id != principal:
            raise HTTPException(status_code=403, detail={"code": "ADMIN_AUTH_REQUIRED", "message": "只能查看本人请求的审计链路"})
    return {"code": 200, **trace}
