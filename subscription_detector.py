"""Conservative recurring-debit detection over verified ledger transactions."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Any, Iterable


def detect_recurring_debits(rows: Iterable[dict[str, Any]], *, user_id: str, today: date | None = None) -> list[dict[str, Any]]:
    """Require repeated same-merchant, same-amount charges at a stable cadence.

    A candidate is evidence for user review, never permission to cancel a
    merchant contract or create a new debit mandate.
    """

    today = today or date.today()
    groups: dict[tuple[str, int], list[tuple[date, str, str]]] = defaultdict(list)
    for row in rows:
        if row.get("account_id") != user_id:
            continue
        merchant = " ".join(str(row.get("merchant") or "").split())[:120]
        if not merchant or str(row.get("category") or "") in {"转账支出", "AA 支出", "AA 收款"}:
            continue
        try:
            raw_cents = Decimal(str(row.get("amount"))) * 100
            if raw_cents != raw_cents.to_integral_value():
                continue
            cents = int(raw_cents)
            charged = datetime.fromisoformat(str(row.get("time") or "").replace("Z", "+00:00")).date()
        except (InvalidOperation, ValueError, TypeError):
            continue
        if cents <= 0 or charged > today:
            continue
        groups[(merchant.casefold(), cents)].append((charged, str(row.get("tx_id") or ""), merchant))

    candidates: list[dict[str, Any]] = []
    for (normalized, cents), charges in groups.items():
        charges = sorted(set(charges))
        if len(charges) < 2:
            continue
        gaps = [(current[0] - previous[0]).days for previous, current in zip(charges, charges[1:])]
        cadence = int(round(median(gaps)))
        if 6 <= cadence <= 8:
            period = "WEEKLY"
            tolerance = 1
        elif 27 <= cadence <= 33:
            period = "MONTHLY"
            tolerance = 4
        elif 350 <= cadence <= 380:
            period = "YEARLY"
            tolerance = 16
        else:
            continue
        if any(abs(gap - cadence) > tolerance for gap in gaps):
            continue
        if (today - charges[-1][0]).days > cadence * 2:
            continue
        next_charge = charges[-1][0] + timedelta(days=cadence)
        while next_charge <= today:
            next_charge += timedelta(days=cadence)
        digest = hashlib.sha256(f"{user_id}|{normalized}|{cents}".encode()).hexdigest()[:12].upper()
        candidates.append({
            "subscription_id": f"DETECTED-{digest}", "merchant": charges[-1][2],
            "amount": cents / 100, "next_charge": next_charge.isoformat(),
            "status": "CANDIDATE", "source": "TRANSACTION_PATTERN", "period": period,
            "interval_days": cadence, "confidence": 0.95 if len(charges) >= 3 else 0.8,
            "evidence_tx_ids": [item[1] for item in charges[-5:] if item[1]],
        })
    return sorted(candidates, key=lambda row: (row["next_charge"], row["merchant"]))
