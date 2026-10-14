"""Feishu private-chat connector for the single-process local Gateway.

The official SDK authenticates WebSocket events and sends replies. Bank sessions
are only ever held in Gateway memory; Feishu messages can select fixed Core GETs.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Header, Request

from agent import BILL_CATEGORY_ALIASES, _local_parse
from bank_tools import BankAPIError, BankClient
from feishu_cards import FeishuReply, binding_card, failure_card, handoff_card, help_card, query_card
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


def _bill_category_supported(message: str, category: Any) -> bool:
    """Do not silently discard category qualifiers the fixed GET cannot express."""
    matches = {
        name for name, aliases in BILL_CATEGORY_ALIASES.items()
        if any(alias in message for alias in aliases)
    }
    if len(matches) > 1 or (category is not None and (not isinstance(category, str) or category not in BILL_CATEGORY_ALIASES)):
        return False
    if matches and matches != {category}:
        return False
    if category and re.search(r"排除|不含|不包括|除了|以外|之外", message):
        return False
    # Ordinary questions such as “钱都花哪儿去了” remain supported. Only inspect
    # category qualifiers beside bill nouns / explicit category labels here.
    qualifiers = re.findall(r"([\w\s]+?)(?=账单|流水|消费记录|交易记录|支出|消费)", message)
    qualifiers += re.findall(r"(?:类别|分类)\s*[:：为是]\s*([^，。！？\s]+)", message)
    qualifiers += re.findall(r"(?:只看|仅看|只查|仅查)\s*([^，。！？\s]+)", message)
    qualifiers += re.findall(r"按([^，。！？\s]+?)(?:类别|分类)", message)
    for value in qualifiers:
        if category:
            for alias in BILL_CATEGORY_ALIASES[category]:
                value = value.replace(alias, "")
        value = re.sub(
            r"所有历史|这个月|上个月|上月|本月|今天|今日|今年|年度|全年|全部|所有|"
            r"分析一下|查询一下|查看一下|我想知道|我想|帮忙|帮我|给我|请问|麻烦|请|查询|查看|看看|分析|统计|查|看|我|的|一下|整体|按类别|按分类",
            "", value,
        )
        if value.strip():
            return False
    return True


def _read_only_reply(principal: str, session_token: str, message: str) -> tuple[str, str | FeishuReply]:
    """Only fixed Core GET routes are reachable from the IM channel."""
    parsed = _local_parse(message)
    intent = str(parsed.get("intent") or "")
    slots = parsed.get("slots") or {}
    scope = str(parsed.get("date_scope") or "ALL")
    if scope not in {"TODAY", "MONTH", "LAST_MONTH", "YEAR", "ALL"} or slots.get("date_range_ambiguous"):
        return "APP_HANDOFF", handoff_card("这项日期范围需要在 App 核对后查询。")
    if slots.get("request_ambiguous") or slots.get("category_ambiguous"):
        return "APP_HANDOFF", handoff_card("这项请求的查询条件需要在 App 核对。")
    category = slots.get("category") if intent in {Intent.BILL_QUERY.value, Intent.BILL_ANALYSIS.value} else None
    if intent in {Intent.BILL_QUERY.value, Intent.BILL_ANALYSIS.value} and not _bill_category_supported(message, category):
        return "APP_HANDOFF", handoff_card("这项账单分类条件暂不支持在飞书查询，请在 App 核对后查询。")
    bill_params = {"date_scope": scope}
    if category is not None:
        bill_params["category"] = category
    bill_query = urlencode(bill_params)
    detected = bool(slots.get("detect_subscriptions"))
    paths = {
        Intent.BALANCE_QUERY.value: f"accounts/{principal}/balance",
        Intent.BILL_QUERY.value: f"accounts/{principal}/transactions?{bill_query}",
        Intent.BILL_ANALYSIS.value: f"accounts/{principal}/bill-analysis?{bill_query}",
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
    if category is not None and (result.get("category") != category or result.get("date_scope") != scope):
        raise SecurityError("核心银行未确认账单查询条件", 502, "FEISHU_CORE_PROTOCOL_ERROR")
    return "COMPLETED", query_card(intent, scope, result, category=category)


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
        return chat_id, failure_card("消息包含不安全内容或超过长度限制，请换一种说法。", title="消息未受理")
    if text.startswith("/绑定 "):
        code = text.removeprefix("/绑定 ").strip()
        digest = hashlib.sha256(code.encode()).hexdigest()
        with _LOCK:
            record = _CODES.pop(digest, None)
        if not record or record["expires"] <= time.time():
            return chat_id, binding_card("invalid")
        from agent_api import principal_for

        try:
            current = principal_for(x_session_token=record["session_token"], x_user_id=None,
                                    action="FEISHU_BIND", request_id=request_id)
        except SecurityError:
            return chat_id, binding_card("expired")
        if current != record["principal"]:
            return chat_id, binding_card("changed")
        with _LOCK:
            if sender_id not in _BINDINGS and len(_BINDINGS) >= _MAX_BINDINGS:
                return chat_id, binding_card("capacity")
            _BINDINGS[sender_id] = record
        audit_event("FEISHU_BOUND", user_id=record["principal"], outcome="SUCCESS", request_id=request_id)
        return chat_id, binding_card("bound")
    if text == "/解绑":
        with _LOCK:
            record = _BINDINGS.pop(sender_id, None)
        if record:
            audit_event("FEISHU_UNBOUND", user_id=record["principal"], outcome="SUCCESS", request_id=request_id)
        return chat_id, binding_card("unbound")
    with _LOCK:
        binding = dict(_BINDINGS.get(sender_id) or {})
    if not binding:
        return chat_id, binding_card("required")
    from agent_api import _guard_rate_limit, principal_for

    try:
        principal = principal_for(x_session_token=binding["session_token"], x_user_id=None,
                                  action="FEISHU_MESSAGE", request_id=request_id)
        if principal != binding["principal"]:
            raise SecurityError("绑定会话主体已变化", 403, "FEISHU_PRINCIPAL_CHANGED")
        _guard_rate_limit(principal)
        status, reply = ("COMPLETED", help_card()) if text in {"/帮助", "帮助", "查看入口", "开始"} else _read_only_reply(principal, binding["session_token"], text)
    except SecurityError as exc:
        if exc.status_code in {401, 403}:
            with _LOCK:
                _BINDINGS.pop(sender_id, None)
            return chat_id, binding_card("expired")
        audit_event("FEISHU_READ_QUERY", user_id=binding["principal"], outcome="FAILED", request_id=request_id)
        return chat_id, failure_card("查询暂时不可用，请稍后重试。")
    except (KeyError, TypeError, ValueError):
        audit_event("FEISHU_READ_QUERY", user_id=binding["principal"], outcome="FAILED", request_id=request_id)
        return chat_id, failure_card("暂未取得有效的银行查询结果，请稍后重试。")
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
