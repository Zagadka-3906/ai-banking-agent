"""Red-tier factors: liveness challenges, device attestations, OTPs and ``verify_mfa``."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Mapping, Optional

from bank_security.audit import audit_event
from bank_security.authorization import (
    FACE_CHALLENGES,
    FACE_CHALLENGE_MAX_RECORDS,
    issue_mfa_proof,
    lookup_auth_locked,
)
from bank_security.circuit import record_security_failure
from bank_security.config import (
    AUTH_SECRET,
    FACE_CHALLENGE_TTL_SECONDS,
    MFA_TEST_OTP,
    MFA_TTL_SECONDS,
    STATE_LOCK,
    demo_mode,
)
from bank_security.devices import (
    DEVICES,
    DEVICE_KEYS_AVAILABLE,
    DEVICE_STATUS_ACTIVE,
    FACE_ATTESTATION_V2_PREFIX,
    crypto_ec,
    crypto_hashes,
    _load_der_public_key,
    persist_devices_locked,
    device_facing_payload,
)
from bank_security.errors import SecurityError
from bank_security.sanitize import credential_fingerprint


def _purge_face_challenges() -> None:
    now = time.time()
    for challenge_id, record in list(FACE_CHALLENGES.items()):
        if record.get("used") or float(record.get("expires", 0)) <= now:
            FACE_CHALLENGES.pop(challenge_id, None)
    if len(FACE_CHALLENGES) > FACE_CHALLENGE_MAX_RECORDS:
        for challenge_id in sorted(FACE_CHALLENGES, key=lambda item: FACE_CHALLENGES[item].get("issued", 0))[: len(FACE_CHALLENGES) - FACE_CHALLENGE_MAX_RECORDS]:
            FACE_CHALLENGES.pop(challenge_id, None)


def issue_face_challenge(auth_token: str, *, user_id: Optional[str] = None, request_id: Optional[str] = None) -> dict[str, Any]:
    """Issue a short-lived liveness challenge bound to a RED auth token.

    The returned ``challenge_id`` must be signed by an enrolled Android device
    and presented to :func:`verify_mfa`.
    """

    with STATE_LOCK:
        payload, record = lookup_auth_locked(auth_token)
        principal = payload.get("from_user")
        if user_id and principal != user_id:
            record_security_failure(user_id, "face_challenge_identity_mismatch", request_id=request_id)
            raise SecurityError("人脸挑战与身份不一致", 403, "IDENTITY_MISMATCH")
        if payload.get("risk_level") != "RED":
            raise SecurityError("当前操作不需要人脸验证", 400, "MFA_NOT_REQUIRED")
        if record.get("mfa_verified"):
            raise SecurityError("MFA 凭证已验证，请勿重复发起", 409, "MFA_ALREADY_VERIFIED")
        now = int(time.time())
        challenge_id = secrets.token_urlsafe(18)
        # One outstanding challenge per token: re-issuing revokes the previous.
        for existing in FACE_CHALLENGES.values():
            if existing.get("jti") == payload.get("jti") and not existing.get("used"):
                existing["used"] = True
        FACE_CHALLENGES[challenge_id] = {
            "jti": payload.get("jti"),
            "sub": principal,
            "issued": now,
            "expires": now + FACE_CHALLENGE_TTL_SECONDS,
            "used": False,
        }
        _purge_face_challenges()
        audit_event(
            "FACE_CHALLENGE_ISSUED",
            user_id=principal,
            risk_level="RED",
            decision="ALLOW",
            outcome="SUCCESS",
            request_id=request_id,
            metadata={"jti": payload.get("jti"), "challenge_hash": credential_fingerprint(challenge_id), "ttl": FACE_CHALLENGE_TTL_SECONDS},
        )
        return {"challenge_id": challenge_id, "expires_at": now + FACE_CHALLENGE_TTL_SECONDS, "auth_token_jti": payload.get("jti")}


def _verify_device_attestation(
    attestation: str,
    *,
    auth_payload: Mapping[str, Any],
    user_id: Optional[str],
) -> Optional[dict[str, Any]]:
    """Verify a device-signed liveness attestation; return the challenge record.

    Every binding is checked before the signature is even examined, and a failure
    returns ``None`` so the caller reports a single, uniform "MFA failed" - the
    caller does not learn which check failed, and no failure is attributed here
    (``verify_mfa`` already records the attempt).
    """

    if not DEVICE_KEYS_AVAILABLE:
        return None
    try:
        envelope = json.loads(base64.b64decode(attestation[len(FACE_ATTESTATION_V2_PREFIX):], validate=True).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(envelope, Mapping):
        return None
    device_id = str(envelope.get("device_id") or "")
    challenge_id = str(envelope.get("challenge_id") or "")
    signature_b64 = str(envelope.get("signature") or "")
    if not device_id or not challenge_id or not signature_b64:
        return None
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except Exception:
        return None

    now = time.time()
    with STATE_LOCK:
        device = DEVICES.get(device_id)
        if not device or device.get("status") != DEVICE_STATUS_ACTIVE:
            return None
        challenge = FACE_CHALLENGES.get(challenge_id)
        if not challenge or challenge.get("used"):
            return None
        if float(challenge.get("expires", 0)) <= now:
            return None
        subject = str(challenge.get("sub") or "")
        # The challenge, the authorization nonce and the device must all agree on
        # the same principal; otherwise a device enrolled to one account could
        # satisfy a challenge issued for another.
        if challenge.get("jti") != auth_payload.get("jti"):
            return None
        if subject != auth_payload.get("from_user"):
            return None
        if user_id and subject != user_id:
            return None
        if device.get("user_id") != subject:
            return None
        try:
            public_key = _load_der_public_key(base64.b64decode(str(device.get("public_key")), validate=True))
        except Exception:
            return None
        if not isinstance(public_key, crypto_ec.EllipticCurvePublicKey):
            return None
        payload = device_facing_payload(challenge_id, subject, str(challenge.get("jti") or ""))
        try:
            public_key.verify(signature, payload, crypto_ec.ECDSA(crypto_hashes.SHA256()))
        except Exception:
            return None
        device["last_seen_at"] = int(now)
        persist_devices_locked()
    return challenge


def verify_face_attestation(attestation: Optional[str], *, auth_payload: Mapping[str, Any], user_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Validate an enrolled-device P-256 attestation."""

    if not isinstance(attestation, str) or not attestation.startswith(FACE_ATTESTATION_V2_PREFIX):
        return None
    return _verify_device_attestation(attestation, auth_payload=auth_payload, user_id=user_id)


