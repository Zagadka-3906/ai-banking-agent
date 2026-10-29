"""Durable ledger snapshot: a restart keeps committed state, tamper freezes writes.

The ledger is in memory because this is a sandbox, but "restart empties every
balance" is the failure mode the device registry and the audit file already
refuse.  These tests pin the contract of ``BANK_LEDGER_STORE_PATH``:

* a committed transfer is snapshotted and replayed at start-up, so balances,
  transactions and risk counters survive a process restart;
* a file that fails its integrity digest is refused, never loaded, and its
  evidence is not overwritten by later mutations;
* the digest is keyed (HMAC) when ``BANK_AUTH_SECRET`` is configured, so a
  forged snapshot needs the server secret rather than an editor.
"""

import json
from copy import deepcopy

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from bank_core import persistence  # noqa: E402
import security  # noqa: E402
from support import patch_security  # noqa: E402


@pytest.fixture()
def client():
    main.reset_state()
    yield TestClient(main.app)
    main.reset_state()


@pytest.fixture()
def store_path(tmp_path, monkeypatch):
    path = tmp_path / "ledger.json"
    monkeypatch.setenv("BANK_LEDGER_STORE_PATH", str(path))
    # The real load happens once at import time; re-point it at the test file.
    summary = main.load_ledger_store()
    assert summary["path"] == str(path)
    assert not summary.get("rejected")
    return path


def _yellow_transfer(client, idempotency_key=None, expected_status=200):
    headers = {"X-User-Id": "user_zhang"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    precheck = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 300},
        headers={"X-User-Id": "user_zhang"},
    )
    assert precheck.status_code == 200, precheck.text
    token = precheck.json()["auth_token"]
    confirmed = client.post("/api/v1/auth/confirm", json={}, headers={**headers, "X-Auth-Token": token})
    assert confirmed.status_code == 200, confirmed.text
    executed = client.post(
        "/api/v1/transfers/execute",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 300},
        headers={**headers, "X-Auth-Token": token},
    )
    assert executed.status_code == expected_status, executed.text
    return executed.json()


def test_a_committed_transfer_survives_a_restart(client, store_path):
    settled = _yellow_transfer(client)
    assert store_path.exists()

    # Simulate a process restart: back to the seed ledger, then replay the
    # persisted snapshot exactly like the import-time load does.
    main.reset_state()
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000.0

    summary = main.load_ledger_store()
    assert summary["loaded_state"] is True
    assert main.ACCOUNTS["user_zhang"]["balance"] == settled["sender_balance"]
    assert main.ACCOUNTS["user_li"]["balance"] == 2300.0
    assert any(tx.get("account_id") == "user_zhang" and tx.get("amount") == 300.0 for tx in main.TRANSACTIONS)
    assert main.ledger_store_status()["file_loaded"] is True


def test_receipt_and_ledger_commit_once_before_the_success_audit(client, store_path, monkeypatch):
    real_replace = persistence.os.replace
    snapshots = []

    def observe_commit(source, target):
        with open(source, encoding="utf-8") as handle:
            snapshots.append(json.load(handle)["state"])
        assert not any(event["action"] == "TRANSFER_EXECUTE" and event["outcome"] == "SUCCESS" for event in security.AUDIT_LOGS)
        return real_replace(source, target)

    monkeypatch.setattr(persistence.os, "replace", observe_commit)
    settled = _yellow_transfer(client, "coupled-commit-operation")
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot["ACCOUNTS"]["user_zhang"]["balance"] == settled["sender_balance"]
    assert next(iter(snapshot["OPERATION_RECEIPTS"].values()))["response"] == settled
    assert any(event["action"] == "TRANSFER_EXECUTE" and event["outcome"] == "SUCCESS" for event in security.AUDIT_LOGS)


