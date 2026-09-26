"""Local AA event records, visible through the authenticated Core API."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from security import STATE_LOCK

OUTBOX: list[dict[str, Any]] = []

_PUBLIC_FIELDS = (
    "event_id", "outbox_id", "owner_id", "recipient_id", "split_id", "amount",
    "outcome", "message", "created_at",
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
        row = {"event_id": event_id, "outbox_id": f"NOB{uuid.uuid4().hex[:12].upper()}", "owner_id": owner_id, "recipient_id": recipient_id, "split_id": split_id, "amount": amount, "outcome": outcome, "message": message, "created_at": now}
        OUTBOX.append(row)
        return row


def visible(user_id: str, *, split_id: Optional[str] = None) -> list[dict[str, Any]]:
    with STATE_LOCK:
        return [_public_row(row) for row in OUTBOX if (row.get("owner_id") == user_id or row.get("recipient_id") == user_id) and (split_id is None or row.get("split_id") == split_id)]
