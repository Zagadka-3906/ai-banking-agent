"""Feishu private-chat connector for the single-process local Gateway.

The official SDK authenticates WebSocket events and sends replies. Bank sessions
are only ever held in Gateway memory; Feishu messages can select fixed Core GETs.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import secrets
import threading
import time
from typing import Any

from fastapi import APIRouter, Header, Request

from agent import _local_parse
from bank_tools import BankAPIError, BankClient
from feishu_cards import FeishuReply, handoff_card, query_card
from planner import Intent
from security import SecurityError, audit_event, sanitize_text

router = APIRouter(prefix="/api/v1/im/feishu", tags=["feishu-im"])
_LOCK = threading.RLock()
_CODES: dict[str, dict[str, Any]] = {}
_BINDINGS: dict[str, dict[str, Any]] = {}
_EVENTS: dict[str, float] = {}
_CODE_TTL = 300
_EVENT_TTL = 600
_MAX_CODES = 1000
_MAX_BINDINGS = 10000
_MAX_EVENTS = 10000
_BOT_STATE = "disabled"


def _enabled() -> bool:
    return os.getenv("BANK_FEISHU_ENABLED", "0") == "1"


def reset_state() -> None:
    """Clear ephemeral state for isolated tests."""
    global _BOT_STATE
    with _LOCK:
        _CODES.clear()
        _BINDINGS.clear()
        _EVENTS.clear()
        _BOT_STATE = "disabled"


def _read_only_reply(principal: str, session_token: str, message: str) -> tuple[str, str | FeishuReply]:
    """Only fixed Core GET routes are reachable from the IM channel."""
    parsed = _local_parse(message)
    intent = str(parsed.get("intent") or "")
    scope = str(parsed.get("date_scope") or "ALL")
    if scope not in {"TODAY", "MONTH", "LAST_MONTH", "YEAR", "ALL"}:
        scope = "ALL"
    detected = bool((parsed.get("slots") or {}).get("detect_subscriptions"))
    paths = {
        Intent.BALANCE_QUERY.value: f"accounts/{principal}/balance",
        Intent.BILL_QUERY.value: f"accounts/{principal}/transactions?date_scope={scope}",
        Intent.BILL_ANALYSIS.value: f"accounts/{principal}/bill-analysis?date_scope={scope}",
        Intent.SUBSCRIPTION_QUERY.value: f"subscriptions/{principal}{'/detect' if detected else ''}",
        Intent.CARD_QUERY.value: f"cards/{principal}",
        Intent.WEALTH_QUERY.value: "wealth/products",
    }
    path = paths.get(intent)
    if not path:
        return "APP_HANDOFF", handoff_card()
    try:
        response = BankClient(user_id=principal, session_token=session_token)._request("GET", path, read_only=True)
        result = response.json()
    except (BankAPIError, ValueError) as exc:
        raise SecurityError("核心银行查询失败，请稍后重试", 503, "FEISHU_CORE_READ_FAILED") from exc
    if not isinstance(result, dict) or result.get("code") != 200:
        raise SecurityError("核心银行结果格式无效", 502, "FEISHU_CORE_PROTOCOL_ERROR")
    return "COMPLETED", query_card(intent, scope, result)


@router.post("/binding-code")
def issue_binding_code(request: Request, x_session_token: str | None = Header(None, alias="X-Session-Token")):
    from agent_api import _guard_rate_limit, principal_for

    if not _enabled():
        raise SecurityError("飞书渠道未启用", 503, "FEISHU_DISABLED")
    principal = principal_for(x_session_token=x_session_token, x_user_id=None, action="FEISHU_BINDING_ISSUE", request_id=request.state.request_id)
    if not x_session_token:
        raise SecurityError("需要登录会话", 401, "SESSION_REQUIRED")
    _guard_rate_limit(principal)
    code = secrets.token_urlsafe(18)
    with _LOCK:
        now = time.time()
        for digest, record in list(_CODES.items()):
            if record["expires"] <= now or record["principal"] == principal:
                _CODES.pop(digest, None)
        if len(_CODES) >= _MAX_CODES:
            raise SecurityError("绑定请求过多", 429, "FEISHU_BINDING_BUSY")
        _CODES[hashlib.sha256(code.encode()).hexdigest()] = {
            "principal": principal, "session_token": x_session_token, "expires": now + _CODE_TTL,
        }
    audit_event("FEISHU_BINDING_ISSUED", user_id=principal, outcome="SUCCESS", request_id=request.state.request_id)
    return {"code": 200, "binding_code": code, "expires_in": _CODE_TTL}


@router.delete("/binding")
def remove_binding(request: Request, x_session_token: str | None = Header(None, alias="X-Session-Token")):
    from agent_api import principal_for

    principal = principal_for(x_session_token=x_session_token, x_user_id=None, action="FEISHU_UNBIND", request_id=request.state.request_id)
    with _LOCK:
        keys = [key for key, record in _BINDINGS.items() if record["principal"] == principal]
        for key in keys:
            del _BINDINGS[key]
    audit_event("FEISHU_UNBOUND", user_id=principal, outcome="SUCCESS", request_id=request.state.request_id)
    return {"code": 200, "status": "UNBOUND", "removed": len(keys)}


def _remember_event(event_id: str, created_ms: int) -> bool:
    now = time.time()
    if abs(now - created_ms / 1000) > _EVENT_TTL:
        return False
    with _LOCK:
        for key, expiry in list(_EVENTS.items()):
            if expiry <= now:
                _EVENTS.pop(key, None)
        if event_id in _EVENTS or len(_EVENTS) >= _MAX_EVENTS:
            return False
        _EVENTS[event_id] = now + _EVENT_TTL
    return True


def process_message(event: Any) -> tuple[str, str | FeishuReply] | None:
    """Process a verified SDK event; return (chat_id, reply) for one private chat."""
    if not _enabled():
        return None
    header = getattr(event, "header", None)
    payload = getattr(event, "event", None)
    message = getattr(payload, "message", None)
    sender = getattr(payload, "sender", None)
    sender_id = getattr(getattr(sender, "sender_id", None), "open_id", None)
    chat_id = getattr(message, "chat_id", None)
    event_id = getattr(header, "event_id", None)
    message_id = getattr(message, "message_id", None)
    if (getattr(header, "app_id", None) != os.getenv("FEISHU_APP_ID") or
            getattr(message, "chat_type", None) != "p2p" or
            getattr(message, "message_type", None) != "text" or
            getattr(sender, "sender_type", None) != "user" or
            not sender_id or not chat_id or not event_id or not message_id):
        return None
    try:
        created_ms = int(getattr(message, "create_time", 0))
        content = json.loads(getattr(message, "content", ""))
        raw_text = content.get("text") if isinstance(content, dict) else None
    except (TypeError, ValueError):
        return None
    if not isinstance(raw_text, str) or not _remember_event(str(event_id), created_ms):
        return None
    request_id = str(event_id)
    try:
        text = sanitize_text(raw_text, field="飞书消息", max_length=2000).strip()
    except SecurityError:
        return chat_id, "消息包含不安全内容或超过长度限制，请换一种说法。"
    if text.startswith("/绑定 "):
        code = text.removeprefix("/绑定 ").strip()
        digest = hashlib.sha256(code.encode()).hexdigest()
        with _LOCK:
            record = _CODES.pop(digest, None)
        if not record or record["expires"] <= time.time():
            return chat_id, "绑定码无效或已过期，请在 Android 登录后重新获取。"
        from agent_api import principal_for

        try:
            current = principal_for(x_session_token=record["session_token"], x_user_id=None,
                                    action="FEISHU_BIND", request_id=request_id)
        except SecurityError:
            return chat_id, "银行登录会话已失效，请在 Android 重新登录并获取绑定码。"
        if current != record["principal"]:
            return chat_id, "绑定码对应的账户已变化，请重新登录并获取绑定码。"
        with _LOCK:
            if sender_id not in _BINDINGS and len(_BINDINGS) >= _MAX_BINDINGS:
                return chat_id, "绑定人数已达上限，请稍后重试。"
            _BINDINGS[sender_id] = record
        audit_event("FEISHU_BOUND", user_id=record["principal"], outcome="SUCCESS", request_id=request_id)
        return chat_id, "绑定成功。当前仅支持私聊查询；会话过期后需重新绑定。"
    if text == "/解绑":
        with _LOCK:
            record = _BINDINGS.pop(sender_id, None)
        if record:
            audit_event("FEISHU_UNBOUND", user_id=record["principal"], outcome="SUCCESS", request_id=request_id)
        return chat_id, "已解除绑定。"
    with _LOCK:
        binding = dict(_BINDINGS.get(sender_id) or {})
    if not binding:
        return chat_id, "请先在 Android 登录，获取飞书绑定码，再私聊发送 /绑定 绑定码。"
    from agent_api import _guard_rate_limit, principal_for

    try:
        principal = principal_for(x_session_token=binding["session_token"], x_user_id=None,
                                  action="FEISHU_MESSAGE", request_id=request_id)
        if principal != binding["principal"]:
            raise SecurityError("绑定会话主体已变化", 403, "FEISHU_PRINCIPAL_CHANGED")
        _guard_rate_limit(principal)
        status, reply = _read_only_reply(principal, binding["session_token"], text)
    except SecurityError as exc:
        if exc.status_code in {401, 403}:
            with _LOCK:
                _BINDINGS.pop(sender_id, None)
            return chat_id, "银行登录会话已失效，请在 Android 重新登录并绑定。"
        audit_event("FEISHU_READ_QUERY", user_id=binding["principal"], outcome="FAILED", request_id=request_id)
        return chat_id, "查询暂时不可用，请稍后重试。"
    except (KeyError, TypeError, ValueError):
        audit_event("FEISHU_READ_QUERY", user_id=binding["principal"], outcome="FAILED", request_id=request_id)
        return chat_id, "核心银行结果无效，请稍后重试。"
    audit_event("FEISHU_READ_QUERY" if status == "COMPLETED" else "FEISHU_WRITE_DEFERRED",
                user_id=principal, decision="ALLOW" if status == "COMPLETED" else "BLOCK",
                outcome=status, request_id=request_id)
    return chat_id, reply


def _send_reply(client: Any, chat_id: str, reply: str | FeishuReply, event_id: str) -> bool:
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

    payload = reply if isinstance(reply, FeishuReply) else FeishuReply.text(reply)
    request = (CreateMessageRequest.builder().receive_id_type("chat_id")
               .request_body(CreateMessageRequestBody.builder().receive_id(chat_id)
                             .msg_type(payload.msg_type).content(json.dumps(payload.content, ensure_ascii=False))
                             .uuid(event_id).build()).build())
    response = client.im.v1.message.create(request)
    return bool(response.success())


def start_bot() -> None:
    """Called on Gateway startup. One Gateway worker owns one WS connection."""
    global _BOT_STATE
    if not _enabled():
        return
    app_id = os.getenv("FEISHU_APP_ID", "").strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        raise RuntimeError("BANK_FEISHU_ENABLED=1 requires FEISHU_APP_ID and FEISHU_APP_SECRET")
    import importlib.util

    if importlib.util.find_spec("lark_oapi") is None:
        raise RuntimeError("Feishu SDK missing: pip install -r requirements-feishu.txt")

    def worker() -> None:
        global _BOT_STATE
        import lark_oapi as lark

        api_client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
        inbox: queue.Queue[Any] = queue.Queue(maxsize=1000)

        def on_message(event: Any) -> None:
            # Feishu requires the event handler to finish within three seconds.
            # Actual Core reads and send-message calls run on a separate worker.
            inbox.put_nowait(event)

        def replies() -> None:
            while True:
                event = inbox.get()
                try:
                    result = process_message(event)
                    if result is None:
                        continue
                    chat_id, reply = result
                    if _send_reply(api_client, chat_id, reply, str(event.header.event_id)):
                        continue
                except Exception:
                    pass
                audit_event("FEISHU_REPLY", outcome="FAILED",
                            request_id=str(getattr(getattr(event, "header", None), "event_id", "")))

        threading.Thread(target=replies, name="feishu-replies", daemon=True).start()

        handler = (lark.EventDispatcherHandler.builder("", "")
                   .register_p2_im_message_receive_v1(on_message).build())
        _BOT_STATE = "connecting"
        try:
            ws_client = lark.ws.Client(app_id, app_secret, event_handler=handler, log_level=lark.LogLevel.WARNING)
            _BOT_STATE = "listener_started"
            ws_client.start()
        except Exception:
            _BOT_STATE = "failed"
            audit_event("FEISHU_CONNECTION", outcome="FAILED")

    threading.Thread(target=worker, name="feishu-ws", daemon=True).start()


@router.get("/status")
def bot_status():
    return {"code": 200, "enabled": _enabled(), "state": _BOT_STATE}