def test_operation_receipt_survives_restart_and_remains_owner_scoped(client, store_path):
    operation = "test-persistent-operation-123456"
    settled = _yellow_transfer(client, operation)
    main.reset_state()
    assert main.load_ledger_store()["loaded_state"] is True
    receipt = client.get(f"/api/v1/operations/{operation}?endpoint=transfer_execute", headers={"X-User-Id": "user_zhang"}).json()
    assert receipt["status"] == "COMPLETED"
    assert receipt["receipt"]["transaction_id"] == settled["transaction_id"]
    receipt_entry = next(iter(main.OPERATION_RECEIPTS.values()))
    assert "auth_token" not in json.dumps(main.OPERATION_RECEIPTS)
    from security import SecurityError
    with pytest.raises(SecurityError) as conflict:
        main.idempotency_begin("user_zhang", "transfer_execute", operation, {"amount": 999}, request_id="replay-mismatch")
    assert conflict.value.code == "IDEMPOTENCY_KEY_REUSE"
    assert receipt_entry["fingerprint"]
    foreign = client.get(f"/api/v1/operations/{operation}?endpoint=transfer_execute", headers={"X-User-Id": "user_li"}).json()
    assert foreign["status"] == "UNKNOWN" and foreign["receipt"] is None


def test_failed_mutations_do_not_snapshot(client, store_path):
    headers = {"X-User-Id": "user_zhang"}
    refused = client.post(
        "/api/v1/transfers/execute",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 300},
        headers=headers,
    )
    assert refused.status_code == 403  # no authorization nonce, refused
    assert not store_path.exists()


def test_a_tampered_snapshot_is_refused_and_writes_frozen(client, store_path):
    _yellow_transfer(client)
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    payload["state"]["ACCOUNTS"]["user_zhang"]["balance"] = 999999.0
    store_path.write_text(json.dumps(payload), encoding="utf-8")

    summary = main.load_ledger_store()
    assert summary["rejected"] is True
    assert main.ledger_store_status()["writes_frozen"] is True

    # The forged balance never entered memory...
    assert main.ACCOUNTS["user_zhang"]["balance"] != 999999.0

    # ...and subsequent mutations are refused without overwriting evidence.
    before = store_path.read_text(encoding="utf-8")
    balance = main.ACCOUNTS["user_zhang"]["balance"]
    assert _yellow_transfer(client, expected_status=503)["detail"]["code"] == "LEDGER_STORE_BLOCKED"
    assert main.ACCOUNTS["user_zhang"]["balance"] == balance
    assert store_path.read_text(encoding="utf-8") == before


def test_the_digest_is_keyed_when_the_secret_is_configured(client, store_path, monkeypatch):
    patch_security(monkeypatch, "AUTH_SECRET", b"unit-test-key")
    patch_security(monkeypatch, "AUTH_SECRET_CONFIGURED", True)
    monkeypatch.setattr(persistence, "AUTH_SECRET_CONFIGURED", True)

    _yellow_transfer(client)
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert payload["integrity"]["alg"] == "hmac-sha256"

    # A file whose digest no longer matches is refused - any scheme catches
    # this.  The meaningful property (an attacker who can rewrite the file but
    # does not hold the key still cannot forge one) is covered by
    # test_a_downgraded_digest_algorithm_cannot_forge_a_balance below.
    main.reset_state()
    forged = json.loads(store_path.read_text(encoding="utf-8"))
    forged["state"]["ACCOUNTS"]["user_zhang"]["balance"] = 1.0
    store_path.write_text(json.dumps(forged), encoding="utf-8")
    assert main.load_ledger_store()["rejected"] is True


