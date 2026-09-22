"""Defensive HTTP client for the mock banking core.

The client contains transport concerns only: timeouts, correlation IDs and
safe error normalization.  It deliberately never retries money-moving
requests, because a timeout must not result in a duplicate financial action.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Mapping, Optional
from urllib.parse import urlencode

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


class _dualmethod:
    """Descriptor supporting both the new instance API and legacy class calls.

    The original demo exposed ``BankClient.precheck_risk(...)``,
    ``execute_transfer(...)`` and ``lock_fund(...)`` as static helpers.  The
    hardened client needs per-instance sessions and identity headers, so these
    methods are implemented as normal instance methods but retain the old
    calling form by creating a default client when accessed on the class.
    """

    def __init__(self, function):
        self.function = function
        self.__name__ = getattr(function, "__name__", "dualmethod")
        self.__doc__ = getattr(function, "__doc__", None)

    def __get__(self, instance, owner):
        client = instance if instance is not None else owner()

        def bound(*args, **kwargs):
            return self.function(client, *args, **kwargs)

        return bound


class BankClient:
    """HTTP adapter with instance and backwards-compatible static methods."""

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

    # Read operations -----------------------------------------------------
    def balance(self, user_id: Optional[str] = None) -> dict[str, Any]:
        uid = user_id or self.user_id
        if not uid:
            raise ValueError("user_id is required")
        return self._request("GET", f"accounts/{uid}/balance", headers=self._headers(principal=uid), read_only=True).json()

    def transactions(self, user_id: Optional[str] = None, *, scope: Optional[str] = None) -> list[dict[str, Any]]:
        uid = user_id or self.user_id
        if not uid:
            raise ValueError("user_id is required")
        path = f"accounts/{uid}/transactions"
        if scope:
            path += "?" + urlencode({"date_scope": scope})
        payload = self._request("GET", path, headers=self._headers(principal=uid), read_only=True).json()
        return list(payload.get("data", payload.get("records", [])))

    def audit_logs(self, *, user_id: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        query_params: dict[str, Any] = {"limit": max(1, min(int(limit), 1000))}
        if user_id:
            query_params["user_id"] = user_id
        query = "?" + urlencode(query_params)
        payload = self._request("GET", f"audit/logs{query}", read_only=True).json()
        return list(payload.get("data", payload.get("events", [])))

    def wealth_risk_assessment(
        self,
        *,
        horizon_years: int = 1,
        loss_tolerance: str = "LOW",
        experience: str = "NONE",
    ) -> dict[str, Any]:
        if not self.user_id:
            raise ValueError("user_id is required")
        return self._request(
            "POST",
            "wealth/risk-assessment",
            json={"user_id": self.user_id, "horizon_years": horizon_years, "loss_tolerance": loss_tolerance, "experience": experience},
            headers=self._headers(principal=self.user_id),
        ).json()

    def wealth_compare(self, product_ids: list[str]) -> dict[str, Any]:
        if not self.user_id:
            raise ValueError("user_id is required")
        if not isinstance(product_ids, list) or len(product_ids) < 2:
            raise ValueError("at least two product_ids are required")
        query = urlencode({"user_id": self.user_id, "product_ids": ",".join(product_ids)})
        return self._request(
            "GET",
            f"wealth/compare?{query}",
            headers=self._headers(principal=self.user_id),
            read_only=True,
        ).json()

    # Authorization and mutations ----------------------------------------
    @_dualmethod
    def precheck_risk(
        self,
        from_user: str,
        target: str,
        amount: float,
        *,
        remark: str = "智能转账",
        idempotency_key: Optional[str] = None,
    ) -> requests.Response:
        payload = {"from_user": from_user, "target_name_or_id": target, "amount": amount, "remark": remark}
        headers = self._headers(principal=from_user)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return self._request("POST", "risk/precheck", json=payload, headers=headers, raise_for_status=False)

    # Session / authorization --------------------------------------------
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

    def verify_mfa(
        self,
        auth_token: str,
        *,
        otp: str,
        face_attestation: Optional[str] = None,
        face_verified: Optional[bool] = None,
        request_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Submit the red-zone factors.

        The biometric factor requires a signed, single-use ``face_attestation``
        ticket produced from a server-issued challenge; a bare boolean no
        longer satisfies it.  ``face_verified`` is still accepted for schema
        parity and must remain a real JSON boolean when supplied.
        """

        if not self.user_id:
            raise ValueError("user_id is required for MFA verification")
        # Keep the client-side contract as strict as the Core API's
        # ``StrictBool`` field.  ``bool("false")`` is ``True`` in Python and
        # would make a malformed integration request look like a successful
        # biometric assertion before it even reaches the server.
        if face_verified is not None and not isinstance(face_verified, bool):
            raise TypeError("face_verified must be a JSON boolean")
        body: dict[str, Any] = {"otp": otp}
        if face_verified is not None:
            body["face_verified"] = face_verified
        if face_attestation:
            body["face_attestation"] = face_attestation
        return self._request(
            "POST",
            "risk/mfa/verify",
            json=body,
            headers=self._headers(principal=self.user_id, auth_token=auth_token, request_id=request_id),
        ).json()

    def operation_precheck(
        self,
        user_id: str,
        operation: str,
        *,
        amount: float = 0.0,
        target: Optional[str] = None,
        product_id: Optional[str] = None,
        card_id: Optional[str] = None,
        new_limit: Optional[float] = None,
        merchant: Optional[str] = None,
        participants: Optional[list[str]] = None,
        product_ids: Optional[list[str]] = None,
        scheduled_at: Optional[str] = None,
        reason: str = "智能银行操作",
        idempotency_key: Optional[str] = None,
    ) -> requests.Response:
        headers = self._headers(principal=user_id)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return self._request(
            "POST",
            "risk/precheck/operation",
            json={"user_id": user_id, "operation": operation, "amount": amount, "target": target, "product_id": product_id, "card_id": card_id, "new_limit": new_limit, "merchant": merchant, "participants": participants or [], "product_ids": product_ids or [], "scheduled_at": scheduled_at, "reason": reason},
            headers=headers,
            raise_for_status=False,
        )

    def execute_operation(
        self,
        user_id: str,
        operation: str,
        *,
        amount: float = 0.0,
        target: Optional[str] = None,
        product_id: Optional[str] = None,
        card_id: Optional[str] = None,
        new_limit: Optional[float] = None,
        merchant: Optional[str] = None,
        participants: Optional[list[str]] = None,
        product_ids: Optional[list[str]] = None,
        scheduled_at: Optional[str] = None,
        reason: str = "智能银行操作",
        auth_token: Optional[str] = None,
        mfa_proof: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> requests.Response:
        headers = self._headers(principal=user_id, auth_token=auth_token, mfa_proof=mfa_proof)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return self._request(
            "POST",
            "operations/execute",
            json={"user_id": user_id, "operation": operation, "amount": amount, "target": target, "product_id": product_id, "card_id": card_id, "new_limit": new_limit, "merchant": merchant, "participants": participants or [], "product_ids": product_ids or [], "scheduled_at": scheduled_at, "reason": reason, "auth_token": auth_token, "mfa_proof": mfa_proof},
            headers=headers,
            raise_for_status=False,
        )

    @_dualmethod
    def execute_transfer(
        self,
        from_user: str,
        target: str,
        amount: float,
        auth_token: str,
        *,
        remark: str = "智能转账",
        mfa_proof: Optional[str] = None,
        confirmation: Optional[bool] = None,
        idempotency_key: Optional[str] = None,
    ) -> requests.Response:
        headers = self._headers(principal=from_user, auth_token=auth_token, mfa_proof=mfa_proof, confirmation=confirmation)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return self._request(
            "POST",
            "transfers/execute",
            json={"from_user": from_user, "target_name_or_id": target, "amount": amount, "remark": remark},
            headers=headers,
            raise_for_status=False,
        )

    @_dualmethod
    def lock_fund(
        self,
        user_id: str,
        amount: float,
        reason: str,
        *,
        auth_token: Optional[str] = None,
        mfa_proof: Optional[str] = None,
        confirmation: Optional[bool] = None,
        event_at: Optional[str] = None,
        reminder_at: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> requests.Response:
        headers = self._headers(principal=user_id, auth_token=auth_token, mfa_proof=mfa_proof, confirmation=confirmation)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return self._request(
            "POST",
            "accounts/lock_fund",
            json={"user_id": user_id, "amount": amount, "reason": reason, "event_at": event_at, "reminder_at": reminder_at},
            headers=headers,
            raise_for_status=False,
        )

    def lock_precheck(self, user_id: str, amount: float, reason: str = "跨场景联动储备", *, event_at: Optional[str] = None, reminder_at: Optional[str] = None, idempotency_key: Optional[str] = None) -> requests.Response:
        headers = self._headers(principal=user_id)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return self._request("POST", "risk/precheck/lock", json={"user_id": user_id, "amount": amount, "reason": reason, "event_at": event_at, "reminder_at": reminder_at}, headers=headers, raise_for_status=False)

    def unlock_precheck(self, user_id: str, amount: float, *, lock_id: Optional[str] = None, reason: str = "解除资金隔离", idempotency_key: Optional[str] = None) -> requests.Response:
        headers = self._headers(principal=user_id)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return self._request("POST", "risk/precheck/unlock", json={"user_id": user_id, "amount": amount, "lock_id": lock_id, "reason": reason}, headers=headers, raise_for_status=False)

    def unlock_fund(
        self,
        user_id: str,
        amount: float,
        *,
        lock_id: Optional[str] = None,
        auth_token: Optional[str] = None,
    ) -> requests.Response:
        return self._request(
            "POST",
            "accounts/unlock_fund",
            json={"user_id": user_id, "amount": amount, "lock_id": lock_id},
            headers=self._headers(principal=user_id, auth_token=auth_token),
            raise_for_status=False,
        )

    # Original static API retained for existing integrations. ------------
    @staticmethod
    def get_balance(user_id: str):
        return BankClient(user_id=user_id).balance(user_id)

    @staticmethod
    def get_transactions(user_id: str):
        return BankClient(user_id=user_id).transactions(user_id)
