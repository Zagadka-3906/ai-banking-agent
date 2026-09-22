"""Disposable enrolled-device signer for Android channel end-to-end probes."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Mapping

import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


@dataclass
class DeviceSigner:
    """Exercise the same P-256 payload and envelope format as the Android app."""

    private_key: ec.EllipticCurvePrivateKey = field(default_factory=lambda: ec.generate_private_key(ec.SECP256R1()))
    device_id: str = ""

    def enroll(self, core_url: str, headers: Mapping[str, str], *, label: str) -> str:
        public_der = self.private_key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        response = requests.post(
            f"{core_url.rstrip('/')}/api/v1/devices/register",
            json={
                "public_key": base64.b64encode(public_der).decode("ascii"),
                "label": label,
                "platform": "android-e2e",
                "app_version": "probe",
            },
            headers=dict(headers),
            timeout=20,
        )
        response.raise_for_status()
        self.device_id = str(response.json()["device_id"])
        return self.device_id

    def sign(self, challenge_id: str, *, user_id: str, token_jti: str) -> str:
        if not self.device_id:
            raise RuntimeError("device must be enrolled before signing")
        payload = f"bank-agent-face-v1|{challenge_id}|{user_id}|{token_jti}".encode("utf-8")
        signature = self.private_key.sign(payload, ec.ECDSA(hashes.SHA256()))
        envelope = {
            "device_id": self.device_id,
            "challenge_id": challenge_id,
            "signature": base64.b64encode(signature).decode("ascii"),
        }
        encoded = base64.b64encode(json.dumps(envelope, separators=(",", ":")).encode("utf-8")).decode("ascii")
        return "v2:" + encoded
