"""Device trust root (P-256 enrollment) and the durable device registry.

A configured registry file is loaded when this module is imported.
"""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import hmac
import json
import os
import time
from typing import Any, Mapping, Optional

from bank_security.audit import audit_event
from bank_security.config import AUTH_SECRET_CONFIGURED, STATE_LOCK, _env_int, logger
from bank_security.errors import SecurityError
from bank_security.sanitize import sanitize_text
from bank_security.store_integrity import resolve_store_digest_algorithm, store_integrity_digest


# ECDSA verification for the device trust root behind the red tier's biometric
# factor.  Imported defensively so a minimal installation still starts; when the
# dependency is missing the feature fails *closed* (a device attestation is
# refused) rather than silently accepting an unverified one.
try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes as _crypto_hashes
    from cryptography.hazmat.primitives.asymmetric import ec as _crypto_ec
    from cryptography.hazmat.primitives.serialization import load_der_public_key as _load_der_public_key
except ImportError:  # pragma: no cover - dependency guidance path
    InvalidSignature = None  # type: ignore[assignment]
    _crypto_hashes = None  # type: ignore[assignment]
    _crypto_ec = None  # type: ignore[assignment]
    _load_der_public_key = None  # type: ignore[assignment]

DEVICE_KEYS_AVAILABLE = _load_der_public_key is not None


# ---------------------------------------------------------------------------
# Device trust root
#
# The biometric factor is only worth something if the server can tell *which
# device* produced it.  The original implementation signed attestations with one
# shared HMAC key, which every client would have to embed - and a key inside an
# APK can be extracted, so the red tier would collapse to OTP-only.  A device
# instead generates a P-256 key inside its own Keystore, registers the public
# half once, and signs each server-issued liveness challenge.  The private half
# never leaves the device.
# ---------------------------------------------------------------------------

DEVICE_CURVE = "secp256r1"
DEVICE_MAX_RECORDS = _env_int("BANK_DEVICE_MAX_RECORDS", 2_000, 16, 200_000)
DEVICE_STATUS_ACTIVE = "ACTIVE"
DEVICE_STATUS_REVOKED = "REVOKED"

# Prefix of an attestation produced by an enrolled Android device (JSON
# envelope, base64).
FACE_ATTESTATION_V2_PREFIX = "v2:"
# Must match DeviceKeyManager.canonicalPayload() in the Android client byte for
# byte; a mismatch shows up as an InvalidSignature, never as a silent accept.
FACE_PAYLOAD_PREFIX = "bank-agent-face-v1"

DEVICES: dict[str, dict[str, Any]] = {}


def device_facing_payload(challenge_id: str, user_id: str, token_jti: str) -> bytes:
    """The exact bytes a device signs, and the server re-computes."""

    return f"{FACE_PAYLOAD_PREFIX}|{challenge_id}|{user_id}|{token_jti}".encode("utf-8")


def _purge_devices() -> None:
    if len(DEVICES) <= DEVICE_MAX_RECORDS:
        return
    # Evict the least recently seen devices first; an active device is re-seen
    # whenever it signs, so a stale entry is the safest thing to drop.
    ordered = sorted(DEVICES, key=lambda item: DEVICES[item].get("last_seen_at", 0))
    for device_id in ordered[: len(DEVICES) - DEVICE_MAX_RECORDS]:
        DEVICES.pop(device_id, None)


def _decode_device_public_key(public_key_b64: str) -> tuple[bytes, Any]:
    """Decode and validate a device public key (SPKI DER, P-256 only)."""

    if not DEVICE_KEYS_AVAILABLE:
        raise SecurityError("服务端未启用设备密钥校验", 503, "DEVICE_KEYS_UNAVAILABLE")
    value = sanitize_text(public_key_b64, field="设备公钥", max_length=4096)
    if not value:
        raise SecurityError("缺少设备公钥", 400, "INVALID_DEVICE_KEY")
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise SecurityError("设备公钥编码无效", 400, "INVALID_DEVICE_KEY") from exc
    try:
        key = _load_der_public_key(raw)
    except Exception as exc:
        raise SecurityError("设备公钥格式无效", 400, "INVALID_DEVICE_KEY") from exc
    # Pin the curve: accepting anything the client offers would let an attacker
    # register a weak/unknown key type and then "prove" liveness with it.
    if not isinstance(key, _crypto_ec.EllipticCurvePublicKey) or key.curve.name != DEVICE_CURVE:
        raise SecurityError("设备公钥必须是 P-256", 400, "UNSUPPORTED_DEVICE_KEY")
    return raw, key