def verify_login_otp(otp: Any) -> bool:
    """Validate the SMS code for a *login* attempt.

    Shares its source of truth with the MFA factor: the fixed demo code while
    ``BANK_DEMO_MODE`` is on, otherwise whatever the deployment configured.
    Without a configured code this returns False, so an unconfigured deployment
    cannot be logged into with an empty string.
    """

    expected = MFA_TEST_OTP if demo_mode() else os.getenv("BANK_MFA_OTP", "")
    return isinstance(otp, str) and bool(expected) and hmac.compare_digest(otp.strip(), expected)


def issue_demo_otp(auth_token: str, *, user_id: str, request_id: Optional[str] = None) -> dict[str, Any]:
    """A local demo delivery channel, never a claim of SMS delivery."""
    if not demo_mode():
        raise SecurityError("演示验证码仅限 Mock 模式", 403, "DEMO_ONLY")
    with STATE_LOCK:
        payload, record = lookup_auth_locked(auth_token)
        if payload.get("from_user") != user_id:
            raise SecurityError("验证码主体不一致", 403, "IDENTITY_MISMATCH")
        if payload.get("risk_level") != "RED":
            raise SecurityError("当前操作无需强验证", 400, "MFA_NOT_REQUIRED")
        if record.get("mfa_verified") or int(record.get("mfa_attempts", 0)) >= 3:
            raise SecurityError("当前验证不可重新发码", 423, "MFA_LOCKED")
        now = time.time()
        if now - float(record.get("otp_issued_at", 0)) < 30 or int(record.get("otp_issues", 0)) >= 3:
            raise SecurityError("验证码请求过于频繁，请稍后重试", 429, "OTP_RATE_LIMITED")
        code = f"{secrets.randbelow(1_000_000):06d}"
        record["otp_digest"] = hmac.new(AUTH_SECRET, (str(payload["jti"]) + ":" + code).encode(), hashlib.sha256).hexdigest()
        record["otp_expires_at"] = min(float(payload["exp"]), now + 60)
        record["otp_issued_at"] = now
        record["otp_issues"] = int(record.get("otp_issues", 0)) + 1
        audit_event("DEMO_OTP_ISSUED", user_id=user_id, decision="ALLOW", outcome="ISSUED", request_id=request_id,
                    metadata={"jti": payload["jti"], "delivery": "local_demo", "expires_at": record["otp_expires_at"]})
        return {"demo_code": code, "expires_in": max(0, int(record["otp_expires_at"] - now)), "delivery": "local_demo"}


def _factor_sms_otp(ctx: dict[str, Any]) -> bool:
    otp = ctx.get("otp")
    record = ctx.get("auth_record") or {}
    if record.get("otp_digest"):
        digest = hmac.new(AUTH_SECRET, (str(ctx["auth_payload"]["jti"]) + ":" + str(otp or "").strip()).encode(), hashlib.sha256).hexdigest()
        return time.time() < record.get("otp_expires_at", 0) and hmac.compare_digest(digest, record["otp_digest"])
    if demo_mode() and os.getenv("BANK_MFA_DYNAMIC_OTP", "0") == "1":
        return False
    expected = MFA_TEST_OTP if demo_mode() else os.getenv("BANK_MFA_OTP", "")
    return isinstance(otp, str) and bool(expected) and hmac.compare_digest(otp.strip(), expected)


