"""Small post-deploy smoke check.

Usage: python scripts/smoke_test.py [http://127.0.0.1:8000]

Authenticates with BANK_DEMO_USER_ID / BANK_DEMO_PASSWORD (demo defaults),
optionally BANK_LOGIN_OTP, and revokes its temporary session after the probe.
"""

from __future__ import annotations

import os
import sys
import time
from urllib.parse import quote

import requests


def _wait_for_health(origin: str, *, attempts: int = 30) -> requests.Response:
    """Wait briefly for a freshly started Core container to become ready."""

    last_error: requests.RequestException | None = None
    for _ in range(max(1, attempts)):
        try:
            response = requests.get(f"{origin}/healthz", timeout=3)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(1)
    if last_error is not None:
        raise last_error
    raise RuntimeError("health check did not run")


def main() -> int:
    origin = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
    principal = os.getenv("BANK_DEMO_USER_ID", "user_zhang")
    session_token = None
    failed = False
    try:
        _wait_for_health(origin)
        credentials = {"user_id": principal, "password": os.getenv("BANK_DEMO_PASSWORD", "Bank@2026")}
        if os.getenv("BANK_LOGIN_OTP"):
            credentials["otp"] = os.environ["BANK_LOGIN_OTP"]
        login = requests.post(f"{origin}/api/v1/auth/login", json=credentials, timeout=5)
        login.raise_for_status()
        token = login.json()["session_token"]
        if not isinstance(token, str) or not token:
            raise ValueError("login did not return a session credential")
        session_token = token
        balance = requests.get(
            f"{origin}/api/v1/accounts/{quote(principal, safe='')}/balance",
            headers={"X-Session-Token": session_token},
            timeout=5,
        )
        balance.raise_for_status()
        body = balance.json()
        assert float(body["available_balance"]) >= 0
    except (requests.RequestException, RuntimeError, ValueError, TypeError, KeyError, AssertionError) as exc:
        print(f"smoke test failed: {exc}")
        failed = True
    finally:
        if session_token:
            try:
                logout = requests.post(
                    f"{origin}/api/v1/auth/logout",
                    headers={"X-Session-Token": session_token},
                    timeout=5,
                )
                logout.raise_for_status()
            except requests.RequestException as exc:
                print(f"smoke test cleanup failed: {exc}")
                failed = True
    if failed:
        return 1
    print("smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
