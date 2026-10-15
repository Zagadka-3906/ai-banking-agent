"""Integrity digest carried by every durable store file."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Mapping, Optional

from bank_security.config import AUTH_SECRET, AUTH_SECRET_CONFIGURED


def store_integrity_digest(canonical: str, *, keyed: bool) -> dict[str, str]:
    """The integrity digest every durable store file carries.

    Keyed (HMAC) when ``BANK_AUTH_SECRET`` is configured, so a forged file
    needs the server secret; otherwise a plain checksum that only detects
    corruption.  Each store (device registry, ledger snapshot, approval
    registry) reports which mode is in force via its own status, rather than
    implying a protection that is not there.
    """

    if keyed:
        value = hmac.new(AUTH_SECRET, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        return {"alg": "hmac-sha256", "value": value}
    return {"alg": "sha256", "value": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


def resolve_store_digest_algorithm(integrity: Mapping[str, Any]) -> tuple[Optional[bool], Optional[str]]:
    """Decide whether to verify a store file with the keyed digest.

    Returns ``(keyed, error)``: exactly one is set.

    The algorithm must be chosen from *our* configuration, never from the file
    being verified.  Trusting the file's own ``alg`` field let anyone who could
    write the file downgrade ``hmac-sha256`` to a plain ``sha256`` checksum,
    recompute that checksum without ever learning ``BANK_AUTH_SECRET``, and have
    the tampered snapshot accepted as authentic - while ``*_store_status()``
    kept reporting ``integrity_keyed: True``.  A keyed store therefore only
    accepts a keyed file, and an unconfigured deployment (checksum mode, where
    the digest proves nothing about authorship) still rejects a keyed file it
    could not have produced.
    """

    algorithm = str(integrity.get("alg") or "")
    if AUTH_SECRET_CONFIGURED:
        if algorithm != "hmac-sha256":
            return None, (
                "存储文件摘要算法被降级：已配置 BANK_AUTH_SECRET，"
                f"只接受 hmac-sha256（文件声明 {algorithm or '空'}）"
            )
        return True, None
    if algorithm == "hmac-sha256":
        return None, "存储文件为 HMAC 摘要，但本进程未配置 BANK_AUTH_SECRET，无法校验"
    if algorithm != "sha256":
        return None, f"存储文件摘要算法未知: {algorithm or '空'}"
    return False, None
