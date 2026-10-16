"""GREEN reads: balance, transactions and bill analysis."""

from __future__ import annotations

import re
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException

from bank_core.access import (
    get_actor,
    record_failure_and_raise,
    server_request_id,
)
from bank_core.ledger import (
    ACCOUNTS,
    TRANSACTIONS,
)
from bank_core.payments import (
    ensure_account,
)
from planner import analyze_transactions, filter_transactions
from security import (
    SecurityError,
    audit_event,
    sanitize_text,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Green zone: read-only operations
# ---------------------------------------------------------------------------
@router.get("/api/v1/accounts/{user_id}/balance")
def get_balance(user_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = server_request_id(x_request_id)
    # Return a stable not-found result for an unknown resource before doing
    # object-level identity comparison; this avoids leaking whether a valid
    # caller is associated with a different account.
    if user_id not in ACCOUNTS:
        audit_event("BALANCE_QUERY", user_id=x_user_id, decision="BLOCK", outcome="NOT_FOUND", reason="ACCOUNT_NOT_FOUND", request_id=request_id)
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": "账户不存在"})
    actor = get_actor(user_id, x_user_id, action="BALANCE_QUERY", request_id=request_id)
    try:
        account = ensure_account(user_id)
    except SecurityError as exc:
        record_failure_and_raise(exc, user_id=actor, action="BALANCE_QUERY", request_id=request_id)
    result = {
        "code": 200,
        "user_name": account["name"],
        "total_balance": account["balance"],
        "locked_amount": account["locked_amount"],
        "available_balance": account["balance"] - account["locked_amount"],
        "status": account["status"],
    }
    audit_event("BALANCE_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id)
    return result


@router.get("/api/v1/accounts/{user_id}/transactions")
def get_transactions(user_id: str, date_scope: str = "ALL", x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), category: Optional[str] = None):
    request_id = server_request_id(x_request_id)
    if user_id not in ACCOUNTS:
        audit_event("TRANSACTION_QUERY", user_id=x_user_id, decision="BLOCK", outcome="NOT_FOUND", reason="ACCOUNT_NOT_FOUND", request_id=request_id)
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": "账户不存在"})
    actor = get_actor(user_id, x_user_id, action="TRANSACTION_QUERY", request_id=request_id)
    try:
        ensure_account(user_id)
    except SecurityError as exc:
        record_failure_and_raise(exc, user_id=actor, action="TRANSACTION_QUERY", request_id=request_id)
    scope = str(date_scope or "ALL").upper()
    scope = {"今年": "YEAR", "年度": "YEAR", "全年": "YEAR"}.get(scope, scope)
    if scope not in {"TODAY", "MONTH", "LAST_MONTH", "YEAR", "ALL"}:
        raise HTTPException(status_code=400, detail={"code": "INVALID_DATE_SCOPE", "message": "不支持的时间范围"})
    records, category = _bill_records(user_id, scope, category)
    audit_event("TRANSACTION_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"record_count": len(records), "date_scope": scope, "category": category})
    return {"code": 200, "date_scope": scope, "category": category, "total_records": len(records), "data": records}


def _bill_records(user_id: str, scope: str, category: Optional[str]) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Apply the same date/category predicates before listing or aggregating."""
    if category is not None:
        category = sanitize_text(category, field="交易分类", max_length=120)
        if not category or re.search(r"[\x00-\x1f\x7f]", category):
            raise HTTPException(status_code=400, detail={"code": "INVALID_CATEGORY", "message": "交易分类不能为空或包含控制字符"})
    rows = filter_transactions([tx.copy() for tx in TRANSACTIONS if tx.get("account_id") == user_id], scope)
    if category is not None:
        rows = [row for row in rows if str(row.get("category") or "其他").strip() == category]
    return rows, category


@router.get("/api/v1/accounts/{user_id}/bill-analysis")
def get_bill_analysis(user_id: str, date_scope: str = "ALL", x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), category: Optional[str] = None):
    """GREEN: return source-bound bill facts for a report or chart."""

    request_id = server_request_id(x_request_id)
    actor = get_actor(user_id, x_user_id, action="BILL_ANALYSIS", request_id=request_id)
    ensure_account(user_id)
    scope = str(date_scope or "ALL").upper()
    scope = {"今年": "YEAR", "年度": "YEAR", "全年": "YEAR"}.get(scope, scope)
    if scope not in {"TODAY", "MONTH", "LAST_MONTH", "YEAR", "ALL"}:
        raise HTTPException(status_code=400, detail={"code": "INVALID_DATE_SCOPE", "message": "不支持的时间范围"})
    rows, category = _bill_records(user_id, scope, category)
    facts = analyze_transactions(rows, user_id=actor, request_id=request_id)
    audit_event("BILL_ANALYSIS", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"date_scope": scope, "category": category, "record_count": facts["record_count"]})
    return {"code": 200, "date_scope": scope, "category": category, "facts": facts, "records": rows}
