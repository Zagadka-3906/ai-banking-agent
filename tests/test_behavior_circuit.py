"""Behaviour-combination circuit breaker.

The failure counter reacts to rejected credentials; this suite covers the
weaker, more realistic signal: *valid* credentials used in a suspicious
sequence (large transfer then report the card lost, change the password right
after draining the account, repeated limit changes, failures followed by a
success).  A score above the threshold opens the same per-principal lockout.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import main
import security


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("BANK_ADMIN_AUDIT_TOKEN", raising=False)
    monkeypatch.delenv("BANK_REQUIRE_SESSION", raising=False)
    main.reset_state()
    security.reset_security_state()
    with TestClient(main.app) as value:
        yield value
    main.reset_state()
    security.reset_security_state()


def auth(token: str | None = None) -> dict[str, str]:
    headers = {"X-User-Id": "user_zhang"}
    if token:
        headers["X-Auth-Token"] = token
    return headers


def small_transfer(client: TestClient, amount: float = 1000.0):
    """Yellow transfer used as a 'large' signal when the threshold is lowered."""

    precheck = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": amount},
        headers=auth(),
    )
    assert precheck.status_code == 200, precheck.text
    token = precheck.json()["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers=auth(token)).status_code == 200
    return client.post(
        "/api/v1/transfers/execute",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": amount, "auth_token": token},
        headers=auth(token),
    )


def red_operation(client: TestClient, operation: str, **kwargs):
    """Approved + MFA-verified sensitive operation (card / password changes)."""

    body = {"user_id": "user_zhang", "operation": operation, **kwargs}
    precheck = client.post("/api/v1/risk/precheck/operation", json=body, headers=auth())
    assert precheck.status_code == 200, precheck.text
    token = precheck.json()["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers=auth(token)).status_code == 200
    challenge = client.post("/api/v1/auth/mfa/face/challenge", json={"auth_token": token, "user_id": "user_zhang"}, headers=auth(token))
    assert challenge.status_code == 200, challenge.text
    attestation = security.sign_face_attestation(challenge.json()["challenge_id"], user_id="user_zhang", token_jti=challenge.json()["auth_token_jti"])
    mfa = client.post("/api/v1/auth/mfa/verify", json={"otp": "888888", "face_attestation": attestation}, headers=auth(token))
    assert mfa.status_code == 200, mfa.text
    proof = mfa.json()["mfa_proof"]
    return client.post(
        "/api/v1/operations/execute",
        json={**body, "auth_token": token, "mfa_proof": proof},
        headers={**auth(token), "X-MFA-Proof": proof},
    )


def behavior(client: TestClient) -> dict:
    response = client.get("/api/v1/security/behavior/user_zhang", headers=auth())
    assert response.status_code == 200, response.text
    return response.json()


def test_single_large_transfer_stays_below_threshold(client: TestClient, monkeypatch):
    monkeypatch.setenv("BANK_BEHAVIOR_LARGE_AMOUNT_CENTS", "100000")  # 1000 元
    settled = small_transfer(client, 1000.0)
    assert settled.status_code == 200, settled.text

    status = behavior(client)
    assert status["score"] == 30  # one LARGE_TRANSFER signal
    assert status["locked"] is False
    assert status["fired_rules"] == []
    # The account keeps working.
    assert client.get("/api/v1/accounts/user_zhang/balance", headers=auth()).status_code == 200


def test_lost_card_after_large_transfer_locks(client: TestClient, monkeypatch):
    monkeypatch.setenv("BANK_BEHAVIOR_LARGE_AMOUNT_CENTS", "100000")
    assert small_transfer(client, 1000.0).status_code == 200

    reported = red_operation(client, "CARD_REPORT_LOST")
    assert reported.status_code == 200, reported.text

    status = behavior(client)
    assert "LOST_AFTER_LARGE_TRANSFER" in status["fired_rules"]
    assert status["score"] >= status["threshold"]
    assert status["locked"] is True
    assert status["lock_reason"].startswith("BEHAVIOR_RISK:")

    # The lock now blocks every subsequent request for that principal.
    blocked = client.get("/api/v1/accounts/user_zhang/balance", headers=auth())
    assert blocked.status_code == 423
    assert blocked.json()["detail"]["code"] == "CIRCUIT_OPEN"
    assert "BEHAVIOR_RISK" in blocked.json()["detail"]["message"]

    logs = client.get("/api/v1/security/audit", headers=auth())
    assert logs.status_code in {200, 423}


def test_password_change_after_large_transfer_locks(client: TestClient, monkeypatch):
    monkeypatch.setenv("BANK_BEHAVIOR_LARGE_AMOUNT_CENTS", "100000")
    assert small_transfer(client, 1000.0).status_code == 200
    assert red_operation(client, "PASSWORD_CHANGE").status_code == 200

    status = behavior(client)
    assert "PASSWORD_CHANGE_AFTER_LARGE_TRANSFER" in status["fired_rules"]
    assert status["locked"] is True


def test_burst_of_large_transfers_locks(client: TestClient, monkeypatch):
    monkeypatch.setenv("BANK_BEHAVIOR_LARGE_AMOUNT_CENTS", "100")  # every transfer counts
    for _ in range(3):
        assert small_transfer(client, 10.0).status_code == 200

    status = behavior(client)
    assert "BURST_LARGE_TRANSFERS" in status["fired_rules"]
    assert status["locked"] is True


def test_failures_then_success_locks(client: TestClient, monkeypatch):
    monkeypatch.setenv("BANK_BEHAVIOR_LARGE_AMOUNT_CENTS", "100000")  # keep the transfer small
    for _ in range(3):
        denied = client.post("/api/v1/auth/confirm", json={}, headers=auth("forged.token"))
        assert denied.status_code in {401, 403}

    assert small_transfer(client, 10.0).status_code == 200

    status = behavior(client)
    assert "FAILURES_THEN_SUCCESS" in status["fired_rules"]
    assert status["locked"] is True
    assert client.get("/api/v1/accounts/user_zhang/balance", headers=auth()).status_code == 423


def test_threshold_is_configurable(client: TestClient, monkeypatch):
    monkeypatch.setenv("BANK_BEHAVIOR_LARGE_AMOUNT_CENTS", "100000")
    monkeypatch.setenv("BANK_BEHAVIOR_RISK_THRESHOLD", "30")
    assert small_transfer(client, 1000.0).status_code == 200

    status = behavior(client)
    assert status["threshold"] == 30
    assert status["locked"] is True


def test_behavior_status_is_self_scoped(client: TestClient, monkeypatch):
    monkeypatch.setenv("BANK_BEHAVIOR_LARGE_AMOUNT_CENTS", "100000")
    assert small_transfer(client, 1000.0).status_code == 200

    cross_user = client.get("/api/v1/security/behavior/user_zhang", headers={"X-User-Id": "user_li"})
    assert cross_user.status_code == 403
    monkeypatch.setenv("BANK_ADMIN_AUDIT_TOKEN", "behavior-admin-token")
    admin = client.get("/api/v1/security/behavior/user_zhang", headers={"X-Admin-Token": "behavior-admin-token"})
    assert admin.status_code == 200
    assert admin.json()["principal"] == "user_zhang"
    assert admin.json()["signals"][0]["name"] == "LARGE_TRANSFER"


def test_lock_expires_and_principal_recovers(client: TestClient, monkeypatch):
    monkeypatch.setenv("BANK_BEHAVIOR_LARGE_AMOUNT_CENTS", "100000")
    monkeypatch.setenv("BANK_BEHAVIOR_RISK_THRESHOLD", "30")
    assert small_transfer(client, 1000.0).status_code == 200
    assert client.get("/api/v1/accounts/user_zhang/balance", headers=auth()).status_code == 423

    # Simulate the lockout window elapsing.
    security.SECURITY_STATE["user_zhang"]["locked_until"] = 0.0
    assert client.get("/api/v1/accounts/user_zhang/balance", headers=auth()).status_code == 200
    assert behavior(client)["locked"] is False


def test_security_health_reports_behavior_config(client: TestClient):
    health = client.get("/api/v1/security/health").json()["behavior_risk"]
    assert health["threshold"] >= 10
    assert health["window_seconds"] >= 60
    assert "LOST_AFTER_LARGE_TRANSFER" in health["rules"]
    assert "BURST_LARGE_TRANSFERS" in health["rules"]
