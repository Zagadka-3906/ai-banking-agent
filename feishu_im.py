"""Feishu private-chat connector for the single-process local Gateway.

The official SDK authenticates WebSocket events and sends replies. Bank sessions
are only ever held in Gateway memory; Feishu messages can select fixed Core GETs.
"""

from __future__ import annotations

import asyncio
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

from agent import BILL_CATEGORY_ALIASES, INJECTION_LOCKED_NOTICE, _local_parse, report_security_signal
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
_BOT_STATE_SINCE = time.time()
_BOT_LAST_FAILURE: dict[str, Any] | None = None
_RETRY_SECONDS = 30


def _set_state(state: str) -> None:
    global _BOT_STATE, _BOT_STATE_SINCE
    if state != _BOT_STATE:
        _BOT_STATE, _BOT_STATE_SINCE = state, time.time()


def _enabled() -> bool:
    return os.getenv("BANK_FEISHU_ENABLED", "0") == "1"


def reset_state() -> None:
    """Clear ephemeral state for isolated tests."""
    global _BOT_LAST_FAILURE
    with _LOCK:
        _CODES.clear()
        _BINDINGS.clear()
        _EVENTS.clear()
        _set_state("disabled")
        _BOT_LAST_FAILURE = None


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
    if intent == Intent.SUBSCRIPTION_QUERY.value:
        # Yellow tier: the channel cannot collect the required confirmation.
        return "APP_HANDOFF", handoff_card("订阅查询需要您确认后执行，请在 App 内查询。")
    paths = {
        Intent.BALANCE_QUERY.value: f"accounts/{principal}/balance",
        Intent.BILL_QUERY.value: f"accounts/{principal}/transactions?{bill_query}",
        Intent.BILL_ANALYSIS.value: f"accounts/{principal}/bill-analysis?{bill_query}",
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
    except SecurityError as exc:
        with _LOCK:
            binding = dict(_BINDINGS.get(sender_id) or {})
        if exc.code == "PROMPT_INJECTION_BLOCKED" and binding:
            audit_event("PROMPT_INJECTION_BLOCKED", user_id=binding["principal"], decision="BLOCK", outcome="FAILURE",
                        reason=exc.code, request_id=request_id, metadata={"source": "FEISHU"})
            # Charged to the bound bank account; an unbound sender has none.
            if report_security_signal(exc.code, request_id=request_id, source="FEISHU", session_token=binding["session_token"]):
                return chat_id, failure_card(INJECTION_LOCKED_NOTICE, title="账户已临时锁定")
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
    global _BOT_LAST_FAILURE
    try:
        response = client.im.v1.message.create(request)
        success = bool(response.success())
        if not success:
            code = getattr(response, "code", None)
            _BOT_LAST_FAILURE = {"stage": "send_message", "code": code if isinstance(code, int) else None,
                                 "kind": "platform_policy_denied" if code == 21001 else "platform_request_failed"}
        else:
            _BOT_LAST_FAILURE = None
        return success
    except Exception as error:
        # SDK exceptions may carry credentials/URLs. Keep only a numeric code.
        match = re.search(r"code[:=]\s*(\d{3,8})", str(error))
        code = int(match.group(1)) if match else None
        _BOT_LAST_FAILURE = {"stage": "send_message", "code": code,
                             "kind": "platform_policy_denied" if code == 21001 else "sdk_request_failed"}
        return False


def _wait_for_access(probe) -> bool:
    """Recover startup access failures without an operator restarting Gateway."""
    global _BOT_LAST_FAILURE
    _set_state("checking_access")
    while _enabled():
        access = probe()
        if access["ok"]:
            _BOT_LAST_FAILURE = None
            return True
        failure = {"stage": "tenant_token", "code": access["code"], "kind": access["kind"]}
        _set_state("access_failed")
        if failure != _BOT_LAST_FAILURE:
            audit_event("FEISHU_CONNECTION", outcome="FAILED", metadata=failure)
        _BOT_LAST_FAILURE = failure
        # The probe has its own HTTP timeout. Retry only access checks here;
        # never replay bank operations or already attempted outgoing messages.
        time.sleep(_RETRY_SECONDS)
    _set_state("disabled")
    return False


def _listener_failure(error: BaseException) -> dict[str, Any]:
    """Classify an ended listener by numeric code only; SDK text may hold URLs."""
    code = getattr(error, "code", None)
    code = code if isinstance(code, int) and not isinstance(code, bool) else None
    kind = "platform_policy_denied" if code == 21001 else "platform_rejected" if code is not None else "listener_stopped"
    return {"stage": "ws_connect", "code": code, "kind": kind}


def _run_listener(ws_client: Any, loop: Any) -> dict[str, Any]:
    """Run one SDK client until it can no longer recover; return why it ended.

    lark-oapi 1.7.3 retries dropped connections itself, but gives up for good
    when the platform rejects the app (``ClientException``) or the server's
    retry budget runs out. The first case raises from ``start()``; during a
    background reconnect the SDK task just dies while ``start()`` keeps
    blocking. Wrapping the instance's connect/reconnect coroutines (pinned SDK
    internals) marks real connections and stops the shared loop when the
    background reconnect gives up, so the caller can rebuild a client.
    """
    connect, reconnect = ws_client._connect, ws_client._reconnect
    ended: list[BaseException] = []

    async def tracked_connect() -> None:
        await connect()
        _set_state("connected")

    async def supervised_reconnect() -> None:
        try:
            await reconnect()
        except Exception as error:
            ended.append(error)
            loop.stop()
            raise

    ws_client._connect, ws_client._reconnect = tracked_connect, supervised_reconnect
    ws_client.on_reconnecting = lambda: _set_state("reconnecting")
    _set_state("connecting")
    try:
        ws_client.start()
    except Exception as error:
        ended.append(error)
    finally:
        # Drop the old client's ping/receive tasks before a new client shares the loop.
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    return _listener_failure(ended[0]) if ended else _listener_failure(RuntimeError())


def _supervise(probe, new_client, loop) -> None:
    """Keep one listener alive: re-check access, then rebuild the SDK client."""
    global _BOT_LAST_FAILURE
    previous: dict[str, Any] | None = None
    while _wait_for_access(probe):
        failure = _run_listener(new_client(), loop)
        _set_state("listener_failed")
        if failure != previous:
            audit_event("FEISHU_CONNECTION", outcome="FAILED", metadata=failure)
        _BOT_LAST_FAILURE = previous = failure
        if not _enabled():
            break
        # Back off before re-probing. Only the connection is retried; bank
        # reads and outgoing replies are never replayed from here.
        time.sleep(_RETRY_SECONDS)
    _set_state("disabled")


def start_bot() -> None:
    """Called on Gateway startup. One Gateway worker owns one WS connection."""
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
        import lark_oapi as lark
        from feishu_access import probe_access
        from lark_oapi.ws import client as ws_module

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
        _supervise(probe_access,
                   lambda: lark.ws.Client(app_id, app_secret, event_handler=handler, log_level=lark.LogLevel.WARNING),
                   ws_module.loop)

    threading.Thread(target=worker, name="feishu-ws", daemon=True).start()


@router.get("/status")
def bot_status():
    return {"code": 200, "enabled": _enabled(), "state": _BOT_STATE,
            "state_seconds": int(time.time() - _BOT_STATE_SINCE), "last_failure": _BOT_LAST_FAILURE}
