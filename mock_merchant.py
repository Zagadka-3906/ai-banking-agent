"""Deterministic local merchant catalog for the birthday demonstration."""

from __future__ import annotations

from typing import Any
from uuid import uuid4


CATALOG: dict[str, int] = {"鲜花": 4900, "蛋糕": 3900}


def quote_cents(item: str) -> int:
    if item not in CATALOG:
        raise ValueError("merchant item is not allow-listed")
    return CATALOG[item]


def place_order(*, user_id: str, task_id: str, item: str, amount_cents: int) -> dict[str, Any]:
    """Return a mock provider receipt; the Core owns the ledger transaction."""

    if quote_cents(item) != amount_cents or not user_id or not task_id:
        raise ValueError("merchant quote changed")
    return {"mock_order_id": f"MOCK{uuid4().hex[:12].upper()}", "user_id": user_id,
            "task_id": task_id, "item": item, "amount": amount_cents / 100,
            "status": "PLACED", "provider": "LOCAL_MOCK_MERCHANT"}
