"""Provider-neutral, demo-only signed IM webhook adapter.

Bindings deliberately live in memory: an IM identity is only a channel alias
for an already authenticated Gateway session, never an authentication source.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from conversation import SERVICE, ConversationError
from security import audit_event, sanitize_text

router = APIRouter(prefix="/api/v1/im", tags=["im"])
_LOCK = threading.RLock()
_BINDINGS: dict[str, dict[str, Any]] = {}
_CODES: dict[str, dict[str, Any]] = {}
_REPLAY: dict[str, float] = {}
CODE_TTL = 300
TIMESTAMP_WINDOW = 300
MAX_ATTEMPTS = 5


class BindingRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    session_token: Optional[str] = None  # intentionally ignored; header is authoritative


class WebhookEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    event_type: str = Field(..., description="bind or message")
    external_subject: str
    binding_code: Optional[str] = None
    text: Optional[str] = None
    user_id: Optional[str] = None  # untrusted and ignored


def _config() -> dict[str, str]:
    raw = os.getenv("BANK_IM_PROVIDER_SECRETS", "")
    return {item.split("=", 1)[0].strip(): item.split("=", 1)[1] for item in raw.split(",") if "=" in item and item.split("=", 1)[0].strip() and item.split("=", 1)[1]}


def _provider(provider: str) -> str:
    provider = sanitize_text(provider, field="IM 服务商", max_length=64)
    if provider not in _config():
        raise HTTPException(403, detail={"code": "IM_PROVIDER_NOT_ALLOWED", "message": "IM 服务商未配置"})
    return provider


def _key(provider: str, external_subject: str) -> str:
    digest = hmac.new(_config()[provider].encode(), external_subject.encode(), hashlib.sha256).hexdigest()
    return f"{provider}:{digest}"


def _signature_ok(provider: str, timestamp: str, event_id: str, raw: bytes, signature: str) -> bool:
    expected = hmac.new(_config()[provider].encode(), f"{timestamp}.{event_id}.".encode() + raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def _verify_headers(provider: str, timestamp: str, event_id: str, raw: bytes, signature: str) -> None:
    try:
        if abs(time.time() - float(timestamp)) > TIMESTAMP_WINDOW:
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(401, detail={"code": "IM_STALE_REQUEST", "message": "请求时间戳无效或已过期"})
    if not _signature_ok(provider, timestamp, event_id, raw, signature):
        raise HTTPException(401, detail={"code": "IM_INVALID_SIGNATURE", "message": "签名校验失败"})
    with _LOCK:
        now = time.time()
        for key, expires in list(_REPLAY.items()):
            if expires <= now:
                _REPLAY.pop(key, None)
        replay_key = f"{provider}:{event_id}"
        if replay_key in _REPLAY:
            raise HTTPException(409, detail={"code": "IM_EVENT_REPLAY", "message": "事件已处理"})
        _REPLAY[replay_key] = now + TIMESTAMP_WINDOW


def create_binding(principal: str, session_token: str) -> dict[str, Any]:
    code = secrets.token_urlsafe(12)
    digest = hashlib.sha256(code.encode()).hexdigest()
    with _LOCK:
        _CODES[digest] = {"principal": principal, "session_token": session_token, "expires": time.time() + CODE_TTL, "attempts": 0}
    audit_event("IM_BINDING_ISSUED", user_id=principal, outcome="SUCCESS", metadata={"expires_in": CODE_TTL})
    return {"binding_code": code, "expires_in": CODE_TTL}


def reset_state() -> None:
    with _LOCK:
        _BINDINGS.clear()
        _CODES.clear()
        _REPLAY.clear()


@router.delete("/bindings/{provider}/{external_subject}")
async def unbind(
    provider: str,
    external_subject: str,
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
):
    from agent_api import principal_for
    principal = principal_for(x_session_token=x_session_token, x_user_id=x_user_id, action="IM_UNBIND")
    provider = _provider(provider)
    key = _key(provider, sanitize_text(external_subject, field="IM 外部主体", max_length=256))
    with _LOCK:
        binding = _BINDINGS.get(key)
        if not binding or binding.get("principal") != principal:
            raise HTTPException(404, detail={"code": "IM_BINDING_NOT_FOUND", "message": "绑定不存在"})
        _BINDINGS.pop(key, None)
    audit_event("IM_UNBOUND", user_id=principal, outcome="SUCCESS", metadata={"provider": provider})
    return {"code": 200, "status": "UNBOUND"}


@router.post("/bindings")
async def issue_binding(
    request: Request,
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
):
    from agent_api import principal_for
    principal = principal_for(x_session_token=x_session_token, x_user_id=x_user_id, action="IM_BINDING_ISSUE")
    if not x_session_token:
        raise HTTPException(401, detail={"code": "SESSION_REQUIRED", "message": "IM 绑定需要登录会话"})
    return {"code": 200, **create_binding(principal, x_session_token)}


@router.post("/webhook")
async def webhook(
    request: Request,
    x_im_provider: str = Header(..., alias="X-IM-Provider"),
    x_im_timestamp: str = Header(..., alias="X-IM-Timestamp"),
    x_im_event_id: str = Header(..., alias="X-IM-Event-Id"),
    x_im_signature: str = Header(..., alias="X-IM-Signature"),
):
    provider = _provider(x_im_provider)
    raw = await request.body()
    _verify_headers(provider, x_im_timestamp, x_im_event_id, raw, x_im_signature)
    try:
        event = WebhookEvent.model_validate_json(raw)
    except Exception as exc:
        raise HTTPException(400, detail={"code": "IM_INVALID_EVENT", "message": "事件格式无效"}) from exc
    subject = sanitize_text(event.external_subject, field="IM 外部主体", max_length=256)
    if event.event_type == "bind":
        if not event.binding_code:
            raise HTTPException(400, detail={"code": "IM_BINDING_CODE_REQUIRED", "message": "缺少绑定码"})
        digest = hashlib.sha256(event.binding_code.encode()).hexdigest()
        with _LOCK:
            record = _CODES.get(digest)
            if not record or record["expires"] <= time.time():
                raise HTTPException(403, detail={"code": "IM_BINDING_INVALID", "message": "绑定码无效或已过期"})
            if record["attempts"] >= MAX_ATTEMPTS:
                raise HTTPException(429, detail={"code": "IM_BINDING_ATTEMPTS", "message": "绑定尝试次数过多"})
            record["attempts"] += 1
            _CODES.pop(digest, None)
            _BINDINGS[_key(provider, subject)] = {"principal": record["principal"], "session_token": record["session_token"], "provider": provider, "subject": subject}
        return {"code": 200, "status": "BOUND", "provider": provider}
    if event.event_type != "message" or not event.text:
        raise HTTPException(400, detail={"code": "IM_INVALID_EVENT", "message": "仅支持 bind/message 事件"})
    with _LOCK:
        binding = _BINDINGS.get(_key(provider, subject))
    if not binding:
        raise HTTPException(403, detail={"code": "IM_NOT_BOUND", "message": "IM 尚未绑定"})
    from agent_api import principal_for
    principal = principal_for(x_session_token=binding["session_token"], x_user_id=None, action="IM_MESSAGE")
    request_id = f"gw_{uuid.uuid4().hex}"
    try:
        result = SERVICE.start_turn(principal=principal, conversation_id=f"im-{provider}-{hashlib.sha256(subject.encode()).hexdigest()[:16]}", message_id=f"im-{x_im_event_id}", text=sanitize_text(event.text, field="IM 消息", max_length=2000), session_token=binding["session_token"], request_id=request_id)
    except ConversationError as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.detail}) from exc
    if result.get("status") == "APPROVAL_REQUIRED":
        original_approval = result.get("approval")
        result = {k: result.get(k) for k in ("code", "conversation_id", "status", "reply", "request_id", "intent", "risk_level")}
        # Only opaque approval presentation crosses the IM boundary.
        result["approval"] = {k: original_approval.get(k) for k in ("approval_id", "summary", "risk_level", "mfa_required")} if isinstance(original_approval, dict) else None
        result["reply"] = "该操作需要在手机银行或 API 中完成确认，IM 不执行审批或 MFA。"
    return result
