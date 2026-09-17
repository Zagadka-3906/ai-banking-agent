from fastapi.testclient import TestClient
import pytest

import main


@pytest.fixture()
def client():
    main.reset_state()
    with TestClient(main.app) as value:
        yield value
    main.reset_state()


def _session(client: TestClient) -> dict[str, str]:
    result = client.post("/api/v1/auth/session", json={"user_id": "user_zhang"}, headers={"X-User-Id": "user_zhang"})
    assert result.status_code == 200, result.text
    return {"X-Session-Token": result.json()["session_token"], "X-User-Id": "user_zhang"}


def test_card_binding_projects_only_safe_fields(client: TestClient):
    payload = {"card_number": "4111 1111 1111 1111", "source": "OCR", "user_confirmed": True}
    response = client.post("/api/v1/cards/user_zhang/add", json=payload, headers={"X-User-Id": "user_zhang"},)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["last4"] == "1111"
    assert "4111111111111111" not in response.text
    assert "24681012" not in response.text
    assert all("pan_ref" not in row for row in client.get("/api/v1/cards/user_zhang", headers={"X-User-Id": "user_zhang"}).json()["data"])
    assert all("4111111111111111" not in str(value) and "24681012" not in str(value) for value in main.CARDS.values())
    assert all("4111111111111111" not in str(value) and "24681012" not in str(value) for value in main.AUDIT_LOGS)
    assert all("4111111111111111" not in str(value) and "24681012" not in str(value) for value in main.IDEMPOTENCY_RECORDS.values())


def test_binding_validation_and_duplicate_are_rejected(client: TestClient):
    base = {"source": "MANUAL", "user_confirmed": True}
    for number, code in (("1234", "CARD_BINDING_REJECTED"), ("4111111111111112", "CARD_BINDING_REJECTED"), ("4111111111111111", "CARD_CONFIRMATION_REQUIRED")):
        body = {**base, "card_number": number, "user_confirmed": number == "4111111111111111" and False}
        response = client.post("/api/v1/cards/user_zhang/add", json=body, headers={"X-User-Id": "user_zhang"})
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["code"] == code
    ok = client.post("/api/v1/cards/user_zhang/add", json={**base, "card_number": "4111111111111111"}, headers={"X-User-Id": "user_zhang"})
    assert ok.status_code == 200, ok.text
    duplicate = client.post("/api/v1/cards/user_zhang/add", json={**base, "card_number": "4111111111111111"}, headers={"X-User-Id": "user_zhang"})
    assert duplicate.json()["detail"]["code"] == "CARD_BINDING_REJECTED"


def test_reveal_requires_session_pin_and_locks_after_five_attempts(client: TestClient):
    no_session = client.post("/api/v1/cards/user_zhang/CARD-001/reveal", json={"pin": "24681012"}, headers={"X-User-Id": "user_zhang"})
    assert no_session.status_code == 401
    session = _session(client)
    for _ in range(4):
        denied = client.post("/api/v1/cards/user_zhang/CARD-001/reveal", json={"pin": "00000000"}, headers=session)
        assert denied.status_code in {401, 403}
        assert "6222020000023082" not in denied.text
    locked = client.post("/api/v1/cards/user_zhang/CARD-001/reveal", json={"pin": "00000000"}, headers=session)
    assert locked.status_code == 423
    assert locked.json()["detail"]["code"] == "CARD_DISCLOSURE_LOCKED"

    main.reset_state()
    session = _session(client)
    revealed = client.post("/api/v1/cards/user_zhang/CARD-001/reveal", json={"pin": "24681012"}, headers=session)
    assert revealed.status_code == 200, revealed.text
    assert revealed.json()["data"]["card_number"] == "6222020000023082"
    assert revealed.headers["cache-control"] == "no-store"
    assert "pin" not in revealed.json()["data"]


def test_reveal_never_discloses_unknown_cross_user_lost_or_virtual_cards(client: TestClient):
    session = _session(client)
    for path in (
        "/api/v1/cards/user_zhang/CARD-404/reveal",
        "/api/v1/cards/user_li/CARD-001/reveal",
    ):
        denied = client.post(path, json={"pin": "24681012"}, headers=session)
        assert denied.status_code in {401, 403}
        assert all(value not in denied.text for value in ("6222020000023082", "张三", "招商银行"))

    main.CARDS["user_zhang"][0]["status"] = "LOST"
    lost = client.post("/api/v1/cards/user_zhang/CARD-001/reveal", json={"pin": "24681012"}, headers=session)
    assert lost.status_code == 401
    main.CARDS["user_zhang"][0]["status"] = "ACTIVE"
    main.CARDS["user_zhang"].append({"card_id": "CARD-VIRTUAL", "number_masked": "**** **** **** 9001", "last4": "9001", "type": "虚拟卡", "status": "ACTIVE", "daily_limit": 5000.0})
    virtual = client.post("/api/v1/cards/user_zhang/CARD-VIRTUAL/reveal", json={"pin": "24681012"}, headers=session)
    assert virtual.status_code == 401
    assert "6222020000023082" not in virtual.text


def test_card_binding_persistence_and_idempotency_are_redacted(client: TestClient, monkeypatch, tmp_path):
    monkeypatch.setenv("BANK_LEDGER_STORE_PATH", str(tmp_path / "ledger.json"))
    headers = {"X-User-Id": "user_zhang", "Idempotency-Key": "card-safe-001"}
    response = client.post("/api/v1/cards/user_zhang/add", json={"card_number": "4111111111111111", "source": "MANUAL", "user_confirmed": True}, headers=headers)
    assert response.status_code == 200, response.text
    main.persist_ledger()
    snapshot = (tmp_path / "ledger.json").read_text(encoding="utf-8")
    sensitive = ("6222020000023082", "4111111111111111", "24681012")
    assert all(value not in snapshot for value in sensitive)
    assert all(value not in str(main.AUDIT_LOGS) for value in sensitive)
    assert all(value not in str(main.IDEMPOTENCY_RECORDS) for value in sensitive)
