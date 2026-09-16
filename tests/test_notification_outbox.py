from __future__ import annotations

import pytest
import threading
import copy
import json
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

import main
from notification_outbox import OUTBOX, enqueue, dispatch
from notifications import PushResult


@pytest.fixture()
def client():
    main.reset_state()
    with TestClient(main.app) as value:
        yield value
    main.reset_state()


def test_outbox_stable_event_and_retry(monkeypatch):
    OUTBOX.clear()
    row = enqueue("AA:SPL1:user_b", owner_id="user_a", recipient_id="user_b", split_id="SPL1", amount=10, message="AA")
    assert enqueue("AA:SPL1:user_b", owner_id="user_a", recipient_id="user_b", split_id="SPL1", amount=10, message="AA") is row

    class Failing:
        name = "fake"
        def send(self, tokens, **kwargs):
            return PushResult(failed=1, detail="offline", provider="fake")

    monkeypatch.setattr("notification_outbox.push_tokens_for", lambda _: ["token"])
    assert dispatch(transport=Failing())[0]["status"] == "RETRY"


def test_success_is_sent_once_and_concurrent_dispatch_claims_once(monkeypatch):
    OUTBOX.clear()
    enqueue("AA:S:U", owner_id="a", recipient_id="u", split_id="S", amount=1, message="AA")
    monkeypatch.setattr("notification_outbox.push_tokens_for", lambda _: ["token"])
    calls = 0
    entered = threading.Event()
    release = threading.Event()

    class Slow:
        name = "fake"
        def send(self, tokens, **kwargs):
            nonlocal calls
            calls += 1
            entered.set()
            release.wait(timeout=2)
            return PushResult(delivered=1, provider="fake")

    result = []
    threads = [threading.Thread(target=lambda: result.append(dispatch(transport=Slow()))) for _ in range(2)]
    threads[0].start()
    assert entered.wait(timeout=2)
    threads[1].start()
    threading.Event().wait(0.05)
    release.set()
    for thread in threads:
        thread.join(timeout=3)
    assert calls == 1
    assert OUTBOX[0]["status"] == "SENT"
    assert dispatch(transport=Slow()) == []


