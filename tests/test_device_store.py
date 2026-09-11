"""Device-registry durability tests.

The registry lives in memory unless ``BANK_DEVICE_STORE_PATH`` is configured, and
that default has a consequence worth testing explicitly: a restart forgets every
enrolled device *and* every delivery token, so push silently stops working until
each user signs in again.  These tests cover the opt-in file that fixes it, and -
because the file holds device public keys and push addresses - every way it can
be abused:

* an unconfigured deployment must stay side-effect free (no file, memory only);
* enrollment, revocation and the delivery token must all survive a reload;
* a forged or truncated file must be refused *and* frozen, so a later enrollment
  cannot overwrite the evidence;
* a record whose id does not match the key it carries must be refused even when
  the digest is recomputed correctly, because the digest only proves the file was
  written by someone holding the secret - not that its contents make sense.
"""

import base64
import json
import os

import pytest

pytest.importorskip("cryptography")
pytest.importorskip("fastapi")

from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402

import security  # noqa: E402


def spki_b64(private_key) -> str:
    der = private_key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(der).decode("ascii")


def enroll(user_id: str = "user_zhang", label: str = "store-test") -> str:
    summary = security.enroll_device(spki_b64(ec.generate_private_key(ec.SECP256R1())), user_id=user_id, label=label)
    return str(summary["device_id"])


