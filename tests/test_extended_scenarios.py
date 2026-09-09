from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import main


@pytest.fixture()
def client():
    main.reset_state()
    with TestClient(main.app) as value:
        yield value
    main.reset_state()


def headers(token: str | None = None):
    result = {"X-User-Id": "user_zhang"}
    if token:
        result["X-Auth-Token"] = token
    return result


def approve(client: TestClient, operation: str, **kwargs):
    body = {"user_id": "user_zhang", "operation": operation, **kwargs}
    pre = client.post("/api/v1/risk/precheck/operation", json=body, headers=headers())
    assert pre.status_code == 200, pre.text
    token = pre.json()["auth_token"]
    ok = client.post("/api/v1/auth/confirm", json={}, headers=headers(token))
    assert ok.status_code == 200, ok.text
    return token


def approve_red(client: TestClient, operation: str, **kwargs):
    token = approve(client, operation, **kwargs)
    mfa = client.post("/api/v1/auth/mfa/verify", json={"otp": "888888", "face_verified": True}, headers=headers(token))
    assert mfa.status_code == 200, mfa.text
    return token, mfa.json()["mfa_proof"]


def test_last_month_query_is_filtered(client: TestClient):
    response = client.get("/api/v1/accounts/user_zhang/transactions?date_scope=LAST_MONTH", headers=headers())
    assert response.status_code == 200
    rows = response.json()["data"]
    assert rows and all(row["time"].startswith("2026-08") for row in rows)


def test_subscription_cancel_and_card_apply_use_approval_gate(client: TestClient):
    token = approve(client, "SUBSCRIPTION_CANCEL", target="Apple")
    cancelled = client.post(
        "/api/v1/operations/execute",
        json={"user_id": "user_zhang", "operation": "SUBSCRIPTION_CANCEL", "target": "Apple", "merchant": "Apple", "auth_token": token},
        headers=headers(token),
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"]["subscription"]["status"] == "CANCELLED"

    token = approve(client, "CARD_APPLY")
    issued = client.post(
        "/api/v1/cards/apply",
        json={"user_id": "user_zhang", "auth_token": token},
        headers=headers(token),
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["data"]["card"]["type"] == "VIRTUAL"


def test_wealth_purchase_requires_server_mfa(client: TestClient):
    pre = client.post(
        "/api/v1/risk/precheck/operation",
        json={"user_id": "user_zhang", "operation": "WEALTH_PURCHASE", "product_id": "MMF001", "amount": 1000, "reason": "长期储蓄"},
        headers=headers(),
    )
    assert pre.status_code == 200 and pre.json()["risk_level"] == "RED"
    token = pre.json()["auth_token"]
    client.post("/api/v1/auth/confirm", json={}, headers=headers(token))
    blocked = client.post("/api/v1/operations/execute", json={"user_id": "user_zhang", "operation": "WEALTH_PURCHASE", "product_id": "MMF001", "amount": 1000, "reason": "长期储蓄", "auth_token": token}, headers=headers(token))
    assert blocked.status_code in {403, 428}
    mfa = client.post("/api/v1/auth/mfa/verify", json={"otp": "888888", "face_verified": True}, headers=headers(token))
    assert mfa.status_code == 200
    completed = client.post("/api/v1/operations/execute", json={"user_id": "user_zhang", "operation": "WEALTH_PURCHASE", "product_id": "MMF001", "amount": 1000, "reason": "长期储蓄", "auth_token": token, "mfa_proof": mfa.json()["mfa_proof"]}, headers={**headers(token), "X-MFA-Proof": mfa.json()["mfa_proof"]})
    assert completed.status_code == 200, completed.text


def test_risk_assessment_is_available_to_agent_and_compare_is_source_bound(client: TestClient):
    assessment = client.post(
        "/api/v1/wealth/risk-assessment",
        json={"user_id": "user_zhang", "horizon_years": 3, "loss_tolerance": "MEDIUM", "experience": "BASIC"},
        headers=headers(),
    )
    assert assessment.status_code == 200, assessment.text
    assert assessment.json()["risk_profile"] == "BALANCED"
    assert main.WEALTH_RISK_PROFILES["user_zhang"]["risk_profile"] == "BALANCED"

    comparison = client.get(
        "/api/v1/wealth/compare?product_ids=MMF001,BOND002",
        headers=headers(),
    )
    assert comparison.status_code == 200, comparison.text
    assert [item["product_id"] for item in comparison.json()["data"]] == ["MMF001", "BOND002"]


def test_purchase_requires_a_recorded_risk_assessment(client: TestClient):
    main.WEALTH_RISK_PROFILES.pop("user_zhang", None)
    blocked = client.post(
        "/api/v1/risk/precheck/operation",
        json={"user_id": "user_zhang", "operation": "WEALTH_PURCHASE", "product_id": "MMF001", "amount": 100},
        headers=headers(),
    )
    assert blocked.status_code == 428
    assert blocked.json()["detail"]["code"] == "RISK_ASSESSMENT_REQUIRED"


def test_password_change_is_red_and_card_lost_state_has_explicit_unreport(client: TestClient):
    token, proof = approve_red(client, "PASSWORD_CHANGE")
    version_before = main.ACCOUNTS["user_zhang"]["credential_version"]
    changed = client.post(
        "/api/v1/auth/password/change",
        json={"user_id": "user_zhang", "auth_token": token},
        headers={**headers(token), "X-MFA-Proof": proof},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["data"]["status"] == "PASSWORD_CHANGED"
    assert main.ACCOUNTS["user_zhang"]["credential_version"] == version_before + 1

    lost_token, lost_proof = approve_red(client, "CARD_REPORT_LOST")
    lost = client.post(
        "/api/v1/cards/report-lost",
        json={"user_id": "user_zhang", "auth_token": lost_token},
        headers={**headers(lost_token), "X-MFA-Proof": lost_proof},
    )
    assert lost.status_code == 200
    restore_token, restore_proof = approve_red(client, "CARD_UNREPORT_LOST")
    restored = client.post(
        "/api/v1/cards/unreport-lost",
        json={"user_id": "user_zhang", "auth_token": restore_token},
        headers={**headers(restore_token), "X-MFA-Proof": restore_proof},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["card"]["status"] == "ACTIVE"


def test_task_takeover_is_owner_scoped(client: TestClient):
    token = client.post("/api/v1/risk/precheck/lock", json={"user_id": "user_zhang", "amount": 10, "reason": "测试", "event_at": "2099-01-01T09:00:00+08:00"}, headers=headers()).json()["auth_token"]
    client.post("/api/v1/auth/confirm", json={}, headers=headers(token))
    locked = client.post("/api/v1/accounts/lock_fund", json={"user_id": "user_zhang", "amount": 10, "reason": "测试", "event_at": "2099-01-01T09:00:00+08:00"}, headers=headers(token)).json()
    # The reservation task is already SUCCEEDED; human takeover applies to
    # the pending reminder/work item instead of reopening a committed step.
    task_id = locked.get("reminder_task_id") or locked["task_id"]
    takeover = client.post(f"/api/v1/tasks/{task_id}/takeover", json={"reason": "需要人工复核"}, headers=headers())
    assert takeover.status_code == 200
    assert takeover.json()["data"]["status"] == "HUMAN_TAKEOVER"
