"""Bounded model calls and real graph progress, kept outside persisted state."""
from __future__ import annotations

import contextvars
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Callable, Optional

from security import audit_event

_observer: contextvars.ContextVar[Optional[Callable[[dict], None]]] = contextvars.ContextVar("bank_workflow_observer", default=None)
_model_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="bank-model")
# Two running calls plus eight queued calls. Timed-out running calls retain
# admission until the SDK exits; cancelled queued calls release it immediately.
_model_slots = threading.BoundedSemaphore(10)
STAGES = {
    "intent_parser": "理解请求", "risk_checker": "核验业务条件",
    "human_review": "确认操作", "executor": "办理业务",
    "responder": "核对回执", "workflow_next": "推进任务",
}


def model_timeout() -> float:
    try:
        return max(0.1, min(float(os.getenv("BANK_MODEL_TIMEOUT_SECONDS", "12")), 60.0))
    except ValueError:
        return 12.0


def invoke_model(model: Any, messages: list) -> Any:
    # A timed-out SDK call retains its slot until it really exits. Repeated
    # timeouts cannot accumulate an unbounded set of background workers.
    if not _model_slots.acquire(blocking=False):
        raise TimeoutError("MODEL_CAPACITY_EXHAUSTED")
    context = contextvars.copy_context()
    try:
        future = _model_pool.submit(context.run, model.invoke, messages)
    except BaseException:
        _model_slots.release()
        raise
    future.add_done_callback(lambda _: _model_slots.release())
    try:
        return future.result(timeout=model_timeout())
    except TimeoutError:
        future.cancel()
        raise


@contextmanager
def observe_stages(callback: Callable[[dict], None]):
    token = _observer.set(callback)
    try:
        yield
    finally:
        _observer.reset(token)


def instrument(name: str, function: Callable):
    def run(state):
        started = time.monotonic()
        compound = (state.get("context") or {}).get("compound_workflow") or {}
        step = int(compound.get("index", 0)) + 1

        def emit(phase):
            data = {"stage": name, "label": STAGES[name], "phase": phase,
                    "elapsed_ms": int((time.monotonic() - started) * 1000), "step": step}
            callback = _observer.get()
            if callback:
                callback({"event": "stage", "data": data})
            audit_event("WORKFLOW_STAGE", user_id=state.get("user_id"),
                        request_id=state.get("request_id"), decision="OBSERVE",
                        outcome=phase.upper(), metadata=data)

        emit("started")
        try:
            result = function(state)
        except BaseException as exc:
            # LangGraph's interrupt is control flow, never a failed operation.
            emit("waiting" if type(exc).__name__ == "GraphInterrupt" else "failed")
            raise
        if name == "responder" and result.get("business_status") is None:
            if state.get("business_status"):
                result["business_status"] = state["business_status"]
            elif not state.get("final_response") and state.get("execution_result"):
                result["business_status"] = "SUCCEEDED"
        if name == "risk_checker" and result.get("risk_level") == "ERROR":
            result["business_status"] = "BLOCKED"
        emit("failed" if result.get("business_status") in {"FAILED", "BLOCKED"} else "completed")
        return result
    return run
