"""Channel-neutral conversation service for the banking agent.

This module owns the three things a *channel* must never own:

* the compiled LangGraph agent and its checkpointer;
* the conversation thread ids, derived **on the server** from the
  authenticated principal so a client can never address another principal's
  conversation;
* the server-side approval registry that holds the signed authorization nonce
  while the user decides.

A client (the native Android app, an IM adapter, a CLI) therefore only ever
sees an opaque, single-use ``approval_id``.  The signed ``auth_token`` and the
MFA ``mfa_proof`` stay inside this process.  That is what makes an approval
safe to deliver over a channel whose messages can be forwarded, screenshotted
or archived by a third party - unlike a raw nonce, a forwarded approval id is
worthless without the bound principal's authenticated session.

Audit continuity: every call made while resolving an approval reuses the graph
turn's own ``request_id``, so ``/api/v1/security/audit/trace/{request_id}``
still returns ONE decision chain (precheck -> confirm -> MFA -> settle) even
though the approval arrived on a second HTTP request, possibly minutes later.

Known limitation (deliberate, scheduled for the device-key milestone): a
resumed turn writes ``mfa_proof`` into the LangGraph checkpoint.  The proof is
single-use and expires with the authorization nonce (<= 120 s), and the
checkpoint store is a local file, but a production deployment should let the
device settle directly against the core so the proof never enters the
conversation layer at all.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Optional

from bank_tools import BankClient
from security import get_audit_logs, verify_audit_chain

try:  # The graph is optional so this module stays importable in minimal setups.
    from agent import build_graph, channel_session
except Exception:  # pragma: no cover - dependency guidance path
    build_graph = None  # type: ignore[assignment]

    @contextmanager
    def channel_session(token: Optional[str]):
        """Fallback when the graph module is unavailable: present nothing."""

        yield

try:
    from langgraph.checkpoint.memory import MemorySaver
except Exception:  # pragma: no cover
    MemorySaver = None  # type: ignore[assignment,misc]

try:
    from langgraph.types import Command
except Exception:  # pragma: no cover
    Command = None  # type: ignore[assignment,misc]
from bank_agent.conversation_base import (  # noqa: F401
    APPROVAL_TTL_SECONDS,
    CHANNEL_NAMESPACE,
    ConversationError,
    TURN_CACHE_MAX_RECORDS,
    TURN_CACHE_TTL_SECONDS,
    conversation_thread_id,
    logger,
)
from bank_agent.approval_store import (  # noqa: F401
    Approval,
    approval_store_status,
    load_approval_store,
)
from bank_agent import approval_store
from bank_agent.conversation_approvals import ApprovalsMixin
from bank_agent.conversation_turns import TurnsMixin



def _build_checkpointer() -> tuple[Any, str]:
    """Return ``(checkpointer, backend_name)``.

    Prefers the SQLite saver so an approval that arrives on a later request -
    possibly handled by a different worker, or after a restart - can still be
    resumed.  Falls back to the in-process saver and says so out loud rather
    than silently losing conversations on restart.
    """

    path = (os.getenv("BANK_CONVERSATION_DB", "agent_conversations.sqlite3") or "").strip()
    if path and path.lower() not in {"memory", ":memory:"}:
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver

            connection = sqlite3.connect(path, check_same_thread=False)
            saver = SqliteSaver(connection)
            saver.setup()
            logger.info("conversation checkpointer: sqlite (%s)", path)
            return saver, "sqlite"
        except Exception:  # pragma: no cover - optional dependency / bad path
            logger.exception("sqlite checkpointer unavailable; falling back to memory")
    if MemorySaver is None:  # pragma: no cover
        return None, "unavailable"
    logger.warning("conversation checkpointer: memory (conversations do not survive a restart)")
    return MemorySaver(), "memory"


_GRAPH_LOCK = threading.Lock()
_GRAPH: Any = None
_GRAPH_BACKEND = "uninitialised"


def get_graph() -> Any:
    """Return the process-wide compiled graph (lazily, once)."""

    global _GRAPH, _GRAPH_BACKEND
    with _GRAPH_LOCK:
        if _GRAPH is None:
            if build_graph is None:  # pragma: no cover - minimal installation
                raise ConversationError("LangGraph 依赖未安装，无法处理会话", 503, "AGENT_UNAVAILABLE")
            checkpointer, backend = _build_checkpointer()
            _GRAPH = build_graph(checkpointer)
            _GRAPH_BACKEND = backend
            if _GRAPH is None:  # pragma: no cover
                raise ConversationError("Agent 图不可用", 503, "AGENT_UNAVAILABLE")
        return _GRAPH


def checkpointer_backend() -> str:
    """Backend actually in use.

    Initialises the graph on first call: reporting "uninitialised" from a
    health probe would be worse than useless, and building the graph here makes
    a missing dependency fail loudly at the first probe instead of the first
    user message.
    """

    if _GRAPH is None:
        try:
            get_graph()
        except Exception:
            logger.exception("agent graph unavailable")
            return "unavailable"
    return _GRAPH_BACKEND




class ConversationService(TurnsMixin, ApprovalsMixin):
    """Drives agent turns and resolves the approvals they pause on."""

    def _graph(self):
        """The compiled graph, looked up here so tests can patch ``conversation.get_graph``."""

        return get_graph()

    def __init__(self, client_factory: Optional[Callable[..., BankClient]] = None) -> None:
        self._lock = threading.RLock()
        self._approvals: dict[str, Approval] = {}
        self._turns: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
        self._pending_dialogue: dict[str, dict[str, Any]] = {}
        self._thread_locks: dict[str, threading.RLock] = {}
        # The factory takes the caller's session credential so every call the
        # gateway makes on their behalf can present it.  A channel that spoke for
        # a user without their credential would be acting on an identity it
        # merely asserted.
        self._client_factory = client_factory or (
            lambda principal, session_token=None: BankClient(user_id=principal, session_token=session_token)
        )
        self._load_approvals()

    def _load_approvals(self) -> None:
        """Restore approvals persisted by a previous process, if any.

        Global (not per-instance) state is loaded once so a rejected file
        freezes writes for the process, mirroring the device registry.  Expired
        or stale records are tolerated here and dropped by the next purge.
        """

        # The summary lives in ``approval_store`` (read by ``approval_store_status``).
        summary = approval_store._APPROVAL_STORE_LOAD_SUMMARY = load_approval_store()
        if summary.get("rejected"):
            logger.error(
                "审批文件未通过校验，已拒绝加载并冻结写入（待审批需重新发起）: %s",
                summary.get("error"),
            )
            return
        records = summary.get("records") or {}
        for approval_id, record in records.items():
            try:
                self._approvals[str(approval_id)] = Approval.from_store_record(record)
            except (KeyError, TypeError, ValueError):
                logger.warning("Skipping malformed persisted approval %s", approval_id)

    def _client(self, principal: str, session_token: Optional[str]) -> BankClient:
        try:
            return self._client_factory(principal, session_token)
        except TypeError:  # pragma: no cover - legacy single-argument factory
            return self._client_factory(principal)

    def _thread_lock(self, thread_id: str) -> threading.RLock:
        # A conversation must be processed in order: slots awaiting a
        # clarification ("500" filling the amount the agent just asked for)
        # are only meaningful against the previous turn.
        with self._lock:
            lock = self._thread_locks.get(thread_id)
            if lock is None:
                lock = threading.RLock()
                self._thread_locks[thread_id] = lock
            return lock

    def _purge(self) -> None:
        now = time.time()
        with self._lock:
            for key, approval in list(self._approvals.items()):
                if approval.expired and not approval.resolved:
                    self._approvals.pop(key, None)
                elif approval.resolved and approval.expired:
                    self._approvals.pop(key, None)
            for turn_key, (created_at, _) in list(self._turns.items()):
                if now - created_at > TURN_CACHE_TTL_SECONDS:
                    self._turns.pop(turn_key, None)
            live_threads = {item.thread_id for item in self._approvals.values() if not item.resolved and not item.expired}
            self._pending_dialogue = {key: value for key, value in self._pending_dialogue.items() if key in live_threads}
            if len(self._turns) > TURN_CACHE_MAX_RECORDS:
                ordered = sorted(self._turns.items(), key=lambda item: item[1][0])
                for turn_key, _ in ordered[: len(self._turns) - TURN_CACHE_MAX_RECORDS]:
                    self._turns.pop(turn_key, None)

    @staticmethod
    def _config(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": 80}

    def health(self) -> dict[str, Any]:
        with self._lock:
            live = sum(1 for item in self._approvals.values() if not item.resolved and not item.expired)
        return {
            "status": "OK",
            "checkpointer": checkpointer_backend(),
            "namespace": CHANNEL_NAMESPACE,
            "approval_ttl_seconds": APPROVAL_TTL_SECONDS,
            "pending_approvals": live,
            "approval_store": approval_store_status(),
        }

    def aggregate_trace(self, *, principal: str, request_id: str, session_token: Optional[str] = None) -> dict[str, Any]:
        """Combine gateway-local and core events without inventing one chain."""
        local = [e for e in get_audit_logs(limit=1000, user_id=principal) if e.get("request_id") == request_id]
        core_trace: dict[str, Any] = {}
        missing: list[str] = []
        try:
            from urllib.parse import quote
            client = BankClient(user_id=principal, session_token=session_token)
            response = client._request("GET", f"security/audit/trace/{quote(request_id, safe='')}", read_only=True)
            payload = response.json()
            core_trace = payload if isinstance(payload, dict) else {}
            # Core deployments may wrap the trace under ``data`` while older
            # ones merge it beside ``code``.  Accept both envelopes.
            if isinstance(core_trace.get("data"), dict):
                core_trace = core_trace["data"]
            if isinstance(core_trace.get("events"), list):
                core_trace = {**core_trace, "events": [e for e in core_trace["events"] if e.get("user_id") == principal]}
        except Exception:
            missing.append("core")
        if not local:
            missing.append("gateway")
        if not local and not core_trace.get("events"):
            raise ConversationError("审计请求不存在或不属于当前用户", 403, "AUDIT_TRACE_FORBIDDEN")
        events = [{**e, "source": "gateway"} for e in local]
        events.extend({**e, "source": "core"} for e in core_trace.get("events", []))
        events.sort(key=lambda e: (str(e.get("timestamp") or ""), str(e.get("event_id") or "")))
        core_verification = core_trace.get("chain_verification")
        if core_verification is None:
            # The legacy core trace endpoint returns per-event hashes but does
            # not expose verification of its process-local chain.  Do not
            # pretend the gateway can verify a chain it does not own.
            core_verification = {"available": False, "reason": "core_verification_not_exposed"}
        return {"request_id": request_id, "complete": not missing, "missing_sources": missing,
                "chain_verification": {"gateway": verify_audit_chain(), "core": core_verification},
                "events": events}

    def reset(self) -> None:
        """Drop every pending approval and cached turn.

        Test/demo helper, mirroring ``security.reset_security_state``; it is
        deliberately not reachable over HTTP, because discarding a live
        approval is exactly the kind of state change that must stay auditable.
        """

        with self._lock:
            self._approvals.clear()
            self._turns.clear()
            self._thread_locks.clear()
        # A rejected file freezes writes for the life of the process, so a test
        # that deliberately feeds a forged registry must not leave every later
        # test frozen (mirrors ``reset_security_state``).
        approval_store._APPROVAL_STORE_BLOCKED = False
        approval_store._APPROVAL_WRITE_FAILURES = 0
        approval_store._APPROVAL_LAST_WRITE_FAILED = False


SERVICE = ConversationService()
