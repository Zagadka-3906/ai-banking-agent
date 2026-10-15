"""HTTP edge controls: session gate, per-principal rate limit, body limit and security headers."""

from __future__ import annotations

import os
import time
from typing import Any, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from http_controls import cache_bounded_body
from security import (
    SecurityError,
    audit_event,
    resolve_session,
)

API_VERSION = "2.0.0"

# Keep the HTTP edge defensive even when the mock is run directly under
# Uvicorn (without a reverse proxy/WAF).  The body limit is deliberately
# generous for normal JSON requests but prevents an untrusted channel from
# turning prompt/metadata fields into an unbounded memory allocation.
try:
    _MAX_REQUEST_BYTES = max(16_384, min(int(os.getenv("BANK_MAX_REQUEST_BYTES", "1048576")), 10_000_000))
except (TypeError, ValueError):
    _MAX_REQUEST_BYTES = 1_048_576

# Paths that must stay reachable without a session and without rate limiting
# (container probes and the interactive API docs).
_HEALTH_PATHS = {"/healthz", "/api/v1/security/health"}
_DOCS_PREFIXES = ("/docs", "/redoc", "/openapi.json")
# Endpoints that must stay reachable *before* a session exists.  Without this,
# strict session mode would refuse the login request itself and no client could
# ever obtain the credential it is being asked for.
_SESSION_BOOTSTRAP_PATHS = {"/api/v1/auth/login"}
RATE_LIMIT_WINDOW_SECONDS = 60
# Per-principal request windows.  The demo has no reverse proxy, so a simple
# in-process fixed window is the cheapest way to blunt accidental or hostile
# floods; production should also apply an edge/WAF limit.
RATE_LIMIT_STATE: dict[str, dict[str, Any]] = {}


def _rate_limit_exempt(path: str) -> bool:
    return path in _HEALTH_PATHS or path.startswith(_DOCS_PREFIXES)


def _rate_limit_key(request: "Request") -> str:
    """Pick a throttling bucket the caller cannot trivially re-key.

    ``X-User-Id`` is a *object selector*, not a credential: outside strict
    session mode anyone can send any value, so using it as the rate-limit key
    meant a caller could rotate the header and reset the window at will.  The
    verified session principal wins; otherwise the peer address; the header is
    only the fallback for callers with no address at all (the in-process test
    client), where it still keeps one principal's traffic from throttling
    another's.
    """

    session_token = request.headers.get("X-Session-Token")
    if session_token:
        try:
            return f"session:{resolve_session(session_token)}"
        except SecurityError:
            # An invalid session is answered by the middleware above; bucket it
            # by peer so a bad token cannot be used to re-key either.
            pass
    if request.client and request.client.host:
        return f"peer:{request.client.host}"
    return f"user:{request.headers.get('X-User-Id') or 'anonymous'}"


def _strict_session_required() -> bool:
    return os.getenv("BANK_REQUIRE_SESSION", "1").lower() in {"1", "true", "yes", "on"}


def _rate_limit_check(key: str) -> Optional[int]:
    """Return ``Retry-After`` seconds when over the per-minute limit, else None."""

    try:
        limit = int(os.getenv("BANK_RATE_LIMIT_PER_MINUTE", "240"))
    except (TypeError, ValueError):
        limit = 240
    if limit <= 0:
        return None
    now = time.monotonic()
    record = RATE_LIMIT_STATE.get(key)
    if not record or now - float(record.get("start", 0.0)) >= RATE_LIMIT_WINDOW_SECONDS:
        RATE_LIMIT_STATE[key] = {"start": now, "count": 1, "notified": False}
        return None
    record["count"] = int(record.get("count", 0)) + 1
    if record["count"] > limit:
        if not record.get("notified"):
            record["notified"] = True
            audit_event("RATE_LIMITED", user_id=key, decision="BLOCK", outcome="RATE_LIMITED", reason="rate limit exceeded", metadata={"limit": limit, "window_seconds": RATE_LIMIT_WINDOW_SECONDS})
        return max(1, int(RATE_LIMIT_WINDOW_SECONDS - (now - float(record.get("start", now)))))
    return None


def _apply_security_headers(response: JSONResponse, path: str) -> JSONResponse:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


async def request_safety_middleware(request: Request, call_next):
    path = request.url.path
    # 1) A session token is authoritative over a self-asserted X-User-Id: it is
    #    verified first and its subject replaces any client-supplied header, so
    #    a caller can never claim another principal alongside a valid session.
    session_token = request.headers.get("X-Session-Token")
    if session_token:
        try:
            principal = resolve_session(session_token)
        except SecurityError as exc:
            return _apply_security_headers(
                JSONResponse(status_code=exc.status_code, content={"detail": {"code": exc.code, "message": exc.detail}}),
                path,
            )
        forwarded = [(name, value) for name, value in request.scope["headers"] if name.lower() != b"x-user-id"]
        forwarded.append((b"x-user-id", principal.encode("utf-8")))
        request.scope["headers"] = forwarded
        # Downstream handlers read this to bind approvals to the session that
        # requested them, so revoking the session invalidates them.
        request.state.session_token = session_token
    elif _strict_session_required() and path.startswith("/api/v1/") and not _rate_limit_exempt(path) and path not in _SESSION_BOOTSTRAP_PATHS:
        return _apply_security_headers(
            JSONResponse(status_code=401, content={"detail": {"code": "SESSION_REQUIRED", "message": "银行数据访问需要会话凭证"}}),
            path,
        )
    # 2) Per-principal request window (probes and docs are exempt).
    if not _rate_limit_exempt(path):
        # The bucket key must not be something the caller can pick freely: an
        # ``X-User-Id`` header is self-asserted outside strict session mode, so
        # keying on it let anyone rotate the header and get a fresh window per
        # request.  Prefer the verified session principal, then the peer
        # address, and keep the header only as a last resort (an in-process
        # test client has no peer address).
        rate_key = _rate_limit_key(request)
        retry_after = _rate_limit_check(rate_key)
        if retry_after is not None:
            response = _apply_security_headers(
                JSONResponse(status_code=429, content={"detail": {"code": "RATE_LIMITED", "message": "请求过于频繁，请稍后重试"}}),
                path,
            )
            response.headers["Retry-After"] = str(retry_after)
            return response
    try:
        await cache_bounded_body(request, _MAX_REQUEST_BYTES)
    except SecurityError as exc:
        return _apply_security_headers(
            JSONResponse(status_code=exc.status_code, content={"detail": {"code": exc.code, "message": exc.detail}}),
            path,
        )
    response = await call_next(request)
    return _apply_security_headers(response, request.url.path)
