"""Gateway runtime: configuration, the per-turn session credential, the optional model client and the Core HTTP transport.

``_http`` and ``llm`` are the module's patchable seams: every caller looks them
up as ``runtime._http`` / ``runtime.llm``, so tests and scripts replace them here.
"""

from __future__ import annotations

import contextvars
import os
import uuid
from contextlib import contextmanager
from typing import Any, Optional

import requests

from security import audit_event
from workflow_runtime import model_timeout
from bank_agent.state import (
    BankingAgentState,
)


try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - optional dependency in minimal tests
    pass

try:
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, StateGraph
    from langgraph.types import interrupt
except Exception as exc:  # pragma: no cover - makes helper imports testable without extras
    ChatOpenAI = None  # type: ignore[assignment]
    HumanMessage = SystemMessage = None  # type: ignore[assignment]
    MemorySaver = StateGraph = END = interrupt = None  # type: ignore[assignment]
    _LANGGRAPH_IMPORT_ERROR = exc
else:
    _LANGGRAPH_IMPORT_ERROR = None


DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
BANK_API_BASE = os.getenv("BANK_API_BASE", "http://127.0.0.1:8000/api/v1").rstrip("/")
HTTP_TIMEOUT = max(0.5, min(float(os.getenv("BANK_HTTP_TIMEOUT_SECONDS", "8")), 60.0))
INJECTION_LOCKED_NOTICE = "多次检测到不安全指令，账户已临时锁定，请稍后再试。"
MODEL_ENABLED = bool(DEEPSEEK_API_KEY and ChatOpenAI is not None)

# The caller's session credential for the turn currently being executed.
#
# A ContextVar rather than graph state on purpose: the checkpointer persists
# state to disk (and a resumable approval may sit for a while), so a bearer
# credential kept there would outlive the request that legitimately held it.
# ``channel_session`` scopes it to one graph invocation.
_CHANNEL_SESSION: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("bank_channel_session", default=None)


@contextmanager
def channel_session(token: Optional[str]):
    """Present *token* to every core call made inside this block."""

    handle = _CHANNEL_SESSION.set(token or None)
    try:
        yield
    finally:
        _CHANNEL_SESSION.reset(handle)

if MODEL_ENABLED:
    llm = ChatOpenAI(
        model=os.getenv("BANK_LLM_MODEL", "deepseek-chat"),
        api_key=DEEPSEEK_API_KEY,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        temperature=0.0,
        timeout=model_timeout(),
        max_retries=max(0, min(int(os.getenv("BANK_MODEL_MAX_RETRIES", "0")), 1)),
    )
else:
    llm = None


def _http(method: str, path: str, *, state: BankingAgentState, json_body: Optional[dict[str, Any]] = None, headers: Optional[dict[str, str]] = None) -> requests.Response:
    merged = {"X-Request-Id": state.get("request_id") or f"agent_{uuid.uuid4().hex}"}
    # When the core requires a session for writes, the calls a turn makes on the
    # user's behalf must present the user's own credential.  It arrives through a
    # ContextVar rather than graph state: state is written to the checkpointer,
    # and a bearer credential has no business sitting in a checkpoint file.  See
    # ``channel_session``.
    session_token = _CHANNEL_SESSION.get()
    if session_token:
        merged["X-Session-Token"] = session_token
    merged.update(headers or {})
    return requests.request(method, f"{BANK_API_BASE}/{path.lstrip('/')}", json=json_body, headers=merged, timeout=HTTP_TIMEOUT)


def report_security_signal(signal: str, *, request_id: Optional[str], source: str = "AGENT", session_token: Optional[str] = None) -> bool:
    """Charge a channel-side block to the caller's Core circuit breaker.

    Best effort: the block itself already happened and was audited here. Core
    attributes the signal to the session principal only, so a channel without
    a verified session reports nothing. Returns whether the account is locked.
    """

    if not (session_token or _CHANNEL_SESSION.get()):
        return False
    headers = {"X-Session-Token": session_token} if session_token else None
    try:
        response = _http("POST", "security/signals", state={"request_id": request_id}, json_body={"signal": signal, "source": source}, headers=headers)
    except requests.RequestException:
        response = None
    if response is None or response.status_code >= 400:
        audit_event("SECURITY_SIGNAL", request_id=request_id, decision="ERROR", outcome="UNDELIVERED", reason=signal, metadata={"source": source})
        return False
    return _response_json(response).get("locked") is True


def _response_json(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("核心服务返回了无效数据") from exc
    if not isinstance(data, dict):
        raise RuntimeError("核心服务返回格式不正确")
    return data


def _error_message(response: requests.Response) -> str:
    try:
        data = response.json()
        detail = data.get("detail", data)
        if isinstance(detail, dict):
            return str(detail.get("message") or detail.get("code") or "核心服务拒绝")
        return str(detail)
    except Exception:
        return f"核心服务拒绝（HTTP {response.status_code}）"
