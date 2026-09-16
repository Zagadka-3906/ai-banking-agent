import base64
from copy import deepcopy
import hashlib

import pytest
from fastapi.testclient import TestClient

import agent_api
import main
from artifacts import MAX_ARTIFACT_BYTES

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
BILL = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUAAZ0D3wAAAAAASUVORK5CYII="
WAV = "UklGRiUAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQEAAACA"

def payload(content, media_type, filename="mock.png", **extra):
    raw = base64.b64decode(content)
    return {"media_type": media_type, "content_base64": content,
            "sha256": hashlib.sha256(raw).hexdigest(), "filename": filename,
            "conversation_id": "c-art", **extra}

@pytest.fixture(autouse=True)
def clean():
    main.reset_state()
    agent_api.ARTIFACTS.clear()
    agent_api.ARTIFACTS_BY_OWNER_HASH.clear()
    agent_api.RATE_LIMIT_STATE.clear()
    yield
    agent_api.ARTIFACTS.clear()
    agent_api.ARTIFACTS_BY_OWNER_HASH.clear()
    agent_api.RATE_LIMIT_STATE.clear()
    main.reset_state()

def test_three_fixtures_and_idempotency_and_owner_isolation():
    client = TestClient(agent_api.app)
    for body, category in [(payload(PNG, "image/png"), "差旅-住宿"), (payload(BILL, "image/png"), "餐饮"), (payload(WAV, "audio/wav", "voice.wav"), "交通")]:
        response = client.post("/api/v1/agent/artifacts", json=body, headers={"X-User-Id": "u1"})
        assert response.status_code == 200
        data = response.json()
        assert data["provenance"] == "MOCK_FIXTURE" and data["recognition_mode"] == "MOCK_RULES"
        assert data["category"] == category and data["reimbursement_draft"]["status"] == "DRAFT"
        assert data["funds_debited"] is False
        stored = agent_api.ARTIFACTS[data["artifact_id"]]
        assert "content_base64" not in stored and "raw" not in stored and "bytes" not in stored
        if category == "差旅-住宿":
            same = client.post("/api/v1/agent/artifacts", json=body, headers={"X-User-Id": "u1"}).json()
            assert same["artifact_id"] == data["artifact_id"]
            assert client.get(f"/api/v1/agent/artifacts/{data['artifact_id']}", headers={"X-User-Id": "u2"}).status_code == 404

def test_unknown_legal_media_is_not_guessed():
    original = base64.b64decode(PNG)
    raw = original[:-1] + bytes([original[-1] ^ 1])
    body = payload(base64.b64encode(raw).decode(), "image/png")
    data = TestClient(agent_api.app).post("/api/v1/agent/artifacts", json=body, headers={"X-User-Id": "u1"}).json()
    assert data["status"] == "NEEDS_REVIEW" and data["confidence"] == 0
    assert "amount" not in data and "merchant" not in data and "invoice_number" not in data


def test_authentication_and_decoded_size_limit_are_enforced():
    client = TestClient(agent_api.app)
    unauthenticated = client.post("/api/v1/agent/artifacts", json=payload(PNG, "image/png"))
    assert unauthenticated.status_code == 401
    oversized = b"\x89PNG\r\n\x1a\n" + (b"x" * (MAX_ARTIFACT_BYTES - 7))
    response = client.post(
        "/api/v1/agent/artifacts",
        json=payload(base64.b64encode(oversized).decode(), "image/png", "too-large.png"),
        headers={"X-User-Id": "u1"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "ARTIFACT_TOO_LARGE"

@pytest.mark.parametrize("body", [
    {"media_type": "image/png", "content_base64": "!!!", "sha256": "0" * 64, "filename": "x", "conversation_id": "c"},
    {"media_type": "image/png", "content_base64": "", "sha256": "0" * 64, "filename": "x", "conversation_id": "c"},
    payload(PNG, "image/png", sha256="f" * 64),
    payload(PNG, "image/jpeg"),
])
def test_invalid_uploads_rejected(body):
    assert TestClient(agent_api.app).post("/api/v1/agent/artifacts", json=body, headers={"X-User-Id": "u1"}).status_code == 400

def test_extra_money_fields_do_not_create_core_side_effect():
    state_before = (deepcopy(main.ACCOUNTS), deepcopy(main.TRANSACTIONS), deepcopy(main.CARDS))
    body = payload(PNG, "image/png", operation="TRANSFER", refund=True, transfer={"amount": 999999})
    response = TestClient(agent_api.app).post("/api/v1/agent/artifacts", json=body, headers={"X-User-Id": "u1"})
    assert response.status_code == 200 and len(agent_api.ARTIFACTS) == 1
    assert (main.ACCOUNTS, main.TRANSACTIONS, main.CARDS) == state_before
