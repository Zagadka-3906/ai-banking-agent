"""A settled operation remains replayable after changing its prerequisites."""

import pytest
from fastapi.testclient import TestClient

import main
from support import sign_face_attestation


HEADERS = {"X-User-Id": "user_zhang", "Idempotency-Key": "operation-replay-regression"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    main.reset_state()
    monkeypatch.setenv("BANK_LEDGER_STORE_PATH", str(tmp_path / "ledger.json"))
    with TestClient(main.app) as value:
        yield value
    main.reset_state()


def approve(client, body):
    pre = client.post("/api/v1/risk/precheck/operation", json=body, headers=HEADERS)
    assert pre.status_code == 200, pre.text
    token = pre.json()["auth_token"]
    headers = {**HEADERS, "X-Auth-Token": token}
    assert client.post("/api/v1/auth/confirm", json={}, headers=headers).status_code == 200
    proof = None
    if pre.json()["risk_level"] == "RED":
        challenge = client.post("/api/v1/auth/mfa/face/challenge", json={"auth_token": token, "user_id": "user_zhang"}, headers=headers)
        assert challenge.status_code == 200, challenge.text
        data = challenge.json()
        attestation = sign_face_attestation(data["challenge_id"], user_id="user_zhang", token_jti=data["auth_token_jti"])
        verified = client.post("/api/v1/auth/mfa/verify", json={"otp": "888888", "face_attestation": attestation}, headers=headers)
        assert verified.status_code == 200, verified.text
        proof = verified.json()["mfa_proof"]
    return {**body, "auth_token": token, "mfa_proof": proof}


@pytest.mark.parametrize("operation", ["SUBSCRIPTION_CANCEL", "CARD_FREEZE"])
@pytest.mark.parametrize("restart", [False, True])
def test_replay_precedes_changed_business_state(client, operation, restart):
    body = {"user_id": "user_zhang", "operation": operation}
    if operation == "SUBSCRIPTION_CANCEL":
        body["target"] = main.SUBSCRIPTIONS["user_zhang"][0]["subscription_id"]
    else:
        body["card_id"] = "CARD-001"
    execution = approve(client, body)
    first = client.post("/api/v1/operations/execute", json=execution, headers=HEADERS)
    assert first.status_code == 200, first.text
    if restart:
        main.reset_state()
        assert main.load_ledger_store()["loaded_state"] is True
    repeated = client.post("/api/v1/operations/execute", json=execution, headers=HEADERS)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json() == first.json()
    changed = client.post("/api/v1/operations/execute", json={**execution, "reason": "different request"}, headers=HEADERS)
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSE"
    foreign = client.post("/api/v1/operations/execute", json=execution, headers={**HEADERS, "X-User-Id": "user_li"})
    assert foreign.status_code == 403


def test_memory_receipt_is_not_mutated_by_later_card_state(client):
    body = {"user_id": "user_zhang", "operation": "CARD_FREEZE", "card_id": "CARD-001"}
    execution = approve(client, body)
    first = client.post("/api/v1/operations/execute", json=execution, headers=HEADERS)
    assert first.status_code == 200, first.text
    main.CARDS["user_zhang"][0]["status"] = "ACTIVE"
    main.OPERATION_RECEIPTS.clear()  # Exercise the in-memory receipt fallback.
    repeated = client.post("/api/v1/operations/execute", json=execution, headers=HEADERS)
    assert repeated.json() == first.json()
    assert repeated.json()["data"]["card"]["status"] == "FROZEN"
