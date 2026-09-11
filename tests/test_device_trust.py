"""Device trust root tests (P3): the red tier's biometric factor.

The red authorization tier needs a factor the server can attribute to a
*specific device*.  Signing attestations with one shared HMAC key does not
provide that - the key would have to ship inside the client, so anyone able to
unpack the package could mint attestations and the tier would collapse to
OTP-only.

These tests exercise the replacement: a P-256 key generated in the device's
Keystore, enrolled once against an authenticated session, and used to sign each
server-issued liveness challenge.  Both the positive path and every way a
forgery could be attempted are covered, because this is the boundary that stands
between "the user confirmed" and "somebody with a copy of the app confirmed".
"""

import base64
import json
import os

import pytest

pytest.importorskip("cryptography")
pytest.importorskip("fastapi")

from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import security  # noqa: E402

VALID_OTP = os.getenv("BANK_MFA_TEST_OTP", "888888")
# The authorization nonce binds the remark as well, so precheck and execute must
# agree on it or the core refuses the settlement.
REMARK = "设备签名测试"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def new_device_key():
    """A stand-in for a key the Android Keystore would hold."""

    return ec.generate_private_key(ec.SECP256R1())


def spki_b64(private_key) -> str:
    der = private_key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(der).decode("ascii")


def session_headers(client: TestClient, user_id: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/session", json={"user_id": user_id})
    assert response.status_code == 200, response.text
    return {"X-User-Id": user_id, "X-Session-Token": response.json()["session_token"]}


def enroll(client: TestClient, headers: dict[str, str], private_key, label: str = "Pixel-Test") -> str:
    response = client.post(
        "/api/v1/devices/register",
        json={"public_key": spki_b64(private_key), "label": label, "platform": "android", "app_version": "1.0.0"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["device_id"]


def red_precheck(client: TestClient, headers: dict[str, str], amount: float = 2000) -> dict:
    response = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": amount, "remark": REMARK},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["risk_level"] == "RED", body
    return body


def face_challenge(client: TestClient, headers: dict[str, str], token: str) -> dict:
    response = client.post("/api/v1/auth/mfa/face/challenge", json={"auth_token": token, "user_id": "user_zhang"}, headers={**headers, "X-Auth-Token": token})
    assert response.status_code == 200, response.text
    return response.json()


def sign_challenge(private_key, device_id: str, challenge: dict, user_id: str = "user_zhang") -> str:
    """Produce the `v2:` envelope the Android client sends."""

    payload = security.device_facing_payload(challenge["challenge_id"], user_id, challenge["auth_token_jti"])
    signature = private_key.sign(payload, ec.ECDSA(hashes.SHA256()))
    envelope = {
        "device_id": device_id,
        "challenge_id": challenge["challenge_id"],
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    return security.FACE_ATTESTATION_V2_PREFIX + base64.b64encode(json.dumps(envelope).encode("utf-8")).decode("ascii")


def verify_mfa(client: TestClient, headers: dict[str, str], token: str, attestation: str):
    return client.post(
        "/api/v1/auth/mfa/verify",
        json={"otp": VALID_OTP, "face_attestation": attestation},
        headers={**headers, "X-Auth-Token": token},
    )


@pytest.fixture()
def client():
    main.reset_state()
    yield TestClient(main.app)
    main.reset_state()


# ---------------------------------------------------------------------------
# enrollment
# ---------------------------------------------------------------------------


def test_enrollment_requires_a_session_credential(client):
    """A key that can satisfy the biometric factor must not be enrollable with a
    self-asserted header alone."""

    response = client.post(
        "/api/v1/devices/register",
        json={"public_key": spki_b64(new_device_key())},
        headers={"X-User-Id": "user_zhang"},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "SESSION_REQUIRED"


def test_enrollment_rejects_garbage_and_non_p256_keys(client):
    headers = session_headers(client, "user_zhang")

    garbage = client.post("/api/v1/devices/register", json={"public_key": "not-base64!!"}, headers=headers)
    assert garbage.status_code == 400
    assert garbage.json()["detail"]["code"] == "INVALID_DEVICE_KEY"

    # A structurally valid SPKI that is not an EC P-256 key must be refused:
    # accepting an arbitrary key type would let an attacker enroll a key they
    # can "prove" liveness with trivially.
    from cryptography.hazmat.primitives.asymmetric import rsa

    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rsa_spki = base64.b64encode(
        rsa_key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")
    wrong_type = client.post("/api/v1/devices/register", json={"public_key": rsa_spki}, headers=headers)
    assert wrong_type.status_code == 400
    assert wrong_type.json()["detail"]["code"] == "UNSUPPORTED_DEVICE_KEY"


def test_enrollment_is_idempotent(client):
    headers = session_headers(client, "user_zhang")
    key = new_device_key()

    first = client.post("/api/v1/devices/register", json={"public_key": spki_b64(key), "label": "A"}, headers=headers)
    second = client.post("/api/v1/devices/register", json={"public_key": spki_b64(key), "label": "B"}, headers=headers)
    assert first.status_code == 200 and second.status_code == 200
    # The id is derived from the key, so re-enrolling cannot create a duplicate
    # or let a client choose an id that collides with another device.
    assert first.json()["device_id"] == second.json()["device_id"]
    assert first.json()["fingerprint"] == second.json()["fingerprint"]
    assert second.json()["label"] == "B"


def test_a_key_cannot_be_transferred_to_another_account(client):
    zhang = session_headers(client, "user_zhang")
    li = session_headers(client, "user_li")
    key = new_device_key()

    enroll(client, zhang, key)
    stolen = client.post("/api/v1/devices/register", json={"public_key": spki_b64(key)}, headers=li)
    assert stolen.status_code == 409
    assert stolen.json()["detail"]["code"] == "DEVICE_ALREADY_BOUND"


def test_device_listing_is_scoped_and_hides_key_material(client):
    zhang = session_headers(client, "user_zhang")
    li = session_headers(client, "user_li")
    enroll(client, zhang, new_device_key(), label="zhang-phone")

    mine = client.get("/api/v1/devices", headers=zhang)
    assert mine.status_code == 200
    assert len(mine.json()["data"]) == 1
    # The public key itself is not a secret, but shipping key material to a
    # listing endpoint serves no purpose and invites client-side mistakes.
    assert "public_key" not in json.dumps(mine.json())

    theirs = client.get("/api/v1/devices", headers=li)
    assert theirs.json()["data"] == []


def test_health_reports_the_device_registry(client):
    headers = session_headers(client, "user_zhang")
    enroll(client, headers, new_device_key())
    health = client.get("/api/v1/security/health").json()
    assert health["devices"]["enrolled"] == 1
    assert health["devices"]["active"] == 1
    assert health["face_attestation"]["device_signature"] == "enabled"
    assert health["face_attestation"]["curve"] == "secp256r1"


# ---------------------------------------------------------------------------
# the positive path
# ---------------------------------------------------------------------------


def test_red_transfer_succeeds_with_a_device_signature(client):
    headers = session_headers(client, "user_zhang")
    key = new_device_key()
    device_id = enroll(client, headers, key)

    precheck = red_precheck(client, headers)
    token = precheck["auth_token"]
    client.post("/api/v1/auth/confirm", json={}, headers={**headers, "X-Auth-Token": token})
    attestation = sign_challenge(key, device_id, face_challenge(client, headers, token))

    mfa = verify_mfa(client, headers, token, attestation)
    assert mfa.status_code == 200, mfa.text
    proof = mfa.json()["mfa_proof"]

    settled = client.post(
        "/api/v1/transfers/execute",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 2000, "remark": REMARK, "auth_token": token, "mfa_proof": proof},
        headers={**headers, "X-Auth-Token": token, "X-MFA-Proof": proof},
    )
    assert settled.status_code == 200, settled.text
    balance = client.get("/api/v1/accounts/user_zhang/balance", headers=headers).json()
    assert float(balance["available_balance"]) == 13000.0


def test_device_signature_is_verified_even_outside_demo_mode(client, monkeypatch):
    """The device path is the always-on factor, not a demo affordance."""

    headers = session_headers(client, "user_zhang")
    key = new_device_key()
    device_id = enroll(client, headers, key)

    monkeypatch.setattr(security, "DEMO_MODE", False)
    # Keep the SMS factor satisfiable so a failure could only come from the
    # attestation, making the assertion meaningful.
    monkeypatch.setenv("BANK_MFA_OTP", VALID_OTP)

    token = red_precheck(client, headers)["auth_token"]
    attestation = sign_challenge(key, device_id, face_challenge(client, headers, token))
    assert verify_mfa(client, headers, token, attestation).status_code == 200


def test_health_marks_the_shared_key_as_demo_only(client, monkeypatch):
    monkeypatch.setattr(security, "DEMO_MODE", False)
    health = client.get("/api/v1/security/health").json()
    assert health["face_attestation"]["legacy_shared_key"] == "disabled"


# ---------------------------------------------------------------------------
# forgery attempts
# ---------------------------------------------------------------------------


def test_legacy_shared_key_ticket_is_refused_when_demo_mode_is_off(client, monkeypatch):
    headers = session_headers(client, "user_zhang")
    token = red_precheck(client, headers)["auth_token"]
    challenge = face_challenge(client, headers, token)
    legacy = security.sign_face_attestation(challenge["challenge_id"], user_id="user_zhang", token_jti=challenge["auth_token_jti"])

    monkeypatch.setattr(security, "DEMO_MODE", False)
    monkeypatch.setenv("BANK_MFA_OTP", VALID_OTP)
    refused = verify_mfa(client, headers, token, legacy)
    assert refused.status_code == 403
    assert refused.json()["detail"]["code"] == "MFA_FAILED"


def test_legacy_shared_key_ticket_still_works_in_demo_mode(client):
    """The Streamlit demo relies on this path; removing it must be a decision,
    not a side effect."""

    headers = session_headers(client, "user_zhang")
    token = red_precheck(client, headers)["auth_token"]
    challenge = face_challenge(client, headers, token)
    legacy = security.sign_face_attestation(challenge["challenge_id"], user_id="user_zhang", token_jti=challenge["auth_token_jti"])
    assert verify_mfa(client, headers, token, legacy).status_code == 200


def test_signature_from_a_different_key_is_rejected(client):
    headers = session_headers(client, "user_zhang")
    device_id = enroll(client, headers, new_device_key())
    attacker_key = new_device_key()

    token = red_precheck(client, headers)["auth_token"]
    challenge = face_challenge(client, headers, token)
    # Claim the enrolled device id but sign with a key we control.
    forged = sign_challenge(attacker_key, device_id, challenge)
    assert verify_mfa(client, headers, token, forged).status_code == 403


def test_unknown_device_is_rejected(client):
    headers = session_headers(client, "user_zhang")
    key = new_device_key()

    token = red_precheck(client, headers)["auth_token"]
    challenge = face_challenge(client, headers, token)
    unknown = sign_challenge(key, "DEV0000000000000000000000", challenge)
    assert verify_mfa(client, headers, token, unknown).status_code == 403


def test_device_enrolled_to_another_account_is_rejected(client):
    zhang = session_headers(client, "user_zhang")
    li = session_headers(client, "user_li")
    key = new_device_key()
    # ``user_li`` enrolls the key; ``user_zhang`` then tries to use it, where the
    # challenge and the authorization nonce both belong to user_zhang.
    device_id = enroll(client, li, key)

    token = red_precheck(client, zhang)["auth_token"]
    challenge = face_challenge(client, zhang, token)
    assert verify_mfa(client, zhang, token, sign_challenge(key, device_id, challenge)).status_code == 403


def test_a_wrong_subject_binding_is_rejected(client):
    """The device signs a payload naming somebody else, even though the
    challenge and the key are both genuine."""

    headers = session_headers(client, "user_zhang")
    key = new_device_key()
    device_id = enroll(client, headers, key)

    token = red_precheck(client, headers)["auth_token"]
    challenge = face_challenge(client, headers, token)
    misbound = sign_challenge(key, device_id, challenge, user_id="user_li")
    assert verify_mfa(client, headers, token, misbound).status_code == 403


def test_attestation_cannot_be_replayed(client):
    headers = session_headers(client, "user_zhang")
    key = new_device_key()
    device_id = enroll(client, headers, key)

    token = red_precheck(client, headers)["auth_token"]
    attestation = sign_challenge(key, device_id, face_challenge(client, headers, token))
    assert verify_mfa(client, headers, token, attestation).status_code == 200
    replay = verify_mfa(client, headers, token, attestation)
    assert replay.status_code != 200, replay.text


def test_malformed_v2_envelopes_are_rejected(client):
    headers = session_headers(client, "user_zhang")
    key = new_device_key()
    device_id = enroll(client, headers, key)
    token = red_precheck(client, headers)["auth_token"]
    challenge = face_challenge(client, headers, token)

    cases = {
        "not base64": "v2:!!!!",
        "not json": security.FACE_ATTESTATION_V2_PREFIX + base64.b64encode(b"hello").decode("ascii"),
        "missing fields": security.FACE_ATTESTATION_V2_PREFIX + base64.b64encode(b'{"device_id":"x"}').decode("ascii"),
        "empty signature": security.FACE_ATTESTATION_V2_PREFIX
        + base64.b64encode(json.dumps({"device_id": device_id, "challenge_id": challenge["challenge_id"], "signature": ""}).encode()).decode("ascii"),
        "signature not base64": security.FACE_ATTESTATION_V2_PREFIX
        + base64.b64encode(json.dumps({"device_id": device_id, "challenge_id": challenge["challenge_id"], "signature": "###"}).encode()).decode("ascii"),
    }
    for name, value in cases.items():
        response = verify_mfa(client, headers, token, value)
        # A rejection is required; the exact status is not asserted because each
        # failed attempt counts towards the account's failure breaker, so the
        # last one may already be answered with a lockout (see the dedicated
        # breaker test below).  What must never happen is acceptance.
        assert response.status_code >= 400, f"{name}: accepted with {response.status_code}"


def test_repeated_forgeries_trip_the_account_breaker(client):
    """Forging device signatures is an attack, and must be treated as one.

    This also documents why the malformed-envelope test cannot demand a single
    status code: the same failures that reject a forgery eventually lock the
    account out.
    """

    headers = session_headers(client, "user_zhang")
    device_id = enroll(client, headers, new_device_key())
    observed: list[int] = []
    for _ in range(8):
        precheck = client.post(
            "/api/v1/risk/precheck",
            json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 2000, "remark": REMARK},
            headers=headers,
        )
        observed.append(precheck.status_code)
        if precheck.status_code != 200:
            break
        token = precheck.json()["auth_token"]
        challenge = face_challenge(client, headers, token)
        forged = sign_challenge(new_device_key(), device_id, challenge)
        observed.append(verify_mfa(client, headers, token, forged).status_code)

    assert 403 in observed, observed
    assert observed[-1] == 423, observed


# ---------------------------------------------------------------------------
# revocation
# ---------------------------------------------------------------------------


def test_revoked_device_can_no_longer_authorize(client):
    headers = session_headers(client, "user_zhang")
    key = new_device_key()
    device_id = enroll(client, headers, key)

    revoked = client.post(f"/api/v1/devices/{device_id}/revoke", headers=headers)
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"

    token = red_precheck(client, headers)["auth_token"]
    challenge = face_challenge(client, headers, token)
    assert verify_mfa(client, headers, token, sign_challenge(key, device_id, challenge)).status_code == 403

    health = client.get("/api/v1/security/health").json()
    assert health["devices"]["active"] == 0
    assert health["devices"]["revoked"] == 1


def test_a_device_cannot_be_revoked_by_another_account(client):
    zhang = session_headers(client, "user_zhang")
    li = session_headers(client, "user_li")
    device_id = enroll(client, zhang, new_device_key())

    denied = client.post(f"/api/v1/devices/{device_id}/revoke", headers=li)
    # "someone else's device" and "no such device" answer identically, so the
    # endpoint is not an enumeration oracle.
    assert denied.status_code == 404
    assert denied.json()["detail"]["code"] == "DEVICE_NOT_FOUND"
    assert client.get("/api/v1/devices", headers=zhang).json()["data"][0]["status"] == "ACTIVE"


def test_enrollment_and_revocation_are_audited(client):
    headers = session_headers(client, "user_zhang")
    device_id = enroll(client, headers, new_device_key())
    client.post(f"/api/v1/devices/{device_id}/revoke", headers=headers)

    actions = [event["action"] for event in security.AUDIT_LOGS]
    assert "DEVICE_ENROLLED" in actions
    assert "DEVICE_REVOKED" in actions
    # A device fingerprint is an identifier, not a credential, but raw key
    # material must never reach the audit trail.
    serialized = json.dumps(security.AUDIT_LOGS)
    assert "public_key" not in serialized
