# ruff: noqa: F811  # imported pytest fixtures are injected by parameter name
"""Real worker negative tests; no live banking services or large allocations."""

from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
import time

import pytest

import sandbox
from test_agent_gateway import gateway  # noqa: F401
from support import login_session


@pytest.mark.parametrize("expression", [
    "__import__('os').system('echo forbidden')", "open('/tmp/bank-secret').read()",
    "__import__('socket').socket()", "__import__('subprocess').run(['whoami'])",
    "data.__class__.__mro__", "[x for x in data]", "(lambda: 1)()",
])
def test_real_worker_revalidates_network_file_process_and_object_escape(expression):
    with ProcessPoolExecutor(max_workers=1) as pool:
        with pytest.raises(sandbox.SandboxError):
            pool.submit(sandbox._evaluate_worker, expression, []).result(timeout=8)


@pytest.mark.parametrize("expression", [
    "len('x' * 10000000)", "len([0] * 1000000)",
    "len('%10000000s' % 'x')", "(2 ** 10000) > 0", "sum([1e308, 1e308])",
])
def test_small_result_cannot_hide_oversized_or_nonfinite_intermediates(expression):
    with pytest.raises(sandbox.SandboxError):
        sandbox.evaluate_expression(expression, [])


def test_rejected_ast_is_counted_without_recording_source_or_data():
    before = sandbox.sandbox_health().get("rejections", 0)
    with pytest.raises(sandbox.SandboxError):
        sandbox.evaluate_expression("open('private-account-marker')", {"private": "bank-value"})
    health = sandbox.sandbox_health()
    assert health.get("rejections", 0) == before + 1
    assert "private-account-marker" not in repr(health) and "bank-value" not in repr(health)


def test_shared_nested_values_cannot_amplify_total_serialized_size():
    with pytest.raises(sandbox.SandboxError):
        sandbox.evaluate_expression("len([data] * 1000)", "x" * 500)


def test_execution_rejection_audit_does_not_include_expression_or_account_data(monkeypatch):
    import security

    events = []
    monkeypatch.setattr(security, "audit_event", lambda *args, **kwargs: events.append((args, kwargs)))
    with pytest.raises(sandbox.SandboxError):
        sandbox.evaluate_expression("open('private-file-marker')", {"account": "private-account-marker"})
    assert events[0][0] == ("SANDBOX_EXECUTION",)
    assert events[0][1]["decision"] == "BLOCK"
    assert "private-file-marker" not in repr(events) and "private-account-marker" not in repr(events)


def test_direct_worker_rejects_output_before_process_transport():
    with ProcessPoolExecutor(max_workers=1) as pool:
        with pytest.raises(sandbox.SandboxError):
            pool.submit(sandbox._evaluate_worker, "[data] * 1000", "x" * 500).result(timeout=8)


def _sleeping_worker(expression, data):
    time.sleep(3)
    return 0


def test_process_timeout_stops_the_actual_running_child(monkeypatch):
    original_executor = sandbox.ProcessPoolExecutor
    processes = []

    class RecordingExecutor(original_executor):
        def submit(self, *args, **kwargs):
            result = super().submit(*args, **kwargs)
            processes.extend(self._processes.values())
            return result

    monkeypatch.setattr(sandbox, "ProcessPoolExecutor", RecordingExecutor)
    monkeypatch.setattr(sandbox, "_evaluate_worker", _sleeping_worker)
    try:
        with pytest.raises(sandbox.SandboxError, match="超时"):
            sandbox.evaluate_expression("1", [], timeout_seconds=0.1)
        assert processes and all(not process.is_alive() for process in processes)
    finally:
        # Baseline fails safely: never leave the deliberately sleeping child.
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)


def test_process_failure_never_runs_expression_in_thread_by_default(monkeypatch):
    class CannotSpawn:
        def __init__(self, **kwargs):
            pass
        def submit(self, *args):
            raise PermissionError("private OS detail")
        def shutdown(self, **kwargs):
            pass

    monkeypatch.setattr(sandbox, "ProcessPoolExecutor", CannotSpawn)
    with pytest.raises(sandbox.SandboxError):
        sandbox.evaluate_expression("len('x' * 10000000)", [])
    assert sandbox.sandbox_health()["last_executor"] == "process"
    assert sandbox.sandbox_health()["os_isolation"] == "none"
    with pytest.raises(sandbox.SandboxError, match="沙箱隔离进程不可用"):
        sandbox.evaluate_expression("sum(data)", [1, 2, 3])


def test_read_only_bill_aggregation_still_uses_a_real_process():
    rows = [{"category": "餐饮", "amount": 100}, {"category": "退款", "amount": -20}]
    original = deepcopy(rows)
    value = sandbox.evaluate_expression(
        "{'spent': spend_total(data, 'amount'), 'refunds': refund_total(data, 'amount'), "
        "'categories': group_spend(data, 'category', 'amount')}", rows,
    )
    assert value == {"spent": 100.0, "refunds": 20.0, "categories": {"餐饮": 100.0}}
    assert rows == original and sandbox.sandbox_health()["last_executor"] == "process"


def test_gateway_bill_sandbox_audit_is_bound_to_verified_subject_and_server_request(gateway, monkeypatch):
    import agent
    import security

    client, core = gateway
    monkeypatch.setattr(agent, "llm", None)
    token = login_session(core)
    response = client.post("/api/v1/agent/messages", json={
        "text": "分析本月账单", "conversation_id": "sandbox-audit-link",
    }, headers={"X-Session-Token": token, "X-User-Id": "user_li", "X-Request-Id": "forged-client-chain"})
    assert response.status_code == 200, response.text
    request_id = response.json()["request_id"]
    assert request_id.startswith("gw_") and request_id != "forged-client-chain"
    events = [event for event in security.get_audit_logs()
              if event["action"] == "SANDBOX_EXECUTION" and event["request_id"] == request_id]
    assert len(events) == 1 and events[0]["user_id"] == "user_zhang"
    assert events[0]["outcome"] == "SUCCESS"
    assert security.verify_audit_chain()["valid"] is True


def test_core_bill_sandbox_audit_keeps_verified_subject_and_analysis_request(gateway):
    import security

    _, core = gateway
    token = login_session(core)
    request_id = "direct-core-sandbox-analysis"
    response = core.get("/api/v1/accounts/user_zhang/bill-analysis", headers={
        "X-Session-Token": token, "X-User-Id": "user_li", "X-Request-Id": request_id,
    })
    assert response.status_code == 200, response.text
    sandbox_events = [event for event in security.get_audit_logs() if event["action"] == "SANDBOX_EXECUTION"]
    assert len(sandbox_events) == 1
    assert sandbox_events[0]["user_id"] == "user_zhang"
    assert sandbox_events[0]["request_id"] == request_id
    assert sandbox_events[0]["outcome"] == "SUCCESS"
    trace = security.get_audit_trace(request_id)
    assert trace["stages"] == ["SANDBOX_EXECUTION", "BILL_ANALYSIS"]
    assert security.verify_audit_chain()["valid"] is True
