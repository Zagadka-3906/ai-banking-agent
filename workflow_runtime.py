"""Bounded model calls and real graph progress, kept outside persisted state."""
from __future__ import annotations

import contextvars
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from typing import Any, Callable, Optional

from security import audit_event

_observer: contextvars.ContextVar[Optional[Callable[[dict], None]]] = contextvars.ContextVar("bank_workflow_observer", default=None)
_model_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="bank-model")
# Two running calls plus eight queued calls. Timed-out running calls retain
# admission until the SDK exits; cancelled queued calls release it immediately.
_model_slots = threading.BoundedSemaphore(10)
_model_admission_lock = threading.Lock()
_model_drained = threading.Condition(_model_admission_lock)
_model_inflight = 0
_UNDERSTANDING_HEDGE_DELAY_SECONDS = 4.0
_understanding_tail_hedge: contextvars.ContextVar[bool] = contextvars.ContextVar('understanding_tail_hedge', default=False)
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


def _submit_model(model: Any, messages: list, *, speculative: bool = False) -> Future | None:
    global _model_inflight
    slots = _model_slots
    submitted = time.monotonic()
    context = contextvars.copy_context()

    def run():
        started = time.monotonic()
        outcome = 'FAILED'
        try:
            response = model.invoke(messages)
            outcome = 'RETURNED'
            return response
        finally:
            audit_event('MODEL_CALL_TIMING', decision='OBSERVE', outcome=outcome,
                        metadata={'queue_ms': int((started-submitted)*1000),
                                  'execution_ms': int((time.monotonic()-started)*1000), 'speculative': speculative})

    def release(_):
        global _model_inflight
        with _model_admission_lock:
            _model_inflight -= 1
            _model_drained.notify_all()
        slots.release()
    with _model_admission_lock:
        # Reserve and submit under the same lock so a new primary cannot
        # occupy the idle worker between the speculative check and submission.
        if speculative and _model_inflight != 1:
            return None
        if not slots.acquire(blocking=False):
            if speculative:
                return None
            raise TimeoutError('MODEL_CAPACITY_EXHAUSTED')
        _model_inflight += 1
        try:
            future = _model_pool.submit(context.run, run)
        except BaseException:
            _model_inflight -= 1
            _model_drained.notify_all()
            slots.release()
            raise
    future.add_done_callback(release)
    return future


def wait_for_model_calls(timeout: float) -> bool:
    """Offline accounting waits for real SDK completion without using late output."""
    with _model_drained:
        return _model_drained.wait_for(lambda: _model_inflight == 0, timeout=max(0, timeout))


def invoke_model(model: Any, messages: list) -> Any:
    # A timed-out SDK call retains its slot until it really exits. Repeated
    # timeouts cannot accumulate an unbounded set of background workers.
    future = _submit_model(model, messages)
    assert future is not None
    try:
        return future.result(timeout=model_timeout())
    except TimeoutError:
        future.cancel()
        raise


@contextmanager
def understanding_tail_hedge():
    """Only pure initial understanding may use an idle speculative worker."""
    token = _understanding_tail_hedge.set(True)
    try:
        yield
    finally:
        _understanding_tail_hedge.reset(token)


def _invoke_understanding_model(model: Any, messages: list) -> Any:
    deadline = time.monotonic() + model_timeout()
    primary = _submit_model(model, messages)
    assert primary is not None
    futures = [primary]
    try:
        try:
            return primary.result(timeout=min(_UNDERSTANDING_HEDGE_DELAY_SECONDS, max(0, deadline-time.monotonic())))
        except TimeoutError:
            if time.monotonic() >= deadline:
                raise
            # Completion can race with expiry of the initial wait. Recover
            # the completed response (even malformed content), or propagate
            # its actual SDK exception; neither needs another model request.
            if primary.done():
                return primary.result(timeout=0)
        secondary = _submit_model(model, messages, speculative=True)
        if secondary is None:
            return primary.result(timeout=max(0, deadline-time.monotonic()))
        futures.append(secondary)
        audit_event('MODEL_UNDERSTANDING_HEDGE', decision='READ_ONLY', outcome='STARTED',
                    metadata={'attempts': 2, 'delay_ms': int(_UNDERSTANDING_HEDGE_DELAY_SECONDS*1000)})
        pending = set(futures)
        first_error: Exception | None = None
        while pending:
            done, pending = wait(pending, timeout=max(0, deadline-time.monotonic()), return_when=FIRST_COMPLETED)
            if not done:
                raise TimeoutError('MODEL_DEADLINE_EXCEEDED')
            for future in futures:
                if future not in done:
                    continue
                try:
                    result = future.result()
                except Exception as error:
                    first_error = first_error or error
                else:
                    # Take the first transport response, including malformed
                    # content. Never compare answers or select by a score.
                    audit_event('MODEL_UNDERSTANDING_HEDGE', decision='READ_ONLY', outcome='RETURNED',
                                metadata={'attempt': futures.index(future)+1})
                    return result
        assert first_error is not None
        raise first_error
    finally:
        for future in futures:
            # Running calls retain their admission until their SDK really exits.
            future.cancel()


def invoke_json_model(model: Any, messages: list) -> Any:
    """Request an object on the wire; keep admission, timeouts and validation.

    Local injected callables need only implement invoke. SDK adapters that
    expose bind must honor the format or fail; never retry as free-form text.
    """
    from langchain_core.messages import SystemMessage

    instruction = '\nReturn one valid JSON object, with no prose outside the JSON object.'
    structured = list(messages)
    if structured and isinstance(structured[0], SystemMessage) and isinstance(structured[0].content, str):
        structured[0] = structured[0].model_copy(update={'content': structured[0].content + instruction})
    else:
        structured.insert(0, SystemMessage(content=instruction))
    bind = getattr(model, 'bind', None)
    target = bind(response_format={'type': 'json_object'}) if callable(bind) else model
    return _invoke_understanding_model(target, structured) if _understanding_tail_hedge.get() else invoke_model(target, structured)


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
