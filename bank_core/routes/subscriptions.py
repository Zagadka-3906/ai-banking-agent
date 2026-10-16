"""Subscription query results (released only via a confirmed operation) and renewal reminders."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException

from bank_core.access import (
    get_actor,
    record_failure_and_raise,
    server_request_id,
)
from bank_core.ledger import (
    SUBSCRIPTIONS,
    TRANSACTIONS,
    ledger_transaction,
)
from bank_core.operations import (
    resolve_subscription,
)
from bank_core.payments import (
    ensure_account,
)
from bank_core.persistence import (
    persist_ledger,
)
from scheduler import TASK_STORE
from security import (
    STATE_LOCK,
    SecurityError,
    audit_event,
)

router = APIRouter()


def subscription_query_result(user_id: str) -> dict[str, Any]:
    """YELLOW: registered debits plus recurring candidates from verified records.

    Only ``execute_operation`` calls this, after the user confirmed a
    ``SUBSCRIPTION_QUERY`` precheck; there is no unconfirmed read route.
    """

    from subscription_detector import detect_recurring_debits

    subscriptions = [dict(row) for row in SUBSCRIPTIONS.get(user_id, [])]
    registered = [dict(row, source="REGISTERED") for row in subscriptions if row.get("status") == "ACTIVE"]
    registered_merchants = {str(row["merchant"]).casefold() for row in registered}
    detected = [row for row in detect_recurring_debits(TRANSACTIONS, user_id=user_id) if row["merchant"].casefold() not in registered_merchants]
    return {
        "status": "QUERIED",
        "subscriptions": subscriptions,
        "candidates": registered + detected,
        "renewal_reminders": [{"subscription_id": row["subscription_id"], "next_charge": row["next_charge"], "message": f"{row['merchant']} 将于 {row['next_charge']} 扣款 ¥{row['amount']:.2f}"} for row in registered],
    }


@router.post("/api/v1/subscriptions/{user_id}/reminders")
def create_renewal_reminders(user_id: str, merchant: Optional[str] = None, days_before: int = 2, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """Create idempotent renewal reminders, optionally for one named merchant.

    No money moves here, so this is a green write: it only materialises the
    detected renewals as auditable task-center items.  Two days is the default;
    the caller may explicitly choose an offset from zero to 365 days. Repeating
    the same subscription/date/offset returns the existing task without a duplicate.
    """

    request_id = server_request_id(x_request_id)
    actor = get_actor(user_id, x_user_id, action="SUBSCRIPTION_REMINDER", request_id=request_id)
    ensure_account(user_id)
    if isinstance(days_before, bool) or not 0 <= days_before <= 365:
        raise HTTPException(status_code=400, detail={"code": "INVALID_REMINDER_DAYS", "message": "提醒提前天数须在 0 到 365 天之间"})
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    with STATE_LOCK:
        with ledger_transaction():
            try:
                selected_rows = [resolve_subscription(user_id, merchant, None)] if merchant else list(SUBSCRIPTIONS.get(user_id, []))
            except SecurityError as exc:
                record_failure_and_raise(exc, user_id=actor, action="SUBSCRIPTION_REMINDER_CREATE", request_id=request_id)
            for row in selected_rows:
                if row.get("status") != "ACTIVE":
                    continue
                try:
                    charge_date = datetime.strptime(str(row.get("next_charge")), "%Y-%m-%d")
                except (TypeError, ValueError):
                    skipped.append({"subscription_id": row.get("subscription_id"), "reason": "INVALID_NEXT_CHARGE"})
                    continue
                run_at = (charge_date - timedelta(days=days_before)).replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
                task = TASK_STORE.create(
                    actor,
                    "RENEWAL_REMINDER",
                    {
                        "subscription_id": row["subscription_id"],
                        "merchant": row.get("merchant"),
                        "amount": row.get("amount"),
                        "next_charge": row.get("next_charge"),
                        "days_before": days_before,
                        "message": f"{row.get('merchant')} 将于 {row.get('next_charge')} 扣款 ¥{float(row.get('amount') or 0):,.2f}，需要取消请提前操作",
                    },
                    run_at=run_at,
                    idempotency_key=f"renewal:{row['subscription_id']}:{row.get('next_charge')}" + (f":days-{days_before}" if days_before != 2 else ""),
                )
                created.append(task.to_dict())
        persist_ledger()
    audit_event(
        "SUBSCRIPTION_REMINDER_CREATE",
        user_id=actor,
        risk_level="GREEN",
        decision="ALLOW",
        outcome="SUCCESS",
        request_id=request_id,
        metadata={"created": len(created), "skipped": len(skipped)},
    )
    return {"code": 200, "status": "CREATED", "data": created, "skipped": skipped}
