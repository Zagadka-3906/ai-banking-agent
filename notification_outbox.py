"""Small at-least-once notification outbox for the demo ledger."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import uuid

from security import STATE_LOCK, push_tokens_for
from notifications import PushResult, resolve_transport

OUTBOX: list[dict[str, Any]] = []
LEASE_SECONDS = 60
DISPATCH_LOCK = __import__("threading").RLock()

_PUBLIC_FIELDS = (
    "event_id", "outbox_id", "owner_id", "recipient_id", "split_id", "amount",
    "outcome", "message", "status", "attempts", "next_attempt_at", "last_error",
    "created_at", "updated_at", "delivered_at",
)


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return only the stable, client-safe outbox projection."""

    return {key: row.get(key) for key in _PUBLIC_FIELDS}


def enqueue(event_id: str, *, owner_id: str, recipient_id: str, split_id: str, amount: float, message: str, outcome: str = "COLLECTED") -> dict[str, Any]:
    with STATE_LOCK:
        existing = next((row for row in OUTBOX if row.get("event_id") == event_id), None)
        if existing:
            return existing
        now = datetime.now(timezone.utc).isoformat()
        row = {"event_id": event_id, "outbox_id": f"NOB{uuid.uuid4().hex[:12].upper()}", "owner_id": owner_id, "recipient_id": recipient_id, "split_id": split_id, "amount": amount, "outcome": outcome, "message": message, "status": "PENDING", "attempts": 0, "next_attempt_at": now, "last_error": None, "created_at": now, "updated_at": now, "delivered_at": None}
        OUTBOX.append(row)
        return row


def dispatch(*, transport: Optional[Any] = None, event_id: Optional[str] = None) -> list[dict[str, Any]]:
    with DISPATCH_LOCK:
        return _dispatch(transport=transport, event_id=event_id)


def _dispatch(*, transport: Optional[Any] = None, event_id: Optional[str] = None) -> list[dict[str, Any]]:
    provider = transport or resolve_transport()
    results = []
    with STATE_LOCK:
        now = datetime.now(timezone.utc)
        def due(row: dict[str, Any]) -> bool:
            if row.get("status") == "SENDING":
                try:
                    return (now - datetime.fromisoformat(str(row.get("updated_at")).replace("Z", "+00:00"))).total_seconds() >= LEASE_SECONDS
                except (TypeError, ValueError):
                    return False
            if row.get("status") == "RETRY":
                try:
                    return datetime.fromisoformat(str(row.get("next_attempt_at")).replace("Z", "+00:00")) <= now
                except (TypeError, ValueError):
                    # An invalid persisted retry deadline is fail-safe: do not
                    # immediately claim a potentially duplicated notification.
                    return False
            return row.get("status") == "PENDING"
        rows = [r for r in OUTBOX if (event_id is None or r.get("event_id") == event_id) and due(r)]
        for row in rows:
            row["status"] = "SENDING"
            row["attempts"] = int(row.get("attempts", 0)) + 1
            row["updated_at"] = now.isoformat()
    for row in rows:
        tokens = push_tokens_for(row["recipient_id"])
        try:
            result: PushResult = provider.send(tokens, title="银行 AA 分摊", body=row["message"], data={"event_id": row["event_id"], "split_id": row["split_id"]})
            ok = bool(result.delivered) and not result.failed
            error = None if ok else (result.detail or "notification delivery failed")
        except Exception as exc:  # provider failures are retryable and never roll back funds
            ok, error = False, type(exc).__name__
        with STATE_LOCK:
            row["status"] = "SENT" if ok else ("FAILED" if row["attempts"] >= 3 else "RETRY")
            row["last_error"] = "PROVIDER_FAILURE" if error else None
            row["updated_at"] = datetime.now(timezone.utc).isoformat()
            row["next_attempt_at"] = (datetime.now(timezone.utc) + timedelta(seconds=60 * row["attempts"])).isoformat()
            if ok:
                row["delivered_at"] = row["updated_at"]
            results.append(_public_row(row))
    return results


def visible(user_id: str, *, split_id: Optional[str] = None) -> list[dict[str, Any]]:
    with STATE_LOCK:
        return [_public_row(row) for row in OUTBOX if (row.get("owner_id") == user_id or row.get("recipient_id") == user_id) and (split_id is None or row.get("split_id") == split_id)]
