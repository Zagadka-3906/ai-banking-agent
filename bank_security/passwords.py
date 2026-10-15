"""argon2id password hashing for the login endpoint (fails closed)."""

from __future__ import annotations

from typing import Any, Optional

from bank_security.config import _env_int
from bank_security.errors import SecurityError


# Password hashing for the login endpoint.  Also optional at import time, and
# also fails closed: without the library nobody can log in, rather than anybody.
try:
    from argon2 import PasswordHasher as _PasswordHasher
except ImportError:  # pragma: no cover - dependency guidance path
    _PasswordHasher = None  # type: ignore[assignment]

PASSWORDS_AVAILABLE = _PasswordHasher is not None
_PASSWORD_HASHER = _PasswordHasher() if _PasswordHasher is not None else None


# Must come after ``_env_int`` is defined - it validates through it.
MIN_PASSWORD_LENGTH = _env_int("BANK_MIN_PASSWORD_LENGTH", 8, 6, 128)


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
