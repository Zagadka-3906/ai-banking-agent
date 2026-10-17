from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import main
from bank_core import http_edge
import security
from support import patch_security, sign_face_attestation


@pytest.fixture()
def client():
    main.reset_state()
    with TestClient(main.app) as value:
        yield value
    main.reset_state()


def auth(token: str | None = None) -> dict[str, str]:
    result = {"X-User-Id": "user_zhang"}
    if token:
        result["X-Auth-Token"] = token
    return result


def test_confirmation_requires_authenticated_subject_header(client: TestClient):
    precheck = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 10},
        headers=auth(),
    )
    assert precheck.status_code == 200
    token = precheck.json()["auth_token"]

    missing_identity = client.post("/api/v1/auth/confirm", json={"auth_token": token})
    assert missing_identity.status_code in {401, 403}

    confirmed = client.post("/api/v1/auth/confirm", json={}, headers=auth(token))
    assert confirmed.status_code == 200, confirmed.text


def test_untrusted_body_user_cannot_open_victim_circuit(client: TestClient):
    """Authentication failures are attributed to a presented principal only.

    A body ``user_id`` is merely a claim until the signed nonce and login
    header have been checked.  Repeated unauthenticated requests naming a
    victim must therefore not lock that victim's account.
    """

    for _ in range(security.MAX_FAILURES + 1):
        response = client.post(
            "/api/v1/auth/confirm",
            json={"user_id": "user_zhang", "auth_token": "not-a-token"},
        )
        assert response.status_code in {401, 403, 423}

    status = security.circuit_status("user_zhang")
    assert status["locked"] is False
    assert status["failure_count"] == 0


def test_write_endpoints_require_presented_identity(client: TestClient):
    response = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 1},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"


def test_circuit_lockout_expiry_is_not_extended_by_retries(monkeypatch):
    """Blocked polling must not turn a finite lockout into a permanent one."""

    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    patch_security(monkeypatch, "MAX_FAILURES", 2)
    patch_security(monkeypatch, "LOCKOUT_SECONDS", 60)
    patch_security(monkeypatch, "FAILURE_WINDOW_SECONDS", 300)
    security.reset_security_state()

    security.record_security_failure("user_zhang", "bad-token")
    locked = security.record_security_failure("user_zhang", "bad-token")
    assert locked["locked"] is True
    assert locked["locked_until_epoch"] == 160.0

    # A further blocked retry at t=101 must retain the original expiry.
    clock[0] = 101.0
    retried = security.record_security_failure("user_zhang", "CIRCUIT_OPEN")
    assert retried["locked"] is True
    assert retried["locked_until_epoch"] == 160.0

    # Once the original deadline passes, the circuit can close normally.
    clock[0] = 160.1
    assert security.circuit_status("user_zhang")["locked"] is False


