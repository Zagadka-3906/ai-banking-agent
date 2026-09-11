"""Login tests (P2): password authentication, optional SMS, session enforcement.

Before this milestone the core minted a session for any claimed identity, which
made `BANK_REQUIRE_SESSION` decorative.  These tests pin down the properties that
turn it into a real boundary:

* a password is verified with argon2id and never stored or logged in plaintext;
* unknown account, disabled account and wrong password are indistinguishable, so
  the endpoint is not an account-enumeration oracle;
* repeated failures count towards the same per-account breaker as a faulty nonce;
* the demo "issue a session for whoever asks" endpoint stops working outside demo
  mode, and the login endpoint stays reachable *before* a session exists;
* rotating a credential invalidates the sessions obtained with the old one.
"""

import os

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import security  # noqa: E402

DEMO_PASSWORD = "Bank@2026"
VALID_OTP = os.getenv("BANK_MFA_TEST_OTP", "888888")


@pytest.fixture()
def client():
    main.reset_state()
    yield TestClient(main.app)
    main.reset_state()


def login(client: TestClient, user_id: str = "user_zhang", password: str = DEMO_PASSWORD, otp: str | None = None):
    body = {"user_id": user_id, "password": password}
    if otp is not None:
        body["otp"] = otp
    return client.post("/api/v1/auth/login", json=body)


# ---------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------


def test_login_issues_a_usable_session(client):
    response = login(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user_id"] == "user_zhang"
    token = body["session_token"]

    # The credential it returns must actually authenticate, including for a write.
    write = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 100},
        headers={"X-Session-Token": token},
    )
    assert write.status_code == 200, write.text


def test_the_session_endpoint_alone_is_enough_without_an_identity_header(client):
    """The session subject is the identity; a header must not be needed."""

    token = login(client).json()["session_token"]
    response = client.get("/api/v1/accounts/user_zhang/balance", headers={"X-Session-Token": token})
    assert response.status_code == 200, response.text


def test_wrong_password_and_unknown_account_are_indistinguishable(client):
    wrong = login(client, password="definitely-not-it")
    unknown = login(client, user_id="user_nobody", password="definitely-not-it")

    assert wrong.status_code == unknown.status_code == 401
    # Identical bodies: a different message or code for an existing account would
    # let an attacker enumerate the customer list.
    assert wrong.json() == unknown.json()
    assert wrong.json()["detail"]["code"] == "INVALID_CREDENTIALS"


def test_a_disabled_account_cannot_log_in(client):
    main.ACCOUNTS["user_zhang"]["status"] = "FROZEN"
    response = login(client)
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "INVALID_CREDENTIALS"


def test_repeated_failures_trip_the_breaker_and_then_block_the_right_password(client):
    for _ in range(security.MAX_FAILURES):
        assert login(client, password="wrong-password").status_code == 401

    locked = login(client, password="wrong-password")
    assert locked.status_code == 423
    assert locked.json()["detail"]["code"] == "CIRCUIT_OPEN"

    # The lockout is the point: once the breaker is open, even the correct
    # credential is refused, so guessing cannot continue.
    assert login(client).status_code == 423


def test_login_does_not_store_or_echo_the_plaintext(client):
    response = login(client)
    assert response.status_code == 200
    assert DEMO_PASSWORD not in response.text

    stored = main.ACCOUNTS["user_zhang"]["password_hash"]
    assert isinstance(stored, str) and stored.startswith("$argon2id$")
    assert DEMO_PASSWORD not in stored

    # Nor does it reach the audit trail, which is inspectable by operators.
    assert DEMO_PASSWORD not in str(security.AUDIT_LOGS)


# ---------------------------------------------------------------------------
# optional SMS second factor
# ---------------------------------------------------------------------------


def test_sms_code_is_not_required_by_default(client):
    assert login(client).status_code == 200


def test_sms_code_is_enforced_when_the_deployment_asks_for_it(client, monkeypatch):
    monkeypatch.setenv("BANK_LOGIN_REQUIRE_OTP", "1")

    missing = login(client)
    assert missing.status_code == 401
    assert missing.json()["detail"]["code"] == "INVALID_LOGIN_OTP"

    assert login(client, otp="000000").status_code == 401

    accepted = login(client, otp=VALID_OTP)
    assert accepted.status_code == 200, accepted.text


# ---------------------------------------------------------------------------
# the demo bypass and strict session mode
# ---------------------------------------------------------------------------


