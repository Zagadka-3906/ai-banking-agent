"""Defensive HTTP client for the mock banking core.

The client contains transport concerns only: timeouts, correlation IDs and
safe error normalization.  It deliberately never retries money-moving
requests, because a timeout must not result in a duplicate financial action.
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import quote, urlencode
from typing import Any, Mapping, Optional

import requests


BASE_URL = os.getenv("BANK_API_BASE", "http://127.0.0.1:8000/api/v1").rstrip("/")
try:
    DEFAULT_TIMEOUT = float(os.getenv("BANK_HTTP_TIMEOUT_SECONDS", "8"))
except (TypeError, ValueError):
    DEFAULT_TIMEOUT = 8.0


class BankAPIError(RuntimeError):
    """Normalized error returned by the core banking service."""

    def __init__(self, message: str, *, status_code: Optional[int] = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def _json_or_text(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


class BankClient:
    """HTTP adapter used by the gateway and the conversation service."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        *,
        user_id: Optional[str] = None,
        session_token: Optional[str] = None,
        session: Optional[requests.Session] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id
        # A channel acting for a signed-in user presents that user's session on
        # every call; once the core requires sessions for writes, a client
        # without one is simply refused.
        self.session_token = session_token
        self.session = session or requests.Session()
        self.timeout = max(0.5, min(float(timeout), 60.0))

    def _headers(
        self,
        *,
        principal: Optional[str] = None,
        auth_token: Optional[str] = None,
        mfa_proof: Optional[str] = None,
        confirmation: Optional[bool] = None,
        request_id: Optional[str] = None,
        session_token: Optional[str] = None,
    ) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "X-Request-Id": request_id or f"req_{uuid.uuid4().hex}",
        }
        effective_principal = principal or self.user_id
        if effective_principal:
            headers["X-User-Id"] = effective_principal
        effective_session = session_token or self.session_token
        if effective_session:
            headers["X-Session-Token"] = effective_session
        if auth_token:
            headers["X-Auth-Token"] = auth_token
        if mfa_proof:
            headers["X-MFA-Proof"] = mfa_proof
        if confirmation is not None:
            headers["X-User-Confirmation"] = "true" if confirmation else "false"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        read_only: bool = False,
        raise_for_status: bool = True,
    ) -> requests.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        merged = self._headers()
        merged.update(dict(headers or {}))
        # Read calls may be retried once.  Mutating calls are intentionally
        # single-shot; callers should use an idempotency key at the core.
        attempts = 2 if read_only else 1
        last_error: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                response = self.session.request(
                    method,
                    url,
                    json=dict(json) if json is not None else None,
                    headers=merged,
                    timeout=self.timeout,
                )
                if raise_for_status and response.status_code >= 400:
                    payload = _json_or_text(response)
                    detail = payload.get("detail") if isinstance(payload, dict) else payload
                    raise BankAPIError(
                        str(detail or f"core API returned HTTP {response.status_code}"),
                        status_code=response.status_code,
                        payload=payload,
                    )
                return response
            except BankAPIError:
                raise
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    break
            except requests.RequestException as exc:
                last_error = exc
                break
        raise BankAPIError(f"无法连接核心银行服务: {last_error}") from last_error

    # Session / authorization --------------------------------------------
    def operation_status(self, operation_id: str, endpoint: str, *, request_id: Optional[str] = None) -> dict[str, Any]:
        response = self._request("GET", f"operations/{quote(operation_id, safe='')}?{urlencode({'endpoint': endpoint})}",
                                 headers=self._headers(request_id=request_id), read_only=True)
        return response.json()

    def session_subject(self, session_token: str, *, request_id: Optional[str] = None) -> dict[str, Any]:
        """Resolve a session credential to its subject, asking the core.

        The core is the only authority that can answer this: it holds the
        session registry, and a channel process that kept its own copy would
        disagree with the core the moment a session expired or was dropped.
        """

        return self._request(
            "GET",
            "auth/session",
            headers=self._headers(request_id=request_id, session_token=session_token),
            read_only=True,
        ).json()

    def renew_session(self, session_token: str, *, request_id: Optional[str] = None) -> dict[str, Any]:
        """Obtain a successor credential for a still-live login (same login id).

        Logging out of that login revokes the successor too, so a channel that
        keeps renewing never outlives the user's own sign-out.
        """

        return self._request(
            "POST",
            "auth/session/renew",
            headers=self._headers(request_id=request_id, session_token=session_token),
        ).json()

    def confirm(self, auth_token: str, *, request_id: Optional[str] = None) -> dict[str, Any]:
        """Record the user's explicit confirmation for a yellow/red operation.

        ``request_id`` lets a channel reuse the correlation id of the turn that
        produced the nonce, so the core records ``USER_CONFIRMATION`` on the
        same audit chain as the precheck that preceded it instead of starting a
        second, unrelated chain.
        """

        if not self.user_id:
            raise ValueError("user_id is required for confirmation")
        return self._request(
            "POST",
            "risk/confirm",
            json={},
            headers=self._headers(principal=self.user_id, auth_token=auth_token, confirmation=True, request_id=request_id),
        ).json()

    def cancel(self, auth_token: str, *, request_id: Optional[str] = None) -> dict[str, Any]:
        """Revoke a rejected or superseded approval at its issuing core."""

        if not self.user_id:
            raise ValueError("user_id is required for cancellation")
        return self._request(
            "POST", "risk/cancel", json={},
            headers=self._headers(principal=self.user_id, auth_token=auth_token, request_id=request_id),
        ).json()

    def request_face_challenge(self, auth_token: str, *, request_id: Optional[str] = None) -> dict[str, Any]:
        """Ask the core for a liveness challenge the device SDK must sign."""

        if not self.user_id:
            raise ValueError("user_id is required for MFA verification")
        return self._request(
            "POST",
            "risk/mfa/face/challenge",
            json={"auth_token": auth_token},
            headers=self._headers(principal=self.user_id, auth_token=auth_token, request_id=request_id),
        ).json()

    def request_demo_otp(self, auth_token: str, *, request_id: Optional[str] = None) -> dict[str, Any]:
        return self._request("POST", "auth/mfa/otp", json={"auth_token": auth_token},
                             headers=self._headers(principal=self.user_id, auth_token=auth_token, request_id=request_id)).json()

    def verify_mfa(
        self,
        auth_token: str,
        *,
        otp: str,
        face_attestation: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Submit the red-zone factors.

        The biometric factor requires a signed, single-use ``face_attestation``
        produced by an enrolled device from a server-issued challenge.
        """

        if not self.user_id:
            raise ValueError("user_id is required for MFA verification")
        body: dict[str, Any] = {"otp": otp}
        if face_attestation:
            body["face_attestation"] = face_attestation
        return self._request(
            "POST",
            "risk/mfa/verify",
            json=body,
            headers=self._headers(principal=self.user_id, auth_token=auth_token, request_id=request_id),
        ).json()
