"""Push notification dispatch: the server half of the FCM delivery path.

What this module provides:

* the delivery seam (``PushTransport``) plus the device-token registry it reads
  from (``security.push_tokens_for``);
* an audited dispatch call, so "was the user told?" is answerable after the fact;
* the task-to-message rendering used when a scheduled task falls due;
* an :class:`FcmTransport` that exchanges a service-account credential for an
  OAuth2 access token and sends one HTTP v1 message per registered device.

Configuration decides everything.  With no credential
(``BANK_FCM_CREDENTIALS``), or with one that is unreadable, the
:class:`NullTransport` is used and it says *why* - a transport that silently
no-ops would make the audit trail claim a delivery that never happened.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol

import requests

from security import audit_event, push_tokens_for

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
except ImportError:  # pragma: no cover - cryptography is a runtime dependency
    hashes = None  # type: ignore[assignment]
    serialization = None  # type: ignore[assignment]
    padding = None  # type: ignore[assignment]

CRYPTO_AVAILABLE = serialization is not None

logger = logging.getLogger("mock_banking.notifications")


@dataclass
class PushResult:
    """Outcome of one dispatch attempt."""

    delivered: int = 0
    failed: int = 0
    detail: str = ""
    provider: str = "none"

    @property
    def sent_anything(self) -> bool:
        return self.delivered > 0


class PushTransport(Protocol):
    """A delivery provider.  Implemented by :class:`NullTransport` today."""

    name: str

    def send(self, tokens: list[str], *, title: str, body: str, data: Mapping[str, Any]) -> PushResult: ...


@dataclass
class NullTransport:
    """The default: record the intent, deliver nothing, say so.

    ``reason`` is reported verbatim into the audit event, so a reader of the
    trail is never left guessing whether notifications work.
    """

    reason: str = "no push provider configured (set BANK_FCM_CREDENTIALS to enable FCM)"
    name: str = field(default="none", init=False)

    def send(self, tokens: list[str], *, title: str, body: str, data: Mapping[str, Any]) -> PushResult:
        logger.info("push skipped for %d device(s): %s", len(tokens), self.reason)
        return PushResult(delivered=0, failed=len(tokens), detail=self.reason, provider=self.name)


# ---------------------------------------------------------------------------
# FCM HTTP v1
#
# A service account is exchanged for a short-lived OAuth2 access token (a signed
# JWT assertion), and each registered device token is sent one message.  Both
# endpoints are overridable so the flow can be exercised against a local server
# in tests - the code path is verified even though only a real credential can
# prove Google accepts it.
# ---------------------------------------------------------------------------

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
DEFAULT_FCM_BASE_URL = "https://fcm.googleapis.com"
# Refresh this long before the token actually expires, so a request never races
# the expiry it was issued under.
TOKEN_REFRESH_MARGIN_SECONDS = 60


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def build_service_account_jwt(claims: Mapping[str, Any], private_key_pem: str) -> str:
    """Sign an RS256 JWT with a service account's private key."""

    if not (CRYPTO_AVAILABLE and serialization is not None):
        raise RuntimeError("cryptography is required to sign FCM assertions")
    header = {"alg": "RS256", "typ": "JWT"}
    segments = [
        _b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")),
        _b64url(json.dumps(dict(claims), separators=(",", ":"), sort_keys=True).encode("utf-8")),
    ]
    signing_input = ".".join(segments).encode("ascii")
    key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    segments.append(_b64url(signature))
    return ".".join(segments)


