"""Security primitives for the mock banking core.

The project intentionally keeps the *business* data in memory, but the
security boundary is implemented as if the core were a separate service:

* authorization tokens are HMAC signed, short lived and single use;
* yellow operations need an explicit confirmation and red operations need a
  server-side MFA verification (OTP + face attestation in the demo);
* suspicious requests are audited and a per-account circuit breaker is opened
  after repeated failures; and
* untrusted text is checked for common prompt-injection/control-sequence
  patterns before it reaches an LLM or an execution endpoint.

No raw token, OTP or secret is written to the audit trail.  The state is
process-local because this is a teaching/mock core.  A production deployment
should move it to a transactional store (Redis/DB) and use a KMS-managed key.

The implementation lives in the ``bank_security`` package, one module per
concern: ``config`` (environment, secret, shared lock), ``errors``,
``sanitize`` (input cleaning, injection detection, redaction), ``audit`` (hash
chain and per-service file), ``tokens`` (signed credentials), ``circuit``
(failure breaker and behaviour rules), ``sessions``, ``passwords``,
``authorization`` (approvals and the execution gate), ``store_integrity``,
``devices`` (trust root and registry), ``mfa``, ``health`` and ``reset``.

This module re-exports their names so ``import security`` keeps working.  The
re-exported objects are the ones the package uses, so mutating shared state
through ``security`` is safe.  Rebinding a name here is not: monkeypatch the
module that uses it (``tests.support.patch_everywhere``).  Module-level values
that the package itself rebinds are forwarded live by ``__getattr__``.
"""

from __future__ import annotations

# Import order matters: ``config`` loads ``.env`` first, ``audit`` replays the
# audit file and ``devices`` loads the device registry as they are imported.
from bank_security import audit as _audit_module, devices as _devices_module

from bank_security.config import (  # noqa: F401
    AUTH_SECRET,
    AUTH_SECRET_CONFIGURED,
    DEMO_MODE,
    FACE_CHALLENGE_TTL_SECONDS,
    FAILURE_WINDOW_SECONDS,
    LOCKOUT_SECONDS,
    MAX_AMOUNT_CENTS,
    MAX_FAILURES,
    MAX_TEXT_LENGTH,
    MFA_TEST_OTP,
    MFA_TTL_SECONDS,
    STATE_LOCK,
    STRICT_CLIENT_AUTH,
    TOKEN_TTL_SECONDS,
    _configured_secret,
    env_int,
    demo_mode,
    logger,
)
from bank_security.errors import (  # noqa: F401
    InjectionError,
    SecurityError,
)
from bank_security.sanitize import (  # noqa: F401
    _INJECTION_PATTERNS,
    contains_prompt_injection,
    credential_fingerprint,
    redact,
    sanitize_text,
)
from bank_security.audit import (  # noqa: F401
    AUDIT_LOGS,
    AUDIT_MAX_EVENTS,
    AUDIT_SERVICE,
    _AUDIT_LOAD_SUMMARY,
    _default_audit_dir,
    audit_event,
    audit_log_path,
    audit_persistence_status,
    get_audit_logs,
    get_audit_trace,
    load_audit_file,
    verify_audit_chain,
)
from bank_security.tokens import (  # noqa: F401
    _b64decode,
    _b64encode,
    decode_signed,
    signed_payload,
)
from bank_security.circuit import (  # noqa: F401
    BEHAVIOR_LARGE_AMOUNT_CENTS,
    BEHAVIOR_MAX_SIGNALS,
    BEHAVIOR_RISK_THRESHOLD,
    BEHAVIOR_RULES,
    BEHAVIOR_SIGNAL_WEIGHTS,
    BEHAVIOR_STATE,
    BEHAVIOR_WINDOW_SECONDS,
    SECURITY_STATE,
    _behavior_score,
    _evaluate_behavior_rules,
    _principal_key,
    _purge_behavior,
    _purge_failures,
    behavior_large_amount_cents,
    behavior_risk_threshold,
    behavior_status,
    behavior_window_seconds,
    circuit_status,
    ensure_circuit_closed,
    record_behavior_signal,
    record_security_failure,
    record_security_success,
)
from bank_security.sessions import (  # noqa: F401
    SESSION_MAX_RECORDS,
    SESSION_TOKENS,
    SESSION_TTL_SECONDS,
    _issue_session_token,
    _purge_sessions,
    assert_session_still_valid,
    issue_session_token,
    renew_session,
    resolve_session,
    revoke_session,
    revoke_sessions_for,
    session_binding,
)
from bank_security.passwords import (  # noqa: F401
    MIN_PASSWORD_LENGTH,
    PASSWORDS_AVAILABLE,
    _PASSWORD_HASHER,
    _PasswordHasher,
    hash_password,
    verify_password,
)
from bank_security.authorization import (  # noqa: F401
    AUTH_TOKENS,
    AUTH_TOKEN_MAX_RECORDS,
    FACE_CHALLENGES,
    FACE_CHALLENGE_MAX_RECORDS,
    MFA_PROOFS,
    _amount_to_cents,
    issue_mfa_proof,
    lookup_auth_locked,
    _validate_mfa_proof_locked,
    amount_to_cents,
    authorize_execution,
    cancel_auth_token,
    confirm_auth_token,
    inspect_auth_token,
    issue_auth_token,
    validate_actor,
)
from bank_security.store_integrity import (  # noqa: F401
    resolve_store_digest_algorithm,
    store_integrity_digest,
)
from bank_security.devices import (  # noqa: F401
    DEVICES,
    DEVICE_CURVE,
    DEVICE_KEYS_AVAILABLE,
    DEVICE_MAX_RECORDS,
    DEVICE_STATUS_ACTIVE,
    DEVICE_STATUS_REVOKED,
    DEVICE_STORE_VERSION,
    FACE_ATTESTATION_V2_PREFIX,
    FACE_PAYLOAD_PREFIX,
    InvalidSignature,
    crypto_ec,
    crypto_hashes,
    _decode_device_public_key,
    _device_record_valid,
    _device_store_digest,
    _device_store_target,
    _device_summary,
    _load_der_public_key,
    _load_device_store,
    persist_devices_locked,
    _purge_devices,
    device_facing_payload,
    device_store_status,
    enroll_device,
    list_devices,
    load_device_store,
    persist_devices,
    revoke_device,
)
from bank_security.mfa import (  # noqa: F401
    MFA_FACTORS,
    _factor_face_recognition,
    _factor_sms_otp,
    _purge_face_challenges,
    _verify_device_attestation,
    issue_demo_otp,
    issue_face_challenge,
    verify_face_attestation,
    verify_login_otp,
    verify_mfa,
)
from bank_security.health import (  # noqa: F401
    security_health,
)
from bank_security.reset import (  # noqa: F401
    reset_security_state,
)


# Rebound inside their modules (``global``); a re-export would freeze the
# value seen at import time, so these are read from the module on access.
_LIVE_ATTRIBUTES = {
    "_AUDIT_PREVIOUS_HASH": _audit_module,
    "_AUDIT_CHAIN_ANCHOR": _audit_module,
    "_DEVICE_STORE_BLOCKED": _devices_module,
    "_DEVICE_STORE_LOAD_SUMMARY": _devices_module,
}


def __getattr__(name: str):
    module = _LIVE_ATTRIBUTES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(module, name)
