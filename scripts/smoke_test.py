"""Small post-deploy smoke check.

Usage: python scripts/smoke_test.py [http://127.0.0.1:8000]
"""

from __future__ import annotations

import sys
import time

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
    try:
        _wait_for_health(origin)
        balance = requests.get(
            f"{origin}/api/v1/accounts/user_zhang/balance",
            headers={"X-User-Id": "user_zhang"},
            timeout=5,
        )
        balance.raise_for_status()
        body = balance.json()
        assert float(body["available_balance"]) >= 0
    except (requests.RequestException, RuntimeError, ValueError, KeyError, AssertionError) as exc:
        print(f"smoke test failed: {exc}")
        return 1
    print("smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
