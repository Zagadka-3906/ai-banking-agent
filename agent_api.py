"""Channel gateway: the HTTP surface a native client drives the agent through.

This is the service the Android app (and later the IM adapters) talk to.  It is
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
import threading
import time
import uuid
from typing import Any, Iterator, Optional

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from bank_tools import BankAPIError, BankClient
from conversation import SERVICE, ConversationError
from security import SecurityError, audit_event, sanitize_text
from im_webhook import router as im_router
from artifacts import extract_mock, new_artifact_id, validate_upload

app = FastAPI(
    title="Banking Agent Channel Gateway",
    description="渠道网关：自然语言会话与人工审批（不持有账本、不做风控判定）",
    version="1.0.0",
)

_cors_origins = [
    origin.strip()
    for origin in os.getenv("BANK_CORS_ORIGINS", "http://localhost:8501,http://127.0.0.1:8501").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-User-Id", "X-Session-Token", "X-Request-Id", "Idempotency-Key", "X-IM-Provider", "X-IM-Timestamp", "X-IM-Event-Id", "X-IM-Signature"],
)
app.include_router(im_router)

try:
    _MAX_REQUEST_BYTES = max(4_096, min(int(os.getenv("BANK_MAX_REQUEST_BYTES", "1048576")), 10_000_000))
except (TypeError, ValueError):
    _MAX_REQUEST_BYTES = 1_048_576

# The gateway executes an LLM turn per message, so it is worth a cheap
# per-principal window on top of the core's own limit.
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_STATE: dict[str, dict[str, Any]] = {}
# Only sanitized metadata is retained.  The gateway is intentionally not a
# media store; raw bytes live only for the duration of one request.
ARTIFACTS: dict[str, dict[str, Any]] = {}
ARTIFACTS_BY_OWNER_HASH: dict[tuple[str, str], str] = {}
ARTIFACT_LOCK = threading.RLock()


def _demo_identity_allowed() -> bool:
    """Whether a bare ``X-User-Id`` may stand in for a session.

    Only inside ``BANK_DEMO_MODE``.  The real login endpoint arrives with the
    authentication milestone; until then a local demo must stay runnable
    without a credential, and that exception must be impossible to enable by
    accident in a deployment.
    """

    return os.getenv("BANK_DEMO_MODE", "1").lower() in {"1", "true", "yes", "on"}


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
        return max(1, int(RATE_LIMIT_WINDOW_SECONDS - (now - float(record["start"], now))))
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


class ArtifactRequest(StrictModel):
    media_type: str
    content_base64: str
    sha256: str
    filename: str
    conversation_id: str


@app.middleware("http")
async def gateway_middleware(request: Request, call_next):
    path = request.url.path
    if path != "/healthz":
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > _MAX_REQUEST_BYTES:
                    return _security_headers(
                        JSONResponse(status_code=413, content={"detail": {"code": "REQUEST_TOO_LARGE", "message": "请求体超过安全限制"}})
                    )
            except ValueError:
                return _security_headers(
                    JSONResponse(status_code=400, content={"detail": {"code": "INVALID_CONTENT_LENGTH", "message": "请求长度字段无效"}})
                )
    response = await call_next(request)
    return _security_headers(response)


@app.exception_handler(SecurityError)
async def security_error_handler(_: Request, exc: SecurityError) -> JSONResponse:
    headers = {}
    # Surface the retry hint instead of making the client guess.
    retry_after = getattr(exc, "retry_after", None)
    if retry_after:
        headers["Retry-After"] = str(retry_after)
    elif exc.status_code == 423:
        headers["Retry-After"] = "30"
    return _security_headers(
        JSONResponse(
            status_code=exc.status_code,
            content={"detail": {"code": exc.code, "message": exc.detail}},
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
                user_id=x_user_id,
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

    if _demo_identity_allowed() and x_user_id:
        # Demo-only short cut: no credential exists yet under BANK_DEMO_MODE.
        return sanitize_text(x_user_id, field="用户标识", max_length=128)
    raise ConversationError("请先完成登录认证", 401, "AUTHENTICATION_REQUIRED")


def _guard_rate_limit(principal: str) -> None:
    retry_after = _rate_limit_remaining(principal)
    if retry_after is not None:
        response = ConversationError("请求过于频繁，请稍后重试", 429, "RATE_LIMITED")
        response.retry_after = retry_after  # type: ignore[attr-defined]
        raise response


def _turn_request_id(client_value: Optional[str]) -> tuple[str, Optional[str]]:
    """Create the server correlation id; retain client id only as metadata."""
    client_id = sanitize_text(client_value, field="客户端请求编号", max_length=128) if client_value else None
    return f"gw_{uuid.uuid4().hex}", (client_id or None)


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
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
):
    """Run one conversational turn.

    Responds with ``status = COMPLETED`` or ``APPROVAL_REQUIRED``.  In the
    latter case the client must resolve ``approval.approval_id`` before sending
    another message: a new message would otherwise abandon a pending approval.
    """

    request_id, client_request_id = _turn_request_id(x_request_id)
    principal = principal_for(x_session_token=x_session_token, x_user_id=x_user_id, action="AGENT_MESSAGE", request_id=request_id)
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

    request_id, client_request_id = _turn_request_id(x_request_id)
    principal = principal_for(x_session_token=x_session_token, x_user_id=x_user_id, action="AGENT_MESSAGE", request_id=request_id)
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


@app.get("/api/v1/agent/traces/{request_id}")
def get_agent_trace(
    request_id: str,
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
):
    principal = principal_for(x_session_token=x_session_token, x_user_id=x_user_id, action="AGENT_TRACE")
    return {"code": 200, **SERVICE.aggregate_trace(principal=principal, request_id=sanitize_text(request_id, field="请求编号", max_length=128), session_token=x_session_token)}


@app.post("/api/v1/agent/artifacts")
def post_artifact(
    req: ArtifactRequest,
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
):
    """Validate a small media upload and return a deterministic mock draft."""
    request_id, _client_request_id = _turn_request_id(x_request_id)
    principal = principal_for(x_session_token=x_session_token, x_user_id=x_user_id, action="AGENT_ARTIFACT", request_id=request_id)
    _guard_rate_limit(principal)
    try:
        data, digest = validate_upload(req.media_type, req.content_base64, req.sha256)
    except ValueError as exc:
        audit_event("ARTIFACT_UPLOAD", user_id=principal, decision="BLOCK", outcome="FAILURE", reason=str(exc), request_id=request_id, metadata={"media_type": req.media_type})
        raise ConversationError("票据文件校验失败", 400, str(exc)) from exc
    key = (principal, digest)
    filename = sanitize_text(req.filename, field="文件名", max_length=128)
    conversation_id = sanitize_text(req.conversation_id, field="会话编号", max_length=128)
    if not filename or not conversation_id:
        raise ConversationError("文件名和会话编号不能为空", 400, "ARTIFACT_METADATA_REQUIRED")
    with ARTIFACT_LOCK:
        artifact_id = ARTIFACTS_BY_OWNER_HASH.get(key)
        if artifact_id is None:
            artifact_id = new_artifact_id()
            result = extract_mock(digest)
            ARTIFACTS_BY_OWNER_HASH[key] = artifact_id
            ARTIFACTS[artifact_id] = {
                "artifact_id": artifact_id, "owner_id": principal, "sha256": digest,
                "media_type": req.media_type, "filename": filename,
                "size_bytes": len(data), "conversation_id": conversation_id,
                "result": result,
            }
        record = dict(ARTIFACTS[artifact_id])
    result = dict(record["result"])
    audit_event("ARTIFACT_UPLOAD", user_id=principal, decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"artifact_id": artifact_id, "sha256_prefix": digest[:12], "media_type": req.media_type, "size_bytes": len(data), "provenance": result.get("provenance")})
    return {"code": 200, "request_id": request_id, "artifact_id": artifact_id, "sha256": record["sha256"], "media_type": record["media_type"],
            "filename": record["filename"], "size_bytes": record["size_bytes"], **result}


@app.get("/api/v1/agent/artifacts/{artifact_id}")
def get_artifact(
    artifact_id: str,
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
):
    principal = principal_for(x_session_token=x_session_token, x_user_id=x_user_id, action="AGENT_ARTIFACT_READ")
    safe_id = sanitize_text(artifact_id, field="票据编号", max_length=128)
    with ARTIFACT_LOCK:
        stored = ARTIFACTS.get(safe_id)
        record = dict(stored) if stored is not None else None
    if record is None or record["owner_id"] != principal:
        raise ConversationError("票据不存在", 404, "ARTIFACT_NOT_FOUND")
    return {"code": 200, "artifact_id": safe_id, "sha256": record["sha256"], "media_type": record["media_type"],
            "filename": record["filename"], "size_bytes": record["size_bytes"], **record["result"]}


@app.post("/api/v1/agent/approvals/{approval_id}/face-challenge")
def post_face_challenge(
    approval_id: str,
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
):
    """Issue a liveness challenge for the device to sign with its Keystore key.

    The device receives ``challenge_id`` and ``auth_token_jti`` (it needs the
    nonce identifier to bind its signature) but never the authorization nonce.
    """

    principal = principal_for(x_session_token=x_session_token, x_user_id=x_user_id, action="AGENT_FACE_CHALLENGE", request_id=x_request_id)
    _guard_rate_limit(principal)
    return {"code": 200, **SERVICE.face_challenge(principal=principal, approval_id=approval_id, session_token=x_session_token)}


@app.post("/api/v1/agent/approvals/{approval_id}")
def post_approval(
    approval_id: str,
    req: ApprovalRequest,
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
):
    """Apply the user's decision and, when approved, settle the operation."""

    principal = principal_for(x_session_token=x_session_token, x_user_id=x_user_id, action="AGENT_APPROVAL", request_id=x_request_id)
    _guard_rate_limit(principal)
    return SERVICE.resolve_approval(
        principal=principal,
        approval_id=approval_id,
        decision=req.decision,
        otp=req.otp,
        face_attestation=req.face_attestation,
        session_token=x_session_token,
    )
