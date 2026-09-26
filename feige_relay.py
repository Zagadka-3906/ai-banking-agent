"""Local canonical relay boundary for a Feige IM connector.

The Feige product and its wire protocol must be selected before a real
provider connector can translate platform events into this canonical format.
The relay is disabled unless BANK_FEIGE_RELAY_SECRET is configured.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from typing import Any

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict

from agent import _local_parse
from bank_tools import BankAPIError, BankClient
from planner import Intent
from security import SecurityError, audit_event, sanitize_text

router = APIRouter(prefix="/api/v1/im/feige", tags=["feige-im"])
_LOCK = threading.RLock()
_CODES: dict[str, dict[str, Any]] = {}
_BINDINGS: dict[str, dict[str, Any]] = {}
_EVENTS: dict[str, float] = {}
_CODE_TTL = 300
_CLOCK_SKEW = 300
_MAX_PENDING = 1000
_MAX_EVENTS = 10000
_MAX_BINDINGS = 10000


class RelayEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    subject: str
    text: str | None = None
    binding_code: str | None = None


def _secret() -> str:
    value = os.getenv("BANK_FEIGE_RELAY_SECRET", "")
    if len(value.encode()) < 32:
        raise SecurityError("飞鸽中继未配置强密钥", 503, "FEIGE_RELAY_DISABLED")
    return value


def _subject_key(subject: str) -> str:
    return hmac.new(_secret().encode(), subject.encode(), hashlib.sha256).hexdigest()


def _verify_event(timestamp: str, event_id: str, raw: bytes, signature: str) -> None:
    try:
        stamp = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise SecurityError("中继时间戳无效", 401, "FEIGE_INVALID_TIMESTAMP") from exc
    if abs(time.time() - stamp) > _CLOCK_SKEW:
        raise SecurityError("中继消息已过期", 401, "FEIGE_STALE_EVENT")
    event_id = sanitize_text(event_id, field="消息编号", max_length=128)
    if not event_id:
        raise SecurityError("缺少消息编号", 400, "FEIGE_EVENT_ID_REQUIRED")
    expected = hmac.new(_secret().encode(), f"{stamp}.{event_id}.".encode() + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature or ""):
        raise SecurityError("中继签名无效", 401, "FEIGE_INVALID_SIGNATURE")
    with _LOCK:
        now = time.time()
        for key, until in list(_EVENTS.items()):
            if until <= now:
                _EVENTS.pop(key, None)
        if len(_EVENTS) >= _MAX_EVENTS:
            raise SecurityError("中继消息过多", 429, "FEIGE_RELAY_BUSY")
        if event_id in _EVENTS:
            raise SecurityError("重复消息", 409, "FEIGE_EVENT_REPLAY")
        _EVENTS[event_id] = now + _CLOCK_SKEW


def reset_state() -> None:
    with _LOCK:
        _CODES.clear()
        _BINDINGS.clear()
        _EVENTS.clear()


def _read_only_reply(principal: str, session_token: str, message: str) -> tuple[str, str]:
    """Translate a small query allowlist into Core GETs only.

    No model or conversation executor runs in this path. Even a misclassified
    user message can only choose one of these fixed, read-only Core endpoints.
    """

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
        return "APP_HANDOFF", "飞鸽渠道目前仅支持余额、账单、卡片、订阅和理财产品查询。账户变更请在 Android 应用中重新发起并确认。"
    try:
        response = BankClient(user_id=principal, session_token=session_token)._request("GET", path, read_only=True)
        result = response.json()
    except (BankAPIError, ValueError) as exc:
        raise SecurityError("核心银行查询失败，请稍后重试", 503, "FEIGE_CORE_READ_FAILED") from exc
    if not isinstance(result, dict) or result.get("code") != 200:
        raise SecurityError("核心银行结果格式无效", 502, "FEIGE_CORE_PROTOCOL_ERROR")
    if intent == Intent.BALANCE_QUERY.value:
        reply = f"当前可用余额 ¥{float(result['available_balance']):,.2f}；总余额 ¥{float(result['total_balance']):,.2f}，已锁定 ¥{float(result['locked_amount']):,.2f}。"
    elif intent == Intent.BILL_QUERY.value:
        rows = result.get("data") or []
        reply = f"{scope} 流水共 {result.get('total_records', len(rows))} 笔。" + "；".join(f"{row.get('merchant', '交易')} ¥{float(row.get('amount', 0)):,.2f}" for row in rows[:5])
    elif intent == Intent.BILL_ANALYSIS.value:
        facts = result.get("facts") or {}
        reply = f"{scope} 账单共 {facts.get('record_count', 0)} 笔，支出 ¥{float(facts.get('total_spent', 0)):,.2f}。" + "；".join(f"{row.get('category')} ¥{float(row.get('amount', 0)):,.2f}" for row in (facts.get("category_breakdown") or [])[:5])
    elif intent == Intent.SUBSCRIPTION_QUERY.value:
        rows = result.get("data") or []
        reply = "订阅与周期扣费：" + ("；".join(f"{row.get('merchant')} ¥{float(row.get('amount', 0)):,.2f}（{'待核实' if row.get('status') == 'CANDIDATE' else row.get('status')}）" for row in rows[:8]) or "暂无记录")
    elif intent == Intent.CARD_QUERY.value:
        rows = result.get("data") or []
        reply = "卡片：" + ("；".join(f"{row.get('number_masked') or row.get('card_id')}（{row.get('status')}）" for row in rows[:8]) or "暂无卡片")
    else:
        rows = result.get("data") or []
        reply = "理财产品：" + ("；".join(str(row.get("name") or row.get("product_id") or "产品") for row in rows[:8]) or "暂无产品")
    return "COMPLETED", reply


@router.post("/binding-code")
def issue_binding_code(
    request: Request,
    x_session_token: str | None = Header(None, alias="X-Session-Token"),
):
    from agent_api import _guard_rate_limit, principal_for

    principal = principal_for(x_session_token=x_session_token, x_user_id=None, action="FEIGE_BINDING_ISSUE", request_id=request.state.request_id)
    if not x_session_token:
        raise SecurityError("需要登录会话", 401, "SESSION_REQUIRED")
    _secret()
    _guard_rate_limit(principal)
    code = secrets.token_urlsafe(18)
    with _LOCK:
        now = time.time()
        for digest, record in list(_CODES.items()):
            if record["expires"] <= now:
                _CODES.pop(digest, None)
        if len(_CODES) >= _MAX_PENDING:
            raise SecurityError("绑定请求过多", 429, "FEIGE_BINDING_BUSY")
        _CODES[hashlib.sha256(code.encode()).hexdigest()] = {
            "principal": principal, "session_token": x_session_token,
            "expires": time.time() + _CODE_TTL,
        }
    audit_event("FEIGE_BINDING_ISSUED", user_id=principal, outcome="SUCCESS", request_id=request.state.request_id)
    return {"code": 200, "binding_code": code, "expires_in": _CODE_TTL}


@router.delete("/binding")
def remove_binding(
    request: Request,
    subject: str,
    x_session_token: str | None = Header(None, alias="X-Session-Token"),
):
    from agent_api import principal_for

    principal = principal_for(x_session_token=x_session_token, x_user_id=None, action="FEIGE_UNBIND", request_id=request.state.request_id)
    key = _subject_key(sanitize_text(subject, field="IM 用户", max_length=256))
    with _LOCK:
        binding = _BINDINGS.get(key)
        if not binding or binding["principal"] != principal:
            raise SecurityError("绑定不存在", 404, "FEIGE_BINDING_NOT_FOUND")
        _BINDINGS.pop(key)
    audit_event("FEIGE_UNBOUND", user_id=principal, outcome="SUCCESS", request_id=request.state.request_id)
    return {"code": 200, "status": "UNBOUND"}


@router.post("/relay")
async def relay_event(
    request: Request,
    x_feige_timestamp: str = Header(..., alias="X-Feige-Timestamp"),
    x_feige_event_id: str = Header(..., alias="X-Feige-Event-Id"),
    x_feige_signature: str = Header(..., alias="X-Feige-Signature"),
):
    raw = await request.body()
    _verify_event(x_feige_timestamp, x_feige_event_id, raw, x_feige_signature)
    try:
        event = RelayEvent.model_validate_json(raw)
    except ValueError as exc:
        raise SecurityError("中继消息格式无效", 400, "FEIGE_INVALID_EVENT") from exc
    subject = sanitize_text(event.subject, field="IM 用户", max_length=256)
    if not subject:
        raise SecurityError("缺少 IM 用户", 400, "FEIGE_SUBJECT_REQUIRED")
    key = _subject_key(subject)
    if event.kind == "bind":
        digest = hashlib.sha256(str(event.binding_code or "").encode()).hexdigest()
        with _LOCK:
            code = _CODES.pop(digest, None)
            if not code or code["expires"] <= time.time():
                raise SecurityError("绑定码无效或已过期", 403, "FEIGE_INVALID_BINDING_CODE")
            if key not in _BINDINGS and len(_BINDINGS) >= _MAX_BINDINGS:
                raise SecurityError("绑定数量超过上限", 429, "FEIGE_BINDING_BUSY")
            _BINDINGS[key] = code
        audit_event("FEIGE_BOUND", user_id=code["principal"], outcome="SUCCESS", request_id=request.state.request_id)
        return {"code": 200, "status": "BOUND"}
    if event.kind != "message" or not event.text:
        raise SecurityError("只接受绑定或文本消息", 400, "FEIGE_INVALID_EVENT")
    with _LOCK:
        binding = dict(_BINDINGS.get(key) or {})
    if not binding:
        raise SecurityError("IM 用户尚未绑定银行账户", 403, "FEIGE_NOT_BOUND")
    from agent_api import _guard_rate_limit, principal_for

    principal = principal_for(x_session_token=binding["session_token"], x_user_id=None, action="FEIGE_MESSAGE", request_id=request.state.request_id)
    if principal != binding["principal"]:
        raise SecurityError("绑定会话主体已变化", 403, "FEIGE_PRINCIPAL_CHANGED")
    _guard_rate_limit(principal)
    status, reply = _read_only_reply(principal, binding["session_token"], sanitize_text(event.text, field="IM 消息", max_length=2000))
    audit_event("FEIGE_READ_QUERY" if status == "COMPLETED" else "FEIGE_WRITE_DEFERRED", user_id=principal,
                decision="ALLOW" if status == "COMPLETED" else "BLOCK", outcome=status, request_id=request.state.request_id)
    return {"code": 200, "status": status, "reply": reply}
