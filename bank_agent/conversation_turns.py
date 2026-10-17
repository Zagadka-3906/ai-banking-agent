"""Conversation turns: dialogue controls, idempotent turn cache, streaming progress and settlement."""

from __future__ import annotations

import json
import queue
import re
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional

from workflow_contract import business_status, public_data, reply_contract, workflow_plan
from workflow_runtime import observe_stages
from security import SecurityError, audit_event, sanitize_text

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
    ConversationError,
    conversation_thread_id,
)


class TurnsMixin:
    """Mixed into ``conversation.ConversationService``; uses its state and ``_graph()``."""

    def _envelope_from_state(
        self,
        *,
        conversation_id: str,
        state: dict[str, Any],
        request_id: Optional[str],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in ("execution_result", "execution_results", "chart_data", "todo_card", "plan", "risk_quiz", "profile_result", "suggestions"):
            value = state.get(key)
            if value not in (None, [], {}):
                result[key] = value
        status = business_status(state)
        summary = state.get("final_response") or "本次请求没有产生可核验的结果。"
        if "suggestions" not in result:
            # Wording the reply offers ("可以说“…”") is shown as chips at once, without waiting for the model.
            from bank_agent.suggestions import reply_examples

            if examples := reply_examples(str(state.get("final_response") or "")):
                result["suggestions"] = examples
        if workflow_plan(state):
            result["workflow_plan"] = workflow_plan(state)
        return {
            "code": 200,
            "conversation_id": conversation_id,
            "status": "COMPLETED",
            "turn_status": "COMPLETED",
            "business_status": status,
            "response": reply_contract(state, summary, status),
            "reply": summary,
            "request_id": request_id or state.get("request_id"),
            "intent": state.get("intent"),
            "risk_level": state.get("risk_level"),
            "approval": None,
            "result": public_data(result) or None,
        }

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
            "business_status": None,
            "reconciliation": None,
            "completed_workflow": None,
            "continue_workflow": False,
            # The level card and its chips belong to the turn that finished the
            # questionnaire; later answers (subscriptions, balance…) must not repeat them.
            "profile_result": None,
            "suggestions": None,
        }

    @staticmethod
    def _dialogue_control(text: str) -> Optional[str]:
        """Only explicit controls may replace an outstanding approval.

        A fresh request (including "再给王美转50元") keeps waiting on the
        original approval. Cancellation of another service such as a named
        subscription is likewise not a cancellation of the current transfer.
        """

        text = sanitize_text(text, field="用户指令", max_length=2000)
        compact = re.sub(r"[\s，,。.!！?？、]", "", text)
        if re.fullmatch(
            r"(?:算了|不用了|取消|取消吧|取消它|取消掉|不要了|别转了|不转了|不办了|先不办了|先别办了|先别转了|先不转了|"
            r"算了(?:这笔|这次)?(?:不转了|不办了|取消吧)?|"
            r"(?:取消|撤销|终止)(?:这笔|这一笔|本次|这次|当前|刚才|上次)(?:转账|操作|申请|任务|预约)?|"
            r"(?:这笔|这次|当前)(?:不转了|不办了|取消了|取消吧))", compact,
        ):
            return "cancel"
        if re.match(
            r"^(?:(?:不对|等一下|等等|说错了)[，,：:\s]*)?"
            r"(?:(?:把)?(?:金额|收款人|对象|参与人|日期|时间|备注)[，,：:\s]*)?"
            r"(?:改成|改为|改到|改一下|换成|换为|调整为)", text.strip(),
        ):
            return "revise"
        return None

    def _prepare_dialogue_control(
        self, *, principal: str, conversation_id: str, thread_id: str,
        text: str, request_id: str, session_token: Optional[str],
    ) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
        """Cancel or revise under the same conversation lock as settlement."""

        graph = self._graph()
        config = self._config(thread_id)
        state = dict(graph.get_state(config).values or {})
        if state.get("business_status") == "PENDING_RECONCILIATION":
            return self._envelope_from_state(conversation_id=conversation_id, state=state, request_id=None), {}
        pending = self._pending_approval(thread_id)
        control = self._dialogue_control(text)
        if pending is not None and control is None:
            # A new request does not silently replace the waiting one: say so.
            return self._approval_envelope(pending, message=(
                f"您还有一笔待确认的操作：{pending.summary.removeprefix('您正准备进行：')}。"
                "请先在下方确认或取消，再办理新的请求。")), {}
        if control is None:
            return None, {}

        graph = self._graph()
        config = self._config(thread_id)
        state = dict(graph.get_state(config).values or {})
        context = dict(state.get("context") or {})
        has_incomplete_task = bool(context.get("pending_clarification") or context.get("risk_quiz"))
        if pending is None and not has_incomplete_task:
            return None, {}

        if pending is not None:
            # Revoke the actual nonce before touching the graph or preparing
            # another approval; a failed core cancellation leaves it pending.
            cancelled = self.resolve_approval(
                principal=principal, approval_id=pending.approval_id,
                decision="reject", session_token=session_token,
            )
            if control == "cancel":
                return cancelled, {}

        if control == "cancel":
            completed_workflow = (state.get("context") or {}).get("compound_workflow")
            if completed_workflow:
                completed_workflow = json.loads(json.dumps(completed_workflow))
                completed_workflow["steps"][completed_workflow["index"]]["status"] = "CANCELLED"
            cleared = {
                **self._turn_input(principal, text), "request_id": request_id,
                "context": {}, "intent": "UNKNOWN", "target": None,
                "amount": None, "participants": [], "product_ids": [],
                "slots": {}, "scheduled_at": None, "event_at": None,
                "sub_requests": None, "plan": None, "risk_level": "UNKNOWN",
                "final_response": "已取消当前待办操作，没有执行任何资金变更。",
                "business_status": "CANCELLED", "completed_workflow": completed_workflow,
            }
            graph.update_state(config, cleared)
            envelope = self._envelope_from_state(
                conversation_id=conversation_id, state=cleared, request_id=request_id,
            )
            self._audit_response(principal, envelope)
            return envelope, {}

        if pending is not None:
            # The parser's existing slot merger handles short corrections
            # against a normalized payload, never by replaying old raw prose.
            payload = {key: state.get(key) for key in (
                "intent", "target", "amount", "date_scope", "date_range", "extra_info",
                "participants", "product_ids", "slots", "scheduled_at", "event_at",
            )}
            context["pending_clarification"] = {
                "intent": state.get("intent"), "payload": payload,
                "missing": [], "confidence": 0.86, "revision": True,
            }
        return None, {"context": context}

    @staticmethod
    def _audit_response(principal: str, envelope: dict[str, Any], client_request_id: Optional[str] = None) -> None:
        """Record a redacted gateway response; never persist reply or inputs."""
        audit_event(
            "AGENT_RESPONSE", user_id=principal,
            decision="ERROR" if envelope.get("business_status") in {"FAILED", "BLOCKED"} else "ALLOW",
            outcome=str(envelope.get("business_status") or envelope.get("status") or "UNKNOWN"), request_id=envelope.get("request_id"),
            metadata={
                "status": envelope.get("status"), "intent": envelope.get("intent"),
                "risk_level": envelope.get("risk_level"),
                "result_types": sorted((envelope.get("result") or {}).keys()) if isinstance(envelope.get("result"), dict) else [],
                "response_length": len(str(envelope.get("reply") or "")),
                "approval_id": ((envelope.get("approval") or {}).get("approval_id") if isinstance(envelope.get("approval"), dict) else None),
                "client_request_id": client_request_id,
            },
        )

    def _cached_turn(self, principal: str, conversation_id: str, message_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            cached = self._turns.get((principal, conversation_id, message_id))
            if cached is None:
                return None
            envelope = cached[1]
            pending = envelope.get("approval") or {}
            if pending.get("approval_id"):
                approval = self._approvals.get(pending["approval_id"])
                if approval is None or approval.expired:
                    raise ConversationError("审批已超时，请重新发起操作", 410, "APPROVAL_EXPIRED")
                if approval.resolved:
                    raise ConversationError("该审批已处理，请核对当前操作状态", 409, "APPROVAL_ALREADY_RESOLVED")
                return {**envelope, "approval": approval.public_view()}
            return envelope

    def _update_cached_progress(
        self, *, principal: str, conversation_id: str, envelope: dict[str, Any],
        approval_id: Optional[str] = None, operation_id: Optional[str] = None,
    ) -> None:
        """Let redelivery recover the latest outcome without executing again.

        Multiple messages can point at the same pending approval or settlement.
        Advance all of them, preserving their original cache expiry; matching
        the handle keeps later, unrelated conversation turns out of the cache.
        """
        with self._lock:
            for key, (created_at, cached) in self._turns.items():
                if key[:2] != (principal, conversation_id):
                    continue
                same_approval = approval_id and (cached.get("approval") or {}).get("approval_id") == approval_id
                same_operation = operation_id and (cached.get("response") or {}).get("operation_id") == operation_id
                if same_approval or same_operation:
                    self._turns[key] = (created_at, envelope)

    def _remember_turn(self, principal: str, conversation_id: str, message_id: str, envelope: dict[str, Any]) -> None:
        with self._lock:
            self._turns[(principal, conversation_id, message_id)] = (time.time(), envelope)
            self._purge()

    def _settle_turn(self, *, principal: str, conversation_id: str, thread_id: str, graph: Any, config: dict[str, Any], client_request_id: Optional[str] = None, response_request_id: Optional[str] = None) -> dict[str, Any]:
        """Turn a finished (or paused) graph run into the client envelope."""

        interrupt = self._pending_interrupt(graph, config)
        if interrupt is not None:
            approval = self._register_approval(
                principal=principal,
                thread_id=thread_id,
                conversation_id=conversation_id,
                payload=interrupt,
            )
            envelope = self._approval_envelope(approval)
            if response_request_id:
                envelope["request_id"] = response_request_id
            self._audit_response(principal, envelope, client_request_id)
            return envelope
        snapshot = graph.get_state(config)
        envelope = self._envelope_from_state(
            conversation_id=conversation_id,
            state=dict(snapshot.values or {}),
            request_id=None,
        )
        self._audit_response(principal, envelope, client_request_id)
        return envelope

    def start_turn(
        self,
        *,
        principal: str,
        conversation_id: str,
        message_id: str,
        text: str,
        session_token: Optional[str] = None,
        request_id: Optional[str] = None,
        client_request_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Run one user turn, or return the approval the turn is waiting on.

        ``session_token`` is the caller's own credential; it is presented to
        every core call this turn makes, so the core sees the request as the
        signed-in user rather than as a channel asserting an identity.
        """

        self._purge()
        thread_id = conversation_thread_id(principal, conversation_id)
        safe_message_id = sanitize_text(message_id, field="消息编号", max_length=128) or uuid.uuid4().hex

        request_id = request_id or f"gw_{uuid.uuid4().hex}"
        with self._thread_lock(thread_id):
            # Redelivery must also wait for an in-flight approval resolution;
            # otherwise it can return a consumed card before the cache advances.
            cached = self._cached_turn(principal, conversation_id, safe_message_id)
            if cached is not None:
                return cached
            handled, turn_overrides = self._prepare_dialogue_control(
                principal=principal, conversation_id=conversation_id,
                thread_id=thread_id, text=text, request_id=request_id,
                session_token=session_token,
            )
            if handled is not None:
                self._remember_turn(principal, conversation_id, safe_message_id, handled)
                return handled

            graph = self._graph()
            config = self._config(thread_id)
            with channel_session(session_token):
                graph.invoke({**self._turn_input(principal, text), **turn_overrides, "request_id": request_id}, config)
            envelope = self._settle_turn(
                principal=principal,
                conversation_id=conversation_id,
                thread_id=thread_id,
                graph=graph,
                config=config,
                client_request_id=client_request_id,
            )

            self._remember_turn(principal, conversation_id, safe_message_id, envelope)
        return envelope

    def _stream_operation(self, operation: Callable, *, principal: str, request_id: Optional[str] = None) -> Iterator[dict[str, Any]]:
        events: queue.Queue = queue.Queue()

        def worker():
            try:
                with observe_stages(events.put):
                    envelope = operation()
                events.put({"event": "approval" if envelope.get("status") == "APPROVAL_REQUIRED" else "result", "data": envelope})
            except SecurityError as exc:
                code = exc.code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", str(exc.code)) else "REQUEST_REJECTED"
                audit_event("AGENT_STREAM_FAILED", user_id=principal, request_id=request_id, decision="BLOCK", outcome="FAILURE", reason=code)
                message = {"MFA_FAILED": "短信验证码或设备验证未通过，请核对后重试", "CIRCUIT_OPEN": "连续验证失败，操作已暂停，请稍后再试"}.get(code, "请求未完成，请检查当前操作状态后重试")
                events.put({"event": "error", "data": {"code": code, "message": message, "http_status": exc.status_code, "request_id": request_id}})
            except Exception:
                audit_event("AGENT_STREAM_FAILED", user_id=principal, request_id=request_id, decision="BLOCK", outcome="FAILURE", reason="CORE_ERROR")
                events.put({"event": "error", "data": {"code": "CORE_ERROR", "message": "处理失败，请稍后重试", "request_id": request_id}})
            finally:
                events.put(None)

        threading.Thread(target=worker, name="workflow-stream", daemon=True).start()
        # An already-authorized settlement may finish after a disconnected
        # client. It must not be cancelled or reissued with a new key.
        while True:
            try:
                item = events.get(timeout=5)
            except queue.Empty:
                yield {"event": "heartbeat", "data": {"request_id": request_id}}
                continue
            if item is None:
                return
            yield item

    def suggest_followups(self, *, principal: str, conversation_id: str) -> dict[str, Any]:
        """Model-predicted next requests for the latest turn of this caller's conversation.

        Read-only: it never touches the graph. No chips while an approval or the
        risk questionnaire is waiting - those have their own fixed controls.
        """
        from bank_agent.suggestions import followup_suggestions

        thread_id = conversation_thread_id(principal, conversation_id)
        state = dict(self._graph().get_state(self._config(thread_id)).values or {})
        waiting = self._pending_approval(thread_id) is not None or bool(state.get("risk_quiz"))
        suggestions, source = ([], "none") if not state or waiting else followup_suggestions(state)
        # Counts only: the suggested wording is user-facing text, not audit material.
        audit_event("AGENT_SUGGEST", user_id=principal, decision="ALLOW", outcome="SUCCESS",
                    metadata={"count": len(suggestions), "source": source})
        return {"code": 200, "suggestions": suggestions, "source": source}

    def start_turn_stream(self, *, principal: str, conversation_id: str, message_id: str, text: str,
                          session_token: Optional[str] = None, request_id: Optional[str] = None,
                          client_request_id: Optional[str] = None) -> Iterator[dict[str, Any]]:
        return self._stream_operation(lambda: self.start_turn(principal=principal, conversation_id=conversation_id,
            message_id=message_id, text=text, session_token=session_token, request_id=request_id,
            client_request_id=client_request_id), principal=principal, request_id=request_id)
