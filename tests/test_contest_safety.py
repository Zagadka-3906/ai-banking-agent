"""Contest improvements must preserve the bank's authorization boundary."""
from concurrent.futures import ProcessPoolExecutor

import pytest
from fastapi.testclient import TestClient

import main
from bank_core.routes import tasks as task_routes
import security
from sandbox_limits import apply_worker_limits


@pytest.fixture()
def core():
    main.reset_state()
    with TestClient(main.app) as client:
        yield client
    main.reset_state()


def red_token(core):
    result = core.post("/api/v1/risk/precheck", headers={"X-User-Id": "user_zhang"},
                       json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 2000, "remark": ""})
    assert result.status_code == 200
    return result.json()["auth_token"]


def test_otp_bound_to_approval_expires_and_never_logs_plaintext(core, monkeypatch):
    token = red_token(core)
    other = red_token(core)
    code = security.issue_demo_otp(token, user_id="user_zhang")["demo_code"]
    payload, record = security.lookup_auth_locked(token)
    other_payload, other_record = security.lookup_auth_locked(other)
    ctx = {"otp": code, "auth_payload": payload, "auth_record": record}
    assert security._factor_sms_otp(ctx)
    monkeypatch.setenv("BANK_MFA_DYNAMIC_OTP", "1")
    assert not security._factor_sms_otp({"otp": code, "auth_payload": other_payload, "auth_record": other_record})
    assert code not in repr(security.AUDIT_LOGS)
    assert "otp_digest" in record and code not in repr(record)
    record["otp_expires_at"] = 0
    assert not security._factor_sms_otp(ctx)
    assert not security._factor_sms_otp({**ctx, "otp": security.MFA_TEST_OTP})


def test_otp_issuance_cannot_reset_failure_budget(core):
    token = red_token(core)
    security.issue_demo_otp(token, user_id="user_zhang")
    with pytest.raises(security.SecurityError) as caught:
        security.issue_demo_otp(token, user_id="user_zhang")
    assert caught.value.code == "OTP_RATE_LIMITED"
    _, record = security.lookup_auth_locked(token)
    record["mfa_attempts"] = 3
    record["otp_issued_at"] = 0
    with pytest.raises(security.SecurityError):
        security.issue_demo_otp(token, user_id="user_zhang")
    assert record["mfa_attempts"] == 3


def test_otp_rejects_other_owner_and_non_demo(core, monkeypatch):
    token = red_token(core)
    with pytest.raises(security.SecurityError):
        security.issue_demo_otp(token, user_id="user_li")
    monkeypatch.setenv("BANK_DEMO_MODE", "0")
    with pytest.raises(security.SecurityError):
        security.issue_demo_otp(token, user_id="user_zhang")


def test_takeover_pauses_dependent_branch_without_releasing_funds(core):
    root = main.TASK_STORE.create("user_zhang", "REMINDER", {"lock_id": "L1"})
    child = main.TASK_STORE.create("user_zhang", "ORDER_DRAFT", {"lock_id": "L1"}, depends_on=[root.task_id])
    grandchild = main.TASK_STORE.create("user_zhang", "REMINDER", {}, depends_on=[child.task_id])
    unrelated = main.TASK_STORE.create("user_li", "REMINDER", {"lock_id": "L1"})
    before = dict(main.ACCOUNTS["user_zhang"])
    response = core.post(f"/api/v1/tasks/{root.task_id}/takeover", headers={"X-User-Id": "user_zhang"}, json={"reason": "需要人工核对"})
    assert response.status_code == 200, response.text
    assert {root.status, child.status, grandchild.status} == {"HUMAN_TAKEOVER"}
    assert unrelated.status == "PENDING"
    assert main.ACCOUNTS["user_zhang"] == before
    assert main.TASK_STORE.ready() == [unrelated]


def test_takeover_rejects_running_branch_without_partial_mutation(core):
    root = main.TASK_STORE.create("user_zhang", "REMINDER", {})
    child = main.TASK_STORE.create("user_zhang", "REMINDER", {}, depends_on=[root.task_id])
    child.status = "RUNNING"
    response = core.post(f"/api/v1/tasks/{root.task_id}/takeover", headers={"X-User-Id": "user_zhang"}, json={})
    assert response.status_code == 409
    assert root.status == "PENDING" and child.status == "RUNNING"


def test_task_branch_is_restored_if_persistence_fails(core, monkeypatch):
    root = main.TASK_STORE.create("user_zhang", "REMINDER", {})
    child = main.TASK_STORE.create("user_zhang", "REMINDER", {}, depends_on=[root.task_id])
    def fail():
        raise OSError("test disk failure")
    monkeypatch.setattr(task_routes, "persist_ledger", fail)
    with pytest.raises(OSError):
        main._task_action(root.task_id, "TASK_TAKEOVER", main.TaskActionRequest(), "user_zhang", "test-persist")
    assert main.TASK_STORE.get(root.task_id).status == main.TASK_STORE.get(child.task_id).status == "PENDING"
    assert security.AUDIT_LOGS[-1]["outcome"] == "PERSISTENCE_FAILED_ROLLBACK"


def test_actual_worker_installs_os_resource_limits():
    with ProcessPoolExecutor(max_workers=1) as pool:
        assert pool.submit(apply_worker_limits).result(timeout=10) in {"windows_job", "posix_rlimit"}


def _probe_memory_limit():
    apply_worker_limits()
    allocations = []
    try:
        for _ in range(24):
            allocations.append(bytearray(32 * 1024 * 1024))
    except MemoryError:
        return True
    return False


def test_os_memory_limit_actually_rejects_excess_allocation():
    with ProcessPoolExecutor(max_workers=1) as pool:
        assert pool.submit(_probe_memory_limit).result(timeout=15) is True