def test_generic_operation_amounts_are_canonical_cent_values(client: TestClient):
    """Approval, risk accounting and stored schedules use the same cents."""

    scheduled_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    precheck = client.post(
        "/api/v1/risk/precheck/operation",
        json={
            "user_id": "user_zhang",
            "operation": "SCHEDULED_TRANSFER",
            "target": "李四",
            "amount": 0.005,
            "scheduled_at": scheduled_at,
        },
        headers=auth(),
    )
    assert precheck.status_code == 200, precheck.text
    token = precheck.json()["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers=auth(token)).status_code == 200
    executed = client.post(
        "/api/v1/operations/execute",
        json={
            "user_id": "user_zhang",
            "operation": "SCHEDULED_TRANSFER",
            "target": "李四",
            "amount": 0.005,
            "scheduled_at": scheduled_at,
            "auth_token": token,
        },
        headers=auth(token),
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["data"]["amount"] == 0.01


def test_split_tasks_are_scoped_to_participant_set(client: TestClient):
    """Different AA recipients must never alias the same scheduler task."""

    def create(participants: list[str]) -> str:
        precheck = client.post(
            "/api/v1/risk/precheck/operation",
            json={
                "user_id": "user_zhang",
                "operation": "SPLIT_BILL",
                "amount": 300,
                "participants": participants,
            },
            headers=auth(),
        )
        assert precheck.status_code == 200, precheck.text
        token = precheck.json()["auth_token"]
        assert client.post("/api/v1/auth/confirm", json={}, headers=auth(token)).status_code == 200
        executed = client.post(
            "/api/v1/operations/execute",
            json={
                "user_id": "user_zhang",
                "operation": "SPLIT_BILL",
                "amount": 300,
                "participants": participants,
                "auth_token": token,
            },
            headers=auth(token),
        )
        assert executed.status_code == 200, executed.text
        return executed.json()["data"]["task_id"]

    first = create(["李四"])
    second = create(["王美"])
    assert first != second


def test_scheduled_large_transfer_is_red_and_cannot_skip_mfa(client: TestClient):
    scheduled_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    precheck = client.post(
        "/api/v1/risk/precheck/operation",
        json={
            "user_id": "user_zhang",
            "operation": "SCHEDULED_TRANSFER",
            "target": "李四",
            "amount": 2000,
            "scheduled_at": scheduled_at,
            "reason": "大额预约",
        },
        headers=auth(),
    )
    assert precheck.status_code == 200, precheck.text
    body = precheck.json()
    assert body["risk_level"] == "RED"
    token = body["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers=auth(token)).status_code == 200

    execute = client.post(
        "/api/v1/operations/execute",
        json={
            "user_id": "user_zhang",
            "operation": "SCHEDULED_TRANSFER",
            "target": "李四",
            "amount": 2000,
            "scheduled_at": scheduled_at,
            "reason": "大额预约",
            "auth_token": token,
        },
        headers=auth(token),
    )
    assert execute.status_code in {403, 428}


def test_legacy_biometric_flag_cannot_satisfy_mfa(client: TestClient):

    precheck = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 2000},
        headers=auth(),
    )
    assert precheck.status_code == 200
    token = precheck.json()["auth_token"]
    response = client.post(
        "/api/v1/auth/mfa/verify",
        json={"auth_token": token, "otp": "888888", "face_verified": "yes"},
        headers=auth(token),
    )
    assert response.status_code == 403


def test_face_attestation_ticket_is_required_and_bound(client: TestClient):
    """The biometric factor now requires a signed single-use ticket.

    The removed boolean field cannot satisfy MFA, forged tickets fail closed, a
    valid ticket is bound to the token jti, and the challenge is
    consumed on success so the same attestation cannot open another token.
    """

    precheck = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 2000},
        headers=auth(),
    )
    assert precheck.status_code == 200
    token = precheck.json()["auth_token"]

    boolean_only = client.post("/api/v1/auth/mfa/verify", json={"otp": "888888", "face_verified": True}, headers=auth(token))
    assert boolean_only.status_code == 403

    forged = client.post("/api/v1/auth/mfa/verify", json={"otp": "888888", "face_attestation": "forged.body"}, headers=auth(token))
    assert forged.status_code == 403
    assert forged.json()["detail"]["code"] == "MFA_FAILED"

    challenge = client.post("/api/v1/auth/mfa/face/challenge", json={"auth_token": token, "user_id": "user_zhang"}, headers=auth(token))
    assert challenge.status_code == 200, challenge.text
    attestation = sign_face_attestation(challenge.json()["challenge_id"], user_id="user_zhang", token_jti=challenge.json()["auth_token_jti"])
    ok = client.post("/api/v1/auth/mfa/verify", json={"otp": "888888", "face_attestation": attestation}, headers=auth(token))
    assert ok.status_code == 200, ok.text

    # The consumed challenge/jti binding must not open a different token.
    second_precheck = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 2001},
        headers=auth(),
    )
    assert second_precheck.status_code == 200
    reuse = client.post(
        "/api/v1/auth/mfa/verify",
        json={"auth_token": second_precheck.json()["auth_token"], "otp": "888888", "face_attestation": attestation},
        headers=auth(second_precheck.json()["auth_token"]),
    )
    assert reuse.status_code == 403