def test_retry_waits_until_due_then_fails_with_controlled_error(monkeypatch):
    OUTBOX.clear()
    row = enqueue("AA:R:U", owner_id="a", recipient_id="u", split_id="R", amount=1, message="AA")
    monkeypatch.setattr("notification_outbox.push_tokens_for", lambda _: ["token"])
    class Failing:
        name = "fake"
        def send(self, tokens, **kwargs):
            return PushResult(failed=1, detail="SECRET_PROVIDER_RESPONSE", provider="fake")
    dispatch(transport=Failing())
    row["next_attempt_at"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert dispatch(transport=Failing()) == []
    row["next_attempt_at"] = datetime.now(timezone.utc).isoformat()
    row["attempts"] = 2
    result = dispatch(transport=Failing())
    assert result[0]["status"] == "FAILED"
    assert result[0]["last_error"] == "PROVIDER_FAILURE"
    assert "SECRET" not in str(result[0])


def test_retry_deadline_is_utc_iso_and_enforces_backoff(monkeypatch):
    OUTBOX.clear()
    row = enqueue("AA:BACKOFF:U", owner_id="a", recipient_id="u", split_id="BACKOFF", amount=1, message="AA")
    monkeypatch.setattr("notification_outbox.push_tokens_for", lambda _: ["token"])
    calls = 0

    class Failing:
        name = "fake"

        def send(self, tokens, **kwargs):
            nonlocal calls
            calls += 1
            return PushResult(failed=1, detail="offline", provider="fake")

    assert dispatch(transport=Failing())[0]["status"] == "RETRY"
    deadline = datetime.fromisoformat(row["next_attempt_at"])
    assert deadline.tzinfo is not None and deadline.utcoffset() is not None
    assert dispatch(transport=Failing()) == []
    assert calls == 1
    row["next_attempt_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert dispatch(transport=Failing())[0]["status"] == "RETRY"
    assert calls == 2


def test_malformed_retry_deadline_is_not_immediately_claimed(monkeypatch):
    OUTBOX.clear()
    row = enqueue("AA:MALFORMED:U", owner_id="a", recipient_id="u", split_id="MALFORMED", amount=1, message="AA")
    row["status"] = "RETRY"
    row["next_attempt_at"] = "not-a-timestamp"
    calls = []

    class Recording:
        name = "fake"

        def send(self, tokens, **kwargs):
            calls.append(1)
            return PushResult(delivered=1, provider="fake")

    assert dispatch(transport=Recording()) == []
    assert calls == []


def test_outbox_authenticated_visibility(client):
    enqueue("AA:SPL2:user_b", owner_id="user_zhang", recipient_id="user_b", split_id="SPL2", amount=5, message="AA")
    assert client.get("/api/v1/notifications/outbox").status_code == 401
    visible = client.get("/api/v1/notifications/outbox", headers={"X-User-Id": "user_zhang"})
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
    assert {row["outcome"] for row in rows} == {"FAILED", "COLLECTED"}
    assert all(row["event_id"] == f"AA:{split['split_id']}:{row['recipient_id']}" for row in rows)
    assert enqueue(rows[0]["event_id"], owner_id="user_zhang", recipient_id=rows[0]["recipient_id"], split_id=split["split_id"], amount=rows[0]["amount"], message="duplicate") is rows[0]


def test_split_enqueue_failure_rolls_back_entire_operation(client, monkeypatch):
    body, token, headers = _approve_split(client)
    before = {"accounts": copy.deepcopy(main.ACCOUNTS), "splits": copy.deepcopy(main.SPLIT_REQUESTS), "transactions": copy.deepcopy(main.TRANSACTIONS), "outbox": copy.deepcopy(OUTBOX), "tasks": main.TASK_STORE.snapshot()}

    def append_then_fail(*args, **kwargs):
        enqueue(*args, **kwargs)
        raise RuntimeError("simulated enqueue failure")

    monkeypatch.setattr(main, "enqueue_notification", append_then_fail)
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


def test_outbox_visibility_and_dispatch_are_limited_to_owner_or_recipient(client, monkeypatch):
    owner_row = enqueue("AA:VIS:recipient", owner_id="user_zhang", recipient_id="user_li", split_id="VIS", amount=1, message="safe")
    outsider_row = enqueue("AA:OTHER:recipient", owner_id="user_other", recipient_id="user_wife", split_id="OTHER", amount=1, message="private")
    assert client.get("/api/v1/notifications/outbox", headers={"X-User-Id": "user_li"}).json()["data"] == [owner_row]
    assert client.get("/api/v1/notifications/outbox", headers={"X-User-Id": "user_other"}).json()["data"] == [outsider_row]
    assert client.get("/api/v1/notifications/outbox", headers={"X-User-Id": "nobody"}).json()["data"] == []
    monkeypatch.setattr("notification_outbox.push_tokens_for", lambda _: [])
    assert client.post("/api/v1/notifications/outbox/dispatch", headers={"X-User-Id": "nobody"}).json()["data"] == []
    assert owner_row["status"] == "PENDING"


def test_outbox_api_rows_never_expose_delivery_secrets(client):
    row = enqueue("AA:SECRET:user_li", owner_id="user_zhang", recipient_id="user_li", split_id="SECRET", amount=1, message="safe")
    row["push_token"] = "PUSH_TOKEN_SECRET"
    row["provider_secret"] = "PROVIDER_SECRET"
    row["raw_provider_failure"] = "RAW_PROVIDER_FAILURE"
    body = client.get("/api/v1/notifications/outbox", headers={"X-User-Id": "user_li"}).text
    assert "PUSH_TOKEN_SECRET" not in body
    assert "PROVIDER_SECRET" not in body
    assert "RAW_PROVIDER_FAILURE" not in body
