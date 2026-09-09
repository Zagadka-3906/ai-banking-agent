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
# is commonly started directly with ``python -m uvicorn`` (outside Compose),
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


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


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


STATE_LOCK = threading.RLock()

# Public names are kept stable for tests and for the original main.py API.
AUTH_TOKENS: dict[str, dict[str, Any]] = {}
MFA_PROOFS: dict[str, dict[str, Any]] = {}

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

        # File logging is opt-in.  In-memory events keep the mock deterministic
        # and avoid writing secrets to a working tree by default.
        audit_path = os.getenv("BANK_AUDIT_LOG_PATH")
        if audit_path:
            try:
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


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError("invalid base64 value")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _signed_payload(payload: Mapping[str, Any]) -> str:
    body = _b64encode(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = _b64encode(hmac.new(AUTH_SECRET, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def _decode_signed(token: str, expected_type: str) -> dict[str, Any]:
    if not isinstance(token, str) or len(token) > 8192:
        raise SecurityError("安全凭证格式无效", 403, "INVALID_TOKEN")
    pieces = token.split(".")
    if len(pieces) != 2:
        raise SecurityError("安全凭证格式无效", 403, "INVALID_TOKEN")
    body, supplied_sig = pieces
    try:
        expected_sig = _b64encode(hmac.new(AUTH_SECRET, body.encode("ascii"), hashlib.sha256).digest())
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
        raise SecurityError(
            f"安全熔断已开启，请在约 {retry} 秒后重试",
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
        return circuit_status(principal)


def record_security_success(user_id: Optional[str]) -> None:
    principal = _principal_key(user_id)
    with STATE_LOCK:
        state = SECURITY_STATE[principal]
        _purge_failures(state)
        # A successful authenticated operation clears transient failures but
        # never silently clears an active lockout.
        if float(state.get("locked_until", 0.0)) <= time.monotonic():
            state["failures"].clear()


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


def verify_mfa(
    auth_token: str,
    *,
    otp: Optional[str],
    face_verified: bool,
    user_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    """Validate demo MFA factors on the server and issue a bound proof."""

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
        expected = MFA_TEST_OTP if DEMO_MODE else os.getenv("BANK_MFA_OTP", "")
        otp_ok = isinstance(otp, str) and bool(expected) and hmac.compare_digest(otp.strip(), expected)
        # Keep the primitive strict as well as the HTTP schema: callers of the
        # framework-independent security module must provide an actual boolean,
        # not a truthy string/object that could bypass the second factor.
        face_ok = isinstance(face_verified, bool) and face_verified is True
        if not (otp_ok and face_ok):
            record_security_failure(principal, "mfa_factor_failed", request_id=request_id)
            audit_event(
                "MFA_VERIFY",
                user_id=principal,
                risk_level="RED",
                decision="BLOCK",
                outcome="FAILURE",
                reason="MFA factor failed",
                request_id=request_id,
                metadata={"jti": payload.get("jti"), "otp_present": bool(otp), "face_present": face_ok, "attempt": record["mfa_attempts"]},
            )
            raise SecurityError("人脸或短信验证码校验失败", 403, "MFA_FAILED")

        record["mfa_verified"] = True
        record["mfa_verified_at"] = int(time.time())
        # A successful MFA also counts as an explicit confirmation in the
        # high-risk dialog; the user had to submit the verification form.
        record["confirmed"] = True
        proof = _issue_mfa_proof(payload, auth_token)
        audit_event(
            "MFA_VERIFY",
            user_id=principal,
            risk_level="RED",
            decision="ALLOW",
            outcome="SUCCESS",
            request_id=request_id,
            metadata={"jti": payload.get("jti"), "proof_hash": credential_fingerprint(proof)},
        )
        return {"verified": True, "mfa_proof": proof, "expires_at": min(payload["exp"], int(time.time()) + MFA_TTL_SECONDS)}


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
            "demo_mode": DEMO_MODE,
            "active_auth_tokens": sum(1 for rec in AUTH_TOKENS.values() if not rec.get("used") and float(rec.get("expires_at", 0)) > time.time()),
            "audit_events": len(AUDIT_LOGS),
            "open_circuits": locked,
        }


def reset_security_state() -> None:
    """Test helper; never expose this through the public API."""

    global _AUDIT_PREVIOUS_HASH, _AUDIT_CHAIN_ANCHOR
    with STATE_LOCK:
        AUTH_TOKENS.clear()
        MFA_PROOFS.clear()
        SECURITY_STATE.clear()
        AUDIT_LOGS.clear()
        _AUDIT_PREVIOUS_HASH = "0" * 64
        _AUDIT_CHAIN_ANCHOR = "0" * 64
