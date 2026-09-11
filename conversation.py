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

import logging
import os
import queue
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional

from bank_tools import BankAPIError, BankClient
from security import SecurityError, audit_event, sanitize_text

try:  # The graph is optional so this module stays importable in minimal setups.
    from agent import build_graph, channel_session
except Exception:  # pragma: no cover - dependency guidance path
    build_graph = None  # type: ignore[assignment]

    @contextmanager
    def channel_session(token: Optional[str]):  # type: ignore[misc]
        """Fallback when the graph module is unavailable: present nothing."""

        yield

try:
    from langgraph.checkpoint.memory import MemorySaver
except Exception:  # pragma: no cover
    MemorySaver = None  # type: ignore[assignment]

try:
    from langgraph.types import Command
except Exception:  # pragma: no cover
    Command = None  # type: ignore[assignment]


logger = logging.getLogger("mock_banking.conversation")


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


# Approval lifetime.  It must stay *inside* the core's authorization nonce TTL
# (``BANK_TOKEN_TTL_SECONDS``, 120 s by default) because the handle points at a
# nonce that the core will refuse once expired.  A longer handle would let a
# user approve an operation the core has already invalidated, producing a
# confusing failure at settle time instead of an honest "please start over".
APPROVAL_TTL_SECONDS = _env_int("BANK_APPROVAL_TTL_SECONDS", 110, 30, 600)
APPROVAL_MAX_ATTEMPTS = _env_int("BANK_APPROVAL_MAX_ATTEMPTS", 5, 1, 20)
TURN_CACHE_TTL_SECONDS = _env_int("BANK_TURN_CACHE_TTL_SECONDS", 900, 60, 86_400)
TURN_CACHE_MAX_RECORDS = _env_int("BANK_TURN_CACHE_MAX_RECORDS", 2_000, 16, 200_000)

# Namespace for the thread id.  Keeps the same principal's conversations on
# different channels (app vs IM) from colliding in the checkpointer.
CHANNEL_NAMESPACE = os.getenv("BANK_CHANNEL_ID", "app").strip() or "app"

_CONVERSATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# User-facing labels for the graph nodes, used by the streamed variant of a turn.
# Only nodes that actually run are reported, so the client shows real progress
# rather than a decorative animation.
_STAGE_LABELS: dict[str, str] = {
    "intent_parser": "正在解析您的指令",
    "risk_checker": "正在进行风控预检",
    "human_review": "等待您的确认",
    "executor": "正在执行",
    "responder": "正在整理结果",
}


class ConversationError(SecurityError):
    """A channel-level failure that maps directly onto an HTTP response."""


@dataclass
class Approval:
    """A pending human approval, held server-side.

    ``auth_token`` is the signed authorization nonce issued by the core during
    the precheck.  It is never serialized to a client.
    """

    approval_id: str
    principal: str
    thread_id: str
    conversation_id: str
    request_id: str
    risk_level: str
    required_actions: list[str]
    summary: str
    auth_token: Optional[str]
    expires_at: float
    attempts: int = 0
    resolved: bool = False

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    def public_view(self) -> dict[str, Any]:
        """The only projection a client is allowed to see."""

        return {
            "approval_id": self.approval_id,
            "risk_level": self.risk_level,
            "required_actions": list(self.required_actions),
            "summary": self.summary,
            "mfa_required": self.risk_level == "RED",
            "expires_in": max(0, int(self.expires_at - time.time())),
        }


def conversation_thread_id(principal: str, conversation_id: str) -> str:
    """Derive the checkpointer thread id for one principal's conversation.

    The principal comes from the authenticated session, never from the request
    body, and it is part of the key - so supplying someone else's
    ``conversation_id`` cannot address their thread.
    """

    if not _CONVERSATION_ID_RE.match(conversation_id or ""):
        raise ConversationError("会话编号格式无效", 400, "INVALID_CONVERSATION_ID")
    safe_principal = sanitize_text(principal, field="用户标识", max_length=128)
    return f"{CHANNEL_NAMESPACE}:{safe_principal}:{conversation_id}"


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


