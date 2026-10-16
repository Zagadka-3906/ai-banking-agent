"""Approval handling: handles, OTP and device challenges, resume after a decision, reconciliation."""

from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from bank_tools import BankAPIError
from workflow_contract import reply_contract
from security import audit_event

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
from bank_agent.conversation_base import (  # noqa: F401
    APPROVAL_MAX_ATTEMPTS,
    APPROVAL_TTL_SECONDS,
    ConversationError,
    conversation_thread_id,
)
from bank_agent.approval_store import (  # noqa: F401
    Approval,
    _persist_approvals_locked,
)


class ApprovalsMixin:
    """Mixed into ``conversation.ConversationService``; uses its state and ``_graph()``."""

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

    def _approval_envelope(self, approval: Approval, message: Optional[str] = None) -> dict[str, Any]:
        state = dict(self._graph().get_state(self._config(approval.thread_id)).values or {})
        response = reply_contract(state, message or approval.summary, "WAITING_APPROVAL")
        return {
            "code": 200,
            "conversation_id": approval.conversation_id,
            "status": "APPROVAL_REQUIRED",
            "turn_status": "WAITING_APPROVAL",
            "business_status": "WAITING_APPROVAL",
            "response": response,
            "reply": message or approval.summary,
            "request_id": approval.request_id,
            "intent": None,
            "risk_level": approval.risk_level,
            "approval": approval.public_view(),
            "result": {"execution_results": [item["data"] for item in response["sections"]], "workflow_plan": response["workflow_plan"]} if response["sections"] or response["workflow_plan"] else None,
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
            _persist_approvals_locked(self._approvals)
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

    def resolve_approval_stream(self, **kwargs) -> Iterator[dict[str, Any]]:
        self._require_approval(str(kwargs["principal"]), str(kwargs["approval_id"]))
        return self._stream_operation(lambda: self.resolve_approval(**kwargs), principal=kwargs["principal"])

    def reconcile_operation(self, *, principal: str, conversation_id: str, operation_id: str, session_token: Optional[str] = None) -> dict[str, Any]:
        from agent import _advance_plan, responder_node
        thread_id = conversation_thread_id(principal, conversation_id)
        graph = self._graph()
        config = self._config(thread_id)
        with self._thread_lock(thread_id):
            state = dict(graph.get_state(config).values or {})
            record = state.get("reconciliation") or {}
            if not record or record.get("operation_id") != operation_id:
                raise ConversationError("未找到本会话待核对的操作", 404, "OPERATION_NOT_FOUND")
            try:
                status = self._client(principal, session_token).operation_status(operation_id, record["endpoint"], request_id=state.get("request_id"))
            except BankAPIError:
                return self._envelope_from_state(conversation_id=conversation_id, state=state, request_id=None)
            if state.get("business_status") != "PENDING_RECONCILIATION":
                return self._envelope_from_state(conversation_id=conversation_id, state=state, request_id=None)
            if status.get("status") != "COMPLETED" or not isinstance(status.get("receipt"), dict):
                return self._envelope_from_state(conversation_id=conversation_id, state=state, request_id=None)
            result = {"type": record["result_type"], "data": status["receipt"]}
            checked = responder_node({**state, "business_status": None, "execution_result": result, "execution_results": None, "final_response": None})
            if checked.get("business_status") == "FAILED":
                return self._envelope_from_state(conversation_id=conversation_id, state=state, request_id=None)
            graph.update_state(config, {"execution_result": result, "execution_results": None, "final_response": None,
                                       "business_status": None, "plan": _advance_plan(state, str(state.get("intent")), outcome="SUCCESS")}, as_node="executor")
            with channel_session(session_token):
                graph.invoke(None, config)
            envelope = self._settle_turn(principal=principal, conversation_id=conversation_id, thread_id=thread_id,
                                        graph=graph, config=config, client_request_id=None, response_request_id=state.get("request_id"))
            self._update_cached_progress(principal=principal, conversation_id=conversation_id,
                                         operation_id=operation_id, envelope=envelope)
            return envelope

    def demo_otp(self, *, principal: str, approval_id: str, session_token: Optional[str] = None) -> dict[str, Any]:
        approval = self._require_approval(principal, approval_id)
        if approval.risk_level != "RED" or not approval.auth_token:
            raise ConversationError("当前审批无需或不能获取验证码", 400, "MFA_NOT_REQUIRED")
        try:
            response = self._client(principal, session_token).request_demo_otp(approval.auth_token, request_id=approval.request_id)
        except BankAPIError as exc:
            raise self._core_failure(exc) from exc
        return {"approval_id": approval_id, "demo_code": response.get("demo_code"),
                "expires_in": response.get("expires_in"), "delivery": "local_demo"}

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
                self._mark_resolved(approval)
                raise ConversationError("审批已超时，请重新发起操作", 410, "APPROVAL_EXPIRED")

            if normalized == "reject":
                # A superseded card must lose its actual core authorization,
                # not merely disappear from the channel's approval registry.
                if approval.auth_token:
                    try:
                        cancelled = self._client(principal, session_token).cancel(
                            approval.auth_token, request_id=approval.request_id,
                        )
                    except BankAPIError as exc:
                        raise self._core_failure(exc) from exc
                    if not isinstance(cancelled, dict) or cancelled.get("cancelled") is not True:
                        raise ConversationError("核心服务未确认取消，请稍后重试", 502, "CORE_PROTOCOL_ERROR")
                self._mark_resolved(approval)
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
                self._mark_resolved(approval)
                raise ConversationError("审批尝试次数过多，请重新发起操作", 429, "APPROVAL_ATTEMPTS_EXHAUSTED")
            if not approval.auth_token:
                self._mark_resolved(approval)
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

            self._mark_resolved(approval)
            return self._resume(approval, resume_payload, session_token)

    def _mark_resolved(self, approval: Approval) -> None:
        """Take the handle out of circulation and write that through to disk.

        Every terminal transition (reject, expiry, exhausted attempts, settled)
        must persist before the caller hears about it: a resolved approval that
        came back as pending after a restart would invite a second decision on
        a nonce the core has already consumed or invalidated.
        """

        approval.resolved = True
        with self._lock:
            _persist_approvals_locked(self._approvals)

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
        graph = self._graph()
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
        pending = self._pending_interrupt(graph, config)
        if pending is not None:
            if not payload.get("confirmed") or pending.get("auth_token") == approval.auth_token:
                raise ConversationError("审批未生效，操作仍在等待确认", 409, "APPROVAL_STILL_PENDING")
            envelope = self._settle_turn(principal=approval.principal, conversation_id=approval.conversation_id,
                                        thread_id=approval.thread_id, graph=graph, config=config, client_request_id=None, response_request_id=approval.request_id)
            self._update_cached_progress(principal=approval.principal, conversation_id=approval.conversation_id,
                                         approval_id=approval.approval_id, envelope=envelope)
            return envelope
        if not payload.get("confirmed"):
            # Neither bare amounts nor pronouns after cancellation may revive
            # an operation the user just explicitly abandoned.
            completed_workflow = (state.get("context") or {}).get("compound_workflow")
            if completed_workflow:
                completed_workflow = json.loads(json.dumps(completed_workflow))
                completed_workflow["steps"][completed_workflow["index"]]["status"] = "CANCELLED"
            cleared = {
                "context": {}, "auth_token": None, "mfa_proof": None,
                "user_confirmed": False, "clarification_required": False,
                "plan": None,
                "business_status": "CANCELLED",
                "completed_workflow": completed_workflow,
            }
            graph.update_state(config, cleared)
            state.update(cleared)
        envelope = self._envelope_from_state(
            conversation_id=approval.conversation_id,
            state=state,
            request_id=approval.request_id,
        )
        self._audit_response(approval.principal, envelope)
        self._update_cached_progress(principal=approval.principal, conversation_id=approval.conversation_id,
                                     approval_id=approval.approval_id, envelope=envelope)
        return envelope

    @staticmethod
    def _core_failure(exc: BankAPIError) -> ConversationError:
        payload = exc.payload if isinstance(exc.payload, dict) else {}
        detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
        code = str(detail.get("code") or "CORE_REJECTED")
        message = str(detail.get("message") or exc)
        status = exc.status_code or 502
        return ConversationError(message, status, code)
