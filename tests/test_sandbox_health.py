import sandbox


def _reset_telemetry() -> None:
    with sandbox._TELEMETRY_LOCK:
        sandbox._TELEMETRY.update(
            {
                "last_executor": "none",
                "last_fallback_reason": None,
                "os_isolation": "none",
                "process_successes": 0,
                "thread_successes": 0,
                "fallbacks": 0,
                "timeouts": 0,
                "failures": 0,
                "last_transition_timestamp": None,
            }
        )


def test_health_initial_state_is_unobserved(monkeypatch):
    _reset_telemetry()
    health = sandbox.sandbox_health()
    assert health["preferred_executor"] == "process"
    assert health["last_executor"] == "none"
    assert health["os_isolation"] == "none"
    assert health["last_fallback_reason"] is None
    assert health["last_transition_timestamp"] is None
    assert health["fallbacks"] == 0


def test_process_success_updates_health():
    _reset_telemetry()
    assert sandbox.evaluate_expression("spend_total(data, 'amount')", [{"amount": 3}]) == 3.0
    health = sandbox.sandbox_health()
    assert health["last_executor"] == "process"
    assert health["os_isolation"] == "process"
    assert health["process_successes"] == 1
    assert health["thread_successes"] == 0
    assert health["last_transition_timestamp"] is not None


class _ImmediateFuture:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    def result(self, timeout=None):
        if self._error:
            raise self._error
        return self._result

    def cancel(self):
        return True


class _ErrorFuture:
    def __init__(self, error):
        self._error = error

    def result(self, timeout=None):
        del timeout
        raise self._error

    def cancel(self):
        return True


class _SubmitFailureExecutor:
    def __init__(self, max_workers=1, mp_context=None):
        del max_workers, mp_context

    def submit(self, fn, expression, data):
        del fn, expression, data
        raise PermissionError("private detail")

    def shutdown(self, **kwargs):
        del kwargs


class _ThreadExecutor:
    def submit(self, fn, expression, data):
        return _ImmediateFuture(fn(expression, data))

    def shutdown(self, **kwargs):
        del kwargs


class _BrokenProcessExecutor:
    def __init__(self, max_workers=1, mp_context=None):
        del max_workers, mp_context

    def submit(self, fn, expression, data):
        del fn, expression, data
        return _ErrorFuture(sandbox.BrokenProcessPool("private process detail"))

    def shutdown(self, **kwargs):
        del kwargs


class _TimeoutThreadExecutor:
    def submit(self, fn, expression, data):
        del fn, expression, data
        return _ErrorFuture(sandbox.FutureTimeoutError())

    def shutdown(self, **kwargs):
        del kwargs


class _FailureThreadExecutor:
    def submit(self, fn, expression, data):
        del fn, expression, data
        return _ErrorFuture(RuntimeError("private thread detail"))

    def shutdown(self, **kwargs):
        del kwargs


def test_submit_failure_is_reported_without_exception_details(monkeypatch):
    _reset_telemetry()
    monkeypatch.setenv("BANK_SANDBOX_ALLOW_THREAD_FALLBACK", "1")
    monkeypatch.setattr(sandbox, "ProcessPoolExecutor", _SubmitFailureExecutor)
    monkeypatch.setattr(sandbox, "_thread_fallback_executor", lambda previous: _ThreadExecutor())
    assert sandbox.evaluate_expression("len(data)", [1, 2]) == 2
    health = sandbox.sandbox_health()
    assert health["last_executor"] == "thread_fallback"
    assert health["os_isolation"] == "none"
    assert health["last_fallback_reason"] == "submit_failure"
    assert health["fallbacks"] == 1
    assert health["thread_successes"] == 1
    assert "private detail" not in repr(health)


def test_broken_process_falls_back_to_thread(monkeypatch):
    _reset_telemetry()
    monkeypatch.setenv("BANK_SANDBOX_ALLOW_THREAD_FALLBACK", "1")
    monkeypatch.setattr(sandbox, "ProcessPoolExecutor", _BrokenProcessExecutor)
    monkeypatch.setattr(sandbox, "_thread_fallback_executor", lambda previous: _ThreadExecutor())
    assert sandbox.evaluate_expression("len(data)", [1, 2]) == 2
    health = sandbox.sandbox_health()
    assert health["last_executor"] == "thread_fallback"
    assert health["os_isolation"] == "none"
    assert health["last_fallback_reason"] == "broken_process_pool"
    assert health["fallbacks"] == 1
    assert health["thread_successes"] == 1
    assert health["timeouts"] == 0
    assert health["failures"] == 0
    assert "private process detail" not in repr(health)


def test_thread_fallback_timeout_is_counted(monkeypatch):
    _reset_telemetry()
    monkeypatch.setenv("BANK_SANDBOX_ALLOW_THREAD_FALLBACK", "1")
    monkeypatch.setattr(sandbox, "ProcessPoolExecutor", _SubmitFailureExecutor)
    monkeypatch.setattr(sandbox, "_thread_fallback_executor", lambda previous: _TimeoutThreadExecutor())
    try:
        sandbox.evaluate_expression("len(data)", [1], timeout_seconds=0.1)
    except sandbox.SandboxError as exc:
        assert str(exc) == "沙箱执行超时"
    else:
        raise AssertionError("expected sandbox timeout")
    health = sandbox.sandbox_health()
    assert health["last_executor"] == "thread_fallback"
    assert health["os_isolation"] == "none"
    assert health["last_fallback_reason"] == "submit_failure"
    assert health["fallbacks"] == 1
    assert health["thread_successes"] == 0
    assert health["timeouts"] == 1
    assert health["failures"] == 0


def test_thread_fallback_failure_is_counted_and_sanitized(monkeypatch):
    _reset_telemetry()
    monkeypatch.setenv("BANK_SANDBOX_ALLOW_THREAD_FALLBACK", "1")
    monkeypatch.setattr(sandbox, "ProcessPoolExecutor", _SubmitFailureExecutor)
    monkeypatch.setattr(sandbox, "_thread_fallback_executor", lambda previous: _FailureThreadExecutor())
    try:
        sandbox.evaluate_expression("len(data)", [1])
    except sandbox.SandboxError as exc:
        assert str(exc) == "沙箱执行失败: RuntimeError"
        assert "private thread detail" not in str(exc)
    else:
        raise AssertionError("expected sandbox failure")
    health = sandbox.sandbox_health()
    assert health["last_executor"] == "thread_fallback"
    assert health["os_isolation"] == "none"
    assert health["last_fallback_reason"] == "submit_failure"
    assert health["fallbacks"] == 1
    assert health["thread_successes"] == 0
    assert health["timeouts"] == 0
    assert health["failures"] == 1


def test_process_spawn_failure_is_closed_by_default(monkeypatch):
    _reset_telemetry()
    monkeypatch.delenv("BANK_SANDBOX_ALLOW_THREAD_FALLBACK", raising=False)
    monkeypatch.setattr(sandbox, "ProcessPoolExecutor", _SubmitFailureExecutor)
    try:
        sandbox.evaluate_expression("len(data)", [1, 2])
    except sandbox.SandboxError as exc:
        assert str(exc) == "沙箱隔离进程不可用"
    else:
        raise AssertionError("expected process isolation failure")
    health = sandbox.sandbox_health()
    assert health["fallbacks"] == 0
    assert health["thread_fallback_enabled"] is False
    assert health["failures"] == 1