def _device_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    """Client-safe projection: no key material, no other user's data."""

    return {
        "device_id": record.get("device_id"),
        "user_id": record.get("user_id"),
        "fingerprint": record.get("fingerprint"),
        "algorithm": "ECDSA-P256-SHA256",
        "status": record.get("status"),
        "label": record.get("label"),
        "platform": record.get("platform"),
        "app_version": record.get("app_version"),
        "created_at": record.get("created_at"),
        "last_seen_at": record.get("last_seen_at"),
    }


def enroll_device(
    public_key_b64: str,
    *,
    user_id: str,
    label: Optional[str] = None,
    platform: Optional[str] = None,
    app_version: Optional[str] = None,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    """Register a device public key for one principal.

    The device id is derived from the key itself, so enrollment is naturally
    idempotent and a client cannot choose an id that collides with (or
    impersonates) another device.  A key already bound to a different account is
    refused rather than transferred.
    """

    principal = sanitize_text(user_id, field="用户标识", max_length=128)
    if not principal:
        raise SecurityError("缺少用户标识", 401, "AUTHENTICATION_REQUIRED")
    raw, _key = _decode_device_public_key(public_key_b64)
    device_id = "DEV" + hashlib.sha256(raw).hexdigest()[:24].upper()
    fingerprint = hashlib.sha256(raw).hexdigest()
    safe_label = sanitize_text(label, field="设备名称", max_length=64) if label else None
    safe_platform = sanitize_text(platform, field="平台", max_length=32) if platform else None
    safe_version = sanitize_text(app_version, field="应用版本", max_length=32) if app_version else None
    now = int(time.time())

    with STATE_LOCK:
        existing = DEVICES.get(device_id)
        if existing is not None:
            if existing.get("user_id") != principal:
                raise SecurityError("该设备已绑定其他用户，请先解绑", 409, "DEVICE_ALREADY_BOUND")
            if existing.get("status") != DEVICE_STATUS_ACTIVE:
                raise SecurityError("该设备已被停用", 403, "DEVICE_REVOKED")
            existing["last_seen_at"] = now
            if safe_label:
                existing["label"] = safe_label
            if safe_version:
                existing["app_version"] = safe_version
            _persist_devices_locked()
            audit_event(
                "DEVICE_SEEN",
                user_id=principal,
                decision="ALLOW",
                outcome="ALREADY_ENROLLED",
                request_id=request_id,
                metadata={"device_id": device_id, "fingerprint": fingerprint},
            )
            return _device_summary(existing)

        record = {
            "device_id": device_id,
            "user_id": principal,
            "public_key": base64.b64encode(raw).decode("ascii"),
            "fingerprint": fingerprint,
            "label": safe_label,
            "platform": safe_platform,
            "app_version": safe_version,
            "status": DEVICE_STATUS_ACTIVE,
            "created_at": now,
            "last_seen_at": now,
        }
        DEVICES[device_id] = record
        _purge_devices()
        _persist_devices_locked()

    audit_event(
        "DEVICE_ENROLLED",
        user_id=principal,
        decision="ALLOW",
        outcome="SUCCESS",
        request_id=request_id,
        metadata={"device_id": device_id, "fingerprint": fingerprint, "platform": safe_platform},
    )
    return _device_summary(record)


def list_devices(user_id: str) -> list[dict[str, Any]]:
    """Every device registered to one principal, newest first."""

    principal = sanitize_text(user_id, field="用户标识", max_length=128)
    with STATE_LOCK:
        owned = [item for item in DEVICES.values() if item.get("user_id") == principal]
    return [_device_summary(item) for item in sorted(owned, key=lambda row: row.get("created_at", 0), reverse=True)]


def revoke_device(device_id: str, *, user_id: str, request_id: Optional[str] = None) -> dict[str, Any]:
    """Stop accepting attestations from one device.

    Ownership is checked against the principal, so a device can only be revoked
    by the account it belongs to.
    """

    principal = sanitize_text(user_id, field="用户标识", max_length=128)
    candidate = sanitize_text(device_id, field="设备编号", max_length=64)
    with STATE_LOCK:
        record = DEVICES.get(candidate)
        if record is None or record.get("user_id") != principal:
            # Same answer for "unknown" and "someone else's": no enumeration
            # oracle for device ids.
            raise SecurityError("设备不存在", 404, "DEVICE_NOT_FOUND")
        record["status"] = DEVICE_STATUS_REVOKED
        record["revoked_at"] = int(time.time())
        # A revocation that a restart forgets would silently re-arm a device the
        # user just disabled.
        _persist_devices_locked()
    audit_event(
        "DEVICE_REVOKED",
        user_id=principal,
        decision="ALLOW",
        outcome="REVOKED",
        request_id=request_id,
        metadata={"device_id": candidate},
    )
    return _device_summary(record)


# ---------------------------------------------------------------------------
# Device-registry durability
#
# The registry is in memory by default, so a restart forgets every enrolled
# device. Point ``BANK_DEVICE_STORE_PATH`` at a file to keep the
# trust registry across restarts.
#
# What is stored is security-sensitive.  Device public keys are what a liveness
# signature is checked against, so the file carries an integrity digest. With a configured
# ``BANK_AUTH_SECRET`` the digest is keyed and a forged file cannot be produced
# without the server secret; without one it only detects corruption, and
# ``device_store_status`` reports that rather than implying protection that is
# not there.
#
# A file that fails its digest, or that holds a structurally invalid record, is
# neither loaded nor overwritten: nothing in it is trusted, and writes are frozen
# so a later enrollment cannot quietly destroy the evidence.
# ---------------------------------------------------------------------------

DEVICE_STORE_VERSION = 1
_DEVICE_STORE_BLOCKED = False


def _device_store_target(path: Optional[str] = None) -> Optional[str]:
    """The configured store path, or None when durability is off."""

    target = (path if path is not None else os.getenv("BANK_DEVICE_STORE_PATH")) or ""
    return target.strip() or None


def _device_store_digest(canonical: str, *, keyed: bool) -> dict[str, str]:
    return store_integrity_digest(canonical, keyed=keyed)


def _device_record_valid(device_id: str, record: Any) -> bool:
    """Re-validate one persisted record before it is trusted again.

    A file is only as trustworthy as its digest, so this is a second line of
    defence: the id must still be the one *derived from the key it carries*, the
    key must still be P-256, and the status must still be one we understand.
    """

    if not isinstance(record, dict):
        return False
    if str(record.get("device_id") or "") != device_id:
        return False
    if not isinstance(record.get("user_id"), str) or not record["user_id"].strip():
        return False
    if record.get("status") not in {DEVICE_STATUS_ACTIVE, DEVICE_STATUS_REVOKED}:
        return False
    for stamp in ("created_at", "last_seen_at"):
        if not isinstance(record.get(stamp), (int, float)):
            return False
    try:
        raw, _key = _decode_device_public_key(str(record.get("public_key") or ""))
    except Exception:
        return False
    return "DEV" + hashlib.sha256(raw).hexdigest()[:24].upper() == device_id


def _persist_devices_locked() -> None:
    """Write the registry atomically.  Assumes ``STATE_LOCK`` is held."""

    target = _device_store_target()
    if not target or _DEVICE_STORE_BLOCKED:
        return
    devices = {device_id: deepcopy(DEVICES[device_id]) for device_id in sorted(DEVICES)}
    canonical = json.dumps(devices, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    snapshot = {
        "version": DEVICE_STORE_VERSION,
        "updated_at": int(time.time()),
        "devices": devices,
        "integrity": _device_store_digest(canonical, keyed=AUTH_SECRET_CONFIGURED),
    }
    temporary = f"{target}.tmp"
    try:
        directory = os.path.dirname(os.path.abspath(target))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass  # best effort; Windows ACLs are not POSIX modes
        # Replace, never truncate: a crash mid-write must not leave a half file
        # that the next start would refuse as tampered.
        os.replace(temporary, target)
    except OSError:
        logger.exception("Unable to persist the device store")


def persist_devices() -> dict[str, Any]:
    """Persist on demand; a no-op when durability is off or writes are frozen."""

    with STATE_LOCK:
        _persist_devices_locked()
    return device_store_status()


def load_device_store(path: Optional[str] = None) -> dict[str, Any]:
    """Load a persisted registry, refusing anything that fails validation.

    Opt-in and idempotent per process: with no ``BANK_DEVICE_STORE_PATH`` the
    in-memory demo stays deterministic and side-effect free.
    """

    global _DEVICE_STORE_LOAD_SUMMARY
    _DEVICE_STORE_LOAD_SUMMARY = _load_device_store(path)
    return _DEVICE_STORE_LOAD_SUMMARY


def _load_device_store(path: Optional[str] = None) -> dict[str, Any]:

    global _DEVICE_STORE_BLOCKED
    target = _device_store_target(path)
    if not target:
        return {"loaded_devices": 0, "path": None, "message": "BANK_DEVICE_STORE_PATH not configured"}
    try:
        with open(target, "r", encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except FileNotFoundError:
        return {"loaded_devices": 0, "path": target, "message": "设备库不存在，从空库开始"}
    except OSError as exc:
        _DEVICE_STORE_BLOCKED = True
        return {"loaded_devices": 0, "path": target, "rejected": True, "error": f"设备库无法读取: {type(exc).__name__}"}
    except json.JSONDecodeError:
        _DEVICE_STORE_BLOCKED = True
        return {"loaded_devices": 0, "path": target, "rejected": True, "error": "设备库不是合法 JSON"}

    if not isinstance(snapshot, dict) or snapshot.get("version") != DEVICE_STORE_VERSION:
        _DEVICE_STORE_BLOCKED = True
        return {"loaded_devices": 0, "path": target, "rejected": True, "error": "设备库版本不受支持"}
    devices = snapshot.get("devices")
    integrity = snapshot.get("integrity")
    if not isinstance(devices, dict) or not isinstance(integrity, dict):
        _DEVICE_STORE_BLOCKED = True
        return {"loaded_devices": 0, "path": target, "rejected": True, "error": "设备库结构无效"}

    canonical = json.dumps(devices, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    keyed, algorithm_error = resolve_store_digest_algorithm(integrity)
    if algorithm_error:
        _DEVICE_STORE_BLOCKED = True
        return {"loaded_devices": 0, "path": target, "rejected": True, "error": algorithm_error}
    expected = _device_store_digest(canonical, keyed=bool(keyed))["value"]
    if not hmac.compare_digest(expected, str(integrity.get("value") or "")):
        _DEVICE_STORE_BLOCKED = True
        return {"loaded_devices": 0, "path": target, "rejected": True, "error": "设备库完整性校验失败"}

    for device_id, record in devices.items():
        if not _device_record_valid(str(device_id), record):
            _DEVICE_STORE_BLOCKED = True
            return {"loaded_devices": 0, "path": target, "rejected": True, "error": f"设备记录无效: {device_id}"}

    with STATE_LOCK:
        for device_id, record in devices.items():
            DEVICES[str(device_id)] = deepcopy(record)
        _purge_devices()
    return {
        "loaded_devices": len(devices),
        "path": target,
        "rejected": False,
        "keyed": bool(keyed),
    }


def device_store_status() -> dict[str, Any]:
    """Describe registry durability without leaking the configured path."""

    configured = bool(_device_store_target())
    return {
        "mode": "memory+file" if configured else "memory_only",
        "file_configured": configured,
        "file_loaded_devices": _DEVICE_STORE_LOAD_SUMMARY.get("loaded_devices", 0),
        "file_rejected": bool(_DEVICE_STORE_LOAD_SUMMARY.get("rejected")),
        "writes_frozen": _DEVICE_STORE_BLOCKED,
        # False means the file carries a plain checksum: whoever can write it can
        # also forge it.  Configure BANK_AUTH_SECRET to make the digest keyed.
        "integrity_keyed": AUTH_SECRET_CONFIGURED,
    }


def _unfreeze_device_store() -> None:
    """Lift the write freeze a rejected store file imposes (test reset only)."""

    global _DEVICE_STORE_BLOCKED
    _DEVICE_STORE_BLOCKED = False


# Same opt-in shape for the device registry: without a path the demo keeps
# devices in memory only, and without persistence a restart silently drops every
# enrollment and delivery token.
load_device_store()
if _DEVICE_STORE_LOAD_SUMMARY.get("rejected"):
    logger.error(
        "设备库未通过校验，已拒绝加载并冻结写入（设备需重新登录注册）: %s",
        _DEVICE_STORE_LOAD_SUMMARY.get("error"),
    )
