from __future__ import annotations

import pytest
import copy
import json
from fastapi.testclient import TestClient

import main
from bank_core.routes import execution as execution_routes
from notification_outbox import OUTBOX, enqueue


@pytest.fixture()
def client():
    main.reset_state()
    with TestClient(main.app) as value:
        yield value
    main.reset_state()


def session_headers(client, user_id="user_zhang"):
    response = client.post("/api/v1/auth/login", json={"user_id": user_id, "password": "Bank@2026"})
    assert response.status_code == 200, response.text
    return {"X-Session-Token": response.json()["session_token"]}


def test_outbox_deduplicates_event_ids():
    OUTBOX.clear()
    row = enqueue("AA:SPL1:user_b", owner_id="user_a", recipient_id="user_b", split_id="SPL1", amount=10, message="AA")
    assert enqueue("AA:SPL1:user_b", owner_id="user_a", recipient_id="user_b", split_id="SPL1", amount=10, message="AA") is row


def test_outbox_authenticated_visibility(client):
    enqueue("AA:SPL2:user_b", owner_id="user_zhang", recipient_id="user_b", split_id="SPL2", amount=5, message="AA")
    assert client.get("/api/v1/notifications/outbox").status_code == 401
    assert client.get("/api/v1/notifications/outbox", headers={"X-User-Id": "user_zhang"}).status_code == 401
    visible = client.get("/api/v1/notifications/outbox", headers=session_headers(client))
    assert visible.status_code == 200
    assert visible.json()["data"][0]["event_id"] == "AA:SPL2:user_b"


