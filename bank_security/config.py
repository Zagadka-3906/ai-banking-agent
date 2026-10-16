"""Process configuration shared by every security module.

Loads ``.env`` before anything reads the environment, then fixes the
secret, TTLs, limits and the single lock that guards all security state.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading


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


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
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


# A random process key is safer than a hard-coded development key.  Operators
# should set BANK_AUTH_SECRET in deployments; a restart invalidates outstanding
# demo tokens when no persistent secret is configured.
_configured_secret = os.getenv("BANK_AUTH_SECRET") or os.getenv("BANK_TOKEN_SECRET")
AUTH_SECRET_CONFIGURED = bool(_configured_secret)
AUTH_SECRET = (_configured_secret or secrets.token_urlsafe(48)).encode("utf-8")

TOKEN_TTL_SECONDS = env_int("BANK_TOKEN_TTL_SECONDS", 120, 30, 900)
MFA_TTL_SECONDS = env_int("BANK_MFA_TTL_SECONDS", 120, 30, 900)
MAX_FAILURES = env_int("BANK_CIRCUIT_FAILURE_THRESHOLD", 5, 2, 20)
FAILURE_WINDOW_SECONDS = env_int("BANK_CIRCUIT_WINDOW_SECONDS", 300, 30, 3600)
LOCKOUT_SECONDS = env_int("BANK_CIRCUIT_LOCKOUT_SECONDS", 300, 30, 86400)
MAX_TEXT_LENGTH = env_int("BANK_MAX_INPUT_LENGTH", 2000, 128, 10000)
MAX_AMOUNT_CENTS = env_int("BANK_MAX_TRANSFER_CENTS", 100_000_000, 100, 10_000_000_000)
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
FACE_CHALLENGE_TTL_SECONDS = env_int("BANK_FACE_CHALLENGE_TTL_SECONDS", 60, 10, 300)


STATE_LOCK = threading.RLock()
