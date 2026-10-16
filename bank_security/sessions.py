"""Reusable short-lived session credentials: issue, renew, bind and revoke."""

from __future__ import annotations

import secrets
import time
from typing import Any, Optional

from bank_security.audit import audit_event
from bank_security.config import STATE_LOCK, env_int
from bank_security.errors import SecurityError
from bank_security.sanitize import credential_fingerprint, sanitize_text
from bank_security.tokens import decode_signed, signed_payload


# Reusable, short-lived session credentials.  Unlike the single-use nonce they
# are not consumed by a settlement, but they expire quickly and are bound to a
# principal, so a channel no longer has to self-assert X-User-Id.
SESSION_TOKENS: dict[str, dict[str, Any]] = {}
SESSION_TTL_SECONDS = env_int("BANK_SESSION_TTL_SECONDS", 900, 60, 86_400)
SESSION_MAX_RECORDS = env_int("BANK_SESSION_MAX_RECORDS", 5000, 100, 200_000)


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
    token = signed_payload(payload)
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
            payload = decode_signed(token, "session")
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
