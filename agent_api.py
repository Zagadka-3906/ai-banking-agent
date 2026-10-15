"""Channel gateway: the HTTP surface a native client drives the agent through.

This is the service the Android emulator app talks to.  It is
deliberately *thin*: it authenticates the caller, derives the principal, and
forwards the turn to :mod:`conversation`; all policy, risk classification,
authorization and ledger work stays in the core banking API.

Two properties matter and are enforced here rather than trusted to clients:

* the principal comes from ``X-Session-Token`` (resolved by the core's
  session registry) and never from a request body, so a client cannot act as
  somebody else by editing JSON;
* approvals are addressed by an opaque ``approval_id`` bound to that
  principal.  The signed nonce and the MFA proof never leave the server.

Run it next to the core::

    uvicorn main:app      --port 8000     # core banking authority
    uvicorn agent_api:app --port 8100     # this channel gateway
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Iterator, Optional

from fastapi import FastAPI, Header, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

# Name this process before ``security`` is imported: it selects the Gateway's
# own audit file, separate from Core's chain (see ``audit_log_path``).
os.environ["BANK_AUDIT_SERVICE"] = "gateway"

from http_controls import cache_bounded_body

from bank_tools import BankAPIError, BankClient
from conversation import SERVICE, ConversationError
from security import SecurityError, audit_event, sanitize_text
from feishu_im import router as feishu_im_router, start_bot as start_feishu_bot


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    start_feishu_bot()
    yield

app = FastAPI(
    title="Banking Agent Channel Gateway",
    description="渠道网关：自然语言会话与人工审批（不持有账本、不做风控判定）",
    version="1.0.0",
    lifespan=_lifespan,
)

app.include_router(feishu_im_router)

try:
    _MAX_REQUEST_BYTES = max(4_096, min(int(os.getenv("BANK_MAX_REQUEST_BYTES", "1048576")), 10_000_000))
except (TypeError, ValueError):
    _MAX_REQUEST_BYTES = 1_048_576

# The gateway executes an LLM turn per message, so it is worth a cheap
# per-principal window on top of the core's own limit.
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_STATE: dict[str, dict[str, Any]] = {}


def _rate_limit_remaining(key: str) -> Optional[int]:
    try:
        limit = int(os.getenv("BANK_AGENT_RATE_LIMIT_PER_MINUTE", "60"))
    except (TypeError, ValueError):
        limit = 60
    if limit <= 0:
        return None
    now = time.monotonic()
    record = RATE_LIMIT_STATE.get(key)
    if not record or now - float(record.get("start", 0.0)) >= RATE_LIMIT_WINDOW_SECONDS:
        RATE_LIMIT_STATE[key] = {"start": now, "count": 1}
        return None
    record["count"] = int(record.get("count", 0)) + 1
    if record["count"] > limit:
        return max(1, int(RATE_LIMIT_WINDOW_SECONDS - (now - float(record.get("start", now)))))
    return None


def _security_headers(response: JSONResponse) -> JSONResponse:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class MessageRequest(StrictModel):
    text: str = Field(..., description="用户自然语言指令")
    conversation_id: Optional[str] = Field(None, description="会话编号；省略则由服务端创建")
    message_id: Optional[str] = Field(None, description="客户端消息编号，用于重投去重")


class ApprovalRequest(StrictModel):
    decision: str = Field(..., description="approve 或 reject")
    otp: Optional[str] = None
    face_attestation: Optional[str] = None


@app.middleware("http")
async def gateway_middleware(request: Request, call_next):
    # Assign correlation before any rejection, including malformed metadata.
    # A caller-supplied id is never an authority for an audit chain.
    request.state.request_id = f"gw_{uuid.uuid4().hex}"
    path = request.url.path
    if path != "/healthz":
        try:
            await cache_bounded_body(request, _MAX_REQUEST_BYTES)
        except SecurityError as exc:
            return _rejection_response(request, exc)
    response = await call_next(request)
    response.headers["X-Request-Id"] = request.state.request_id
    return _security_headers(response)


@app.exception_handler(SecurityError)
async def security_error_handler(request: Request, exc: SecurityError) -> JSONResponse:
    return _rejection_response(request, exc)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = _audit_rejection(request, code="INVALID_REQUEST", status_code=422)
    # Preserve FastAPI's field-error contract in the response, but never copy
    # errors()/body into the audit record: both may contain secret input values.
    return _security_headers(JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors()), "request_id": request_id},
        headers={"X-Request-Id": request_id},
    ))


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, _: Exception) -> JSONResponse:
    return _rejection_response(request, SecurityError("服务暂时不可用，请稍后重试", 500, "INTERNAL_ERROR"))


def _audit_rejection(request: Request, *, code: str, status_code: int) -> str:
    request_id = request.state.request_id
    principal = getattr(request.state, "principal", None)
    route = request.scope.get("route")
    audit_event(
        "AGENT_INPUT_REJECTED" if code in {"PROMPT_INJECTION_BLOCKED", "EMPTY_MESSAGE", "INVALID_REQUEST"} else "AGENT_REQUEST_REJECTED",
        user_id=principal,
        decision="BLOCK",
        outcome="FAILURE",
        reason=code,
        request_id=request_id,
        # Never store input text, arbitrary headers, credentials or the error
        # message, which may include values supplied by an upstream service.
        metadata={"method": request.method, "route": getattr(route, "path", None),
                  "status_code": status_code,
                  "identity_source": getattr(request.state, "identity_source", "unverified")},
    )
    return request_id


def _rejection_response(request: Request, exc: SecurityError) -> JSONResponse:
    request_id = _audit_rejection(request, code=exc.code, status_code=exc.status_code)
    if exc.code == "PROMPT_INJECTION_BLOCKED" and getattr(request.state, "identity_source", None) == "session":
        # Rejected before the graph (for example while an approval is pending):
        # charge it to the same Core breaker as an in-graph block.
        from agent import INJECTION_LOCKED_NOTICE, report_security_signal

        if report_security_signal(exc.code, request_id=request_id, session_token=request.headers.get("X-Session-Token")):
            exc = SecurityError(exc.detail + INJECTION_LOCKED_NOTICE, exc.status_code, exc.code)
    headers = {"X-Request-Id": request_id}
    # Surface the retry hint instead of making the client guess.
    retry_after = getattr(exc, "retry_after", None)
    if retry_after:
        headers["Retry-After"] = str(retry_after)
    elif exc.status_code == 423:
        headers["Retry-After"] = "30"
    return _security_headers(
        JSONResponse(
            status_code=exc.status_code,
            content={"detail": {"code": exc.code, "message": exc.detail, "request_id": request_id}},
            headers=headers or None,
        )
    )


def _core_session_subject(session_token: str, request_id: Optional[str]) -> dict[str, Any]:
    """Ask the core which principal a session credential belongs to.

    Kept as a module-level seam so tests can substitute an in-process core
    without a listening socket.
    """

    return BankClient().session_subject(session_token, request_id=request_id)


def principal_for(
    *,
    x_session_token: Optional[str],
    x_user_id: Optional[str],
    action: str,
    request_id: Optional[str] = None,
) -> str:
    """Resolve the authenticated principal, or refuse.

    A session credential is always verified *by the core*, which owns the
    session registry.  Keeping a second copy of that registry in this process
    would be wrong twice over: it would drift the moment a session expired or was
    revoked, and it silently breaks the moment the gateway runs as its own
    process (or as several replicas), because a token minted by the core is not
    in the gateway's memory at all.

    The core's subject also wins over any self-asserted ``X-User-Id``, so a
    caller can never claim another principal while holding a valid session.
    """

    if x_session_token:
        try:
            payload = _core_session_subject(x_session_token, request_id)
        except BankAPIError as exc:
            audit_event(
                action,
                user_id=None,
                decision="BLOCK",
                outcome="FAILURE",
                reason="SESSION_UNVERIFIED",
                request_id=request_id,
            )
            # Distinguish "your credential is no longer good" from "the core is
            # down": reporting an outage as an expired login would send users
            # through a pointless re-login.
            if exc.status_code in (401, 403):
                raise ConversationError("登录状态已失效，请重新登录", 401, "INVALID_SESSION") from exc
            raise ConversationError("核心银行服务暂时不可用，请稍后重试", 503, "CORE_UNAVAILABLE") from exc
        principal = str(payload.get("user_id") or "").strip()
        if not principal:
            raise ConversationError("会话凭证缺少用户主体", 401, "INVALID_SESSION")
        return principal

    raise ConversationError("请先完成登录认证", 401, "AUTHENTICATION_REQUIRED")


def _guard_rate_limit(principal: str) -> None:
    retry_after = _rate_limit_remaining(principal)
    if retry_after is not None:
        response = ConversationError("请求过于频繁，请稍后重试", 429, "RATE_LIMITED")
        response.retry_after = retry_after  # type: ignore[attr-defined]
        raise response


def _turn_request_id(client_value: Optional[str], *, request_id: str) -> tuple[str, Optional[str]]:
    """Create the server correlation id; retain client id only as metadata."""
    client_id = sanitize_text(client_value, field="客户端请求编号", max_length=128) if client_value else None
    return request_id, (client_id or None)


def _request_principal(request: Request, *, session_token: Optional[str], user_id: Optional[str], action: str) -> str:
    principal = principal_for(x_session_token=session_token, x_user_id=user_id, action=action,
                              request_id=request.state.request_id)
    request.state.principal = principal
    request.state.identity_source = "session" if session_token else "demo_header"
    return principal


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    """Liveness probe: reports the checkpointer backend, never client data.

    The conversation service's own status is nested rather than merged at the
    top level, so the probe's liveness answer and the service's health answer
    cannot overwrite each other under a shared ``status`` key.
    """

    return {
        "status": "ok",
        "service": "banking-agent-gateway",
        "version": app.version,
        "conversation": SERVICE.health(),
    }


@app.post("/api/v1/agent/messages")
def post_message(
    req: MessageRequest,
    request: Request,
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
):
    """Run one conversational turn.

    Responds with ``status = COMPLETED`` or ``APPROVAL_REQUIRED``.  In the
    latter case the client must resolve ``approval.approval_id`` before sending
    another message: a new message would otherwise abandon a pending approval.
    """

    principal = _request_principal(request, session_token=x_session_token, user_id=x_user_id, action="AGENT_MESSAGE")
    request_id, client_request_id = _turn_request_id(x_request_id, request_id=request.state.request_id)
    _guard_rate_limit(principal)
    text = sanitize_text(req.text, field="用户指令", max_length=2000)
    if not text:
        raise ConversationError("消息内容不能为空", 400, "EMPTY_MESSAGE")
    conversation_id = req.conversation_id or f"c{uuid.uuid4().hex[:20]}"
    return SERVICE.start_turn(
        principal=principal,
        conversation_id=conversation_id,
        message_id=req.message_id or f"m{uuid.uuid4().hex[:20]}",
        text=text,
        session_token=x_session_token,
        request_id=request_id,
        client_request_id=client_request_id,
    )


def _sse(event: str, data: Any) -> str:
    """Encode one Server-Sent Event frame.

    ``ensure_ascii=False`` keeps the Chinese replies readable on the wire (and
    roughly halves their size).  The framing itself stays ASCII, as the format
    requires; a JSON payload never contains a bare newline to break it.
    """

    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/v1/agent/messages/stream")
def post_message_stream(
    req: MessageRequest,
    request: Request,
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
) -> StreamingResponse:
    """The same turn as ``/messages``, reported while it runs.

    Emits ``stage`` frames as the graph advances, then exactly one terminal
    ``result`` / ``approval`` / ``error`` frame.  Authentication and rate
    limiting happen *before* the stream opens, so those failures arrive as
    ordinary HTTP responses instead of in-band events a client might ignore.
    """

    principal = _request_principal(request, session_token=x_session_token, user_id=x_user_id, action="AGENT_MESSAGE")
    request_id, client_request_id = _turn_request_id(x_request_id, request_id=request.state.request_id)
    _guard_rate_limit(principal)
    text = sanitize_text(req.text, field="用户指令", max_length=2000)
    if not text:
        raise ConversationError("消息内容不能为空", 400, "EMPTY_MESSAGE")
    conversation_id = req.conversation_id or f"c{uuid.uuid4().hex[:20]}"
    message_id = req.message_id or f"m{uuid.uuid4().hex[:20]}"

    def frames() -> Iterator[str]:
        for item in SERVICE.start_turn_stream(
            principal=principal,
            conversation_id=conversation_id,
            message_id=message_id,
            text=text,
            session_token=x_session_token,
            request_id=request_id,
            client_request_id=client_request_id,
        ):
            yield _sse(str(item.get("event") or "message"), item.get("data"))

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={
            # A proxy that buffers would defeat the whole point of streaming.
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/v1/agent/conversations/{conversation_id}/operations/{operation_id}")
def get_operation_result(conversation_id: str, operation_id: str, request: Request,
                         x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
                         x_user_id: Optional[str] = Header(None, alias="X-User-Id")):
    principal = _request_principal(request, session_token=x_session_token, user_id=x_user_id, action="AGENT_RECONCILE")
    _guard_rate_limit(principal)
    return SERVICE.reconcile_operation(principal=principal, conversation_id=conversation_id,
        operation_id=sanitize_text(operation_id, field="操作编号", max_length=128), session_token=x_session_token)


@app.post("/api/v1/agent/approvals/{approval_id}/stream")
def post_approval_stream(approval_id: str, req: ApprovalRequest, request: Request,
                         x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
                         x_user_id: Optional[str] = Header(None, alias="X-User-Id")):
    principal = _request_principal(request, session_token=x_session_token, user_id=x_user_id, action="AGENT_APPROVAL")
    _guard_rate_limit(principal)
    events = SERVICE.resolve_approval_stream(principal=principal, approval_id=approval_id, decision=req.decision,
        otp=req.otp, face_attestation=req.face_attestation, session_token=x_session_token)
    return StreamingResponse((_sse(item["event"], item["data"]) for item in events), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.get("/api/v1/agent/traces/{request_id}")
def get_agent_trace(
    request_id: str,
    request: Request,
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
):
    principal = _request_principal(request, session_token=x_session_token, user_id=x_user_id, action="AGENT_TRACE")
    return {"code": 200, **SERVICE.aggregate_trace(principal=principal, request_id=sanitize_text(request_id, field="请求编号", max_length=128), session_token=x_session_token)}


@app.post("/api/v1/agent/approvals/{approval_id}/demo-otp")
def post_demo_otp(approval_id: str, request: Request,
                  x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
                  x_user_id: Optional[str] = Header(None, alias="X-User-Id")):
    principal = _request_principal(request, session_token=x_session_token, user_id=x_user_id, action="AGENT_DEMO_OTP")
    _guard_rate_limit(principal)
    return JSONResponse({"code": 200, **SERVICE.demo_otp(principal=principal, approval_id=approval_id, session_token=x_session_token)},
                        headers={"Cache-Control": "no-store"})


@app.post("/api/v1/agent/approvals/{approval_id}/face-challenge")
def post_face_challenge(
    approval_id: str,
    request: Request,
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
):
    """Issue a liveness challenge for the device to sign with its Keystore key.

    The device receives ``challenge_id`` and ``auth_token_jti`` (it needs the
    nonce identifier to bind its signature) but never the authorization nonce.
    """

    principal = _request_principal(request, session_token=x_session_token, user_id=x_user_id, action="AGENT_FACE_CHALLENGE")
    _guard_rate_limit(principal)
    return {"code": 200, **SERVICE.face_challenge(principal=principal, approval_id=approval_id, session_token=x_session_token)}


@app.post("/api/v1/agent/approvals/{approval_id}")
def post_approval(
    approval_id: str,
    req: ApprovalRequest,
    request: Request,
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
):
    """Apply the user's decision and, when approved, settle the operation."""

    principal = _request_principal(request, session_token=x_session_token, user_id=x_user_id, action="AGENT_APPROVAL")
    _guard_rate_limit(principal)
    return SERVICE.resolve_approval(
        principal=principal,
        approval_id=approval_id,
        decision=req.decision,
        otp=req.otp,
        face_attestation=req.face_attestation,
        session_token=x_session_token,
    )
