"""The background scheduler advances due tasks without anyone calling run-due."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
from bank_core import task_scheduler
from scheduler import TaskState


@pytest.fixture()
def client():
    main.reset_state()
    with TestClient(main.app) as value:
        yield value
    main.reset_state()


def _birthday_lock(client: TestClient) -> dict:
    headers = {"X-User-Id": "user_zhang"}
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    pre = client.post("/api/v1/risk/precheck/lock", json={"user_id": "user_zhang", "amount": 100, "reason": "生日储备", "event_at": future}, headers=headers)
    assert pre.status_code == 200, pre.text
    token = pre.json()["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers={**headers, "X-Auth-Token": token}).status_code == 200
    locked = client.post(
        "/api/v1/accounts/lock_fund",
        json={"user_id": "user_zhang", "amount": 100, "reason": "生日储备", "event_at": future,
              "reminder_at": pre.json()["reminder_at"], "auth_token": token},
        headers={**headers, "X-Auth-Token": token},
    )
    assert locked.status_code == 200, locked.text
    return locked.json()


def test_scheduler_tick_completes_reminders_and_parks_orders_for_approval(client):
    locked = _birthday_lock(client)
    reminder_id = locked["reminder_task_id"]
    order_ids = locked["order_task_ids"]
    for task_id in [reminder_id, *order_ids]:
        main.TASK_STORE.get(task_id).run_at = "2000-01-01T00:00:00+00:00"

    assert task_scheduler.run_scheduler_once() == 3

    assert main.TASK_STORE.get(reminder_id).status == TaskState.SUCCEEDED
    # Money never moves on a tick: each order waits for a fresh approval.
    assert all(main.TASK_STORE.get(task_id).status == TaskState.WAITING_USER for task_id in order_ids)
    assert task_scheduler.run_scheduler_once() == 0


def test_scheduler_leaves_tasks_that_are_not_due(client):
    locked = _birthday_lock(client)
    assert task_scheduler.run_scheduler_once() == 0
    assert main.TASK_STORE.get(locked["reminder_task_id"]).status == TaskState.PENDING


def test_scheduler_interval_is_configurable_and_can_be_disabled(monkeypatch):
    monkeypatch.setenv("BANK_TASK_SCHEDULER_SECONDS", "0")
    assert task_scheduler.scheduler_interval_seconds() == 0
    assert task_scheduler.start_scheduler() is False
    monkeypatch.setenv("BANK_TASK_SCHEDULER_SECONDS", "1")
    assert task_scheduler.scheduler_interval_seconds() == 5.0
    monkeypatch.setenv("BANK_TASK_SCHEDULER_SECONDS", "abc")
    assert task_scheduler.scheduler_interval_seconds() == 60.0


def test_scheduler_thread_starts_and_stops(monkeypatch):
    monkeypatch.setenv("BANK_TASK_SCHEDULER_SECONDS", "3600")
    try:
        assert task_scheduler.start_scheduler() is True
        assert task_scheduler.scheduler_status()["running"] is True
    finally:
        task_scheduler.stop_scheduler()
    assert task_scheduler.scheduler_status()["running"] is False
