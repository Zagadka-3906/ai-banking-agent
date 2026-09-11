"""Push notification dispatch: the server half of the FCM delivery path.

What this module *does* provide:

* the delivery seam (``PushTransport``) plus the device-token registry it reads
  from (``security.push_tokens_for``);
* an audited dispatch call, so "was the user told?" is answerable after the fact;
* the task-to-message rendering used when a scheduled task falls due.

What it deliberately does **not** provide: an actual transport.  Firebase Cloud
Messaging needs a service-account credential this deployment does not have
(``BANK_FCM_CREDENTIALS``), and a transport that silently no-ops would make the
audit trail claim a notification was sent when nothing left the process.  The
default transport therefore reports "no provider configured", and that phrasing
is what lands in the audit event.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol

from security import audit_event, push_tokens_for

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


def resolve_transport() -> PushTransport:
    """Return the transport this deployment is configured for.

    Today that is always :class:`NullTransport`: the FCM transport is the one
    piece still missing, and it needs credentials rather than code to be
    meaningful.  The check is here so that adding it later is a change in one
    place.
    """

    credentials = os.getenv("BANK_FCM_CREDENTIALS", "").strip()
    if credentials:
        logger.warning(
            "BANK_FCM_CREDENTIALS is set (%s) but no FCM transport is implemented; "
            "notifications will not be delivered",
            credentials,
        )
        return NullTransport(reason="FCM transport not implemented; credential present but unused")
    return NullTransport()


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
