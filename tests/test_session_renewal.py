"""Renewal preserves one login's approvals without weakening logout isolation."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time

import pytest
from fastapi.testclient import TestClient

import main
import security
from support import patch_security


@pytest.fixture()
def client():
    main.reset_state()
    yield TestClient(main.app)
    main.reset_state()


def _renew(client, token):
    response = client.post("/api/v1/auth/session/renew", headers={"X-Session-Token": token})
    assert response.status_code == 200, response.text
    return response.json()["session_token"]


def _approval(client, session):
    response = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 100},
        headers={"X-Session-Token": session},
    )
    assert response.status_code == 200, response.text
    return response.json()["auth_token"]


def _settle(client, session, approval):
    headers = {"X-Session-Token": session, "X-Auth-Token": approval}
    confirmation = client.post("/api/v1/auth/confirm", json={}, headers=headers)
    assert confirmation.status_code == 200, confirmation.text
    return client.post(
        "/api/v1/transfers/execute",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 100, "auth_token": approval},
        headers=headers,
    )


@pytest.mark.parametrize("logout_index", [0, 1, 2])
def test_logout_revokes_all_renewals_but_not_independent_logins(client, logout_index):
    original = security.issue_session_token("user_zhang")["session_token"]
    renewed = _renew(client, original)
    latest = _renew(client, renewed)
    family = [original, renewed, latest]
    other_login = security.issue_session_token("user_zhang")["session_token"]
    other_successor = _renew(client, other_login)

    result = client.post("/api/v1/auth/logout", headers={"X-Session-Token": family[logout_index]})
    assert result.status_code == 200, result.text
    for token in family:
        assert client.get("/api/v1/auth/session", headers={"X-Session-Token": token}).status_code == 401
        assert client.post("/api/v1/auth/session/renew", headers={"X-Session-Token": token}).status_code == 401
    for token in (other_login, other_successor):
        assert client.get("/api/v1/auth/session", headers={"X-Session-Token": token}).status_code == 200


def test_approval_can_settle_with_renewed_session(client):
    original = security.issue_session_token("user_zhang")["session_token"]
    approval = _approval(client, original)
    renewed = _renew(client, original)
    balance = main.ACCOUNTS["user_zhang"]["balance"]

    result = _settle(client, renewed, approval)

    assert result.status_code == 200, result.text
    assert main.ACCOUNTS["user_zhang"]["balance"] == balance - 100


def test_approval_cannot_cross_independent_logins_for_the_same_account(client):
    original = security.issue_session_token("user_zhang")["session_token"]
    approval = _approval(client, original)
    other_login = security.issue_session_token("user_zhang")["session_token"]
    unrelated_successor = _renew(client, other_login)
    balance = main.ACCOUNTS["user_zhang"]["balance"]

    result = _settle(client, unrelated_successor, approval)

    assert result.status_code == 401, result.text
    assert result.json()["detail"]["code"] == "SESSION_REVOKED"
    assert main.ACCOUNTS["user_zhang"]["balance"] == balance
    assert not security.inspect_auth_token(approval)[1]["used"]
    assert _settle(client, _renew(client, original), approval).status_code == 200


def test_live_successor_keeps_approval_after_original_credential_expires(client, monkeypatch):
    original = security.issue_session_token("user_zhang", ttl_seconds=60)["session_token"]
    approval = _approval(client, original)
    renewed = _renew(client, original)
    after_original_expiry = security.SESSION_TOKENS[original]["expires_at"] + 1
    monkeypatch.setattr(time, "time", lambda: after_original_expiry)
    # Issuance purges expired credentials, so success cannot depend on retaining
    # the original token in the registry or accepting it beyond its own TTL.
    security.issue_session_token("user_li")
    assert original not in security.SESSION_TOKENS
    assert client.get("/api/v1/auth/session", headers={"X-Session-Token": original}).status_code == 401

    result = _settle(client, renewed, approval)

    assert result.status_code == 200, result.text


def test_session_renewal_does_not_extend_the_approval_deadline(client, monkeypatch):
    original = security.issue_session_token("user_zhang")["session_token"]
    approval = _approval(client, original)
    renewed = _renew(client, original)
    expired_at = security.inspect_auth_token(approval)[0]["exp"] + 1
    monkeypatch.setattr(time, "time", lambda: expired_at)
    assert security.resolve_session(renewed) == "user_zhang"
    balance = main.ACCOUNTS["user_zhang"]["balance"]

    result = client.post(
        "/api/v1/transfers/execute",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 100, "auth_token": approval},
        headers={"X-Session-Token": renewed, "X-Auth-Token": approval},
    )

    assert result.status_code == 403, result.text
    assert result.json()["detail"]["code"] == "TOKEN_EXPIRED"
    assert main.ACCOUNTS["user_zhang"]["balance"] == balance


def test_logout_invalidates_approval_even_after_renewal(client):
    original = security.issue_session_token("user_zhang")["session_token"]
    approval = _approval(client, original)
    renewed = _renew(client, original)
    assert client.post("/api/v1/auth/logout", headers={"X-Session-Token": renewed}).status_code == 200
    new_login = security.issue_session_token("user_zhang")["session_token"]
    balance = main.ACCOUNTS["user_zhang"]["balance"]

    result = _settle(client, new_login, approval)

    assert result.status_code == 401, result.text
    assert main.ACCOUNTS["user_zhang"]["balance"] == balance


def test_concurrent_renewal_cannot_outlive_logout(client, monkeypatch):
    original = security.issue_session_token("user_zhang")["session_token"]
    issuing = Event()
    release_issuance = Event()
    logout_started = Event()
    logout_finished = Event()
    issue = security._issue_session_token

    def paused_issue(*args, **kwargs):
        issuing.set()
        assert release_issuance.wait(5)
        return issue(*args, **kwargs)

    def logout():
        logout_started.set()
        try:
            return security.revoke_session(original)
        finally:
            logout_finished.set()

    patch_security(monkeypatch, "_issue_session_token", paused_issue)
    with ThreadPoolExecutor(max_workers=2) as pool:
        renewal = pool.submit(security.renew_session, original)
        try:
            assert issuing.wait(5)
            revocation = pool.submit(logout)
            assert logout_started.wait(5)
            # A renewal already validating the login must finish before logout
            # can revoke the family; otherwise the successor becomes orphaned.
            assert not logout_finished.wait(0.1)
        finally:
            release_issuance.set()
        successor = renewal.result(timeout=5)["session_token"]
        assert revocation.result(timeout=5)["revoked"]

    for token in (original, successor):
        with pytest.raises(security.SecurityError):
            security.resolve_session(token)
