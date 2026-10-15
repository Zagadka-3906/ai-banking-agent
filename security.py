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
"""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import unicodedata
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping, Optional

# Load a local ``.env`` before reading security configuration.  The Core API
# is commonly started directly with ``python -m uvicorn``,
# where Uvicorn does not load dotenv files for us.  Existing process
# environment variables remain authoritative because ``override`` defaults to
# ``False``; deployments can therefore inject secrets through the environment
# or a secret manager as usual.
try:  # Keep the security module importable in minimal test environments.
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv is a runtime dependency
    pass
else:
    load_dotenv()


logger = logging.getLogger("mock_banking.security")

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

# Password hashing for the login endpoint.  Also optional at import time, and
# also fails closed: without the library nobody can log in, rather than anybody.
try:
    from argon2 import PasswordHasher as _PasswordHasher
except ImportError:  # pragma: no cover - dependency guidance path
    _PasswordHasher = None  # type: ignore[assignment]

PASSWORDS_AVAILABLE = _PasswordHasher is not None
_PASSWORD_HASHER = _PasswordHasher() if _PasswordHasher is not None else None


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def demo_mode() -> bool:
    """Whether demo-only affordances are enabled.

    Read per call rather than captured at import time, so a deployment that
    reloads its configuration - or an operator who turns demo mode off in the
    environment - takes effect immediately.

    This used to return the module-level ``DEMO_MODE`` constant while claiming
    to read per call.  Setting ``BANK_DEMO_MODE=0`` at runtime therefore
    switched off ``agent_api``'s demo identity short-circuit (which does read
    the environment) but left the core still minting password-free session
    credentials at ``POST /api/v1/auth/session`` - i.e. the switch that is
    documented as the way to close that door did not close it.
    """

    return os.getenv("BANK_DEMO_MODE", "1" if DEMO_MODE else "0").lower() in {"1", "true", "yes", "on"}


# Must come after ``_env_int`` is defined - it validates through it.
MIN_PASSWORD_LENGTH = _env_int("BANK_MIN_PASSWORD_LENGTH", 8, 6, 128)


# A random process key is safer than a hard-coded development key.  Operators
# should set BANK_AUTH_SECRET in deployments; a restart invalidates outstanding
# demo tokens when no persistent secret is configured.
_configured_secret = os.getenv("BANK_AUTH_SECRET") or os.getenv("BANK_TOKEN_SECRET")
AUTH_SECRET_CONFIGURED = bool(_configured_secret)
AUTH_SECRET = (_configured_secret or secrets.token_urlsafe(48)).encode("utf-8")

TOKEN_TTL_SECONDS = _env_int("BANK_TOKEN_TTL_SECONDS", 120, 30, 900)
MFA_TTL_SECONDS = _env_int("BANK_MFA_TTL_SECONDS", 120, 30, 900)
MAX_FAILURES = _env_int("BANK_CIRCUIT_FAILURE_THRESHOLD", 5, 2, 20)
FAILURE_WINDOW_SECONDS = _env_int("BANK_CIRCUIT_WINDOW_SECONDS", 300, 30, 3600)
LOCKOUT_SECONDS = _env_int("BANK_CIRCUIT_LOCKOUT_SECONDS", 300, 30, 86400)
MAX_TEXT_LENGTH = _env_int("BANK_MAX_INPUT_LENGTH", 2000, 128, 10000)
MAX_AMOUNT_CENTS = _env_int("BANK_MAX_TRANSFER_CENTS", 100_000_000, 100, 10_000_000_000)
STRICT_CLIENT_AUTH = os.getenv("BANK_REQUIRE_CLIENT_AUTH", "0").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
# The fixed code is deliberately restricted to the local mock.  Set a random
# BANK_MFA_TEST_OTP (or disable demo mode) when exposing the service anywhere
# beyond a local competition demo.
DEMO_MODE = os.getenv("BANK_DEMO_MODE", "1").lower() in {"1", "true", "yes", "on"}
MFA_TEST_OTP = os.getenv("BANK_MFA_TEST_OTP", "888888")

# The server issues a short-lived liveness challenge bound to an authorization
# jti.  Only a P-256 signature from an enrolled Android device can satisfy it.
FACE_CHALLENGE_TTL_SECONDS = _env_int("BANK_FACE_CHALLENGE_TTL_SECONDS", 60, 10, 300)


STATE_LOCK = threading.RLock()

# Public names are kept stable for tests and for the original main.py API.
AUTH_TOKENS: dict[str, dict[str, Any]] = {}
MFA_PROOFS: dict[str, dict[str, Any]] = {}

# Server-issued biometric challenges.  Each challenge is bound to a single
# auth-token jti and user, expires quickly, and can be satisfied at most once;
# re-issuing a challenge for the same token revokes the previous one.
FACE_CHALLENGES: dict[str, dict[str, Any]] = {}
FACE_CHALLENGE_MAX_RECORDS = _env_int("BANK_FACE_CHALLENGE_MAX_RECORDS", 5000, 100, 200_000)

# Reusable, short-lived session credentials.  Unlike the single-use nonce they
# are not consumed by a settlement, but they expire quickly and are bound to a
# principal, so a channel no longer has to self-assert X-User-Id.
SESSION_TOKENS: dict[str, dict[str, Any]] = {}
SESSION_TTL_SECONDS = _env_int("BANK_SESSION_TTL_SECONDS", 900, 60, 86_400)
SESSION_MAX_RECORDS = _env_int("BANK_SESSION_MAX_RECORDS", 5000, 100, 200_000)

# Per-principal failure windows.  deque values are (monotonic timestamp,
# reason), which avoids clock rollback issues for lockout decisions.
SECURITY_STATE: dict[str, dict[str, Any]] = defaultdict(
    lambda: {"failures": deque(), "locked_until": 0.0, "lock_reason": None}
)

AUDIT_LOGS: list[dict[str, Any]] = []
AUDIT_MAX_EVENTS = _env_int("BANK_AUDIT_MAX_EVENTS", 5000, 100, 100_000)
AUTH_TOKEN_MAX_RECORDS = _env_int("BANK_AUTH_TOKEN_MAX_RECORDS", 20_000, 100, 200_000)
_AUDIT_PREVIOUS_HASH = "0" * 64
# ``AUDIT_LOGS`` is intentionally bounded for the in-memory demo.  Retain the
# predecessor hash of the first retained event so verification remains valid
# after log rotation instead of restarting from genesis and reporting a false
# tamper signal.
_AUDIT_CHAIN_ANCHOR = "0" * 64


class SecurityError(Exception):
    """A safe, user-facing security failure.

    ``status_code`` is consumed by the FastAPI adapter.  Keeping the class
    framework-independent makes the same checks usable from an agent worker
    or a CLI test.
    """

    def __init__(self, detail: str, status_code: int = 403, code: str = "SECURITY_ERROR"):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.code = code


class InjectionError(SecurityError):
    def __init__(self, detail: str = "检测到不安全输入，操作已阻断"):
        super().__init__(detail, status_code=400, code="PROMPT_INJECTION_BLOCKED")


# Patterns are intentionally conservative and only target instructions that
# attempt to alter the agent's control plane.  Normal banking words such as
# “系统” or “验证码” alone are allowed.
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?", re.I),
    re.compile(r"忽略(?:之前|上面|此前).{0,12}(?:指令|规则|提示|要求)"),
    re.compile(r"(?:绕过|跳过|解除|取消).{0,12}(?:权限|风控|验证|安全)"),
    re.compile(r"(?:system|developer|assistant)\s*(?:prompt|message|指令)", re.I),
    re.compile(r"(?:reveal|show|print|泄露|输出).{0,20}(?:prompt|系统提示|密钥|token|凭证)", re.I),
    re.compile(r"(?:jailbreak|dan\s+mode|developer\s+mode|越狱|开发者模式)", re.I),
    re.compile(r"<\|\s*(?:system|assistant|tool|im_start|im_end)\s*\|>", re.I),
    # Delimiter/role tags used by the extraction prompt must remain data, not
    # a way for an utterance to close its sandbox and inject a new instruction.
    re.compile(r"</?\s*(?:user_utterance|system|developer|assistant|tool|instructions?)\b", re.I),
    re.compile(r"(?:tool[_ -]?call|function[_ -]?call|调用工具|执行代码|shell\s+command)", re.I),
    re.compile(r"(?:__import__|os\.system|subprocess|powershell|cmd\.exe|drop\s+table|union\s+select)", re.I),
]


def contains_prompt_injection(value: Any) -> bool:
    """Return whether *value* contains a control-plane injection attempt."""

    if value is None:
        return False
    try:
        text = unicodedata.normalize("NFKC", str(value))
        # Unicode format characters (for example ZERO WIDTH SPACE and
        # RIGHT-TO-LEFT OVERRIDE) are invisible in logs and can be inserted
        # between words to evade delimiter/role regexes.  They do not carry
        # useful meaning in a control instruction, so remove them before the
        # policy scan.  This keeps the original value untouched for ordinary
        # business storage while making the security decision canonical.
        text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    except Exception:
        return True
    if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in text):
        return True
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)


def sanitize_text(value: Any, *, field: str = "文本", max_length: int = MAX_TEXT_LENGTH) -> str:
    """Normalize and validate text received from a user/model.

    This function does not attempt to interpret natural language.  It only
    enforces a size/control-character boundary and rejects known attempts to
    manipulate the instruction hierarchy.
    """

    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    text = unicodedata.normalize("NFKC", value).strip()
    if not text:
        return ""
    if len(text) > max_length:
        raise InjectionError(f"{field}长度超过安全限制")
    if contains_prompt_injection(text):
        raise InjectionError(f"{field}包含疑似提示词注入或控制指令")
    return text


def redact(value: Any) -> Any:
    """Recursively redact credentials before putting data in an audit event."""

    sensitive = {
        "auth_token",
        "token",
        "x_auth_token",
        "mfa_token",
        "mfa_proof",
        "otp",
        "demo_code",
        "otp_digest",
        "sms_code",
        "secret",
        "password",
        "face_attestation",
        "authorization",
        "access_token",
        "api_key",
        "client_secret",
        "private_key",
        "cookie",
        "set-cookie",
    }
    if isinstance(value, Mapping):
        return {
            str(k): ("[REDACTED]" if str(k).lower() in sensitive else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple, deque)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        # Catch credentials passed under an unexpected metadata key as well as
        # the explicit key allow-list above.  Signed nonces/proofs are long
        # URL-safe strings containing a dot; API keys are typically long
        # high-entropy ASCII strings.  Short business identifiers and normal
        # Chinese descriptions are left untouched.
        compact = value.strip()
        credential_like = (
            len(compact) >= 32
            and bool(re.fullmatch(r"[A-Za-z0-9_\-.~+/=]+", compact))
            and ("." in compact or len(compact) >= 40)
        )
        if credential_like:
            return "[REDACTED]"
        if len(value) > 200:
            return value[:200] + "…"
    return value


def credential_fingerprint(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def audit_event(
    action: str,
    *,
    user_id: Optional[str] = None,
    risk_level: Optional[str] = None,
    decision: Optional[str] = None,
    outcome: Optional[str] = None,
    reason: Optional[str] = None,
    request_id: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Append a tamper-evident, redacted audit event and return it."""

    global _AUDIT_PREVIOUS_HASH, _AUDIT_CHAIN_ANCHOR
    now = datetime.now(timezone.utc)
    with STATE_LOCK:
        event: dict[str, Any] = {
            "event_id": f"AUD{secrets.token_hex(8).upper()}",
            "timestamp": now.isoformat(),
            "action": action,
            "user_id": redact(user_id),
            "risk_level": risk_level,
            "decision": decision,
            "outcome": outcome,
            "reason": redact(reason),
            "request_id": redact(request_id),
            "metadata": redact(dict(metadata or {})),
        }
        canonical = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        event["integrity_hash"] = hashlib.sha256(
            (_AUDIT_PREVIOUS_HASH + canonical).encode("utf-8")
        ).hexdigest()
        _AUDIT_PREVIOUS_HASH = event["integrity_hash"]
        AUDIT_LOGS.append(event)
        if len(AUDIT_LOGS) > AUDIT_MAX_EVENTS:
            excess = len(AUDIT_LOGS) - AUDIT_MAX_EVENTS
            _AUDIT_CHAIN_ANCHOR = AUDIT_LOGS[excess - 1]["integrity_hash"]
            del AUDIT_LOGS[:excess]

        # Core and Gateway each append their own chain (see ``audit_log_path``);
        # events are redacted before they reach the file.
        audit_path = audit_log_path()
        if audit_path:
            try:
                os.makedirs(os.path.dirname(audit_path) or ".", exist_ok=True)
                with open(audit_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            except OSError:
                logger.exception("Unable to append audit event")
        # Return an isolated snapshot so callers cannot mutate the stored event
        # (including nested metadata) and thereby invalidate or rewrite the
        # in-memory audit trail after it has been hashed.
        return deepcopy(event)


def get_audit_logs(*, limit: int = 100, user_id: Optional[str] = None) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 1000))
    with STATE_LOCK:
        selected = [e for e in AUDIT_LOGS if user_id is None or e.get("user_id") == user_id]
        return deepcopy(selected[-limit:])