def _factor_face_recognition(ctx: dict[str, Any]) -> bool:
    challenge = verify_face_attestation(ctx.get("face_attestation"), auth_payload=ctx["auth_payload"], user_id=ctx.get("user_id"))
    if challenge is not None:
        ctx["_face_challenge"] = challenge
        return True
    return False


# Registry of red-zone factor validators keyed by the ``required_actions``
# labels carried in the precheck token.  ``USER_CLICK_CONFIRM`` is implicit
# (a successful MFA submission is itself an explicit confirmation).  Extra
# factors (for example a U-shield challenge signature) can be registered here
# without touching the verification flow.
MFA_FACTORS: dict[str, Any] = {
    "SMS_OTP": _factor_sms_otp,
    "FACE_RECOGNITION": _factor_face_recognition,
}


def verify_mfa(
    auth_token: str,
    *,
    otp: Optional[str],
    face_attestation: Optional[str] = None,
    user_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    """Validate red-zone factors on the server and issue a bound proof.

    Factors are derived from the precheck token's ``required_actions`` and
    resolved through the :data:`MFA_FACTORS` registry.  The biometric factor is
    a single-use device signature bound to a server challenge and this token's
    ``jti``.
    """

    with STATE_LOCK:
        payload, record = lookup_auth_locked(auth_token)
        principal = payload.get("from_user")
        if user_id and principal != user_id:
            record_security_failure(user_id, "mfa_identity_mismatch", request_id=request_id)
            raise SecurityError("MFA 身份与凭证不一致", 403, "IDENTITY_MISMATCH")
        if payload.get("risk_level") != "RED":
            raise SecurityError("当前操作不需要红色级别 MFA", 400, "MFA_NOT_REQUIRED")
        if record.get("mfa_verified"):
            raise SecurityError("MFA 凭证已验证，请勿重复提交", 409, "MFA_ALREADY_VERIFIED")
        attempts = int(record.get("mfa_attempts", 0))
        if attempts >= 3:
            record_security_failure(principal, "mfa_attempt_limit", request_id=request_id)
            raise SecurityError("MFA 尝试次数过多，已触发安全熔断", 423, "MFA_LOCKED")

        record["mfa_attempts"] = attempts + 1
        required = [str(action) for action in (payload.get("required_actions") or [])]
        factors = [action for action in required if action != "USER_CLICK_CONFIRM"]
        if not factors:
            raise SecurityError("该操作未配置可验证的 MFA 因子", 500, "MFA_NOT_CONFIGURED")
        face_challenge: Optional[dict[str, Any]] = None
        failed: list[str] = []
        for action in factors:
            validator = MFA_FACTORS.get(action)
            if validator is None:
                raise SecurityError(f"未知的 MFA 因子: {action}", 500, "MFA_NOT_CONFIGURED")
            ctx: dict[str, Any] = {
                "otp": otp,
                "face_attestation": face_attestation,
                "auth_payload": payload,
                "auth_record": record,
                "user_id": principal,
            }
            if validator(ctx):
                if action == "FACE_RECOGNITION":
                    face_challenge = ctx.get("_face_challenge")
            else:
                failed.append(action)
        if failed:
            record_security_failure(principal, "mfa_factor_failed", request_id=request_id)
            audit_event(
                "MFA_VERIFY",
                user_id=principal,
                risk_level="RED",
                decision="BLOCK",
                outcome="FAILURE",
                reason="MFA factor failed",
                request_id=request_id,
                metadata={"jti": payload.get("jti"), "failed_factors": failed, "attestation_present": bool(face_attestation), "attempt": record["mfa_attempts"]},
            )
            raise SecurityError(_mfa_failure_message(failed), 403, "MFA_FAILED")

        record["mfa_verified"] = True
        record["mfa_verified_at"] = int(time.time())
        # A successful MFA also counts as an explicit confirmation in the
        # high-risk dialog; the user had to submit the verification form.
        record["confirmed"] = True
        if face_challenge is not None:
            face_challenge["used"] = True
        proof = issue_mfa_proof(payload, auth_token)
        audit_event(
            "MFA_VERIFY",
            user_id=principal,
            risk_level="RED",
            decision="ALLOW",
            outcome="SUCCESS",
            request_id=request_id,
            metadata={"jti": payload.get("jti"), "proof_hash": credential_fingerprint(proof), "factors": factors},
        )
        return {"verified": True, "mfa_proof": proof, "expires_at": min(payload["exp"], int(time.time()) + MFA_TTL_SECONDS), "factors": factors}


def _mfa_failure_message(failed: list[str]) -> str:
    """Say which factor failed, so the user knows what to redo."""

    if failed == ["SMS_OTP"]:
        return "短信验证码不正确或已过期，请重新获取验证码后再试；设备验证已通过"
    if failed == ["FACE_RECOGNITION"]:
        return "设备验证未通过，请重新进行指纹或面容验证"
    return "短信验证码和设备验证都未通过，请重新获取验证码并完成设备验证"
