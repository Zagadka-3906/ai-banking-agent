from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import main
from planner import build_task_plan
from support import sign_face_attestation


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


def precheck(client: TestClient, **body):
    return client.post("/api/v1/risk/precheck/operation", json={"user_id": "user_zhang", "operation": "FRAUD_PROTECT", **body}, headers={**h(), "Idempotency-Key": "fraud-precheck-0001"})


def authorize(client: TestClient):
    pre = precheck(client)
    assert pre.status_code == 200, pre.text
    data = pre.json()
    assert data["risk_level"] == "RED"
    assert data["transaction"]["tx_id"] == "TX1004"
    assert data["target"] == "CARD-001"
    token = data["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers=h(token)).status_code == 200
    challenge = client.post("/api/v1/auth/mfa/face/challenge", json={"auth_token": token, "user_id": "user_zhang"}, headers=h(token))
    assert challenge.status_code == 200
    attestation = sign_face_attestation(challenge.json()["challenge_id"], user_id="user_zhang", token_jti=challenge.json()["auth_token_jti"])
    mfa = client.post("/api/v1/auth/mfa/verify", json={"otp": "888888", "face_attestation": attestation}, headers=h(token))
    assert mfa.status_code == 200, mfa.text
    return token, mfa.json()["mfa_proof"]


def test_plan_has_ordered_five_step_protection_dag():
    plan = build_task_plan({"intent": "FRAUD_DISPUTE"})
    assert [task.action for task in plan.tasks] == ["VERIFY_TRANSACTION", "EXPLAIN_RISK", "MFA_CONFIRM", "FREEZE_CARD", "CREATE_DISPUTE_CASE"]
    assert plan.tasks[-1].depends_on == ["freeze_card"]


def test_precheck_is_red_and_does_not_mutate_state(client):
    response = precheck(client)
    assert response.status_code == 200, response.text
    assert main.CARDS["user_zhang"][0]["status"] == "ACTIVE"
    assert main.DISPUTE_CASES == []
    assert "境外交易" in response.json()["risk_reasons"]


def test_missing_mfa_does_not_freeze_or_create_case(client):
    pre = precheck(client)
    token = pre.json()["auth_token"]
    response = client.post("/api/v1/operations/execute", json={"user_id": "user_zhang", "operation": "FRAUD_PROTECT", "transaction_id": "TX1004", "auth_token": token, "user_confirmed": True}, headers={**h(token), "Idempotency-Key": "fraud-execute-missing-mfa"})
    assert response.status_code >= 400
    assert main.CARDS["user_zhang"][0]["status"] == "ACTIVE"
    assert main.DISPUTE_CASES == []


def test_wrong_owner_is_rejected(client):
    wrong = client.post("/api/v1/risk/precheck/operation", json={"user_id": "user_li", "operation": "FRAUD_PROTECT", "transaction_id": "TX1004"}, headers={"X-User-Id": "user_li", "Idempotency-Key": "fraud-wrong-owner"})
    assert wrong.status_code >= 400


def test_transaction_amount_tamper_invalidates_existing_authorization(client):
    token, proof = authorize(client)
    next(row for row in main.TRANSACTIONS if row["tx_id"] == "TX1004")["amount"] = 1.0
    changed = client.post("/api/v1/operations/execute", json={"user_id": "user_zhang", "operation": "FRAUD_PROTECT", "transaction_id": "TX1004", "auth_token": token, "mfa_proof": proof, "user_confirmed": True}, headers={**h(token), "Idempotency-Key": "fraud-tampered-amount"})
    assert changed.status_code >= 400
    assert main.CARDS["user_zhang"][0]["status"] == "ACTIVE"
    assert main.DISPUTE_CASES == []


def test_card_binding_tamper_invalidates_existing_authorization(client):
    token, proof = authorize(client)
    next(row for row in main.TRANSACTIONS if row["tx_id"] == "TX1004")["card_id"] = "CARD-TAMPERED"
    changed = client.post("/api/v1/operations/execute", json={"user_id": "user_zhang", "operation": "FRAUD_PROTECT", "transaction_id": "TX1004", "auth_token": token, "mfa_proof": proof, "user_confirmed": True}, headers={**h(token), "Idempotency-Key": "fraud-tampered-card"})
    assert changed.status_code >= 400
    assert main.CARDS["user_zhang"][0]["status"] == "ACTIVE"
    assert main.DISPUTE_CASES == []


def test_valid_flow_freezes_linked_card_and_is_idempotent(client):
    token, proof = authorize(client)
    body = {"user_id": "user_zhang", "operation": "FRAUD_PROTECT", "transaction_id": "TX1004", "auth_token": token, "mfa_proof": proof, "user_confirmed": True}
    first = client.post("/api/v1/operations/execute", json=body, headers={**h(token), "Idempotency-Key": "fraud-execute-0001"})
    assert first.status_code == 200, first.text
    assert first.json()["data"]["status"] == "CARD_PROTECTED"
    assert first.json()["data"]["dispute_case"]["status"] == "PENDING_REVIEW"
    case = first.json()["data"]["dispute_case"]
    assert case["transaction_snapshot_hash"]
    assert case["protection_status"] == "CARD_PROTECTED"
    assert case["risk_reasons"]
    assert main.CARDS["user_zhang"][0]["status"] == "FROZEN"
    assert len(main.DISPUTE_CASES) == 1
    retry = client.post("/api/v1/operations/execute", json=body, headers={**h(token), "Idempotency-Key": "fraud-execute-0001"})
    assert retry.status_code == 200
    assert retry.json()["data"]["dispute_case"]["case_id"] == first.json()["data"]["dispute_case"]["case_id"]
    assert len(main.DISPUTE_CASES) == 1
