"""Shared authenticated-client and enrolled-device helpers for tests."""

from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import security


DEMO_PASSWORD = "Bank@2026"


def login_session(client: Any, user_id: str = "user_zhang") -> str:
    """Authenticate through the same password endpoint used by Android and IM."""

    response = client.post(
        "/api/v1/auth/login",
        json={"user_id": user_id, "password": DEMO_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["session_token"])


def session_headers(client: Any, user_id: str = "user_zhang") -> dict[str, str]:
    return {"X-User-Id": user_id, "X-Session-Token": login_session(client, user_id)}


def sign_face_attestation(challenge_id: str, *, user_id: str, token_jti: str) -> str:
    """Enroll a disposable P-256 device and sign the Android wire payload."""

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_der = private_key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    device = security.enroll_device(
        base64.b64encode(public_der).decode("ascii"),
        user_id=user_id,
        label="pytest-device",
        platform="android-test",
        app_version="test",
    )
    signature = private_key.sign(
        security.device_facing_payload(challenge_id, user_id, token_jti),
        ec.ECDSA(hashes.SHA256()),
    )
    envelope = {
        "device_id": device["device_id"],
        "challenge_id": challenge_id,
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    encoded = base64.b64encode(json.dumps(envelope, separators=(",", ":")).encode("utf-8")).decode("ascii")
    return security.FACE_ATTESTATION_V2_PREFIX + encoded