def get_audit_trace(request_id: str) -> dict[str, Any]:
    """Return the ordered decision chain for one request_id.

    The core writes every precheck, confirmation, MFA, settlement and token
    consumption event with the caller's X-Request-Id, so one id reconstructs
    the full authoritative decision path of a business request.  Agent-side
    events (intent parse, responder validation) live in the agent process and
    are not part of this core-side chain in the demo deployment.
    """

    with STATE_LOCK:
        events = [deepcopy(event) for event in AUDIT_LOGS if event.get("request_id") == request_id]
    if not events:
        return {"request_id": request_id, "principal": None, "stage_count": 0, "stages": [], "events": []}
    return {
        "request_id": request_id,
        "principal": events[0].get("user_id"),
        "stage_count": len(events),
        "stages": [event.get("action") for event in events],
        "risk_levels": [event.get("risk_level") for event in events],
        "first_at": events[0].get("timestamp"),
        "last_at": events[-1].get("timestamp"),
        "terminal_outcome": next((event.get("outcome") for event in reversed(events) if event.get("action") != "WORKFLOW_STAGE"), None),
        "events": events,
    }


def verify_audit_chain() -> dict[str, Any]:
    """Verify the in-memory audit hash chain without exposing credentials."""

    checked = 0
    with STATE_LOCK:
        previous = _AUDIT_CHAIN_ANCHOR
        for event in AUDIT_LOGS:
            supplied = event.get("integrity_hash")
            body = {key: value for key, value in event.items() if key != "integrity_hash"}
            canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            expected = hashlib.sha256((previous + canonical).encode("utf-8")).hexdigest()
            if not isinstance(supplied, str) or not hmac.compare_digest(expected, supplied):
                return {"valid": False, "checked_events": checked, "failed_event_id": event.get("event_id")}
            previous = supplied
            checked += 1
    return {"valid": True, "checked_events": checked, "failed_event_id": None}


AUDIT_SERVICE = os.getenv("BANK_AUDIT_SERVICE", "").strip().lower() or None


