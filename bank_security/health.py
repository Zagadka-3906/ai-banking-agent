"""Security health summary for monitoring endpoints."""

from __future__ import annotations

import os
import time
from typing import Any

from bank_security.audit import AUDIT_LOGS, audit_persistence_status
from bank_security.authorization import AUTH_TOKENS, FACE_CHALLENGES
from bank_security.circuit import (
    BEHAVIOR_RULES,
    BEHAVIOR_STATE,
    SECURITY_STATE,
    behavior_large_amount_cents,
    behavior_risk_threshold,
    behavior_window_seconds,
)
from bank_security.config import (
    AUTH_SECRET_CONFIGURED,
    FACE_CHALLENGE_TTL_SECONDS,
    STATE_LOCK,
    demo_mode,
)
from bank_security.devices import (
    DEVICES,
    DEVICE_CURVE,
    DEVICE_KEYS_AVAILABLE,
    DEVICE_STATUS_ACTIVE,
    DEVICE_STATUS_REVOKED,
    device_store_status,
)
from bank_security.mfa import MFA_FACTORS
from bank_security.passwords import MIN_PASSWORD_LENGTH, PASSWORDS_AVAILABLE
from bank_security.sessions import SESSION_TOKENS, SESSION_TTL_SECONDS


def security_health() -> dict[str, Any]:
    with STATE_LOCK:
        now = time.monotonic()
        locked = sum(1 for state in SECURITY_STATE.values() if float(state.get("locked_until", 0)) > now)
        return {
            "status": "DEGRADED" if locked else "OK",
            "auth_secret_configured": AUTH_SECRET_CONFIGURED,
            "demo_mode": demo_mode(),
            "active_auth_tokens": sum(1 for rec in AUTH_TOKENS.values() if not rec.get("used") and float(rec.get("expires_at", 0)) > time.time()),
            "active_sessions": sum(1 for rec in SESSION_TOKENS.values() if float(rec.get("expires_at", 0)) > time.time()),
            "session_ttl_seconds": SESSION_TTL_SECONDS,
            "audit_events": len(AUDIT_LOGS),
            "open_circuits": locked,
            "audit_persistence": audit_persistence_status(),
            "mfa_factors": sorted(MFA_FACTORS),
            "behavior_risk": {
                "threshold": behavior_risk_threshold(),
                "window_seconds": behavior_window_seconds(),
                "large_amount_cents": behavior_large_amount_cents(),
                "rules": [rule["rule_id"] for rule in BEHAVIOR_RULES],
                "tracked_principals": len(BEHAVIOR_STATE),
                "locked_principals": sum(1 for state in SECURITY_STATE.values() if float(state.get("locked_until", 0)) > now),
            },
            "face_attestation": {
                "device_signature": "enabled" if DEVICE_KEYS_AVAILABLE else "unavailable",
                "curve": DEVICE_CURVE,
                "challenge_ttl_seconds": FACE_CHALLENGE_TTL_SECONDS,
                "active_challenges": sum(1 for rec in FACE_CHALLENGES.values() if not rec.get("used") and float(rec.get("expires", 0)) > time.time()),
            },
            "devices": {
                "enrolled": len(DEVICES),
                "active": sum(1 for item in DEVICES.values() if item.get("status") == DEVICE_STATUS_ACTIVE),
                "revoked": sum(1 for item in DEVICES.values() if item.get("status") == DEVICE_STATUS_REVOKED),
                "persistence": device_store_status(),
            },
            "login": {
                "password_hashing": "argon2id" if PASSWORDS_AVAILABLE else "unavailable",
                "min_password_length": MIN_PASSWORD_LENGTH,
                "otp_required": os.getenv("BANK_LOGIN_REQUIRE_OTP", "0").lower() in {"1", "true", "yes", "on"},
                # Whether a deployment refuses bare identity headers on writes.
                "session_required_for_writes": os.getenv("BANK_REQUIRE_SESSION", "0").lower() in {"1", "true", "yes", "on"},
            },
        }