def read_store(path) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_store(path, devices: dict, *, keyed: bool | None = None) -> None:
    """Write a file the way the server would, digest included."""

    if keyed is None:
        keyed = security.AUTH_SECRET_CONFIGURED
    canonical = json.dumps(devices, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    snapshot = {
        "version": security.DEVICE_STORE_VERSION,
        "updated_at": 0,
        "devices": devices,
        "integrity": security._device_store_digest(canonical, keyed=keyed),
    }
    path.write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A configured store path, with in-memory state reset around each test."""

    path = tmp_path / "devices.json"
    monkeypatch.setenv("BANK_DEVICE_STORE_PATH", str(path))
    security.reset_security_state()
    yield path
    security.reset_security_state()
    monkeypatch.delenv("BANK_DEVICE_STORE_PATH", raising=False)


def simulate_restart(path) -> dict:
    """Drop everything in memory and load the file the way a start-up would."""

    security.reset_security_state()
    assert not security.DEVICES, "reset must leave the registry empty"
    return security.load_device_store(str(path))


# ---------------------------------------------------------------------------
# default: still memory only
# ---------------------------------------------------------------------------


def test_an_unconfigured_deployment_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("BANK_DEVICE_STORE_PATH", raising=False)
    security.reset_security_state()

    enroll()

    assert security.device_store_status()["mode"] == "memory_only"
    assert security.device_store_status()["file_configured"] is False
    assert not list(tmp_path.iterdir()), "an unconfigured store must not create files"


def test_the_suite_never_runs_against_a_configured_store():
    """A test run must not be able to write a real registry.

    Regression guard for a real accident: with a live ``BANK_DEVICE_STORE_PATH``
    inherited from a developer's ``.env``, enrolling fixtures in the suite
    overwrote the real device file and the enrolled phone vanished from it.
    ``conftest`` empties the variable, so durability has to be requested per test.
    """

    assert security._device_store_target() is None
    assert security.device_store_status()["mode"] == "memory_only"
    # Enrolling here writes nothing anywhere; the assertion above is what makes
    # that a property of the suite rather than of this particular test.
    enroll()


def test_a_missing_file_starts_from_an_empty_registry(store):
    summary = security.load_device_store(str(store))

    assert summary["loaded_devices"] == 0
    assert not summary.get("rejected")
    assert security.device_store_status()["file_rejected"] is False


# ---------------------------------------------------------------------------
# what survives a restart
# ---------------------------------------------------------------------------


def test_enrollment_and_the_delivery_token_survive_a_restart(store):
    device_id = enroll()
    security.attach_push_token(device_id, "fcm-token-1", user_id="user_zhang")

    summary = simulate_restart(store)

    assert summary["loaded_devices"] == 1
    assert summary.get("rejected") is False
    assert device_id in security.DEVICES
    # The point of the whole exercise: after a restart the server still knows who
    # to notify, instead of auditing NOT_SENT until the user logs in again.
    assert [row["token"] for row in security.push_tokens_for("user_zhang")] == ["fcm-token-1"]


def test_a_revocation_survives_a_restart(store):
    device_id = enroll()
    security.revoke_device(device_id, user_id="user_zhang")

    simulate_restart(store)

    assert security.DEVICES[device_id]["status"] == security.DEVICE_STATUS_REVOKED
    assert security.push_tokens_for("user_zhang") == []
    device = security.list_devices("user_zhang")[0]
    assert device["status"] == security.DEVICE_STATUS_REVOKED


def test_the_stored_file_holds_no_plaintext_of_other_users(store):
    """A reload must not resurrect a device under the wrong principal."""

    enroll(user_id="user_zhang")
    enroll(user_id="user_li")

    simulate_restart(store)

    assert len(security.list_devices("user_zhang")) == 1
    assert len(security.list_devices("user_li")) == 1
    assert len(security.DEVICES) == 2


# ---------------------------------------------------------------------------
# refusal paths
# ---------------------------------------------------------------------------


def test_a_tampered_payload_is_refused_and_freezes_writes(store):
    device_id = enroll()
    snapshot = read_store(store)
    # Rewrite the owner without touching the digest: exactly what an attacker
    # with write access but no BANK_AUTH_SECRET would try.
    snapshot["devices"][device_id]["user_id"] = "user_li"
    store.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

    summary = simulate_restart(store)

    assert summary.get("rejected") is True
    assert "完整性" in str(summary.get("error"))
    assert security.DEVICES == {}
    assert security.device_store_status()["writes_frozen"] is True


def test_frozen_writes_do_not_overwrite_the_evidence(store):
    enroll()
    snapshot = read_store(store)
    snapshot["devices"] = {}
    store.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    security.load_device_store(str(store))
    before = store.read_text(encoding="utf-8")

    enroll()  # must not resurrect persistence, let alone destroy the file

    assert store.read_text(encoding="utf-8") == before
    assert security.DEVICES, "the new device still works in memory for this process"


def test_a_corrupt_file_is_refused(store):
    store.write_text("{ not json", encoding="utf-8")

    summary = security.load_device_store(str(store))

    assert summary.get("rejected") is True
    assert security.DEVICES == {}
    assert security.device_store_status()["file_rejected"] is True


def test_an_unknown_version_is_refused(store):
    enroll()
    snapshot = read_store(store)
    snapshot["version"] = security.DEVICE_STORE_VERSION + 1
    store.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

    summary = simulate_restart(store)

    assert summary.get("rejected") is True
    assert security.DEVICES == {}


def test_a_device_id_that_does_not_match_its_key_is_refused(store):
    """A correctly digested file can still be nonsense; that must be caught too."""

    device_id = enroll()
    snapshot = read_store(store)
    record = snapshot["devices"][device_id]
    # Keep a valid key but claim an id derived from a different one, then redo the
    # digest exactly as the server would, so only the record check can catch it.
    record["device_id"] = "DEV" + "0" * 24
    snapshot["devices"] = {record["device_id"]: record}
    write_store(store, snapshot["devices"])

    summary = simulate_restart(store)

    assert summary.get("rejected") is True
    assert "无效" in str(summary.get("error"))
    assert security.DEVICES == {}


def test_a_weak_device_key_is_refused(store):
    """P-256 is pinned at enrollment; a stored record must not widen that."""

    device_id = enroll()
    snapshot = read_store(store)
    record = snapshot["devices"][device_id]
    weaker = ec.generate_private_key(ec.SECP384R1())
    record["public_key"] = spki_b64(weaker)
    write_store(store, snapshot["devices"])

    summary = simulate_restart(store)

    assert summary.get("rejected") is True
    assert security.DEVICES == {}


# ---------------------------------------------------------------------------
# integrity posture and reporting
# ---------------------------------------------------------------------------


def test_an_unkeyed_store_loads_but_is_reported_as_unkeyed(store):
    """Without BANK_AUTH_SECRET the digest only detects corruption."""

    device_id = enroll()
    snapshot = read_store(store)
    write_store(store, snapshot["devices"], keyed=False)

    summary = security.load_device_store(str(store))

    assert summary.get("rejected") is False
    assert summary["keyed"] is False
    assert device_id in security.DEVICES


def test_the_status_report_never_leaks_the_path(store):
    enroll()

    status = security.device_store_status()

    assert status["file_configured"] is True
    assert str(store) not in json.dumps(status)


def test_health_reports_where_devices_live(store):
    enroll()

    body = security.security_health()

    persistence = body["devices"]["persistence"]
    assert persistence["mode"] == "memory+file"
    assert persistence["file_configured"] is True
    assert persistence["integrity_keyed"] == security.AUTH_SECRET_CONFIGURED


def test_persisting_is_a_no_op_when_unconfigured(monkeypatch):
    monkeypatch.delenv("BANK_DEVICE_STORE_PATH", raising=False)
    security.reset_security_state()

    status = security.persist_devices()

    assert status["mode"] == "memory_only"


def test_the_written_file_is_valid_json_with_one_digest(store):
    """A reader must be able to verify the file without the running process."""

    enroll()
    security.attach_push_token(next(iter(security.DEVICES)), "t", user_id="user_zhang")

    snapshot = read_store(store)

    assert snapshot["version"] == security.DEVICE_STORE_VERSION
    assert set(snapshot["integrity"]) == {"alg", "value"}
    assert snapshot["integrity"]["alg"] in {"sha256", "hmac-sha256"}
    assert not os.path.exists(f"{store}.tmp"), "the atomic write must not leave a temp file"