def _approve_split(client, amount=100.0):
    body = {"user_id": "user_zhang", "operation": "SPLIT_BILL", "amount": amount, "participants": ["李四", "王美"]}
    headers = {"X-User-Id": "user_zhang"}
    pre = client.post("/api/v1/risk/precheck/operation", json=body, headers=headers)
    assert pre.status_code == 200, pre.text
    token = pre.json()["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers={**headers, "X-Auth-Token": token}).status_code == 200
    return body, token, headers


def test_real_split_emits_one_stable_event_per_participant_with_outcome_metadata(client):
    main.ACCOUNTS["user_li"]["balance"] = 10.0
    body, token, headers = _approve_split(client)
    response = client.post("/api/v1/operations/execute", json={**body, "auth_token": token}, headers={**headers, "X-Auth-Token": token})
    assert response.status_code == 200, response.text
    split = response.json()["data"]
    rows = [row for row in OUTBOX if row["split_id"] == split["split_id"]]
    assert len(rows) == 2
    assert {row["recipient_id"] for row in rows} == {"user_li", "user_wife"}
    assert {row["outcome"] for row in rows} == {"PENDING"}
    assert all(row["event_id"] == f"AA:{split['split_id']}:{row['recipient_id']}" for row in rows)
    assert enqueue(rows[0]["event_id"], owner_id="user_zhang", recipient_id=rows[0]["recipient_id"], split_id=split["split_id"], amount=rows[0]["amount"], message="duplicate") is rows[0]


def test_split_enqueue_failure_rolls_back_entire_operation(client, monkeypatch):
    body, token, headers = _approve_split(client)
    before = {"accounts": copy.deepcopy(main.ACCOUNTS), "splits": copy.deepcopy(main.SPLIT_REQUESTS), "transactions": copy.deepcopy(main.TRANSACTIONS), "outbox": copy.deepcopy(OUTBOX), "tasks": main.TASK_STORE.snapshot()}

    def append_then_fail(*args, **kwargs):
        enqueue(*args, **kwargs)
        raise RuntimeError("simulated enqueue failure")

    monkeypatch.setattr(execution_routes, "enqueue_notification", append_then_fail)
    response = client.post("/api/v1/operations/execute", json={**body, "auth_token": token}, headers={**headers, "X-Auth-Token": token})
    assert response.status_code == 500
    assert main.ACCOUNTS == before["accounts"]
    assert main.SPLIT_REQUESTS == before["splits"]
    assert main.TRANSACTIONS == before["transactions"]
    assert OUTBOX == before["outbox"]
    assert main.TASK_STORE.snapshot() == before["tasks"]


def test_outbox_persistence_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "ledger.json"
    monkeypatch.setenv("BANK_LEDGER_STORE_PATH", str(path))
    main.reset_state()
    row = enqueue("AA:PERSIST:user_b", owner_id="user_zhang", recipient_id="user_b", split_id="PERSIST", amount=2, message="AA")
    assert main.persist_ledger()["file_configured"]
    OUTBOX.clear()
    assert main.load_ledger_store()["loaded_state"] is True
    assert OUTBOX == [row]


def test_v1_snapshot_without_outbox_defaults_to_empty(tmp_path, monkeypatch):
    path = tmp_path / "ledger.json"
    monkeypatch.setenv("BANK_LEDGER_STORE_PATH", str(path))
    main.reset_state()
    enqueue("AA:OLD:user_b", owner_id="user_zhang", recipient_id="user_b", split_id="OLD", amount=1, message="AA")
    main.persist_ledger()
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    snapshot["state"].pop("OUTBOX")
    canonical = json.dumps(snapshot["state"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    snapshot["integrity"] = main._ledger_store_digest(canonical, keyed=main.AUTH_SECRET_CONFIGURED)
    path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    OUTBOX.clear()
    assert main.load_ledger_store()["loaded_state"] is True
    assert OUTBOX == []


def test_invalid_persisted_outbox_shape_is_rejected_and_freezes_writes(tmp_path, monkeypatch):
    path = tmp_path / "ledger.json"
    monkeypatch.setenv("BANK_LEDGER_STORE_PATH", str(path))
    main.reset_state()
    main.persist_ledger()
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    snapshot["state"]["OUTBOX"] = {"not": "a list"}
    canonical = json.dumps(snapshot["state"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    snapshot["integrity"] = main._ledger_store_digest(canonical, keyed=main.AUTH_SECRET_CONFIGURED)
    path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    summary = main.load_ledger_store()
    assert summary["rejected"] is True
    assert main.ledger_store_status()["writes_frozen"] is True


def test_reset_state_clears_outbox():
    enqueue("AA:RESET:user_b", owner_id="user_zhang", recipient_id="user_b", split_id="RESET", amount=1, message="AA")
    main.reset_state()
    assert OUTBOX == []


def test_outbox_visibility_is_limited_to_owner_or_recipient(client):
    owner_row = enqueue("AA:VIS:recipient", owner_id="user_zhang", recipient_id="user_li", split_id="VIS", amount=1, message="safe")
    outsider_row = enqueue("AA:OTHER:recipient", owner_id="user_wife", recipient_id="user_wife", split_id="OTHER", amount=1, message="private")
    assert client.get("/api/v1/notifications/outbox", headers=session_headers(client, "user_li")).json()["data"] == [owner_row]
    assert client.get("/api/v1/notifications/outbox", headers=session_headers(client, "user_wife")).json()["data"] == [outsider_row]
    assert client.get("/api/v1/notifications/outbox", headers=session_headers(client), params={"split_id": "OTHER"}).json()["data"] == []
    assert owner_row["created_at"]


def test_outbox_api_rows_never_expose_internal_fields(client):
    row = enqueue("AA:SECRET:user_li", owner_id="user_zhang", recipient_id="user_li", split_id="SECRET", amount=1, message="safe")
    row["internal_token"] = "INTERNAL_TOKEN_SECRET"
    row["internal_debug"] = "INTERNAL_DEBUG_SECRET"
    body = client.get("/api/v1/notifications/outbox", headers=session_headers(client, "user_li")).text
    assert "INTERNAL_TOKEN_SECRET" not in body
    assert "INTERNAL_DEBUG_SECRET" not in body