class ConversationService:
    """Drives agent turns and resolves the approvals they pause on."""

    def __init__(self, client_factory: Optional[Callable[..., BankClient]] = None) -> None:
        self._lock = threading.RLock()
        self._approvals: dict[str, Approval] = {}
        self._turns: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
        self._thread_locks: dict[str, threading.RLock] = {}
        # The factory takes the caller's session credential so every call the
        # gateway makes on their behalf can present it.  A channel that spoke for
        # a user without their credential would be acting on an identity it
        # merely asserted.
        self._client_factory = client_factory or (
            lambda principal, session_token=None: BankClient(user_id=principal, session_token=session_token)
        )

    def _client(self, principal: str, session_token: Optional[str]) -> BankClient:
        try:
            return self._client_factory(principal, session_token)
        except TypeError:  # pragma: no cover - legacy single-argument factory
            return self._client_factory(principal)

    # -- internal helpers -------------------------------------------------

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
            for key, (created_at, _) in list(self._turns.items()):
                if now - created_at > TURN_CACHE_TTL_SECONDS:
                    self._turns.pop(key, None)
            if len(self._turns) > TURN_CACHE_MAX_RECORDS:
                ordered = sorted(self._turns.items(), key=lambda item: item[1][0])
                for key, _ in ordered[: len(self._turns) - TURN_CACHE_MAX_RECORDS]:
                    self._turns.pop(key, None)

    @staticmethod
    def _config(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    @staticmethod
    def _pending_interrupt(graph: Any, config: dict[str, Any]) -> Optional[dict[str, Any]]:
        snapshot = graph.get_state(config)
        for task in getattr(snapshot, "tasks", ()) or ():
            for item in getattr(task, "interrupts", ()) or ():
                value = getattr(item, "value", None)
                if isinstance(value, dict):
                    return value
        return None

    def _pending_approval(self, thread_id: str) -> Optional[Approval]:
        with self._lock:
            for approval in self._approvals.values():
                if approval.thread_id == thread_id and not approval.resolved and not approval.expired:
                    return approval
        return None

    def _envelope_from_state(
        self,
        *,
        conversation_id: str,
        state: dict[str, Any],
        request_id: Optional[str],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in ("execution_result", "execution_results", "chart_data", "todo_card", "plan"):
            value = state.get(key)
            if value not in (None, [], {}):
                result[key] = value
        return {
            "code": 200,
            "conversation_id": conversation_id,
            "status": "COMPLETED",
            "reply": state.get("final_response") or "本次请求没有产生可核验的结果。",
            "request_id": request_id or state.get("request_id"),
            "intent": state.get("intent"),
            "risk_level": state.get("risk_level"),
            "approval": None,
            "result": result or None,
        }

    def _approval_envelope(self, approval: Approval, message: Optional[str] = None) -> dict[str, Any]:
        return {
            "code": 200,
            "conversation_id": approval.conversation_id,
            "status": "APPROVAL_REQUIRED",
            "reply": message or approval.summary,
            "request_id": approval.request_id,
            "intent": None,
            "risk_level": approval.risk_level,
            "approval": approval.public_view(),
            "result": None,
        }

    def _register_approval(self, *, principal: str, thread_id: str, conversation_id: str, payload: dict[str, Any]) -> Approval:
        approval = Approval(
            approval_id=f"APR{uuid.uuid4().hex[:20].upper()}",
            principal=principal,
            thread_id=thread_id,
            conversation_id=conversation_id,
            request_id=str(payload.get("request_id") or ""),
            risk_level=str(payload.get("risk_level") or "YELLOW"),
            required_actions=[str(item) for item in (payload.get("required_actions") or [])],
            summary=str(payload.get("message") or "该操作需要您的确认。"),
            auth_token=payload.get("auth_token"),
            expires_at=time.time() + APPROVAL_TTL_SECONDS,
        )
        with self._lock:
            self._approvals[approval.approval_id] = approval
        audit_event(
            "CHANNEL_APPROVAL_ISSUED",
            user_id=principal,
            risk_level=approval.risk_level,
            decision="CHALLENGE",
            outcome="APPROVAL_PENDING",
            request_id=approval.request_id,
            metadata={"approval_id": approval.approval_id, "required_actions": approval.required_actions},
        )
        return approval

    # -- public API -------------------------------------------------------

    @staticmethod
    def _turn_input(principal: str, text: str) -> dict[str, Any]:
        """Input for one fresh turn.

        LangGraph merges a turn's output into the persisted thread state, so
        per-turn transients are reset explicitly: without this a confirmation (or
        an authorization nonce) from an earlier turn could survive into an
        unrelated later request.
        """

        return {
            "user_id": principal,
            "user_input": text,
            "user_confirmed": False,
            "mfa_proof": None,
            "auth_token": None,
            "execution_result": None,
            "execution_results": None,
            "chart_data": None,
            "todo_card": None,
            "final_response": None,
            "clarification_required": False,
        }

    def _cached_turn(self, principal: str, conversation_id: str, message_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            cached = self._turns.get((principal, conversation_id, message_id))
        return cached[1] if cached is not None else None

    def _remember_turn(self, principal: str, conversation_id: str, message_id: str, envelope: dict[str, Any]) -> None:
        with self._lock:
            self._turns[(principal, conversation_id, message_id)] = (time.time(), envelope)
            self._purge()

    def _settle_turn(self, *, principal: str, conversation_id: str, thread_id: str, graph: Any, config: dict[str, Any]) -> dict[str, Any]:
        """Turn a finished (or paused) graph run into the client envelope."""

        interrupt = self._pending_interrupt(graph, config)
        if interrupt is not None:
            approval = self._register_approval(
                principal=principal,
                thread_id=thread_id,
                conversation_id=conversation_id,
                payload=interrupt,
            )
            return self._approval_envelope(approval)
        snapshot = graph.get_state(config)
        return self._envelope_from_state(
            conversation_id=conversation_id,
            state=dict(snapshot.values or {}),
            request_id=None,
        )

    def start_turn(
        self,
        *,
        principal: str,
        conversation_id: str,
        message_id: str,
        text: str,
        session_token: Optional[str] = None,
    ) -> dict[str, Any]:
        """Run one user turn, or return the approval the turn is waiting on.

        ``session_token`` is the caller's own credential; it is presented to
        every core call this turn makes, so the core sees the request as the
        signed-in user rather than as a channel asserting an identity.
        """

        self._purge()
        thread_id = conversation_thread_id(principal, conversation_id)
        safe_message_id = sanitize_text(message_id, field="消息编号", max_length=128) or uuid.uuid4().hex

        cached = self._cached_turn(principal, conversation_id, safe_message_id)
        if cached is not None:
            # A retried delivery of the same message must never produce a second
            # precheck (and therefore a second approval nonce).
            return cached

        with self._thread_lock(thread_id):
            pending = self._pending_approval(thread_id)
            if pending is not None:
                # The conversation is mid-approval.  Answering a fresh request
                # here would silently abandon it, so the client is told to
                # resolve the outstanding approval first.
                return self._approval_envelope(pending)

            graph = get_graph()
            config = self._config(thread_id)
            with channel_session(session_token):
                graph.invoke(self._turn_input(principal, text), config)
            envelope = self._settle_turn(
                principal=principal,
                conversation_id=conversation_id,
                thread_id=thread_id,
                graph=graph,
                config=config,
            )

        self._remember_turn(principal, conversation_id, safe_message_id, envelope)
        return envelope

    def _turn_events(
        self,
        *,
        principal: str,
        conversation_id: str,
        message_id: str,
        text: str,
        session_token: Optional[str],
    ) -> Iterator[dict[str, Any]]:
        """The streamed variant of :meth:`start_turn`, as a generator of events.

        What streams here is *stage* progress (解析 -> 风控预检 -> 执行 -> 结果),
        not model tokens: the language model is only ever asked for one small
        JSON intent object, so there is no token stream a user would want to
        watch.  Reporting the real stages is honest progress rather than
        decorative typing.
        """

        self._purge()
        thread_id = conversation_thread_id(principal, conversation_id)
        safe_message_id = sanitize_text(message_id, field="消息编号", max_length=128) or uuid.uuid4().hex

        cached = self._cached_turn(principal, conversation_id, safe_message_id)
        if cached is not None:
            yield {"event": "result", "data": cached}
            return

        with self._thread_lock(thread_id):
            pending = self._pending_approval(thread_id)
            if pending is not None:
                yield {"event": "approval", "data": self._approval_envelope(pending)}
                return

            graph = get_graph()
            config = self._config(thread_id)
            with channel_session(session_token):
                for chunk in graph.stream(self._turn_input(principal, text), config, stream_mode="updates"):
                    for node in chunk:
                        label = _STAGE_LABELS.get(str(node))
                        if label:
                            yield {"event": "stage", "data": {"stage": str(node), "label": label}}
            envelope = self._settle_turn(
                principal=principal,
                conversation_id=conversation_id,
                thread_id=thread_id,
                graph=graph,
                config=config,
            )

        self._remember_turn(principal, conversation_id, safe_message_id, envelope)
        event = "approval" if envelope.get("status") == "APPROVAL_REQUIRED" else "result"
        yield {"event": event, "data": envelope}

    def start_turn_stream(
        self,
        *,
        principal: str,
        conversation_id: str,
        message_id: str,
        text: str,
        session_token: Optional[str] = None,
    ) -> Iterator[dict[str, Any]]:
        """Stream one turn's progress, then exactly one terminal event.

        The turn runs in its own worker thread and this generator only drains a
        queue.  That matters for correctness, not tidiness: a channel worker
        iterates a sync generator by calling ``next()`` repeatedly, and those
        calls are not guaranteed to land on the same thread - while the caller's
        session credential is carried in a ContextVar, which *is* thread/context
        local.  Running the turn in one dedicated thread keeps the credential in
        scope for every core call it makes.
        """

        events: "queue.Queue[Optional[dict[str, Any]]]" = queue.Queue()

        def worker() -> None:
            try:
                for item in self._turn_events(
                    principal=principal,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    text=text,
                    session_token=session_token,
                ):
                    events.put(item)
            except SecurityError as exc:
                events.put({"event": "error", "data": {"code": exc.code, "message": exc.detail}})
            except Exception:  # pragma: no cover - defensive
                logger.exception("streamed turn failed")
                events.put({"event": "error", "data": {"code": "CORE_ERROR", "message": "处理失败，请稍后重试"}})
            finally:
                events.put(None)

        thread = threading.Thread(target=worker, name="turn-stream", daemon=True)
        thread.start()
        try:
            while True:
                item = events.get()
                if item is None:
                    break
                yield item
        finally:
            # Never leave the turn running after the client disconnects: it holds
            # the conversation lock the next message needs.
            thread.join(timeout=30)

    def face_challenge(self, *, principal: str, approval_id: str, session_token: Optional[str] = None) -> dict[str, Any]:
        """Fetch a liveness challenge for the *device* to sign.

        The device needs ``auth_token_jti`` to bind its signature, but it never
        receives the nonce itself: the gateway keeps it and performs the MFA
        verification on the device's behalf.
        """

        approval = self._require_approval(principal, approval_id)
        if approval.risk_level != "RED":
            raise ConversationError("该操作不需要人脸因子", 400, "MFA_NOT_REQUIRED")
        if not approval.auth_token:
            raise ConversationError("授权凭证已失效，请重新发起操作", 410, "APPROVAL_STALE")

        client = self._client(principal, session_token)
        try:
            # Reuse the graph turn's request id so the challenge is recorded on
            # the same audit chain as the precheck it belongs to.
            response = client.request_face_challenge(approval.auth_token, request_id=approval.request_id)
        except BankAPIError as exc:
            raise self._core_failure(exc) from exc
        payload = response if isinstance(response, dict) else {}
        if not payload.get("challenge_id") or not payload.get("auth_token_jti"):
            raise ConversationError("核心服务未返回有效的人脸挑战", 502, "CORE_PROTOCOL_ERROR")
        return {
            "approval_id": approval.approval_id,
            "challenge_id": payload["challenge_id"],
            "auth_token_jti": payload["auth_token_jti"],
            "expires_in": payload.get("expires_in") or payload.get("ttl_seconds"),
        }

    def resolve_approval(
        self,
        *,
        principal: str,
        approval_id: str,
        decision: str,
        otp: Optional[str] = None,
        face_attestation: Optional[str] = None,
        session_token: Optional[str] = None,
    ) -> dict[str, Any]:
        """Apply the user's decision and, when approved, settle the operation."""

        normalized = str(decision or "").strip().lower()
        if normalized not in {"approve", "reject"}:
            raise ConversationError("审批决定必须是 approve 或 reject", 400, "INVALID_DECISION")

        approval = self._require_approval(principal, approval_id)
        # One conversation resolves one approval at a time; the per-thread lock
        # also stops a double tap from issuing two core calls.
        with self._thread_lock(approval.thread_id):
            if approval.resolved:
                raise ConversationError("该审批已处理", 409, "APPROVAL_ALREADY_RESOLVED")
            if approval.expired:
                approval.resolved = True
                raise ConversationError("审批已超时，请重新发起操作", 410, "APPROVAL_EXPIRED")

            if normalized == "reject":
                # A rejection consumes the handle: the nonce is never used and
                # simply expires at the core.
                approval.resolved = True
                audit_event(
                    "CHANNEL_APPROVAL_REJECTED",
                    user_id=principal,
                    risk_level=approval.risk_level,
                    decision="BLOCK",
                    outcome="REJECTED",
                    request_id=approval.request_id,
                    metadata={"approval_id": approval.approval_id},
                )
                return self._resume(
                    approval,
                    {"confirmed": False},
                    session_token,
                )

            approval.attempts += 1
            if approval.attempts > APPROVAL_MAX_ATTEMPTS:
                approval.resolved = True
                raise ConversationError("审批尝试次数过多，请重新发起操作", 429, "APPROVAL_ATTEMPTS_EXHAUSTED")
            if not approval.auth_token:
                approval.resolved = True
                raise ConversationError("授权凭证已失效，请重新发起操作", 410, "APPROVAL_STALE")

            resume_payload: dict[str, Any]
            if approval.risk_level == "RED":
                # The red tier needs a server-verified MFA proof: the device
                # signs the liveness challenge and collects the SMS code, the
                # gateway performs the verification and keeps the proof.
                if not otp or not face_attestation:
                    raise ConversationError(
                        "高风险操作需要短信验证码与人脸票据",
                        400,
                        "MFA_FACTORS_REQUIRED",
                    )
                try:
                    verified = self._client(principal, session_token).verify_mfa(
                        approval.auth_token,
                        otp=otp,
                        face_attestation=face_attestation,
                        request_id=approval.request_id,
                    )
                except BankAPIError as exc:
                    # A failed factor does NOT consume the approval: the core
                    # allows a few attempts, so the user may retype the code.
                    raise self._core_failure(exc) from exc
                proof = (verified or {}).get("mfa_proof")
                if not proof:
                    raise ConversationError("核心服务未签发 MFA 凭证", 502, "CORE_PROTOCOL_ERROR")
                resume_payload = {"confirmed": True, "mfa_proof": proof}
            else:
                try:
                    self._client(principal, session_token).confirm(approval.auth_token, request_id=approval.request_id)
                except BankAPIError as exc:
                    raise self._core_failure(exc) from exc
                resume_payload = {"confirmed": True}

            approval.resolved = True
            return self._resume(approval, resume_payload, session_token)

    # -- internals --------------------------------------------------------

    def _require_approval(self, principal: str, approval_id: str) -> Approval:
        self._purge()
        with self._lock:
            approval = self._approvals.get(str(approval_id or "").strip())
        if approval is None:
            raise ConversationError("审批不存在或已失效", 404, "APPROVAL_NOT_FOUND")
        # The handle is only meaningful together with the principal it was
        # issued to - that is the whole point of not shipping the nonce.
        if approval.principal != principal:
            audit_event(
                "CHANNEL_APPROVAL_MISMATCH",
                user_id=principal,
                decision="BLOCK",
                outcome="FAILURE",
                reason="APPROVAL_PRINCIPAL_MISMATCH",
                metadata={"approval_id": approval.approval_id},
            )
            raise ConversationError("审批不属于当前用户", 403, "APPROVAL_PRINCIPAL_MISMATCH")
        return approval

    def _resume(self, approval: Approval, payload: dict[str, Any], session_token: Optional[str] = None) -> dict[str, Any]:
        graph = get_graph()
        if Command is None:  # pragma: no cover
            raise ConversationError("LangGraph 依赖未安装", 503, "AGENT_UNAVAILABLE")
        config = self._config(approval.thread_id)
        # The resumed turn settles the operation, so it must present the caller's
        # credential exactly like the original turn did.
        with channel_session(session_token):
            graph.invoke(Command(resume=payload), config)
        snapshot = graph.get_state(config)
        state = dict(snapshot.values or {})
        # The interrupt must be gone: if the graph paused again, the approval
        # was not actually applied and the client has to know.
        if self._pending_interrupt(graph, config) is not None:
            raise ConversationError("审批未生效，操作仍在等待确认", 409, "APPROVAL_STILL_PENDING")
        return self._envelope_from_state(
            conversation_id=approval.conversation_id,
            state=state,
            request_id=approval.request_id,
        )

    @staticmethod
    def _core_failure(exc: BankAPIError) -> ConversationError:
        payload = exc.payload if isinstance(exc.payload, dict) else {}
        detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
        code = str(detail.get("code") or "CORE_REJECTED")
        message = str(detail.get("message") or exc)
        status = exc.status_code or 502
        return ConversationError(message, status, code)

    def health(self) -> dict[str, Any]:
        with self._lock:
            live = sum(1 for item in self._approvals.values() if not item.resolved and not item.expired)
        return {
            "status": "OK",
            "checkpointer": checkpointer_backend(),
            "namespace": CHANNEL_NAMESPACE,
            "approval_ttl_seconds": APPROVAL_TTL_SECONDS,
            "pending_approvals": live,
        }

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


SERVICE = ConversationService()