@dataclass
class FcmTransport:
    """Delivers notifications through Firebase Cloud Messaging (HTTP v1)."""

    credentials_path: str
    base_url: str = DEFAULT_FCM_BASE_URL
    token_url: Optional[str] = None
    timeout: float = 10.0
    name: str = field(default="fcm", init=False)

    def __post_init__(self) -> None:
        with open(self.credentials_path, "r", encoding="utf-8") as handle:
            self._account = json.load(handle)
        for required in ("client_email", "private_key", "project_id", "token_uri"):
            if not self._account.get(required):
                raise ValueError(f"service account is missing {required}")
        self._token_url = self.token_url or str(self._account["token_uri"])
        self._access_token: Optional[str] = None
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    @property
    def project_id(self) -> str:
        return str(self._account["project_id"])

    def _access_token_or_refresh(self, *, force: bool = False) -> str:
        with self._token_lock:
            if not force and self._access_token and time.time() < self._token_expires_at - TOKEN_REFRESH_MARGIN_SECONDS:
                return self._access_token
            now = int(time.time())
            assertion = build_service_account_jwt(
                {
                    "iss": str(self._account["client_email"]),
                    "scope": FCM_SCOPE,
                    "aud": self._token_url,
                    "iat": now,
                    "exp": now + 3600,
                },
                str(self._account["private_key"]),
            )
            response = requests.post(
                self._token_url,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            token = str(payload.get("access_token") or "")
            if not token:
                raise RuntimeError("token endpoint returned no access_token")
            self._access_token = token
            self._token_expires_at = time.time() + float(payload.get("expires_in") or 3600)
            return token

    def send(self, tokens: list[str], *, title: str, body: str, data: Mapping[str, Any]) -> PushResult:
        if not tokens:
            return PushResult(detail="no device tokens", provider=self.name)

        endpoint = f"{self.base_url}/v1/projects/{self.project_id}/messages:send"
        delivered = 0
        failures: list[str] = []
        for token in tokens:
            outcome = self._send_one(endpoint, token, title=title, body=body, data=data)
            if outcome is None:
                delivered += 1
            else:
                failures.append(outcome)

        detail = "ok" if not failures else f"{len(failures)} device(s) refused: {failures[0]}"
        return PushResult(delivered=delivered, failed=len(failures), detail=detail, provider=self.name)

    def _send_one(self, endpoint: str, token: str, *, title: str, body: str, data: Mapping[str, Any]) -> Optional[str]:
        """Send to one token; return None on success or a short reason."""

        message = {
            "message": {
                "token": token,
                # Data-only on purpose: the app then owns the wording and the
                # deep link (FirebaseMessagingService.onMessageReceived), instead
                # of the system rendering a notification the app never sees.
                "data": {**{str(k): str(v) for k, v in data.items()}, "title": title, "body": body},
                "android": {"priority": "high"},
            }
        }
        for attempt in (0, 1):
            try:
                response = requests.post(
                    endpoint,
                    json=message,
                    headers={"Authorization": f"Bearer {self._access_token_or_refresh(force=attempt == 1)}"},
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                return f"{type(exc).__name__}"
            if response.status_code == 401 and attempt == 0:
                # The access token expired between issue and use; refresh once.
                continue
            if response.status_code >= 400:
                return f"HTTP {response.status_code}: {response.text[:120]}"
            return None
        return "unauthorized after refresh"


def resolve_transport() -> PushTransport:
    """Return the transport this deployment is configured for.

    Configuration is the whole decision: a readable service-account file means
    real delivery, anything else means :class:`NullTransport` carrying the
    *specific* reason (missing file, unreadable JSON, no private key).  A
    credential that is present but broken must never look like success.
    """

    credentials = os.getenv("BANK_FCM_CREDENTIALS", "").strip()
    if not credentials:
        return NullTransport()
    if not os.path.exists(credentials):
        logger.warning("BANK_FCM_CREDENTIALS points at a missing file: %s", credentials)
        return NullTransport(reason=f"FCM credential file not found: {credentials}")
    try:
        return FcmTransport(credentials_path=credentials)
    except Exception as exc:  # unreadable / malformed / wrong key type
        logger.warning("FCM credential unusable (%s): %s", type(exc).__name__, exc)
        return NullTransport(reason=f"FCM credential unusable: {type(exc).__name__}")


def dispatch_to_user(
    user_id: str,
    *,
    title: str,
    body: str,
    data: Optional[Mapping[str, Any]] = None,
    request_id: Optional[str] = None,
) -> PushResult:
    """Notify every active device of one principal, and audit the outcome."""

    payload = {str(key): str(value) for key, value in (data or {}).items()}
    tokens = push_tokens_for(user_id)
    if not tokens:
        audit_event(
            "PUSH_DISPATCHED",
            user_id=user_id,
            decision="SKIP",
            outcome="NO_DEVICE",
            request_id=request_id,
            metadata={"reason": "no registered push token", "title": title},
        )
        return PushResult(detail="no registered push token")

    transport = resolve_transport()
    result = transport.send([row["token"] for row in tokens], title=title, body=body, data=payload)
    audit_event(
        "PUSH_DISPATCHED",
        user_id=user_id,
        decision="ALLOW" if result.sent_anything else "SKIP",
        outcome="SENT" if result.sent_anything else "NOT_SENT",
        request_id=request_id,
        metadata={
            "provider": result.provider,
            "devices": len(tokens),
            "delivered": result.delivered,
            "failed": result.failed,
            "detail": result.detail,
            "title": title,
            "data": payload,
        },
    )
    return result


# What a due task says to the user.  A scheduled transfer and an order draft
# both pause for a *fresh* confirmation at run time, so the message says exactly
# that instead of implying something already happened.
_TASK_MESSAGES: dict[str, tuple[str, str]] = {
    "SCHEDULED_TRANSFER": ("预约转账待确认", "一笔预约转账已到执行时间，需要您确认后才会转出。"),
    "ORDER_DRAFT": ("待办事项待确认", "生日提醒的订单草稿已到期，需要您确认后才会下单。"),
    "REMINDER": ("提醒", "您设置的提醒已到期。"),
}


def notify_waiting_user(task: Any, *, request_id: Optional[str] = None) -> PushResult:
    """Notify a task's owner that their task now needs them.

    Called by the scheduler the moment a task moves to ``WAITING_USER`` - the
    point at which nothing more will happen without the user.
    """

    task_type = str(getattr(task, "task_type", "") or "")
    title, body = _TASK_MESSAGES.get(task_type, ("任务待处理", "有一项任务需要您的确认。"))
    return dispatch_to_user(
        str(getattr(task, "owner_id", "") or ""),
        title=title,
        body=body,
        data={"task_id": str(getattr(task, "task_id", "") or ""), "task_type": task_type},
        request_id=request_id,
    )