def test_face_challenge_requires_red_token_and_identity(client: TestClient):
    precheck = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 10},
        headers=auth(),
    )
    assert precheck.status_code == 200
    assert precheck.json()["risk_level"] == "YELLOW"
    token = precheck.json()["auth_token"]
    not_red = client.post("/api/v1/auth/mfa/face/challenge", json={"auth_token": token, "user_id": "user_zhang"}, headers=auth(token))
    assert not_red.status_code == 400
    assert not_red.json()["detail"]["code"] == "MFA_NOT_REQUIRED"


def test_operation_context_cannot_change_after_approval(client: TestClient):
    precheck = client.post(
        "/api/v1/risk/precheck/operation",
        json={"user_id": "user_zhang", "operation": "SUBSCRIPTION_CANCEL", "target": "Apple"},
        headers=auth(),
    )
    assert precheck.status_code == 200, precheck.text
    token = precheck.json()["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers=auth(token)).status_code == 200

    changed = client.post(
        "/api/v1/operations/execute",
        json={
            "user_id": "user_zhang",
            "operation": "SUBSCRIPTION_CANCEL",
            "target": "Apple",
            "merchant": "视频会员",
            "auth_token": token,
        },
        headers=auth(token),
    )
    assert changed.status_code == 400
    assert changed.json()["detail"]["code"] == "TOKEN_CONTEXT_MISMATCH"


def test_generic_precheck_requires_result_defining_slots(client: TestClient):
    missing_product = client.post(
        "/api/v1/risk/precheck/operation",
        json={"user_id": "user_zhang", "operation": "WEALTH_PURCHASE", "amount": 1000},
        headers=auth(),
    )
    assert missing_product.status_code == 400
    missing_merchant = client.post(
        "/api/v1/risk/precheck/operation",
        json={"user_id": "user_zhang", "operation": "SUBSCRIPTION_CANCEL"},
        headers=auth(),
    )
    assert missing_merchant.status_code == 400


def test_split_participants_must_resolve_to_contacts(client: TestClient):
    """AA notifications may target only active Core contacts."""

    unknown = client.post(
        "/api/v1/risk/precheck/operation",
        json={
            "user_id": "user_zhang",
            "operation": "SPLIT_BILL",
            "amount": 300,
            "participants": ["未登记联系人"],
        },
        headers=auth(),
    )
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["code"] == "TARGET_NOT_FOUND"

    duplicate_account = client.post(
        "/api/v1/risk/precheck/operation",
        json={
            "user_id": "user_zhang",
            "operation": "SPLIT_BILL",
            "amount": 300,
            "participants": ["李四", "13900139000"],
        },
        headers=auth(),
    )
    assert duplicate_account.status_code == 400
    assert duplicate_account.json()["detail"]["code"] == "INVALID_PARTICIPANTS"

    known = client.post(
        "/api/v1/risk/precheck/operation",
        json={
            "user_id": "user_zhang",
            "operation": "SPLIT_BILL",
            "amount": 300,
            "participants": ["李四", "王美"],
        },
        headers=auth(),
    )
    assert known.status_code == 200, known.text


def test_convenience_route_cannot_override_operation_name(client: TestClient):
    response = client.post(
        "/api/v1/cards/apply",
        json={"user_id": "user_zhang", "operation": "CARD_FREEZE"},
        headers=auth(),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "OPERATION_ROUTE_MISMATCH"


def test_rotated_audit_chain_remains_verifiable(monkeypatch):
    security.reset_security_state()
    patch_security(monkeypatch, "AUDIT_MAX_EVENTS", 3)
    for index in range(6):
        security.audit_event("TEST", metadata={"index": index, "access_token": "secret-value"})
    assert len(security.AUDIT_LOGS) == 3
    assert security.verify_audit_chain()["valid"]
    assert security.AUDIT_LOGS[-1]["metadata"]["access_token"] == "[REDACTED]"
    security.reset_security_state()


def test_http_edge_sets_no_store_and_rejects_oversized_body(client: TestClient):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    api_response = client.get("/api/v1/accounts/user_zhang/balance", headers=auth())
    assert api_response.headers["cache-control"] == "no-store"

    old_limit = http_edge._MAX_REQUEST_BYTES
    http_edge._MAX_REQUEST_BYTES = 32
    try:
        oversized = client.post("/api/v1/risk/precheck", json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 10}, headers=auth())
        assert oversized.status_code == 413
        assert oversized.headers["x-content-type-options"] == "nosniff"
        assert oversized.headers["cache-control"] == "no-store"
    finally:
        http_edge._MAX_REQUEST_BYTES = old_limit


def test_lock_failure_rolls_back_ledger_and_tasks(client: TestClient, monkeypatch):
    before = client.get("/api/v1/accounts/user_zhang/balance", headers=auth()).json()
    pre = client.post(
        "/api/v1/risk/precheck/lock",
        json={"user_id": "user_zhang", "amount": 100, "reason": "回滚测试"},
        headers=auth(),
    )
    assert pre.status_code == 200
    token = pre.json()["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers=auth(token)).status_code == 200

    def fail_create(*args, **kwargs):
        raise RuntimeError("simulated task-store outage")

    monkeypatch.setattr(main.TASK_STORE, "create", fail_create)
    failed = client.post(
        "/api/v1/accounts/lock_fund",
        json={"user_id": "user_zhang", "amount": 100, "reason": "回滚测试"},
        headers=auth(token),
    )
    assert failed.status_code == 500
    after = client.get("/api/v1/accounts/user_zhang/balance", headers=auth()).json()
    assert after["total_balance"] == before["total_balance"]
    assert after["locked_amount"] == before["locked_amount"]
    assert not main.LOCKS


def test_cancelling_scheduled_task_releases_risk_reservation(client: TestClient):
    scheduled_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    pre = client.post(
        "/api/v1/risk/precheck/operation",
        json={"user_id": "user_zhang", "operation": "SCHEDULED_TRANSFER", "target": "李四", "amount": 600, "scheduled_at": scheduled_at},
        headers=auth(),
    )
    assert pre.status_code == 200 and pre.json()["risk_level"] == "YELLOW"
    token = pre.json()["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers=auth(token)).status_code == 200
    execute = client.post(
        "/api/v1/operations/execute",
        json={"user_id": "user_zhang", "operation": "SCHEDULED_TRANSFER", "target": "李四", "amount": 600, "scheduled_at": scheduled_at, "auth_token": token},
        headers=auth(token),
    )
    assert execute.status_code == 200, execute.text
    task_id = execute.json()["data"]["task_id"]
    cancelled = client.post(f"/api/v1/tasks/{task_id}/cancel", json={}, headers=auth())
    assert cancelled.status_code == 200
    assert main.SCHEDULED_TRANSFERS[0]["status"] == "CANCELLED"

    next_pre = client.post(
        "/api/v1/risk/precheck/operation",
        json={"user_id": "user_zhang", "operation": "SCHEDULED_TRANSFER", "target": "李四", "amount": 600, "scheduled_at": scheduled_at},
        headers=auth(),
    )
    assert next_pre.status_code == 200 and next_pre.json()["risk_level"] == "YELLOW"


def test_birthday_order_drafts_pause_for_confirmation_and_cascade_cancel(client: TestClient, monkeypatch):
    # Force the user-scoped branch of /tasks/run-due so the test is independent
    # of whether a deployment configured BANK_ADMIN_AUDIT_TOKEN.
    monkeypatch.delenv("BANK_ADMIN_AUDIT_TOKEN", raising=False)
    event_at = (datetime.now(timezone.utc) + timedelta(days=4)).isoformat()
    pre = client.post(
        "/api/v1/risk/precheck/lock",
        json={"user_id": "user_zhang", "amount": 100, "reason": "生日预算", "event_at": event_at},
        headers=auth(),
    )
    assert pre.status_code == 200, pre.text
    token = pre.json()["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers=auth(token)).status_code == 200
    locked = client.post(
        "/api/v1/accounts/lock_fund",
        json={"user_id": "user_zhang", "amount": 100, "reason": "生日预算", "event_at": event_at},
        headers=auth(token),
    )
    assert locked.status_code == 200, locked.text
    body = locked.json()
    assert len(body["order_task_ids"]) == 2

    # Cancelling the prerequisite before it is delivered cascades to the two
    # dependent drafts, so neither can later become runnable.
    cancelled = client.post(f"/api/v1/tasks/{body['reminder_task_id']}/cancel", json={}, headers=auth())
    assert cancelled.status_code == 200
    current = {task["task_id"]: task for task in client.get("/api/v1/tasks", headers=auth()).json()["data"]}
    assert all(current[item]["status"] == "CANCELLED" for item in body["order_task_ids"])

    # A separate reservation demonstrates the due-time gate: order drafts
    # pause for a fresh decision instead of placing a merchant order.
    event_at = (datetime.now(timezone.utc) + timedelta(days=4)).isoformat()
    pre = client.post(
        "/api/v1/risk/precheck/lock",
        json={"user_id": "user_zhang", "amount": 100, "reason": "生日预算2", "event_at": event_at},
        headers=auth(),
    )
    token = pre.json()["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers=auth(token)).status_code == 200
    locked = client.post(
        "/api/v1/accounts/lock_fund",
        json={"user_id": "user_zhang", "amount": 100, "reason": "生日预算2", "event_at": event_at},
        headers=auth(token),
    )
    assert locked.status_code == 200, locked.text
    body = locked.json()
    for task in main.TASK_STORE.list(owner_id="user_zhang"):
        if task.task_id in set(body["order_task_ids"]) or task.task_id == body["reminder_task_id"]:
            task.run_at = "2000-01-01T00:00:00+00:00"
    due = client.post("/api/v1/tasks/run-due", headers=auth())
    assert due.status_code == 200
    current = {task["task_id"]: task for task in client.get("/api/v1/tasks", headers=auth()).json()["data"]}
    assert all(current[item]["status"] == "WAITING_USER" for item in body["order_task_ids"])


def test_large_fund_reservation_requires_red_mfa(client: TestClient):
    pre = client.post(
        "/api/v1/risk/precheck/lock",
        json={"user_id": "user_zhang", "amount": 2000, "reason": "大额生日预算", "event_at": "2099-01-01T09:00:00+00:00"},
        headers=auth(),
    )
    assert pre.status_code == 200, pre.text
    assert pre.json()["risk_level"] == "RED"
    token = pre.json()["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers=auth(token)).status_code == 200
    blocked = client.post(
        "/api/v1/accounts/lock_fund",
        json={"user_id": "user_zhang", "amount": 2000, "reason": "大额生日预算", "event_at": "2099-01-01T09:00:00+00:00", "auth_token": token},
        headers=auth(token),
    )
    assert blocked.status_code == 403
    challenge = client.post("/api/v1/auth/mfa/face/challenge", json={"auth_token": token, "user_id": "user_zhang"}, headers=auth(token))
    assert challenge.status_code == 200, challenge.text
    attestation = sign_face_attestation(challenge.json()["challenge_id"], user_id="user_zhang", token_jti=challenge.json()["auth_token_jti"])
    mfa = client.post("/api/v1/auth/mfa/verify", json={"otp": "888888", "face_attestation": attestation}, headers=auth(token))
    assert mfa.status_code == 200
    completed = client.post(
        "/api/v1/accounts/lock_fund",
        json={"user_id": "user_zhang", "amount": 2000, "reason": "大额生日预算", "event_at": "2099-01-01T09:00:00+00:00", "auth_token": token, "mfa_proof": mfa.json()["mfa_proof"]},
        headers={**auth(token), "X-MFA-Proof": mfa.json()["mfa_proof"]},
    )
    assert completed.status_code == 200, completed.text
    # The lock audit must record the real (RED) risk level, not a hardcoded one.
    logs = client.get("/api/v1/security/audit", headers=auth()).json()["data"]
    lock_events = [event for event in logs if event["action"] == "LOCK_FUND" and event["outcome"] == "SUCCESS"]
    assert lock_events and lock_events[-1]["risk_level"] == "RED"


def test_audit_trace_reconstructs_decision_chain(client: TestClient, monkeypatch):
    """One X-Request-Id must reconstruct precheck → confirm → settle → consume."""

    request_id = "req_e2e_trace_0001"
    precheck = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 10},
        headers={**auth(), "X-Request-Id": request_id},
    )
    assert precheck.status_code == 200, precheck.text
    token = precheck.json()["auth_token"]
    confirm = client.post("/api/v1/auth/confirm", json={}, headers={**auth(token), "X-Request-Id": request_id})
    assert confirm.status_code == 200
    settled = client.post(
        "/api/v1/transfers/execute",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 10},
        headers={**auth(token), "X-Request-Id": request_id},
    )
    assert settled.status_code == 200, settled.text

    trace = client.get(f"/api/v1/security/audit/trace/{request_id}", headers=auth())
    assert trace.status_code == 200, trace.text
    body = trace.json()
    assert body["principal"] == "user_zhang"
    assert body["stages"][0] == "RISK_PRECHECK"
    # The token is consumed while the authorized mutation block exits, so the
    # endpoint's own success event is the terminal stage of the chain.
    assert body["stages"][-1] == "TRANSFER_EXECUTE"
    assert {"RISK_PRECHECK", "USER_CONFIRMATION", "TRANSFER_EXECUTE", "AUTH_TOKEN_CONSUME"}.issubset(set(body["stages"]))
    assert body["terminal_outcome"] == "SUCCESS"
    assert all(event["request_id"] == request_id for event in body["events"])

    # A different subject may not read someone else's decision chain.
    cross_user = client.get(f"/api/v1/security/audit/trace/{request_id}", headers={"X-User-Id": "user_li"})
    assert cross_user.status_code == 403
    # An unknown request id is indistinguishable from someone else's request
    # for a normal user (no enumeration oracle).
    missing = client.get("/api/v1/security/audit/trace/req_does_not_exist", headers=auth())
    assert missing.status_code == 403
    monkeypatch.setenv("BANK_ADMIN_AUDIT_TOKEN", "admin-audit-token")
    admin_view = client.get("/api/v1/security/audit/trace/req_does_not_exist", headers={"X-Admin-Token": "admin-audit-token"})
    assert admin_view.status_code == 200
    assert admin_view.json()["stage_count"] == 0
    admin_trace = client.get(f"/api/v1/security/audit/trace/{request_id}", headers={"X-Admin-Token": "admin-audit-token"})
    assert admin_trace.status_code == 200
    assert admin_trace.json()["principal"] == "user_zhang"


def test_subscription_match_and_card_state_are_fail_closed(client: TestClient):
    main.SUBSCRIPTIONS["user_zhang"].append(
        {"subscription_id": "SUB-APPLE-2", "merchant": "Apple 订阅", "amount": 9.0, "next_charge": "2099-01-01", "status": "ACTIVE"}
    )
    ambiguous = client.post(
        "/api/v1/risk/precheck/operation",
        json={"user_id": "user_zhang", "operation": "SUBSCRIPTION_CANCEL", "target": "Apple"},
        headers=auth(),
    )
    assert ambiguous.status_code == 409
    assert ambiguous.json()["detail"]["code"] == "SUBSCRIPTION_AMBIGUOUS"

    main.CARDS["user_li"] = []
    missing_card = client.post(
        "/api/v1/risk/precheck/operation",
        json={"user_id": "user_li", "operation": "CARD_FREEZE"},
        headers={"X-User-Id": "user_li"},
    )
    assert missing_card.status_code == 404
    assert missing_card.json()["detail"]["code"] == "CARD_NOT_FOUND"


def test_revoking_the_session_invalidates_an_in_flight_authorization(client: TestClient):
    """Logout (or credential rotation) must kill approvals already minted.

    Regression: the authorization nonce is signed independently of any session
    and the execute endpoint identified the caller from a self-asserted header,
    so an approval obtained before a logout still settled afterwards - the
    login was cut off while the money was not.
    """

    login = client.post("/api/v1/auth/login", json={"user_id": "user_zhang", "password": "Bank@2026"})
    assert login.status_code == 200, login.text
    session = login.json()["session_token"]
    with_session = {"X-User-Id": "user_zhang", "X-Session-Token": session}

    precheck = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 100},
        headers=with_session,
    )
    assert precheck.status_code == 200, precheck.text
    token = precheck.json()["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers={**with_session, "X-Auth-Token": token}).status_code == 200

    assert client.post("/api/v1/auth/logout", json={}, headers=with_session).status_code == 200
    assert client.get("/api/v1/accounts/user_zhang/balance", headers=with_session).status_code == 401

    balance_before = main.ACCOUNTS["user_zhang"]["balance"]
    settled = client.post(
        "/api/v1/transfers/execute",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 100, "auth_token": token},
        headers={"X-User-Id": "user_zhang", "X-Auth-Token": token},
    )

    assert settled.status_code == 401, settled.text
    assert settled.json()["detail"]["code"] in {"SESSION_REQUIRED", "SESSION_REVOKED"}
    assert main.ACCOUNTS["user_zhang"]["balance"] == balance_before


def test_an_approval_from_a_session_survives_while_that_session_lives(client: TestClient):
    """The binding must not break the ordinary logged-in flow."""

    login = client.post("/api/v1/auth/login", json={"user_id": "user_zhang", "password": "Bank@2026"})
    session = login.json()["session_token"]
    with_session = {"X-User-Id": "user_zhang", "X-Session-Token": session}

    precheck = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 100},
        headers=with_session,
    )
    token = precheck.json()["auth_token"]
    client.post("/api/v1/auth/confirm", json={}, headers={**with_session, "X-Auth-Token": token})

    settled = client.post(
        "/api/v1/transfers/execute",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 100, "auth_token": token},
        headers={**with_session, "X-Auth-Token": token},
    )

    assert settled.status_code == 200, settled.text
    assert settled.json()["status"] == "SUCCESS"


def test_a_wrong_code_with_a_valid_device_signature_counts_once_and_says_why(client: TestClient):
    """Real-device report: the fingerprint passed, only the code was wrong, and the
    breaker opened after three tries because each one was counted twice."""

    precheck = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 2000},
        headers=auth(),
    )
    token = precheck.json()["auth_token"]
    challenge = client.post("/api/v1/auth/mfa/face/challenge", json={"auth_token": token, "user_id": "user_zhang"}, headers=auth(token))
    attestation = sign_face_attestation(challenge.json()["challenge_id"], user_id="user_zhang", token_jti=challenge.json()["auth_token_jti"])
    wrong = client.post("/api/v1/auth/mfa/verify", json={"otp": "123456", "face_attestation": attestation}, headers=auth(token))
    assert wrong.status_code == 403
    message = wrong.json()["detail"]["message"]
    assert "短信验证码不正确" in message and "设备验证已通过" in message
    assert security.circuit_status("user_zhang")["failure_count"] == 1
