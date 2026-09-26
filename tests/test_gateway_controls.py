"""Gateway-level controls: session credentials and per-principal rate limiting.

These tests cover the two controls that sit in front of every endpoint: a
session token replaces the self-asserted ``X-User-Id`` as the authoritative
identity source, and a per-principal request window blunts accidental or
hostile floods without affecting health probes.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import main
import security
from support import login_session


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("BANK_ADMIN_AUDIT_TOKEN", raising=False)
    monkeypatch.setenv("BANK_REQUIRE_SESSION", "0")
    monkeypatch.delenv("BANK_RATE_LIMIT_PER_MINUTE", raising=False)
    main.reset_state()
    security.reset_security_state()
    with TestClient(main.app) as value:
        yield value
    main.reset_state()
    security.reset_security_state()


def _session(client: TestClient, user_id: str = "user_zhang") -> str:
    return login_session(client, user_id)


def test_session_token_authenticates_reads_without_identity_header(client: TestClient):
    token = _session(client)
    balance = client.get("/api/v1/accounts/user_zhang/balance", headers={"X-Session-Token": token})
    assert balance.status_code == 200, balance.text
    assert balance.json()["available_balance"] >= 0

    health = client.get("/api/v1/security/health").json()
    assert health["active_sessions"] >= 1


def test_session_token_can_authorise_a_yellow_transfer(client: TestClient):
    token = _session(client)
    session_headers = {"X-Session-Token": token}
    precheck = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 10},
        headers=session_headers,
    )
    assert precheck.status_code == 200, precheck.text
    auth_token = precheck.json()["auth_token"]
    confirm = client.post("/api/v1/auth/confirm", json={}, headers={**session_headers, "X-Auth-Token": auth_token})
    assert confirm.status_code == 200, confirm.text
    settled = client.post(
        "/api/v1/transfers/execute",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 10, "auth_token": auth_token},
        headers={**session_headers, "X-Auth-Token": auth_token},
    )
    assert settled.status_code == 200, settled.text
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14990.0

    # The session identity is the actor of the recorded events: the caller can
    # read their own audit trail with the session alone.
    logs = client.get("/api/v1/security/audit", headers=session_headers)
    assert logs.status_code == 200, logs.text
    actions = [event["action"] for event in logs.json()["data"]]
    assert "SESSION_ISSUED" in actions
    assert "TRANSFER_EXECUTE" in actions


def test_session_subject_overrides_client_supplied_identity(client: TestClient):
    """A valid session cannot be combined with someone else's X-User-Id."""

    token = _session(client, "user_zhang")
    spoofed = client.get(
        "/api/v1/accounts/user_li/balance",
        headers={"X-Session-Token": token, "X-User-Id": "user_li"},
    )
    assert spoofed.status_code in {401, 403}, spoofed.text
    own = client.get("/api/v1/accounts/user_zhang/balance", headers={"X-Session-Token": token, "X-User-Id": "user_li"})
    assert own.status_code == 200, own.text


def test_forged_and_expired_sessions_are_rejected(client: TestClient):
    forged = client.get("/api/v1/accounts/user_zhang/balance", headers={"X-Session-Token": "forged.session"})
    assert forged.status_code == 401
    assert forged.json()["detail"]["code"] in {"INVALID_SESSION", "SESSION_REQUIRED"}

    token = _session(client)
    for record in security.SESSION_TOKENS.values():
        record["expires_at"] = 0
    expired = client.get("/api/v1/accounts/user_zhang/balance", headers={"X-Session-Token": token})
    assert expired.status_code == 401
    assert expired.json()["detail"]["code"] in {"SESSION_EXPIRED", "INVALID_SESSION"}


def test_strict_session_mode_requires_session_for_private_reads_and_writes(client: TestClient, monkeypatch):
    monkeypatch.setenv("BANK_REQUIRE_SESSION", "1")
    payload = {"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 10}
    denied = client.post("/api/v1/risk/precheck", json=payload, headers={"X-User-Id": "user_zhang"})
    assert denied.status_code == 401
    assert denied.json()["detail"]["code"] == "SESSION_REQUIRED"

    assert client.get("/api/v1/accounts/user_zhang/balance", headers={"X-User-Id": "user_zhang"}).status_code == 401
    token = _session(client)
    assert client.get("/api/v1/accounts/user_zhang/balance", headers={"X-Session-Token": token}).status_code == 200
    allowed = client.post("/api/v1/risk/precheck", json=payload, headers={"X-Session-Token": token})
    assert allowed.status_code == 200, allowed.text


def test_rate_limit_returns_429_with_retry_after(client: TestClient, monkeypatch):
    monkeypatch.setenv("BANK_RATE_LIMIT_PER_MINUTE", "3")
    headers = {"X-User-Id": "user_zhang"}
    statuses = [client.get("/api/v1/accounts/user_zhang/balance", headers=headers).status_code for _ in range(4)]
    assert statuses[:3] == [200, 200, 200], statuses
    assert statuses[3] == 429, statuses

    limited = client.get("/api/v1/accounts/user_zhang/balance", headers=headers)
    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "RATE_LIMITED"
    assert int(limited.headers["Retry-After"]) >= 1
    assert limited.headers["X-Content-Type-Options"] == "nosniff"

    # Health probes are exempt.
    assert client.get("/healthz").status_code == 200
    # A different self-asserted X-User-Id must NOT buy a fresh window: the
    # header is an object selector, not a credential, so keying on it let
    # anyone rotate the value and reset the throttle at will.
    assert client.get("/api/v1/accounts/user_li/balance", headers={"X-User-Id": "user_li"}).status_code == 429


def test_rate_limit_buckets_by_verified_session_not_by_claimed_header(client: TestClient, monkeypatch):
    """One caller exhausting its window must not throttle another principal.

    With a verified session the bucket is the session's principal, so a second
    logged-in user is unaffected - which is the property the old per-header
    bucketing appeared to provide, but only by letting the caller pick its own
    bucket.
    """

    monkeypatch.setenv("BANK_RATE_LIMIT_PER_MINUTE", "3")
    noisy_token = _session(client, "user_li")
    for _ in range(4):
        client.get("/api/v1/accounts/user_li/balance", headers={"X-Session-Token": noisy_token})
    assert client.get("/api/v1/accounts/user_li/balance", headers={"X-Session-Token": noisy_token}).status_code == 429

    quiet_token = _session(client, "user_zhang")
    assert client.get("/api/v1/accounts/user_zhang/balance", headers={"X-Session-Token": quiet_token}).status_code == 200


def test_rate_limit_can_be_disabled(client: TestClient, monkeypatch):
    monkeypatch.setenv("BANK_RATE_LIMIT_PER_MINUTE", "0")
    for _ in range(8):
        assert client.get("/api/v1/accounts/user_zhang/balance", headers={"X-User-Id": "user_zhang"}).status_code == 200
