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
    DISPUTE_CASES,
    TRANSACTIONS,
)
from bank_core.payments import (
    ensure_account,
)
from planner import analyze_transactions, filter_transactions
from security import (
    SecurityError,
    STATE_LOCK,
    audit_event,
    sanitize_text,
)

router = APIRouter()


_SERVICE_GUIDES = {
    "dispute": ["可以查询已有争议工单的状态、关联交易和登记时间。", "反诈保护会冻结关联卡片并创建或关联工单，须单独确认和强验证。",
                "当前是演示工单登记，没有真实客服审核队列，也未提供预计完成时间；不会自动退款。",
                "目前没有提供撤回、取消或删除争议工单的功能。", "查询工单不会解除卡片冻结；如需解冻，须另外提出并完成确认和强验证。"],
    "transfer": ["转账需先核对收款人和金额并确认；当日累计超1000元还需强验证。", "预约转账到期后仍等待用户确认，已完成的转账不能直接撤销。",
                 "这是虚构账本，不提供真实跨行到账时效承诺。"],
    "card": ["可直接查询卡片状态。", "冻结、解冻、挂失、解除挂失和调额需单独确认及强验证。",
             "关闭交易渠道需确认，开通渠道需强验证；询问流程不会直接操作卡片。", "本演示中冻结卡片限制该卡付款，账户仍可接收转账。"],
    "wealth": ["可以查询产品、持仓和风险等级，推荐前需完成风险测评。", "申购和赎回须单独确认及强验证；具体规则以虚构产品详情为准。",
               "本系统不提供真实投资建议、保本保证或收益承诺。"],
    "subscription": ["查询和取消订阅都需确认。", "系统推测的订阅候选不能直接取消，需先核实商户记录。",
                     "演示取消记录不代表真实商户合同解除或退款。"],
    "security": ["查询余额、流水或卡片状态无需操作确认；订阅查询按本演示规则仍需确认。",
                 "待确认操作尚未执行，可以先询问规则或查询记录，再决定确认或取消。",
                 "红区操作需要一次性验证码和登记设备的签名验证，聊天中说已验证不能替代验证。",
                 "验证与授权绑定本次业务及对象，不能用于另一笔操作。"],
    "general": ["可查询账户、交易、卡片、理财和争议工单；可询问相关业务规则。",
                "转账、冻结、建单等变更需另行提出并确认，询问进度或办理方法不会执行这些操作。",
                "请说明要了解哪项业务；未提供的规则、人工处理时效或银行外部业务不能推测。"],
}


@router.get("/api/v1/disputes")
def get_disputes(case_id: Optional[str] = None, transaction_id: Optional[str] = None,
                 x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
                 x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = server_request_id(x_request_id)
    actor = get_actor(x_user_id, x_user_id, action="DISPUTE_QUERY", request_id=request_id)
    safe_case = sanitize_text(case_id, field="工单编号", max_length=100).upper() if case_id else None
    safe_tx = sanitize_text(transaction_id, field="交易编号", max_length=100) if transaction_id else None
    fields = ("case_id", "transaction_id", "card_id", "status", "protection_status", "created_at", "updated_at")
    with STATE_LOCK:
        rows = [{key: row.get(key) for key in fields} for row in DISPUTE_CASES
                if row.get("owner_id") == actor and (not safe_case or row.get("case_id") == safe_case)
                and (not safe_tx or row.get("transaction_id") == safe_tx)]
    if (safe_case or safe_tx) and not rows:
        raise HTTPException(404, {"code": "DISPUTE_NOT_FOUND", "message": "未找到当前账户的该争议工单"})
    audit_event("DISPUTE_QUERY", user_id=actor, request_id=request_id, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", metadata={"count": len(rows)})
    return {"code": 200, "user_id": actor, "data": rows, "policy": {"version": 1, "estimated_completion": None,
            "timing_message": _SERVICE_GUIDES["dispute"][2]}, "case_id": safe_case}


@router.get("/api/v1/service-guide")
def service_guide(topic: str = "general", x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
                  x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    actor = get_actor(x_user_id, x_user_id, action="BANKING_HELP", request_id=server_request_id(x_request_id))
    safe_topic = topic if topic in _SERVICE_GUIDES else "general"
    return {"code": 200, "user_id": actor, "topic": safe_topic, "facts": list(_SERVICE_GUIDES[safe_topic]), "version": 1}


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
