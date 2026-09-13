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

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import security  # noqa: E402


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


def _yellow_transfer(client):
    headers = {"X-User-Id": "user_zhang"}
    precheck = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 300},
        headers=headers,
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
    assert executed.status_code == 200, executed.text
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

    # ...and the next committed mutation cannot overwrite the evidence.
    before = store_path.read_text(encoding="utf-8")
    _yellow_transfer(client)
    assert store_path.read_text(encoding="utf-8") == before


def test_the_digest_is_keyed_when_the_secret_is_configured(client, store_path, monkeypatch):
    monkeypatch.setattr(security, "AUTH_SECRET", b"unit-test-key")
    monkeypatch.setattr(security, "AUTH_SECRET_CONFIGURED", True)
    monkeypatch.setattr(main, "AUTH_SECRET_CONFIGURED", True)

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

    monkeypatch.setattr(security, "AUTH_SECRET", b"unit-test-key")
    monkeypatch.setattr(security, "AUTH_SECRET_CONFIGURED", True)
    monkeypatch.setattr(main, "AUTH_SECRET_CONFIGURED", True)

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

    monkeypatch.setattr(security, "AUTH_SECRET_CONFIGURED", False)
    monkeypatch.setattr(main, "AUTH_SECRET_CONFIGURED", False)

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