def test_a_downgraded_digest_algorithm_cannot_forge_a_balance(client, store_path, monkeypatch):
    """The algorithm must come from our config, not from the file.

    Regression: the loader used to honour the file's own ``integrity.alg``, so
    an attacker who could write the snapshot (but who never learned
    ``BANK_AUTH_SECRET``) could relabel it ``sha256``, recompute an unkeyed
    checksum over a tampered state, and have it accepted as authentic - while
    ``ledger_store_status()`` kept reporting ``integrity_keyed: True``.  The
    assertion must therefore recompute the digest exactly as the attacker can,
    otherwise it only proves that a file with a stale digest is refused.
    """

    import hashlib

    patch_security(monkeypatch, "AUTH_SECRET", b"unit-test-key")
    patch_security(monkeypatch, "AUTH_SECRET_CONFIGURED", True)
    monkeypatch.setattr(persistence, "AUTH_SECRET_CONFIGURED", True)

    _yellow_transfer(client)
    main.reset_state()

    forged = json.loads(store_path.read_text(encoding="utf-8"))
    forged["state"]["ACCOUNTS"]["user_zhang"]["balance"] = 999999.0
    canonical = json.dumps(forged["state"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    forged["integrity"] = {"alg": "sha256", "value": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}
    store_path.write_text(json.dumps(forged), encoding="utf-8")

    summary = main.load_ledger_store()
    assert summary["rejected"] is True, summary
    assert "降级" in summary.get("error", "")
    assert main.ACCOUNTS["user_zhang"]["balance"] != 999999.0


def test_an_unconfigured_deployment_refuses_a_keyed_file(client, store_path, monkeypatch):
    """Without the secret we cannot verify an HMAC, so accepting it would be a
    silent 'trust the file' path (and the reverse of the downgrade attack).

    ``BANK_AUTH_SECRET`` is pinned off explicitly: a developer's ``.env``
    otherwise supplies one (python-dotenv is loaded with ``override=False``),
    which would make this test silently exercise the configured branch.
    """

    patch_security(monkeypatch, "AUTH_SECRET_CONFIGURED", False)
    monkeypatch.setattr(persistence, "AUTH_SECRET_CONFIGURED", False)

    _yellow_transfer(client)
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert payload["integrity"]["alg"] == "sha256"  # no secret configured here
    payload["integrity"]["alg"] = "hmac-sha256"
    store_path.write_text(json.dumps(payload), encoding="utf-8")

    summary = main.load_ledger_store()
    assert summary["rejected"] is True, summary
    assert security.AUTH_SECRET_CONFIGURED is False


def test_health_reports_the_ledger_store(client):
    body = client.get("/api/v1/security/health").json()
    assert body["ledger_persistence"]["file_configured"] is False
    assert body["ledger_persistence"]["mode"] == "memory_only"


@pytest.mark.parametrize("failure_at", ["open", "fsync", "replace"])
@pytest.mark.parametrize("operation", [None, "failed-persistent-operation"])
def test_failed_commit_rolls_back_and_cannot_report_success_after_restart(client, store_path, monkeypatch, failure_at, operation):
    # All state, including uninitialised day buckets, belongs to the write
    # boundary. Failed settlement must restore exactly this precheck state.
    main.persist_ledger()
    before_file = store_path.read_bytes()
    before = {name: deepcopy(persistence.LEDGER_COLLECTIONS[name]) for name in persistence.LEDGER_STATE_NAMES}

    def fail(*args, **kwargs):
        raise OSError("simulated storage failure")

    with monkeypatch.context() as storage:
        if failure_at == "open":
            storage.setattr(persistence, "open", fail, raising=False)
        else:
            storage.setattr(persistence.os, failure_at, fail)
        rejected = _yellow_transfer(client, operation, expected_status=503)
    assert rejected["detail"]["code"] == "LEDGER_PERSISTENCE_FAILED"
    assert {name: persistence.LEDGER_COLLECTIONS[name] for name in before} == before
    assert store_path.read_bytes() == before_file
    assert any(record["used"] for record in main.AUTH_TOKENS.values())
    assert security.AUDIT_LOGS[-1]["outcome"] == "PERSISTENCE_FAILED_ROLLBACK"
    assert not any(event["action"] == "TRANSFER_EXECUTE" and event["outcome"] == "SUCCESS" for event in security.AUDIT_LOGS)
    status = main.ledger_store_status()
    assert status["writes_frozen"] is False
    assert status["last_write_failed"] is True
    assert status["write_failures"] == 1
    if operation:
        query = client.get(f"/api/v1/operations/{operation}?endpoint=transfer_execute", headers={"X-User-Id": "user_zhang"})
        assert query.json()["status"] == "UNKNOWN"
        # The failed response was removed from both caches; a fresh approval
        # can reuse the operation key after storage recovers, without restart.
        settled = _yellow_transfer(client, operation)
        assert settled["sender_balance"] == 14700
        assert main.ledger_store_status()["last_write_failed"] is False
    main.reset_state()
    assert main.load_ledger_store()["loaded_state"] is True
    assert main.ACCOUNTS["user_zhang"]["balance"] == (14700 if operation else 15000)
    assert main.ACCOUNTS["user_li"]["balance"] == (2300 if operation else 2000)
    if operation:
        assert client.get(f"/api/v1/operations/{operation}?endpoint=transfer_execute", headers={"X-User-Id": "user_zhang"}).json()["receipt"] == settled