def _default_audit_dir() -> str:
    base = os.getenv("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "bank-agent", "audit")


def audit_log_path() -> Optional[str]:
    """The append-only file this process writes, or None for memory only.

    Core and Gateway read the same ``.env``. Two processes appending separate
    hash chains to one file broke each other's links, so a restart rejected
    genuine events as tampered. A service process (``BANK_AUDIT_SERVICE``, set
    by its entry module) therefore always writes its own file: by default
    ``<BANK_AUDIT_LOG_DIR>/<service>-audit.jsonl`` outside the repository, and
    an explicit ``BANK_AUDIT_LOG_PATH`` gains the service name before its
    suffix. ``BANK_AUDIT_LOG_DIR=off`` keeps the events in memory only.
    """

    explicit = os.getenv("BANK_AUDIT_LOG_PATH", "").strip()
    if explicit:
        if not AUDIT_SERVICE:
            return explicit
        stem, suffix = os.path.splitext(explicit)
        return f"{stem}.{AUDIT_SERVICE}{suffix or '.jsonl'}"
    if not AUDIT_SERVICE:
        return None
    directory = os.getenv("BANK_AUDIT_LOG_DIR", "").strip()
    if directory.lower() == "off":
        return None
    return os.path.join(directory or _default_audit_dir(), f"{AUDIT_SERVICE}-audit.jsonl")


def load_audit_file(path: Optional[str] = None) -> dict[str, Any]:
    """Replay an append-only audit file into the in-memory hash chain.

    Each persisted line is re-hashed against the running chain, so a tampered
    or truncated file is detected instead of being silently trusted.  Runs
    once per process at import, for the file ``audit_log_path`` selects.
    """

    global _AUDIT_PREVIOUS_HASH, _AUDIT_CHAIN_ANCHOR
    target = path or audit_log_path()
    if not target:
        return {"loaded_events": 0, "path": None, "message": "audit file disabled"}
    loaded = 0
    with STATE_LOCK:
        try:
            handle = open(target, "r", encoding="utf-8")
        except FileNotFoundError:
            return {"loaded_events": 0, "path": target, "message": "审计文件不存在，从空链开始"}
        except OSError as exc:
            return {"loaded_events": 0, "path": target, "error": str(exc)}
        with handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed audit line")
                    continue
                if not isinstance(event, dict) or not isinstance(event.get("integrity_hash"), str):
                    logger.warning("Skipping audit line without integrity hash")
                    continue
                body = {key: value for key, value in event.items() if key != "integrity_hash"}
                canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                expected = hashlib.sha256((_AUDIT_PREVIOUS_HASH + canonical).encode("utf-8")).hexdigest()
                if not hmac.compare_digest(expected, event["integrity_hash"]):
                    return {
                        "loaded_events": loaded,
                        "path": target,
                        "tampered": True,
                        "error": f"审计文件哈希链校验失败于 {event.get('event_id')}",
                    }
                _AUDIT_PREVIOUS_HASH = event["integrity_hash"]
                AUDIT_LOGS.append(event)
                loaded += 1
                if len(AUDIT_LOGS) > AUDIT_MAX_EVENTS:
                    excess = len(AUDIT_LOGS) - AUDIT_MAX_EVENTS
                    _AUDIT_CHAIN_ANCHOR = AUDIT_LOGS[excess - 1]["integrity_hash"]
                    del AUDIT_LOGS[:excess]
    return {"loaded_events": loaded, "path": target, "tampered": False}


def audit_persistence_status() -> dict[str, Any]:
    """Describe where audit events are stored, without leaking the path secret."""

    configured = audit_log_path() is not None
    return {
        "mode": "memory+append_only_file" if configured else "memory_only",
        "file_configured": configured,
        "service": AUDIT_SERVICE,
        "file_loaded_events": _AUDIT_LOAD_SUMMARY.get("loaded_events", 0),
        "file_tampered": bool(_AUDIT_LOAD_SUMMARY.get("tampered")),
        "in_memory_events": len(AUDIT_LOGS),
    }


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError("invalid base64 value")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _signed_payload(payload: Mapping[str, Any], secret: Optional[bytes] = None) -> str:
    key = AUTH_SECRET if secret is None else secret
    body = _b64encode(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = _b64encode(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def _decode_signed(token: str, expected_type: str, secret: Optional[bytes] = None) -> dict[str, Any]:
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


def _amount_to_cents(amount: Any) -> int:
    from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

    try:
        # str() avoids binary float artefacts while still accepting numeric
        # values supplied by Pydantic or a test client.
        value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise SecurityError("金额格式无效", 400, "INVALID_AMOUNT")
    if not value.is_finite() or value <= 0:
        raise SecurityError("转账金额必须大于 0", 400, "INVALID_AMOUNT")
    cents = int(value * 100)
    if cents > MAX_AMOUNT_CENTS:
        raise SecurityError("金额超过单笔安全上限", 400, "AMOUNT_LIMIT_EXCEEDED")
    return cents


def amount_to_cents(amount: Any) -> int:
    """Public wrapper used by the API and agent tests."""

    return _amount_to_cents(amount)


def _purge_failures(state: dict[str, Any], now: Optional[float] = None) -> None:
    current = time.monotonic() if now is None else now
    failures: deque = state["failures"]
    while failures and current - failures[0][0] > FAILURE_WINDOW_SECONDS:
        failures.popleft()


def _principal_key(user_id: Optional[str]) -> str:
    """Canonical key for per-principal security state."""

    value = str(user_id).strip() if user_id else ""
    return value or "anonymous"


def circuit_status(user_id: Optional[str]) -> dict[str, Any]:
    principal = _principal_key(user_id)
    now = time.monotonic()
    with STATE_LOCK:
        state = SECURITY_STATE[principal]
        _purge_failures(state, now)
        locked_until = float(state.get("locked_until", 0.0))
        if locked_until and locked_until <= now:
            state["locked_until"] = 0.0
            state["lock_reason"] = None
            locked_until = 0.0
        return {
            "principal": principal,
            "locked": locked_until > now,
            "locked_until_epoch": locked_until if locked_until > now else None,
            "failure_count": len(state["failures"]),
            "lock_reason": state.get("lock_reason"),
        }


def ensure_circuit_closed(user_id: Optional[str]) -> None:
    status = circuit_status(user_id)
    if status["locked"]:
        retry = max(1, int(status["locked_until_epoch"] - time.monotonic()))
        reason = status.get("lock_reason") or "连续失败"
        raise SecurityError(
            f"安全熔断已开启，请在约 {retry} 秒后重试（原因：{reason}）",
            423,
            "CIRCUIT_OPEN",
        )


def record_security_failure(user_id: Optional[str], reason: str, *, request_id: Optional[str] = None) -> dict[str, Any]:
    # Attribute failures only to a principal actually presented by the caller
    # (or to the anonymous bucket).  Callers must not pass an untrusted
    # request-body ``user_id`` here: doing so would let an attacker open
    # somebody else's circuit with forged payloads.
    principal = _principal_key(user_id)
    reason_text = str(reason)[:120]
    now = time.monotonic()
    with STATE_LOCK:
        state = SECURITY_STATE[principal]
        _purge_failures(state, now)
        # Do not extend an already active lockout on every blocked retry.  A
        # client polling faster than LOCKOUT_SECONDS must not be able to keep
        # an account locked indefinitely.  Each retry is still audited.
        was_locked = float(state.get("locked_until", 0.0)) > now
        if not was_locked:
            state["failures"].append((now, reason_text))
        if not was_locked and len(state["failures"]) >= MAX_FAILURES:
            state["locked_until"] = now + LOCKOUT_SECONDS
            state["lock_reason"] = reason_text
            audit_event(
                "CIRCUIT_BREAKER_OPEN",
                user_id=user_id,
                decision="BLOCK",
                outcome="LOCKED",
                reason=reason_text,
                request_id=request_id,
                metadata={"failure_count": len(state["failures"]), "lockout_seconds": LOCKOUT_SECONDS},
            )
        audit_event(
            "SECURITY_FAILURE",
            user_id=user_id,
            decision="BLOCK",
            outcome="FAILURE",
            reason=reason_text,
            request_id=request_id,
            metadata={"failure_count": len(state["failures"])},
        )
        status = circuit_status(principal)
    # Failed credentials also feed the behaviour engine (credential stuffing).
    record_behavior_signal(user_id, "AUTH_FAILURE", metadata={"reason": reason_text}, request_id=request_id)
    return status


def record_security_success(user_id: Optional[str]) -> None:
    principal = _principal_key(user_id)
    with STATE_LOCK:
        state = SECURITY_STATE[principal]
        _purge_failures(state)
        # A successful authenticated operation clears transient failures but
        # never silently clears an active lockout.
        if float(state.get("locked_until", 0.0)) <= time.monotonic():
            state["failures"].clear()
    # Every successful sensitive operation is also a behaviour signal so that
    # combinations such as "three failed attempts then one success" can be
    # recognised.  The signal itself carries no weight.
    record_behavior_signal(user_id, "SENSITIVE_SUCCESS")


# ---------------------------------------------------------------------------
# Behaviour combination circuit breaker
#
# The failure counter above only reacts to *rejected* credentials.  Real fraud
# often uses valid credentials in a suspicious sequence (transfer then report
# the card lost, raise the limit then drain the account, change the password
# right after a large transfer).  Each sensitive operation therefore emits a
# weighted signal, named combination rules fire on the recent timeline, and a
# score above the threshold opens the same per-principal lockout used by the
# failure breaker (423 + Retry-After) with an auditable reason.
# ---------------------------------------------------------------------------
BEHAVIOR_WINDOW_SECONDS = _env_int("BANK_BEHAVIOR_WINDOW_SECONDS", 600, 60, 3600)
BEHAVIOR_RISK_THRESHOLD = _env_int("BANK_BEHAVIOR_RISK_THRESHOLD", 80, 10, 1000)
BEHAVIOR_LARGE_AMOUNT_CENTS = _env_int("BANK_BEHAVIOR_LARGE_AMOUNT_CENTS", 200_000, 100, 10_000_000_000)
BEHAVIOR_MAX_SIGNALS = _env_int("BANK_BEHAVIOR_MAX_SIGNALS", 200, 20, 5000)

BEHAVIOR_SIGNAL_WEIGHTS: dict[str, int] = {
    "SENSITIVE_SUCCESS": 0,
    # The failure counter above already locks at MAX_FAILURES; this weight only
    # contributes to the "failures then success" combination.
    "AUTH_FAILURE": 15,
    "LARGE_TRANSFER": 30,
    "LARGE_LOCK": 20,
    "WEALTH_PURCHASE": 15,
    "WEALTH_REDEEM": 10,
    "CARD_LIMIT_CHANGE": 15,
    "CARD_STATE_CHANGE": 25,
    "CARD_REPORT_LOST": 30,
    "PASSWORD_CHANGE": 35,
}

BEHAVIOR_RULES: tuple[dict[str, Any], ...] = (
    {
        "rule_id": "LOST_AFTER_LARGE_TRANSFER",
        "description": "大额转账后短时间内挂失卡片（典型盗刷后逃逸特征）",
        "trigger": "CARD_REPORT_LOST",
        "requires": ("LARGE_TRANSFER",),
        "weight": 50,
    },
    {
        "rule_id": "PASSWORD_CHANGE_AFTER_LARGE_TRANSFER",
        "description": "大额转账后短时间内修改密码（账户接管特征）",
        "trigger": "PASSWORD_CHANGE",
        "requires": ("LARGE_TRANSFER",),
        "weight": 50,
    },
    {
        "rule_id": "LARGE_TRANSFER_AFTER_LIMIT_CHANGE",
        "description": "调整卡片额度后立即大额转出",
        "trigger": "LARGE_TRANSFER",
        "requires": ("CARD_LIMIT_CHANGE",),
        "weight": 40,
    },
    {
        "rule_id": "BURST_LARGE_TRANSFERS",
        "description": "窗口内连续多笔大额转账",
        "trigger": "LARGE_TRANSFER",
        "min_count": 3,
        "weight": 40,
    },
    {
        "rule_id": "BURST_CARD_CHANGES",
        "description": "窗口内连续多次卡片安全状态变更",
        "trigger": "CARD_STATE_CHANGE",
        "min_count": 3,
        "weight": 30,
    },
    {
        "rule_id": "FAILURES_THEN_SUCCESS",
        "description": "多次凭证失败后紧接着一次成功操作（撞库/暴力尝试特征）",
        "trigger": "SENSITIVE_SUCCESS",
        "requires_count": {"AUTH_FAILURE": 3},
        "weight": 40,
    },
)

BEHAVIOR_STATE: dict[str, dict[str, Any]] = defaultdict(lambda: {"timeline": deque(maxlen=BEHAVIOR_MAX_SIGNALS), "rule_fires": {}})


def behavior_window_seconds() -> int:
    """Effective signal window (read per call so it stays configurable)."""

    return _env_int("BANK_BEHAVIOR_WINDOW_SECONDS", BEHAVIOR_WINDOW_SECONDS, 60, 3600)


def behavior_risk_threshold() -> int:
    """Effective lockout threshold for the accumulated behaviour score."""

    return _env_int("BANK_BEHAVIOR_RISK_THRESHOLD", BEHAVIOR_RISK_THRESHOLD, 10, 1000)


def behavior_large_amount_cents() -> int:
    """Amount above which a settlement counts as a large-transfer signal."""

    return _env_int("BANK_BEHAVIOR_LARGE_AMOUNT_CENTS", BEHAVIOR_LARGE_AMOUNT_CENTS, 100, 10_000_000_000)


def _purge_behavior(state: dict[str, Any], now: float) -> None:
    window = behavior_window_seconds()
    timeline: deque = state["timeline"]
    while timeline and now - float(timeline[0].get("at", 0.0)) > window:
        timeline.popleft()
    for rule_id, fired_at in list(state["rule_fires"].items()):
        if now - float(fired_at) > window:
            state["rule_fires"].pop(rule_id, None)


def _behavior_score(state: dict[str, Any]) -> int:
    return int(sum(int(entry.get("weight", 0)) for entry in state["timeline"]))


def _evaluate_behavior_rules(state: dict[str, Any], trigger: str, now: float) -> list[str]:
    """Fire named combination rules against the recent signal timeline."""

    fired: list[str] = []
    timeline: deque = state["timeline"]
    signals = [entry for entry in timeline if entry.get("kind") == "SIGNAL"]
    for rule in BEHAVIOR_RULES:
        if rule["trigger"] != trigger or rule["rule_id"] in state["rule_fires"]:
            continue
        if "min_count" in rule and sum(1 for entry in signals if entry.get("name") == trigger) < int(rule["min_count"]):
            continue
        if not all(any(entry.get("name") == required for entry in signals) for required in rule.get("requires", ())):
            continue
        if not all(
            sum(1 for entry in signals if entry.get("name") == name) >= int(minimum)
            for name, minimum in (rule.get("requires_count") or {}).items()
        ):
            continue
        timeline.append({"at": now, "kind": "RULE", "name": rule["rule_id"], "weight": int(rule["weight"]), "metadata": {"description": rule["description"]}})
        state["rule_fires"][rule["rule_id"]] = now
        fired.append(rule["rule_id"])
    return fired


def record_behavior_signal(user_id: Optional[str], signal: str, *, metadata: Optional[Mapping[str, Any]] = None, request_id: Optional[str] = None) -> dict[str, Any]:
    """Record a behaviour signal, evaluate combination rules, maybe lock."""

    principal = _principal_key(user_id)
    now = time.monotonic()
    weight = int(BEHAVIOR_SIGNAL_WEIGHTS.get(signal, 0))
    locked_now = False
    lock_reason: Optional[str] = None
    with STATE_LOCK:
        state = BEHAVIOR_STATE[principal]
        _purge_behavior(state, now)
        # Several layers can report the same rejected request (the endpoint
        # wrapper and the primitive both record a failure).  Deduplicate per
        # request so one attempt never counts as two signals.
        if request_id and any(
            entry.get("kind") == "SIGNAL" and entry.get("name") == signal and entry.get("request_id") == request_id
            for entry in state["timeline"]
        ):
            return {
                "principal": principal,
                "signal": signal,
                "weight": weight,
                "score": _behavior_score(state),
                "threshold": behavior_risk_threshold(),
                "fired_rules": [],
                "locked": float(SECURITY_STATE[principal].get("locked_until", 0.0)) > now,
                "just_locked": False,
                "duplicate": True,
                "lock_reason": None,
            }
        state["timeline"].append({"at": now, "kind": "SIGNAL", "name": signal, "weight": weight, "request_id": redact(request_id), "metadata": redact(dict(metadata or {}))})
        fired = _evaluate_behavior_rules(state, signal, now)
        score = _behavior_score(state)
        security_state = SECURITY_STATE[principal]
        if score >= behavior_risk_threshold() and float(security_state.get("locked_until", 0.0)) <= now:
            security_state["locked_until"] = now + LOCKOUT_SECONDS
            security_state["lock_reason"] = f"BEHAVIOR_RISK:{','.join(fired) or signal}"
            locked_now = True
            lock_reason = str(security_state["lock_reason"])
            audit_event(
                "BEHAVIOR_RISK_LOCK",
                user_id=principal,
                risk_level="RED",
                decision="BLOCK",
                outcome="LOCKED",
                reason=lock_reason,
                request_id=request_id,
                metadata={
                    "score": score,
                    "threshold": behavior_risk_threshold(),
                    "rules": fired,
                    "signals": [entry.get("name") for entry in state["timeline"] if entry.get("kind") == "SIGNAL"],
                    "lockout_seconds": LOCKOUT_SECONDS,
                },
            )
        elif fired:
            audit_event(
                "BEHAVIOR_RULE_FIRED",
                user_id=principal,
                risk_level="RED",
                decision="ALLOW",
                outcome="OBSERVED",
                reason=",".join(fired),
                request_id=request_id,
                metadata={"score": score, "threshold": behavior_risk_threshold(), "signal": signal},
            )
        elif weight >= 20:
            audit_event(
                "BEHAVIOR_SIGNAL",
                user_id=principal,
                risk_level="YELLOW",
                decision="ALLOW",
                outcome="OBSERVED",
                reason=signal,
                request_id=request_id,
                metadata={"signal": signal, "weight": weight, "score": score},
            )
        active_lock = float(security_state.get("locked_until", 0.0)) > now
        return {
            "principal": principal,
            "signal": signal,
            "weight": weight,
            "score": score,
            "threshold": behavior_risk_threshold(),
            "fired_rules": fired,
            "locked": active_lock,
            "just_locked": locked_now,
            "lock_reason": str(security_state.get("lock_reason") or "") if active_lock else None,
        }


def behavior_status(user_id: Optional[str]) -> dict[str, Any]:
    """Explain the behaviour risk score of one principal (for review/UI)."""

    principal = _principal_key(user_id)
    now = time.monotonic()
    with STATE_LOCK:
        state = BEHAVIOR_STATE.get(principal)
        security_state = SECURITY_STATE[principal]
        locked_until = float(security_state.get("locked_until", 0.0))
        locked = locked_until > now
        base = {
            "principal": principal,
            "score": 0,
            "threshold": behavior_risk_threshold(),
            "window_seconds": behavior_window_seconds(),
            "signals": [],
            "fired_rules": [],
            "locked": locked,
            "lock_reason": security_state.get("lock_reason") if locked else None,
            "remaining_lock_seconds": max(0, int(locked_until - now)) if locked else 0,
        }
        if not state:
            return base
        _purge_behavior(state, now)
        base["score"] = _behavior_score(state)
        base["fired_rules"] = sorted(state["rule_fires"])
        base["signals"] = [
            {
                "name": entry.get("name"),
                "kind": entry.get("kind"),
                "weight": entry.get("weight"),
                "age_seconds": round(max(0.0, now - float(entry.get("at", now))), 1),
                **({"metadata": entry.get("metadata")} if entry.get("kind") == "SIGNAL" else {}),
            }
            for entry in state["timeline"]
        ]
        return base


def validate_actor(requested_user: Optional[str], presented_user: Optional[str]) -> str:
    """Bind a request to an authenticated principal when one is presented.

    The local demo keeps backwards compatibility by allowing an omitted
    ``X-User-Id`` header unless BANK_REQUIRE_CLIENT_AUTH=1.  Deployments can
    turn strict mode on without changing endpoint code.
    """

    requested = (requested_user or "").strip()
    presented = (presented_user or "").strip()
    if not requested:
        raise SecurityError("缺少用户身份", 401, "AUTHENTICATION_REQUIRED")
    if presented and not hmac.compare_digest(requested, presented):
        raise SecurityError("请求用户与登录身份不一致", 403, "IDENTITY_MISMATCH")
    if STRICT_CLIENT_AUTH and not presented:
        raise SecurityError("请先完成登录认证", 401, "AUTHENTICATION_REQUIRED")
    ensure_circuit_closed(requested)
    return presented or requested


def issue_auth_token(
    *,
    from_user: str,
    target_id: str,
    target_display: str,
    amount: Any,
    remark: str = "智能转账",
    risk_level: str,
    required_actions: list[str],
    operation: str = "TRANSFER",
    context_hash: Optional[str] = None,
    session_token: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
) -> tuple[str, dict[str, Any]]:
    """Issue a signed transfer authorization token and persist its nonce."""

    # Keep this low-level primitive safe even when called by a future tool or
    # plugin instead of the current FastAPI validators.  Never sign arbitrary
    # control text or an unbounded context digest into an authorization token.
    from_user = sanitize_text(from_user, field="付款账户", max_length=128)
    target_id = sanitize_text(target_id, field="操作目标", max_length=128)
    target_display = sanitize_text(target_display, field="目标名称", max_length=200)
    remark = sanitize_text(remark or "智能转账", field="操作备注", max_length=500)
    operation = sanitize_text(operation or "TRANSFER", field="操作类型", max_length=64).upper()
    risk_level = sanitize_text(risk_level, field="风险级别", max_length=16).upper()
    if risk_level not in {"GREEN", "YELLOW", "RED"}:
        raise SecurityError("风险级别无效", 400, "INVALID_RISK_LEVEL")
    if not isinstance(required_actions, list) or len(required_actions) > 16:
        raise SecurityError("授权动作列表无效", 400, "INVALID_REQUIRED_ACTIONS")
    required_actions = [sanitize_text(action, field="授权动作", max_length=64) for action in required_actions]
    if context_hash is not None and (not isinstance(context_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", context_hash)):
        raise SecurityError("操作上下文摘要无效", 400, "INVALID_CONTEXT_HASH")
    cents = _amount_to_cents(amount)
    now = int(time.time())
    ttl = max(30, min(int(ttl_seconds or TOKEN_TTL_SECONDS), 900))
    jti = secrets.token_urlsafe(18)
    payload = {
        "typ": "transfer_auth",
        "ver": 1,
        "jti": jti,
        "sub": from_user,
        "from_user": from_user,
        "target_id": target_id,
        "target_display": target_display,
        "amount_cents": cents,
        "remark_hash": hashlib.sha256((remark or "").encode("utf-8")).hexdigest(),
        "risk_level": risk_level,
        "required_actions": list(required_actions),
        "operation": operation,
        # Generic operations (card/wealth/subscription/scheduled/AA) bind all
        # result-affecting fields to this digest.  Keeping the digest in the
        # signed payload lets the execution gateway reject a request whose
        # details were changed after approval without exposing those details
        # in the credential itself.
        "context_hash": context_hash,
        # Stable login identity across token renewals (when the caller held a
        # session). Settlement requires a live credential from that login, so
        # renewal preserves approvals while logout invalidates them.
        "session_fp": session_binding(session_token),
        "iat": now,
        "exp": now + ttl,
    }
    token = _signed_payload(payload)
    with STATE_LOCK:
        now_float = time.time()
        expired = [key for key, value in AUTH_TOKENS.items() if float(value.get("expires_at", 0)) <= now_float or value.get("used")]
        for key in expired:
            AUTH_TOKENS.pop(key, None)
        if len(AUTH_TOKENS) >= AUTH_TOKEN_MAX_RECORDS:
            oldest = min(AUTH_TOKENS, key=lambda key: AUTH_TOKENS[key].get("created_at", now))
            AUTH_TOKENS.pop(oldest, None)
        AUTH_TOKENS[token] = {
            "jti": jti,
            "payload": payload,
            "token_hash": credential_fingerprint(token),
            "used": False,
            "confirmed": False,
            "mfa_verified": False,
            "mfa_verified_at": None,
            "created_at": now,
            "expires_at": now + ttl,
            "mfa_attempts": 0,
        }
    return token, payload


def _lookup_auth_locked(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _decode_signed(token, "transfer_auth")
    record = AUTH_TOKENS.get(token)
    if not record:
        raise SecurityError("安全凭证不存在或已失效", 403, "TOKEN_NOT_FOUND")
    if record.get("jti") != payload.get("jti") or record.get("token_hash") != credential_fingerprint(token):
        raise SecurityError("安全凭证状态异常", 403, "TOKEN_INVALID")
    if record.get("used"):
        raise SecurityError("安全凭证已使用，不允许重复记账", 403, "TOKEN_REPLAY")
    if float(record.get("expires_at", 0)) <= time.time():
        raise SecurityError("安全凭证已过期", 403, "TOKEN_EXPIRED")
    return payload, record


def inspect_auth_token(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    with STATE_LOCK:
        payload, record = _lookup_auth_locked(token)
        # Inspection is a read-only helper used by the API boundary.  Returning
        # the live record would let an integration accidentally mutate
        # ``used``/``confirmed`` and bypass one-time authorization on a later
        # call.  Internal mutating paths use ``_lookup_auth_locked`` directly.
        return deepcopy(payload), deepcopy(record)


def confirm_auth_token(token: str, *, user_id: Optional[str] = None, request_id: Optional[str] = None) -> dict[str, Any]:
    """Record an explicit user confirmation for a yellow/red operation."""

    with STATE_LOCK:
        payload, record = _lookup_auth_locked(token)
        if user_id and payload.get("from_user") != user_id:
            raise SecurityError("确认身份与凭证不一致", 403, "IDENTITY_MISMATCH")
        record["confirmed"] = True
        audit_event(
            "USER_CONFIRMATION",
            user_id=payload.get("from_user"),
            risk_level=payload.get("risk_level"),
            decision="ALLOW",
            outcome="CONFIRMED",
            request_id=request_id,
            metadata={"jti": payload.get("jti"), "target_id": payload.get("target_id"), "amount_cents": payload.get("amount_cents")},
        )
        return {"confirmed": True, "expires_at": payload.get("exp"), "risk_level": payload.get("risk_level")}


def cancel_auth_token(
    token: str, *, user_id: str, presented_session: Optional[str] = None,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    """Revoke one pending authorization atomically with all of its factors.

    Cancellation and settlement use the same lock, so a nonce cannot settle
    while it is being cancelled. Retrying a successful cancellation is safe.
    """

    with STATE_LOCK:
        payload = _decode_signed(token, "transfer_auth")
        if not user_id or payload.get("from_user") != user_id:
            raise SecurityError("取消身份与凭证不一致", 403, "IDENTITY_MISMATCH")
        assert_session_still_valid(payload.get("session_fp"), presented_session)
        record = AUTH_TOKENS.get(token)
        if not record:
            raise SecurityError("安全凭证不存在或已失效", 403, "TOKEN_NOT_FOUND")
        if record.get("jti") != payload.get("jti") or record.get("token_hash") != credential_fingerprint(token):
            raise SecurityError("安全凭证状态异常", 403, "TOKEN_INVALID")
        if record.get("cancelled"):
            return {"cancelled": True, "status": "CANCELLED"}
        if record.get("used"):
            raise SecurityError("安全凭证已使用，不能取消已完成的操作", 409, "TOKEN_REPLAY")
        record.update(used=True, cancelled=True, cancelled_at=int(time.time()))
        for proof in MFA_PROOFS.values():
            if proof.get("parent_jti") == payload.get("jti"):
                proof["used"] = True
        for challenge in FACE_CHALLENGES.values():
            if challenge.get("jti") == payload.get("jti"):
                challenge["used"] = True
        audit_event(
            "AUTH_TOKEN_CANCEL", user_id=user_id, risk_level=payload.get("risk_level"),
            decision="BLOCK", outcome="CANCELLED", request_id=request_id,
            metadata={"jti": payload.get("jti"), "operation": payload.get("operation")},
        )
        return {"cancelled": True, "status": "CANCELLED"}


def _issue_mfa_proof(payload: Mapping[str, Any], auth_token: str) -> str:
    now = int(time.time())
    proof_jti = secrets.token_urlsafe(18)
    proof_payload = {
        "typ": "mfa_proof",
        "ver": 1,
        "jti": proof_jti,
        "parent_jti": payload["jti"],
        "sub": payload["from_user"],
        "iat": now,
        "exp": now + MFA_TTL_SECONDS,
    }
    proof = _signed_payload(proof_payload)
    MFA_PROOFS[proof] = {
        "jti": proof_jti,
        "parent_jti": payload["jti"],
        "token_hash": credential_fingerprint(proof),
        "used": False,
        "expires_at": now + MFA_TTL_SECONDS,
    }
    return proof


def _purge_sessions() -> None:
    now = time.time()
    for token, record in list(SESSION_TOKENS.items()):
        if float(record.get("expires_at", 0)) <= now:
            SESSION_TOKENS.pop(token, None)
    if len(SESSION_TOKENS) > SESSION_MAX_RECORDS:
        for token in sorted(SESSION_TOKENS, key=lambda item: SESSION_TOKENS[item].get("created_at", 0))[: len(SESSION_TOKENS) - SESSION_MAX_RECORDS]:
            SESSION_TOKENS.pop(token, None)


def issue_session_token(user_id: str, *, ttl_seconds: Optional[int] = None, request_id: Optional[str] = None) -> dict[str, Any]:
    """Issue a reusable, short-lived session credential for a principal.

    The caller is the authenticated login endpoint. Production deployments
    must replace the demo password with a managed identity provider and keep
    the resulting session scoped to the verified principal.
    """

    return _issue_session_token(
        user_id, session_id=secrets.token_urlsafe(18),
        ttl_seconds=ttl_seconds, request_id=request_id,
    )


def _issue_session_token(
    user_id: str, *, session_id: str,
    ttl_seconds: Optional[int] = None, request_id: Optional[str] = None,
) -> dict[str, Any]:
    """Mint a credential for a new login or an already validated renewal."""

    principal = sanitize_text(user_id, field="用户标识", max_length=128)
    if not principal:
        raise SecurityError("缺少用户标识", 400, "INVALID_SESSION_SUBJECT")
    now = int(time.time())
    ttl = max(60, min(int(ttl_seconds or SESSION_TTL_SECONDS), 86_400))
    payload = {"typ": "session", "ver": 1, "jti": secrets.token_urlsafe(18),
               "sid": session_id, "sub": principal, "iat": now, "exp": now + ttl}
    token = _signed_payload(payload)
    with STATE_LOCK:
        SESSION_TOKENS[token] = {
            "jti": payload["jti"],
            "sid": session_id,
            "sub": principal,
            "token_hash": credential_fingerprint(token),
            "created_at": now,
            "expires_at": now + ttl,
        }
        _purge_sessions()
    audit_event("SESSION_ISSUED", user_id=principal, decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"jti": payload["jti"], "ttl": ttl})
    return {"session_token": token, "expires_at": now + ttl, "user_id": principal, "ttl_seconds": ttl}


def renew_session(token: Optional[str], *, request_id: Optional[str] = None) -> dict[str, Any]:
    """Slide an in-use session's window forward without a fresh login.

    A signed token carries its own ``exp`` and cannot be edited in place, so
    renewal mints a successor for the same login. The presented token is
    deliberately *not* revoked: a lost response must not sign the user out, and
    the old token still dies at its original expiry.  Only a session that is
    valid right now can be extended - an expired one must authenticate again.
    """

    # Validation and issuance share the revocation lock: logout cannot fall
    # between them and leave a newly issued successor alive after sign-out.
    with STATE_LOCK:
        principal = resolve_session(token)
        issued = _issue_session_token(
            principal, session_id=SESSION_TOKENS[token]["sid"], request_id=request_id,
        )
    audit_event("SESSION_RENEWED", user_id=principal, decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"ttl": issued["ttl_seconds"]})
    return issued


def hash_password(password: Any) -> str:
    """Hash a plaintext password with argon2id.

    The plaintext never leaves this function: callers store only the digest, and
    nothing (audit trail included) ever records the value that was typed.
    """

    if not PASSWORDS_AVAILABLE:
        raise SecurityError("服务端未启用口令校验", 503, "PASSWORDS_UNAVAILABLE")
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise SecurityError(f"口令长度至少 {MIN_PASSWORD_LENGTH} 位", 400, "WEAK_PASSWORD")
    if len(password) > 512:
        # argon2 is not a reason to accept an unbounded allocation.
        raise SecurityError("口令过长", 400, "WEAK_PASSWORD")
    return _PASSWORD_HASHER.hash(password)


def verify_password(password: Any, digest: Optional[str]) -> bool:
    """Verify a password against a stored digest, failing closed on any error."""

    if not PASSWORDS_AVAILABLE or not isinstance(digest, str) or not digest:
        return False
    if not isinstance(password, str) or not password:
        return False
    try:
        return bool(_PASSWORD_HASHER.verify(digest, password))
    except Exception:
        # A mismatched password, a malformed digest and a library failure are all
        # "not authenticated"; distinguishing them to the caller would leak
        # whether an account exists and whether its digest is intact.
        return False


def session_binding(session_token: Optional[str]) -> Optional[str]:
    """Bind an authorization to one login, including its renewed credentials.

    Only the login identity's digest enters the approval, never a bearer token.
    Separate logins for the same principal have different identities.
    """

    if not isinstance(session_token, str) or not session_token:
        return None
    with STATE_LOCK:
        principal = resolve_session(session_token)
        return credential_fingerprint(f"session:{principal}:{SESSION_TOKENS[session_token]['sid']}")


def assert_session_still_valid(session_fingerprint: Optional[str], presented_session: Optional[str]) -> None:
    """Refuse to settle an approval whose originating session was revoked.

    Revoking a session (logout, credential rotation) used to leave every
    outstanding authorization nonce fully usable: the nonce is signed
    independently of any session, and the execution endpoint identifies the
    caller from a self-asserted header.  So a user could log out - or change a
    password precisely because a device was stolen - and the in-flight approval
    would still move money.

    A still-live successor may complete an unexpired approval after its original
    credential expires. This extends neither the approval's own lifetime nor an
    expired credential; it preserves the authenticated login across renewal.
    A caller without a session (the demo's header-only mode) has nothing to bind.
    """

    if not session_fingerprint:
        return
    if not isinstance(presented_session, str) or not presented_session:
        raise SecurityError("该授权绑定于一个会话，请携带有效的会话凭证", 401, "SESSION_REQUIRED")
    with STATE_LOCK:
        try:
            presented_binding = session_binding(presented_session)
        except SecurityError as exc:
            code = "SESSION_EXPIRED" if exc.code == "SESSION_EXPIRED" else "SESSION_REVOKED"
            raise SecurityError("授权所属会话已失效，请重新登录后再试", 401, code) from exc
        if presented_binding != session_fingerprint:
            raise SecurityError("授权所属会话已失效，请重新登录后再试", 401, "SESSION_REVOKED")


def revoke_sessions_for(user_id: str, *, reason: str = "credential_rotated", request_id: Optional[str] = None) -> int:
    """Drop every session belonging to one principal.

    Rotating a credential has to invalidate the sessions it was used to obtain;
    otherwise a password change would leave an attacker's existing session fully
    usable, which is the opposite of what the user just asked for.
    """

    principal = sanitize_text(user_id, field="用户标识", max_length=128)
    with STATE_LOCK:
        doomed = [token for token, record in SESSION_TOKENS.items() if record.get("sub") == principal]
        for token in doomed:
            SESSION_TOKENS.pop(token, None)
    if doomed:
        audit_event(
            "SESSION_REVOKED",
            user_id=principal,
            decision="ALLOW",
            outcome="REVOKED",
            reason=reason,
            request_id=request_id,
            metadata={"count": len(doomed)},
        )
    return len(doomed)


def revoke_session(token: Optional[str], *, request_id: Optional[str] = None) -> dict[str, Any]:
    """Revoke one login and all credentials issued by its renewals (logout).

    ``revoke_sessions_for`` answers credential rotation, where every session
    must go. Logging out revokes both current and earlier credentials from the
    same login, while independent logins on other devices remain valid.
    """

    with STATE_LOCK:
        principal = resolve_session(token)
        session_id = SESSION_TOKENS[token]["sid"]
        doomed = [key for key, record in SESSION_TOKENS.items()
                  if record.get("sub") == principal and record.get("sid") == session_id]
        for key in doomed:
            SESSION_TOKENS.pop(key, None)
    if doomed:
        audit_event(
            "SESSION_REVOKED",
            user_id=principal,
            decision="ALLOW",
            outcome="REVOKED",
            reason="logout",
            request_id=request_id,
            metadata={"count": len(doomed)},
        )
    return {"user_id": principal, "revoked": bool(doomed)}


def resolve_session(token: Optional[str]) -> str:
    """Return the principal bound to a session token, or raise ``SecurityError``."""

    if not isinstance(token, str) or not token:
        raise SecurityError("缺少会话凭证", 401, "SESSION_REQUIRED")
    with STATE_LOCK:
        try:
            payload = _decode_signed(token, "session")
        except SecurityError as exc:
            raise SecurityError("会话凭证无效或已过期", 401, "INVALID_SESSION") from exc
        record = SESSION_TOKENS.get(token)
        if (not record or record.get("token_hash") != credential_fingerprint(token)
                or record.get("jti") != payload.get("jti")
                or not record.get("sid") or record["sid"] != payload.get("sid")
                or record.get("sub") != payload.get("sub")):
            raise SecurityError("会话凭证无效", 401, "INVALID_SESSION")
        if float(record.get("expires_at", 0)) <= time.time():
            raise SecurityError("会话已过期，请重新登录", 401, "SESSION_EXPIRED")
    principal = str(payload.get("sub") or "")
    if not principal:
        raise SecurityError("会话凭证无效", 401, "INVALID_SESSION")
    return principal


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
        payload, record = _lookup_auth_locked(auth_token)
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
        payload = device_facing_payload(challenge_id, subject, str(challenge.get("jti") or ""))
        try:
            public_key.verify(signature, payload, _crypto_ec.ECDSA(_crypto_hashes.SHA256()))
        except Exception:
            return None
        device["last_seen_at"] = int(now)
        _persist_devices_locked()
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
        payload, record = _lookup_auth_locked(auth_token)
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
        payload, record = _lookup_auth_locked(auth_token)
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
            raise SecurityError("多因子验证未通过：短信验证码或人脸票据无效", 403, "MFA_FAILED")

        record["mfa_verified"] = True
        record["mfa_verified_at"] = int(time.time())
        # A successful MFA also counts as an explicit confirmation in the
        # high-risk dialog; the user had to submit the verification form.
        record["confirmed"] = True
        if face_challenge is not None:
            face_challenge["used"] = True
        proof = _issue_mfa_proof(payload, auth_token)
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


def _validate_mfa_proof_locked(proof: str, payload: Mapping[str, Any], user_id: Optional[str]) -> dict[str, Any]:
    proof_payload = _decode_signed(proof, "mfa_proof")
    record = MFA_PROOFS.get(proof)
    if not record or record.get("token_hash") != credential_fingerprint(proof):
        raise SecurityError("MFA 凭证不存在或无效", 403, "MFA_PROOF_INVALID")
    if record.get("used"):
        raise SecurityError("MFA 凭证已使用", 403, "MFA_PROOF_REPLAY")
    if record.get("parent_jti") != payload.get("jti") or proof_payload.get("parent_jti") != payload.get("jti"):
        raise SecurityError("MFA 凭证与交易不匹配", 403, "MFA_PROOF_MISMATCH")
    if user_id and proof_payload.get("sub") != user_id:
        raise SecurityError("MFA 凭证与用户不匹配", 403, "IDENTITY_MISMATCH")
    if float(record.get("expires_at", 0)) <= time.time():
        raise SecurityError("MFA 凭证已过期", 403, "MFA_PROOF_EXPIRED")
    return record


@contextmanager
def authorize_execution(
    auth_token: str,
    *,
    from_user: str,
    target_id: str,
    amount: Any,
    remark: str = "智能转账",
    confirmation: bool = False,
    mfa_proof: Optional[str] = None,
    operation: str = "TRANSFER",
    context_hash: Optional[str] = None,
    presented_session: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Atomically validate a transfer token and mark it used on success.

    The caller performs the actual balance mutation inside this context.  If
    it raises, the authorization remains available (unless a proof was
    explicitly consumed by the caller), making the operation retryable while
    retaining the audit trail.
    """

    proof_record: Optional[dict[str, Any]] = None
    with STATE_LOCK:
        payload, record = _lookup_auth_locked(auth_token)
        # An approval minted inside a session dies with that session.
        assert_session_still_valid(payload.get("session_fp"), presented_session)
        if payload.get("operation", "TRANSFER") != operation:
            raise SecurityError("安全凭证与业务操作不匹配", 403, "TOKEN_OPERATION_MISMATCH")
        if payload.get("context_hash") != context_hash:
            raise SecurityError("安全凭证与操作详情不匹配，请重新预检", 400, "TOKEN_CONTEXT_MISMATCH")
        if payload.get("from_user") != from_user:
            raise SecurityError("安全凭证与付款账户不匹配", 403, "TOKEN_BINDING_MISMATCH")
        if payload.get("target_id") != target_id:
            raise SecurityError("安全凭证与收款账户不匹配", 400, "TOKEN_BINDING_MISMATCH")
        cents = _amount_to_cents(amount)
        if int(payload.get("amount_cents", -1)) != cents:
            raise SecurityError("安全凭证与转账金额不匹配", 400, "TOKEN_BINDING_MISMATCH")
        remark_hash = hashlib.sha256((remark or "").encode("utf-8")).hexdigest()
        if payload.get("remark_hash") != remark_hash:
            raise SecurityError("安全凭证与备注不匹配，请重新预检", 400, "TOKEN_BINDING_MISMATCH")
        level = payload.get("risk_level")
        if level in {"YELLOW", "RED"} and not (confirmation or record.get("confirmed")):
            raise SecurityError("请先明确确认该项操作", 428, "USER_CONFIRMATION_REQUIRED")
        if level == "RED":
            if not record.get("mfa_verified"):
                raise SecurityError("该操作需要完成 MFA 强验证", 403, "MFA_REQUIRED")
            if not mfa_proof:
                raise SecurityError("缺少一次性 MFA 凭证", 403, "MFA_PROOF_REQUIRED")
            proof_record = _validate_mfa_proof_locked(mfa_proof, payload, from_user)
        try:
            yield payload, record
        except Exception:
            raise
        else:
            record["used"] = True
            record["used_at"] = int(time.time())
            if proof_record is not None:
                proof_record["used"] = True
                proof_record["used_at"] = int(time.time())
            audit_event(
                "AUTH_TOKEN_CONSUME",
                user_id=from_user,
                risk_level=level,
                decision="ALLOW",
                outcome="CONSUMED",
                request_id=request_id,
                metadata={"jti": payload.get("jti"), "target_id": target_id, "amount_cents": cents},
            )


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


def reset_security_state() -> None:
    """Test helper; never expose this through the public API."""

    global _AUDIT_PREVIOUS_HASH, _AUDIT_CHAIN_ANCHOR, _DEVICE_STORE_BLOCKED
    with STATE_LOCK:
        AUTH_TOKENS.clear()
        MFA_PROOFS.clear()
        FACE_CHALLENGES.clear()
        SESSION_TOKENS.clear()
        BEHAVIOR_STATE.clear()
        SECURITY_STATE.clear()
        DEVICES.clear()
        AUDIT_LOGS.clear()
        _AUDIT_PREVIOUS_HASH = "0" * 64
        _AUDIT_CHAIN_ANCHOR = "0" * 64
    # A rejected file freezes writes for the life of the process, so a test that
    # deliberately feeds a forged store must not leave every later test frozen.
    _DEVICE_STORE_BLOCKED = False


# The persisted chain is replayed at import time so verification and traces
# survive a process restart. Without a service or explicit path (tests, CLI
# helpers) the in-memory log stays side-effect free.
_AUDIT_LOAD_SUMMARY: dict[str, Any] = load_audit_file()
if _AUDIT_LOAD_SUMMARY.get("tampered"):
    logger.error("审计文件哈希链校验失败，已保留已校验前缀: %s", _AUDIT_LOAD_SUMMARY.get("error"))

# Same opt-in shape for the device registry: without a path the demo keeps
# devices in memory only, and without persistence a restart silently drops every
# enrollment and delivery token.
load_device_store()
if _DEVICE_STORE_LOAD_SUMMARY.get("rejected"):
    logger.error(
        "设备库未通过校验，已拒绝加载并冻结写入（设备需重新登录注册）: %s",
        _DEVICE_STORE_LOAD_SUMMARY.get("error"),
    )
