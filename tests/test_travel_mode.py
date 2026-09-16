from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import main
from agent import _local_parse
from planner import build_task_plan, missing_slots
from security import sign_face_attestation


@pytest.fixture()
def client():
    main.reset_state()
    with TestClient(main.app) as value:
        yield value
    main.reset_state()


def headers(token=None):
    result = {"X-User-Id": "user_zhang"}
    if token:
        result["X-Auth-Token"] = token
    return result


def body():
    return {"user_id": "user_zhang", "operation": "TRAVEL_MODE_ACTIVATE", "card_id": "CARD-001", "destination": "日本", "currency": "JPY", "start_at": "2027-01-10", "return_at": "2027-01-15", "budget": 8000, "temporary_limit": 10000}


def authorize(client):
    pre = client.post("/api/v1/risk/precheck/operation", json=body(), headers={**headers(), "Idempotency-Key": "travel-precheck"})
    assert pre.status_code == 200, pre.text
    data = pre.json()
    assert data["risk_level"] == "RED"
    assert data["travel_preview"]["fx_snapshot"]["source"] == "MOCK"
    assert data["travel_preview"]["fx_snapshot"]["rate"] > 1
    assert data["travel_preview"]["budget_plan"]["trip_days"] == 5
    assert sum(row["amount_cny"] for row in data["travel_preview"]["budget_plan"]["categories"]) == 8000
    token = data["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers=headers(token)).status_code == 200
    challenge = client.post("/api/v1/auth/mfa/face/challenge", json={"auth_token": token, "user_id": "user_zhang"}, headers=headers(token))
    assert challenge.status_code == 200
    attestation = sign_face_attestation(challenge.json()["challenge_id"], user_id="user_zhang", token_jti=challenge.json()["auth_token_jti"])
    mfa = client.post("/api/v1/auth/mfa/verify", json={"otp": "888888", "face_attestation": attestation}, headers=headers(token))
    assert mfa.status_code == 200
    return token, mfa.json()["mfa_proof"]


def test_travel_activation_is_red_and_no_money_movement(client):
    before = main.ACCOUNTS["user_zhang"]["balance"]
    before_transactions = list(main.TRANSACTIONS)
    token, proof = authorize(client)
    assert main.CARDS["user_zhang"][0]["overseas_daily_limit"] == 3000
    assert main.TRAVEL_PLANS == []
    assert main.ACCOUNTS["user_zhang"]["balance"] == before
    assert main.TRANSACTIONS == before_transactions
    result = client.post("/api/v1/operations/execute", json={**body(), "auth_token": token, "mfa_proof": proof, "user_confirmed": True}, headers={**headers(token), "Idempotency-Key": "travel-execute"})
    assert result.status_code == 200, result.text
    data = result.json()["data"]
    assert data["status"] == "TRAVEL_MODE_ACTIVE"
    assert data["funds_debited"] is False
    assert data["esim_offer"]["execution_mode"] == "MOCK"
    assert main.CARDS["user_zhang"][0]["overseas_daily_limit"] == 10000
    assert main.ACCOUNTS["user_zhang"]["balance"] == before
    assert main.TRANSACTIONS == before_transactions
    assert len(main.TRAVEL_PLANS) == 1


def test_missing_mfa_does_not_change_limit(client):
    pre = client.post("/api/v1/risk/precheck/operation", json=body(), headers={**headers(), "Idempotency-Key": "travel-precheck-missing"})
    result = client.post("/api/v1/operations/execute", json={**body(), "auth_token": pre.json()["auth_token"], "user_confirmed": True}, headers={**headers(pre.json()["auth_token"]), "Idempotency-Key": "travel-execute-missing"})
    assert result.status_code >= 400
    assert main.CARDS["user_zhang"][0]["overseas_daily_limit"] == 3000
    assert main.TRAVEL_PLANS == []


def test_demo_sentence_parses_to_six_step_dag():
    parsed = _local_parse("开启日本旅行模式，2027-01-10到2027-01-15，预算8000元，境外限额10000元")
    assert parsed["intent"] == "TRAVEL_MODE"
    plan = build_task_plan(parsed)
    assert [task.action for task in plan.tasks] == ["PARSE_ITINERARY", "BUILD_BUDGET", "FETCH_MOCK_FX", "MFA_CONFIRM", "ACTIVATE_TRAVEL_MODE", "SCHEDULE_LIMIT_RESTORE"]
    assert plan.tasks[-1].depends_on == ["activate_travel_mode"]
    assert missing_slots({"intent": "TRAVEL_MODE", "slots": {}}) == ["destination", "currency", "start_at", "return_at", "budget", "temporary_limit"]


def activate(client, key="travel-extra"):
    token, proof = authorize(client)
    response = client.post("/api/v1/operations/execute", json={**body(), "auth_token": token, "mfa_proof": proof, "user_confirmed": True}, headers={**headers(token), "Idempotency-Key": key})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_travel_context_tamper_is_rejected(client):
    token, proof = authorize(client)
    main.CARDS["user_zhang"][0]["overseas_daily_limit"] = 3500
    result = client.post("/api/v1/operations/execute", json={**body(), "auth_token": token, "mfa_proof": proof, "user_confirmed": True}, headers={**headers(token), "Idempotency-Key": "travel-tamper-execute"})
    assert result.status_code >= 400
    assert main.CARDS["user_zhang"][0]["overseas_daily_limit"] == 3500
    assert main.TRAVEL_PLANS == []


def test_restore_due_task_and_rebuild_after_task_store_reset(client):
    data = activate(client, "travel-restore")
    plan = main.TRAVEL_PLANS[0]
    task = main.TASK_STORE.get(data["restore_task"]["task_id"])
    plan["restore_at"] = "2020-01-01T00:00:00+00:00"
    task.run_at = plan["restore_at"]
    main.TASK_STORE.reset()
    run = client.post("/api/v1/tasks/run-due", headers=headers())
    assert run.status_code == 200, run.text
    assert main.CARDS["user_zhang"][0]["overseas_daily_limit"] == 3000
    assert plan["status"] == "RESTORED"
    assert run.json()["data"][0]["status"] == "SUCCEEDED"


def test_due_task_restores_original_limit(client):
    data = activate(client, "travel-restore-direct")
    plan = main.TRAVEL_PLANS[0]
    task = main.TASK_STORE.get(data["restore_task"]["task_id"])
    plan["restore_at"] = "2020-01-01T00:00:00+00:00"
    task.run_at = plan["restore_at"]
    run = client.post("/api/v1/tasks/run-due", headers=headers())
    assert run.status_code == 200, run.text
    assert main.CARDS["user_zhang"][0]["overseas_daily_limit"] == 3000
    assert plan["status"] == "RESTORED"
    assert main.TASK_STORE.get(task.task_id).status == "SUCCEEDED"


def test_restore_conflict_does_not_overwrite_user_limit(client):
    data = activate(client, "travel-conflict")
    plan = main.TRAVEL_PLANS[0]
    task = main.TASK_STORE.get(data["restore_task"]["task_id"])
    plan["restore_at"] = "2020-01-01T00:00:00+00:00"
    task.run_at = plan["restore_at"]
    main.CARDS["user_zhang"][0]["overseas_daily_limit"] = 12000
    run = client.post("/api/v1/tasks/run-due", headers=headers())
    assert run.status_code == 200, run.text
    assert main.CARDS["user_zhang"][0]["overseas_daily_limit"] == 12000
    assert plan["status"] == "RESTORE_CONFLICT"
    assert run.json()["data"][0]["status"] == "HUMAN_TAKEOVER"


def test_travel_plan_is_persisted_collection_and_idempotent(client):
    data = activate(client, "travel-idempotent")
    retry = client.post("/api/v1/operations/execute", json={**body(), "auth_token": "ignored"}, headers={**headers(), "Idempotency-Key": "travel-idempotent"})
    assert retry.status_code == 200
    assert retry.json()["data"]["plan"]["plan_id"] == data["plan"]["plan_id"]
    assert retry.json()["data"]["restore_task"]["task_id"] == data["restore_task"]["task_id"]
    assert len(main.TRAVEL_PLANS) == 1
    assert len([task for task in main.TASK_STORE.list(owner_id="user_zhang") if task.task_type == "TRAVEL_LIMIT_RESTORE"]) == 1
    assert "TRAVEL_PLANS" in main.LEDGER_STATE_NAMES
