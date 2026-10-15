"""Task queues and settlement receipts must agree with the durable ledger."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timedelta, timezone
import sys
import json
import threading

import pytest
from fastapi.testclient import TestClient

import main


HEADERS = {"X-User-Id": "user_zhang"}


@pytest.fixture()
def durable(tmp_path, monkeypatch):
    main.reset_state()
    path = tmp_path / "ledger.json"
    monkeypatch.setenv("BANK_LEDGER_STORE_PATH", str(path))
    with TestClient(main.app) as client:
        yield client, path
    main.reset_state()


def restart():
    main.reset_state()
    assert main.load_ledger_store()["loaded_state"] is True


def confirm(client, token):
    response = client.post("/api/v1/auth/confirm", json={}, headers={**HEADERS, "X-Auth-Token": token})
    assert response.status_code == 200, response.text


def schedule(client):
    body = {"user_id": "user_zhang", "operation": "SCHEDULED_TRANSFER", "target": "user_li", "amount": 100,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}
    pre = client.post("/api/v1/risk/precheck/operation", json=body, headers=HEADERS)
    assert pre.status_code == 200, pre.text
    token = pre.json()["auth_token"]
    confirm(client, token)
    result = client.post("/api/v1/operations/execute", json={**body, "auth_token": token}, headers=HEADERS)
    assert result.status_code == 200, result.text
    return result.json()["data"]["task_id"]


def test_scheduled_task_and_settlement_receipt_survive_restarts(durable):
    client, _ = durable
    task_id = schedule(client)
    original = main.TASK_STORE.get(task_id).to_dict()
    restart()
    assert main.TASK_STORE.get(task_id).to_dict() == original
    main.TASK_STORE.get(task_id).run_at = "2000-01-01T00:00:00+00:00"
    due = client.post("/api/v1/tasks/run-due", headers=HEADERS)
    assert due.status_code == 200, due.text
    restart()
    assert main.TASK_STORE.get(task_id).status == "WAITING_USER"

    pre = client.post(f"/api/v1/tasks/{task_id}/precheck", json={"user_id": "user_zhang"}, headers=HEADERS)
    assert pre.status_code == 200, pre.text
    token = pre.json()["auth_token"]
    confirm(client, token)
    headers = {**HEADERS, "Idempotency-Key": "persistent-task-settlement"}
    body = {"user_id": "user_zhang", "auth_token": token}
    settled = client.post(f"/api/v1/tasks/{task_id}/execute", json=body, headers=headers)
    assert settled.status_code == 200, settled.text
    balance = main.ACCOUNTS["user_zhang"]["balance"]
    transaction_count = len(main.TRANSACTIONS)
    restart()
    replay = client.post(f"/api/v1/tasks/{task_id}/execute", json=body, headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json() == settled.json()
    assert main.ACCOUNTS["user_zhang"]["balance"] == balance
    assert len(main.TRANSACTIONS) == transaction_count
    assert main.TASK_STORE.get(task_id).status == "SUCCEEDED"
    receipt_url = "/api/v1/operations/persistent-task-settlement?endpoint=task_execute"
    assert client.get(receipt_url, headers=HEADERS).json()["receipt"] == settled.json()
    assert client.get(receipt_url, headers={"X-User-Id": "user_li"}).json()["status"] == "UNKNOWN"


def test_reservation_dependencies_and_task_ids_survive_restart(durable):
    client, _ = durable
    event_at = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    body = {"user_id": "user_zhang", "amount": 100, "event_at": event_at, "reason": "reservation test"}
    pre = client.post("/api/v1/risk/precheck/lock", json=body, headers=HEADERS)
    assert pre.status_code == 200, pre.text
    token = pre.json()["auth_token"]
    confirm(client, token)
    result = client.post("/api/v1/accounts/lock_fund", json={**body, "reminder_at": pre.json()["reminder_at"], "auth_token": token}, headers=HEADERS)
    assert result.status_code == 200, result.text
    records = main.TASK_STORE.export_records()
    assert len(records) == 4
    restart()
    assert main.TASK_STORE.export_records() == records
    data = result.json()
    for child_id in [data["reminder_task_id"], *data["order_task_ids"]]:
        assert main.TASK_STORE.get(child_id).depends_on == [data["task_id"]]
    assert main.ACCOUNTS["user_zhang"]["locked_amount"] == 100


@pytest.mark.parametrize("action,status", [("cancel", "CANCELLED"), ("takeover", "HUMAN_TAKEOVER")])
def test_task_actions_are_durable(durable, action, status):
    client, _ = durable
    task_id = schedule(client)
    response = client.post(f"/api/v1/tasks/{task_id}/{action}", json={}, headers=HEADERS)
    assert response.status_code == 200, response.text
    restart()
    assert main.TASK_STORE.get(task_id).status == status
    if action == "cancel":
        assert main.SCHEDULED_TRANSFERS[0]["status"] == "CANCELLED"
        assert main._pending_transfer_amount("user_zhang") == 0


def test_renewal_reminder_creation_is_durable_and_deduplicated(durable):
    client, _ = durable
    first = client.post("/api/v1/subscriptions/user_zhang/reminders", headers=HEADERS)
    assert first.status_code == 200, first.text
    assert first.json()["data"]
    restart()
    repeated = client.post("/api/v1/subscriptions/user_zhang/reminders", headers=HEADERS)
    assert repeated.json()["data"] == first.json()["data"]


def test_failed_settlement_restores_queue_and_does_not_write_receipt(durable, monkeypatch):
    client, path = durable
    task_id = schedule(client)
    main.TASK_STORE.get(task_id).run_at = "2000-01-01T00:00:00+00:00"
    assert client.post("/api/v1/tasks/run-due", headers=HEADERS).status_code == 200
    pre = client.post(f"/api/v1/tasks/{task_id}/precheck", json={"user_id": "user_zhang"}, headers=HEADERS)
    assert pre.status_code == 200, pre.text
    token = pre.json()["auth_token"]
    confirm(client, token)
    before = (deepcopy(main.ACCOUNTS), deepcopy(main.SCHEDULED_TRANSFERS), main.TASK_STORE.export_records(), path.read_bytes())
    transition = main.TASK_STORE.transition

    def fail_after_debit(task_id, status, **kwargs):
        if status == "SUCCEEDED":
            raise RuntimeError("simulated task-store failure")
        return transition(task_id, status, **kwargs)

    monkeypatch.setattr(main.TASK_STORE, "transition", fail_after_debit)
    result = client.post(f"/api/v1/tasks/{task_id}/execute", json={"user_id": "user_zhang", "auth_token": token},
                         headers={**HEADERS, "Idempotency-Key": "failed-settlement"})
    assert result.status_code == 500
    assert (main.ACCOUNTS, main.SCHEDULED_TRANSFERS, main.TASK_STORE.export_records(), path.read_bytes()) == before
    assert not main.AUTH_TOKENS[token]["used"]
    assert not main.OPERATION_RECEIPTS


@pytest.mark.parametrize("scheduled", [False, True])
@pytest.mark.parametrize("parallel_failure", ["missing_task", "invalid_transfer"])
def test_concurrent_failed_request_cannot_detach_settlement_accounts(durable, monkeypatch, scheduled, parallel_failure):
    client, _ = durable
    if scheduled:
        task_id = schedule(client)
        main.TASK_STORE.get(task_id).run_at = "2000-01-01T00:00:00+00:00"
        assert client.post("/api/v1/tasks/run-due", headers=HEADERS).status_code == 200
        pre = client.post(f"/api/v1/tasks/{task_id}/precheck", json={"user_id": "user_zhang"}, headers=HEADERS)
        execute_url = f"/api/v1/tasks/{task_id}/execute"
        body = {"user_id": "user_zhang"}
    else:
        body = {"from_user": "user_zhang", "target_name_or_id": "user_li", "amount": 100}
        pre = client.post("/api/v1/risk/precheck", json=body, headers=HEADERS)
        execute_url = "/api/v1/transfers/execute"
    assert pre.status_code == 200, pre.text
    token = pre.json()["auth_token"]
    confirm(client, token)

    resolved = threading.Event()
    resume = threading.Event()
    resolve_target = main._resolve_target

    def pause_after_reading_payee(*args, **kwargs):
        result = resolve_target(*args, **kwargs)
        if not resolved.is_set():
            resolved.set()
            assert resume.wait(5), "settlement was not released"
        return result

    # Patch every module that resolves payees, as the single-module patch did.
    for module in list(sys.modules.values()):
        if getattr(module, "__name__", "").startswith("bank_core") and getattr(module, "_resolve_target", None) is resolve_target:
            monkeypatch.setattr(module, "_resolve_target", pause_after_reading_payee)
    with ThreadPoolExecutor(max_workers=2) as pool:
        execution = pool.submit(client.post, execute_url, json={**body, "auth_token": token},
                                headers={**HEADERS, "X-Auth-Token": token, "Idempotency-Key": "concurrent-settlement"})
        try:
            assert resolved.wait(5), "settlement did not resolve its payee"
            if parallel_failure == "missing_task":
                failed = pool.submit(client.post, "/api/v1/tasks/no-such-task/cancel", json={}, headers=HEADERS)
            else:
                failed = pool.submit(client.post, "/api/v1/transfers/execute",
                                     json={"from_user": "user_zhang", "target_name_or_id": "user_li", "amount": 1},
                                     headers={**HEADERS, "X-Auth-Token": "invalid-token"})
            # Before the fix the failed request can roll the ledger back while
            # settlement holds its old receiver. Correct locking makes it wait.
            try:
                failed.result(timeout=0.1)
            except TimeoutError:
                pass
        finally:
            resume.set()
        settled = execution.result(timeout=5)
        failure = failed.result(timeout=5)
    assert settled.status_code == 200, settled.text
    assert failure.status_code in {403, 404}
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14900
    assert main.ACCOUNTS["user_li"]["balance"] == 2100
    assert sum(main.ACCOUNTS[user]["balance"] for user in ("user_zhang", "user_li")) == 17000
    if scheduled:
        assert main.TASK_STORE.get(task_id).status == "SUCCEEDED"


@pytest.mark.parametrize("damage", ["missing_dependency", "foreign_owner", "invalid_attempts", "invalid_timestamp", "duplicate"])
def test_invalid_task_snapshot_rejects_entire_ledger(durable, damage):
    client, path = durable
    schedule(client)
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    rows = snapshot["state"]["TASK_STORE"]
    if damage == "missing_dependency":
        rows[0]["depends_on"] = ["missing"]
    elif damage == "foreign_owner":
        rows[0]["owner_id"] = "missing-account"
    elif damage == "invalid_attempts":
        rows[0]["attempts"] = -1
    elif damage == "invalid_timestamp":
        rows[0]["created_at"] = None
    else:
        rows.append(deepcopy(rows[0]))
    snapshot["state"]["ACCOUNTS"]["user_zhang"]["balance"] = 1234
    canonical = json.dumps(snapshot["state"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    snapshot["integrity"] = main._ledger_store_digest(canonical, keyed=main.AUTH_SECRET_CONFIGURED)
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    main.reset_state()
    assert main.load_ledger_store()["rejected"] is True
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000
    assert main.TASK_STORE.list() == []


def test_legacy_snapshot_without_task_queue_remains_readable(durable):
    _, path = durable
    main.ACCOUNTS["user_zhang"]["balance"] = 1234
    main.persist_ledger()
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    del snapshot["state"]["TASK_STORE"]
    canonical = json.dumps(snapshot["state"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    snapshot["integrity"] = main._ledger_store_digest(canonical, keyed=main.AUTH_SECRET_CONFIGURED)
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    restart()
    assert main.ACCOUNTS["user_zhang"]["balance"] == 1234
