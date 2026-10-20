"""Concurrency and atomicity contracts for the in-memory mock core.

The security self-assessment claims that concurrent retries settle at most
once.  These tests exercise that claim with real threads against the
in-process FastAPI app: the ledger is guarded by a process-wide lock, the
signed nonce is single-use, and the Idempotency-Key either replays a cached
response or reports an in-progress conflict — never a second debit.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Callable

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import main
import security


@pytest.fixture()
def client():
    main.reset_state()
    security.reset_security_state()
    with TestClient(main.app) as value:
        yield value
    main.reset_state()
    security.reset_security_state()


def _run_concurrently(calls: list[Callable[[], object]]) -> list:
    """Start every call at the same barrier so they genuinely overlap."""

    barrier = Barrier(len(calls))

    def guarded(call: Callable[[], object]):
        barrier.wait(timeout=5)
        return call()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [pool.submit(guarded, call) for call in calls]
        return [future.result(timeout=10) for future in futures]


def _auth(token: str | None = None) -> dict[str, str]:
    headers = {"X-User-Id": "user_zhang"}
    if token:
        headers["X-Auth-Token"] = token
    return headers


def _confirmed_transfer_token(client: TestClient, amount: float) -> str:
    precheck = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": amount},
        headers=_auth(),
    )
    assert precheck.status_code == 200, precheck.text
    token = precheck.json()["auth_token"]
    confirmed = client.post("/api/v1/auth/confirm", json={}, headers=_auth(token))
    assert confirmed.status_code == 200, confirmed.text
    return token


def test_concurrent_idempotent_settle_debits_once(client: TestClient):
    """Two identical requests sharing an Idempotency-Key settle at most once."""

    token = _confirmed_transfer_token(client, 12.0)
    payload = {"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 12.0}
    headers = {**_auth(token), "Idempotency-Key": "concurrent-idem-001"}

    responses = _run_concurrently([
        lambda: client.post("/api/v1/transfers/execute", json=payload, headers=headers),
        lambda: client.post("/api/v1/transfers/execute", json=payload, headers=headers),
    ])

    statuses = sorted(response.status_code for response in responses)
    assert statuses.count(200) >= 1, [response.text for response in responses]
    # The loser either replays the cached response or reports an in-progress
    # conflict; anything else means the key failed to serialise the request.
    assert statuses[-1] in {200, 409}, [response.text for response in responses]
    successes = [response.json() for response in responses if response.status_code == 200]
    assert all(body == successes[0] for body in successes)

    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000.0 - 12.0
    assert main.ACCOUNTS["user_li"]["balance"] == 2000.0 + 12.0
    # One debit for the payer and one credit for the payee - never two of either.
    assert len([row for row in main.TRANSACTIONS if row.get("amount") == 12.0 and row["account_id"] == "user_zhang"]) == 1
    assert len([row for row in main.TRANSACTIONS if row.get("amount") == 12.0 and row["account_id"] == "user_li"]) == 1


def test_concurrent_same_nonce_settles_once(client: TestClient):
    """A single signed nonce can authorise only one settlement, even raced."""

    token = _confirmed_transfer_token(client, 7.0)
    payload = {"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 7.0}
    headers = _auth(token)

    responses = _run_concurrently([
        lambda: client.post("/api/v1/transfers/execute", json=payload, headers=headers),
        lambda: client.post("/api/v1/transfers/execute", json=payload, headers=headers),
    ])

    codes = [response.status_code for response in responses]
    assert codes.count(200) == 1, [response.text for response in responses]
    loser = codes[0] if codes[1] == 200 else codes[1]
    assert loser in {401, 403, 409, 422}, [response.text for response in responses]
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000.0 - 7.0
    assert len([row for row in main.TRANSACTIONS if row.get("amount") == 7.0 and row["account_id"] == "user_zhang"]) == 1
    assert len([row for row in main.TRANSACTIONS if row.get("amount") == 7.0 and row["account_id"] == "user_li"]) == 1


def test_concurrent_lock_fund_reserves_once(client: TestClient):
    """Concurrent lock requests sharing one nonce reserve the funds once."""

    precheck = client.post(
        "/api/v1/risk/precheck/lock",
        json={"user_id": "user_zhang", "amount": 100, "reason": "并发锁资", "event_at": "2099-01-01T09:00:00+00:00"},
        headers=_auth(),
    )
    assert precheck.status_code == 200, precheck.text
    token = precheck.json()["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers=_auth(token)).status_code == 200
    body = {"user_id": "user_zhang", "amount": 100, "reason": "并发锁资", "event_at": "2099-01-01T09:00:00+00:00"}

    responses = _run_concurrently([
        lambda: client.post("/api/v1/accounts/lock_fund", json=body, headers=_auth(token)),
        lambda: client.post("/api/v1/accounts/lock_fund", json=body, headers=_auth(token)),
    ])

    codes = [response.status_code for response in responses]
    assert codes.count(200) == 1, [response.text for response in responses]
    assert main.ACCOUNTS["user_zhang"]["locked_amount"] == 100.0
    assert len(main.LOCKS) == 1


def test_run_due_requires_admin_token_when_configured(client: TestClient, monkeypatch):
    """The admin branch of /tasks/run-due is covered instead of environment-dependent."""

    monkeypatch.setenv("BANK_ADMIN_AUDIT_TOKEN", "run-due-admin-token")
    denied = client.post("/api/v1/tasks/run-due", headers=_auth())
    assert denied.status_code == 403
    allowed = client.post("/api/v1/tasks/run-due", headers={"X-Admin-Token": "run-due-admin-token"})
    assert allowed.status_code == 200
    monkeypatch.delenv("BANK_ADMIN_AUDIT_TOKEN", raising=False)
    user_scoped = client.post("/api/v1/tasks/run-due", headers=_auth())
    assert user_scoped.status_code == 200