def test_the_demo_session_endpoint_is_disabled_outside_demo_mode(client, monkeypatch):
    monkeypatch.setattr(security, "DEMO_MODE", False)

    bypass = client.post("/api/v1/auth/session", json={"user_id": "user_zhang"})
    assert bypass.status_code == 403
    assert bypass.json()["detail"]["code"] == "DEMO_ENDPOINT_DISABLED"

    # ...while the real login path is unaffected.
    assert login(client).status_code == 200


def test_strict_mode_refuses_bare_identity_writes_but_still_allows_login(client, monkeypatch):
    monkeypatch.setenv("BANK_REQUIRE_SESSION", "1")

    # Without this exemption the login request itself would be refused for
    # lacking the session it exists to create - a bootstrap deadlock.
    assert login(client).status_code == 200

    bare = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 100},
        headers={"X-User-Id": "user_zhang"},
    )
    assert bare.status_code == 401
    assert bare.json()["detail"]["code"] == "SESSION_REQUIRED"

    token = login(client).json()["session_token"]
    authorized = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 100},
        headers={"X-Session-Token": token},
    )
    assert authorized.status_code == 200, authorized.text


def test_health_reports_the_login_configuration(client):
    health = client.get("/api/v1/security/health").json()
    assert health["login"]["password_hashing"] == "argon2id"
    assert health["login"]["min_password_length"] >= 6
    assert health["login"]["otp_required"] is False
    assert health["login"]["session_required_for_writes"] is False


# ---------------------------------------------------------------------------
# credential rotation
# ---------------------------------------------------------------------------


def _approve_red(client: TestClient, operation: str, payload: dict, token_headers: dict):
    headers = {"X-User-Id": "user_zhang"}
    precheck = client.post("/api/v1/risk/precheck/operation", json={"user_id": "user_zhang", "operation": operation, **payload}, headers=headers)
    assert precheck.status_code == 200, precheck.text
    token = precheck.json()["auth_token"]
    client.post("/api/v1/auth/confirm", json={}, headers={**headers, "X-Auth-Token": token})
    challenge = client.post("/api/v1/auth/mfa/face/challenge", json={"auth_token": token, "user_id": "user_zhang"}, headers={**headers, "X-Auth-Token": token})
    assert challenge.status_code == 200, challenge.text
    attestation = security.sign_face_attestation(
        challenge.json()["challenge_id"], user_id="user_zhang", token_jti=challenge.json()["auth_token_jti"]
    )
    mfa = client.post("/api/v1/auth/mfa/verify", json={"otp": VALID_OTP, "face_attestation": attestation}, headers={**headers, "X-Auth-Token": token})
    assert mfa.status_code == 200, mfa.text
    proof = mfa.json()["mfa_proof"]
    return client.post(
        "/api/v1/auth/password/change",
        json={"user_id": "user_zhang", "auth_token": token, "new_password": payload["new_password"]},
        headers={**headers, "X-Auth-Token": token, "X-MFA-Proof": proof},
    )


def test_password_change_actually_rotates_the_credential_and_kills_sessions(client):
    stale = login(client).json()["session_token"]
    assert client.get("/api/v1/accounts/user_zhang/balance", headers={"X-Session-Token": stale}).status_code == 200

    rotated = _approve_red(client, "PASSWORD_CHANGE", {"new_password": "Rotated@2026"}, {})
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["data"]["status"] == "PASSWORD_CHANGED"
    assert rotated.json()["data"]["revoked_sessions"] >= 1

    # The old credential no longer works, the new one does.
    assert login(client, password=DEMO_PASSWORD).status_code == 401
    assert login(client, password="Rotated@2026").status_code == 200

    # And the session that was obtained with the old credential is gone.
    assert client.get("/api/v1/accounts/user_zhang/balance", headers={"X-Session-Token": stale}).status_code == 401


def test_password_change_without_a_new_password_is_refused(client):
    response = _approve_red(client, "PASSWORD_CHANGE", {"new_password": None}, {})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "NEW_PASSWORD_REQUIRED"
    # Nothing changed, so the established credential still works.
    assert login(client, password=DEMO_PASSWORD).status_code == 200


def test_a_too_short_new_password_is_refused(client):
    response = _approve_red(client, "PASSWORD_CHANGE", {"new_password": "short"}, {})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "WEAK_PASSWORD"
