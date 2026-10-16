"""HMAC-signed compact credentials shared by sessions, approvals and MFA proofs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Mapping, Optional

from bank_security.config import AUTH_SECRET
from bank_security.errors import SecurityError


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError("invalid base64 value")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def signed_payload(payload: Mapping[str, Any], secret: Optional[bytes] = None) -> str:
    key = AUTH_SECRET if secret is None else secret
    body = _b64encode(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = _b64encode(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def decode_signed(token: str, expected_type: str, secret: Optional[bytes] = None) -> dict[str, Any]:
    key = AUTH_SECRET if secret is None else secret
    if not isinstance(token, str) or len(token) > 8192:
        raise SecurityError("安全凭证格式无效", 403, "INVALID_TOKEN")
    pieces = token.split(".")
    if len(pieces) != 2:
        raise SecurityError("安全凭证格式无效", 403, "INVALID_TOKEN")
    body, supplied_sig = pieces
    try:
        expected_sig = _b64encode(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(expected_sig, supplied_sig):
            raise SecurityError("安全凭证签名无效", 403, "INVALID_TOKEN_SIGNATURE")
        payload = json.loads(_b64decode(body).decode("utf-8"))
    except SecurityError:
        raise
    except Exception:
        raise SecurityError("安全凭证格式无效", 403, "INVALID_TOKEN")
    if not isinstance(payload, dict) or payload.get("typ") != expected_type:
        raise SecurityError("安全凭证类型不匹配", 403, "INVALID_TOKEN_TYPE")
    now = time.time()
    try:
        issued = float(payload["iat"])
        expires = float(payload["exp"])
    except (KeyError, TypeError, ValueError):
        raise SecurityError("安全凭证时间字段无效", 403, "INVALID_TOKEN")
    # A small clock-skew allowance is useful in distributed demos, but tokens
    # cannot be used before their issue time by more than five seconds.
    if issued > now + 5 or expires <= now:
        raise SecurityError("安全凭证已过期", 403, "TOKEN_EXPIRED")
    return payload
