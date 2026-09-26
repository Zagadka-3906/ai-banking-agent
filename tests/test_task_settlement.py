from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture()
def client():
    main.reset_state()
    with TestClient(main.app) as value:
        yield value
    main.reset_state()


def h(token: str | None = None, user: str = "user_zhang"):
    result = {"X-User-Id": user}
    if token:
        result["X-Auth-Token"] = token
    return result


def scheduled(client: TestClient, amount: float = 600) -> str:
    body = {"user_id": "user_zhang", "operation": "SCHEDULED_TRANSFER", "target": "李四", "amount": amount, "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}
    pre = client.post("/api/v1/risk/precheck/operation", json=body, headers=h())
    assert pre.status_code == 200, pre.text
    token = pre.json()["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers=h(token)).status_code == 200
    made = client.post("/api/v1/operations/execute", json={**body, "auth_token": token}, headers=h(token))
    assert made.status_code == 200, made.text
    task_id = made.json()["data"]["task_id"]
    main.TASK_STORE.get(task_id).run_at = "2000-01-01T00:00:00+00:00"
    assert client.post("/api/v1/tasks/run-due", headers=h()).status_code == 200
    return task_id


def approve_task(client: TestClient, task_id: str):
    pre = client.post("/api/v1/tasks/%s/precheck" % task_id, json={"user_id": "user_zhang"}, headers=h())
    assert pre.status_code == 200, pre.text
    token = pre.json()["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers=h(token)).status_code == 200
    return token


def test_due_transfer_fresh_token_exactly_once_and_replay(client):
    task_id = scheduled(client)
    token = approve_task(client, task_id)
    key = "settle-once"
    before = main.ACCOUNTS["user_zhang"]["balance"]
    first = client.post("/api/v1/tasks/%s/execute" % task_id, json={"user_id": "user_zhang", "auth_token": token}, headers={**h(), "Idempotency-Key": key})
    assert first.status_code == 200, first.text
    assert main.ACCOUNTS["user_zhang"]["balance"] == before - 600
    replay = client.post("/api/v1/tasks/%s/execute" % task_id, json={"user_id": "user_zhang", "auth_token": token}, headers={**h(), "Idempotency-Key": key})
    assert replay.status_code == 200
    assert main.ACCOUNTS["user_zhang"]["balance"] == before - 600


def test_due_execute_requires_idempotency_and_creation_token_cannot_settle(client):
    task_id = scheduled(client)
    task = main.TASK_STORE.get(task_id)
    assert task is not None
    denied = client.post("/api/v1/tasks/%s/execute" % task_id, json={"user_id": "user_zhang", "auth_token": "bad"}, headers=h())
    assert denied.status_code == 400
    # The creation nonce is consumed by scheduling and is a different operation.
    # A creation nonce is deliberately not accepted by the settlement operation.
    creation = client.post("/api/v1/risk/precheck/operation", json={"user_id": "user_zhang", "operation": "SCHEDULED_TRANSFER", "target": "李四", "amount": 1, "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}, headers=h()).json()["auth_token"]
    denied = client.post("/api/v1/tasks/%s/execute" % task_id, json={"user_id": "user_zhang", "auth_token": creation}, headers={**h(), "Idempotency-Key": "old-token"})
    assert denied.status_code in {403, 409}


def test_task_owner_and_confirmation_gate(client):
    task_id = scheduled(client)
    assert client.post("/api/v1/tasks/%s/precheck" % task_id, json={"user_id": "user_zhang"}, headers=h("x", "user_other")).status_code in {403, 404}
    pre = client.post("/api/v1/tasks/%s/precheck" % task_id, json={"user_id": "user_zhang"}, headers=h())
    token = pre.json()["auth_token"]
    missing = client.post("/api/v1/tasks/%s/execute" % task_id, json={"user_id": "user_zhang", "auth_token": token}, headers={**h(), "Idempotency-Key": "missing-confirm"})
    assert missing.status_code == 428


def test_order_draft_places_local_mock_order_after_fresh_approval(client):
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    lock_pre = client.post("/api/v1/risk/precheck/lock", json={"user_id": "user_zhang", "amount": 100, "event_at": future}, headers=h())
    token = lock_pre.json()["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers=h(token)).status_code == 200
    locked = client.post("/api/v1/accounts/lock_fund", json={"user_id": "user_zhang", "amount": 100, "reason": "跨场景联动储备", "event_at": future, "reminder_at": lock_pre.json()["reminder_at"], "auth_token": token}, headers=h(token))
    assert locked.status_code == 200, locked.text
    task_id = locked.json()["order_task_ids"][0]
    main.TASK_STORE.get(task_id).run_at = "2000-01-01T00:00:00+00:00"
    assert client.post("/api/v1/tasks/run-due", headers=h()).status_code == 200
    before = (main.ACCOUNTS["user_zhang"]["balance"], dict(main.DAILY_TRANSFERS), len(main.TRANSACTIONS))
    token = approve_task(client, task_id)
    result = client.post("/api/v1/tasks/%s/execute" % task_id, json={"user_id": "user_zhang", "auth_token": token}, headers={**h(), "Idempotency-Key": "mock-order"})
    assert result.status_code == 200, result.text
    assert result.json()["execution_mode"] == "MOCK_MERCHANT"
    assert result.json()["funds_debited"] is True
    assert result.json()["order_status"] == "PLACED"
    assert main.ACCOUNTS["user_zhang"]["balance"] == before[0] - 49.0
    assert main.DAILY_TRANSFERS == before[1]
    assert len(main.TRANSACTIONS) == before[2] + 1


def test_due_risk_excludes_self_but_counts_distinct_pending(client):
    task_id = scheduled(client, 600)
    pre = client.post("/api/v1/tasks/%s/precheck" % task_id, json={"user_id": "user_zhang"}, headers=h())
    assert pre.status_code == 200 and pre.json()["risk_level"] == "YELLOW"
    body = {"user_id": "user_zhang", "operation": "SCHEDULED_TRANSFER", "target": "李四", "amount": 500, "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()}
    other = client.post("/api/v1/risk/precheck/operation", json=body, headers=h())
    assert other.status_code == 200 and other.json()["risk_level"] == "RED"


def test_red_requires_mfa_and_forged_proof_is_safe(client):
    task_id = scheduled(client, 600)
    pending = {"user_id": "user_zhang", "operation": "SCHEDULED_TRANSFER", "target": "李四", "amount": 500, "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()}
    assert client.post("/api/v1/risk/precheck/operation", json=pending, headers=h()).json()["risk_level"] == "RED"
    token = approve_task(client, task_id)
    before = (main.ACCOUNTS["user_zhang"]["balance"], len(main.TRANSACTIONS))
    missing = client.post("/api/v1/tasks/%s/execute" % task_id, json={"user_id": "user_zhang", "auth_token": token}, headers={**h(), "Idempotency-Key": "red-no-mfa"})
    assert missing.status_code == 403
    assert (main.ACCOUNTS["user_zhang"]["balance"], len(main.TRANSACTIONS)) == before
    # A fresh token is needed after the failed attempt; an arbitrary proof must fail.
    token = approve_task(client, task_id)
    forged = client.post("/api/v1/tasks/%s/execute" % task_id, json={"user_id": "user_zhang", "auth_token": token, "mfa_proof": "forged"}, headers={**h(), "Idempotency-Key": "red-forged"})
    assert forged.status_code == 403
    assert main.TASK_STORE.get(task_id).status == "WAITING_USER"


def test_yellow_token_escalates_before_mutation(client):
    task_id = scheduled(client, 600)
    token = approve_task(client, task_id)
    body = {"user_id": "user_zhang", "operation": "SCHEDULED_TRANSFER", "target": "李四", "amount": 500, "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()}
    assert client.post("/api/v1/risk/precheck/operation", json=body, headers=h()).json()["risk_level"] == "RED"
    before = (main.ACCOUNTS["user_zhang"]["balance"], len(main.TRANSACTIONS))
    response = client.post("/api/v1/tasks/%s/execute" % task_id, json={"user_id": "user_zhang", "auth_token": token}, headers={**h(), "Idempotency-Key": "risk-escalation"})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "RISK_CHANGED_REAUTHORIZE"
    assert (main.ACCOUNTS["user_zhang"]["balance"], len(main.TRANSACTIONS)) == before


@pytest.mark.parametrize("change", ["balance", "freeze", "limit", "task"])
def test_settlement_rechecks_mutable_state_without_partial_debit(client, change):
    task_id = scheduled(client, 600)
    token = approve_task(client, task_id)
    task = main.TASK_STORE.get(task_id)
    card = next(card for card in main.CARDS.get("user_zhang", []) if card.get("card_id") == task.payload.get("card_id"))
    if change == "balance":
        main.ACCOUNTS["user_zhang"]["balance"] = 100
    elif change == "freeze":
        card["status"] = "FROZEN"
    elif change == "limit":
        card["daily_limit"] = 1
    else:
        task.payload["target"] = "赵六"
    before = (main.ACCOUNTS["user_zhang"]["balance"], len(main.TRANSACTIONS), dict(main.DAILY_TRANSFERS), dict(main.CARD_DAILY_SPENT))
    response = client.post("/api/v1/tasks/%s/execute" % task_id, json={"user_id": "user_zhang", "auth_token": token}, headers={**h(), "Idempotency-Key": "mutable-" + change})
    assert response.status_code in {400, 404, 409, 423}
    assert main.TASK_STORE.get(task_id).status == "WAITING_USER"
    assert (main.ACCOUNTS["user_zhang"]["balance"], len(main.TRANSACTIONS), dict(main.DAILY_TRANSFERS), dict(main.CARD_DAILY_SPENT)) == before


def test_task_cas_cancel_execute_has_one_winner():
    from scheduler import TASK_STORE, TaskState

    TASK_STORE.reset()
    task = TASK_STORE.create("u", "SCHEDULED_TRANSFER", {})
    assert TASK_STORE.transition_if_status(task.task_id, TaskState.PENDING, TaskState.WAITING_USER) is not None
    first = TASK_STORE.transition_if_status(task.task_id, TaskState.WAITING_USER, TaskState.RUNNING)
    assert first is not None
    with pytest.raises(ValueError):
        TASK_STORE.cancel(task.task_id, owner_id="u")
    assert TASK_STORE.transition_if_status(task.task_id, TaskState.WAITING_USER, TaskState.RUNNING) is None
