"""Shared authenticated-client and enrolled-device helpers for tests."""

from __future__ import annotations

import base64
import json
import sys
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


def patch_everywhere(monkeypatch: Any, package: str, name: str, value: Any) -> None:
    """Replace *name* in every loaded module of *package* that binds the same object.

    ``bank_core`` / ``bank_agent`` modules import helpers by name, so patching
    the defining module alone would miss its callers.
    """

    modules = [module for key, module in list(sys.modules.items()) if key == package or key.startswith(package + ".")]
    targets = {id(getattr(module, name)) for module in modules if hasattr(module, name)}
    assert len(targets) == 1, f"{package}: {name!r} is bound to {len(targets)} different objects"
    for module in modules:
        if hasattr(module, name):
            monkeypatch.setattr(module, name, value)


def patch_security(monkeypatch: Any, name: str, value: Any) -> None:
    """Rebind a ``security`` name where the ``bank_security`` modules read it.

    ``security`` only re-exports the package, so patching the entry alone would
    not reach the functions that use the name.
    """

    patch_everywhere(monkeypatch, "bank_security", name, value)
    monkeypatch.setattr(security, name, value)


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
