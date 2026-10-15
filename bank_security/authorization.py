"""Single-use operation approvals: precheck tokens, confirmation,
cancellation, MFA proofs and the atomic execution gate.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import hmac
import re
import secrets
import time
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Optional

from bank_security.audit import audit_event
from bank_security.circuit import ensure_circuit_closed
from bank_security.config import (
    MAX_AMOUNT_CENTS,
    MFA_TTL_SECONDS,
    STATE_LOCK,
    STRICT_CLIENT_AUTH,
    TOKEN_TTL_SECONDS,
    _env_int,
)
from bank_security.errors import SecurityError
from bank_security.sanitize import credential_fingerprint, sanitize_text
from bank_security.sessions import assert_session_still_valid, session_binding
from bank_security.tokens import _decode_signed, _signed_payload


# Public names are kept stable for tests and for the original main.py API.
AUTH_TOKENS: dict[str, dict[str, Any]] = {}
MFA_PROOFS: dict[str, dict[str, Any]] = {}

# Server-issued biometric challenges.  Each challenge is bound to a single
# auth-token jti and user, expires quickly, and can be satisfied at most once;
# re-issuing a challenge for the same token revokes the previous one.
FACE_CHALLENGES: dict[str, dict[str, Any]] = {}
FACE_CHALLENGE_MAX_RECORDS = _env_int("BANK_FACE_CHALLENGE_MAX_RECORDS", 5000, 100, 200_000)


AUTH_TOKEN_MAX_RECORDS = _env_int("BANK_AUTH_TOKEN_MAX_RECORDS", 20_000, 100, 200_000)


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
