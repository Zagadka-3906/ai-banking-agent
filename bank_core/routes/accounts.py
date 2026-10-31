"""GREEN reads: balance, transactions and bill analysis."""

from __future__ import annotations

import re
import time
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

from banking_knowledge import KNOWLEDGE_VERSION, TOPICS, capability_catalog, knowledge_documents

from bank_core.cards import CARD_MAX_DAILY_LIMIT, user_cards
from bank_core.payments import daily_spent
from bank_security.circuit import BEHAVIOR_STATE, BEHAVIOR_STEP_UP_RULES, behavior_window_seconds


_SERVICE_GUIDES = {topic: [item["text"] for item in knowledge_documents(topic)] for topic in TOPICS}


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
            "timing_message": next(item["text"] for item in knowledge_documents("dispute") if item["id"] == "guide.dispute.timing")}, "case_id": safe_case}


@router.get("/api/v1/service-guide")
def service_guide(topic: str = "general", x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
                  x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), rule_ids: Optional[str] = None):
    request_id = server_request_id(x_request_id)
    actor = get_actor(x_user_id, x_user_id, action="BANKING_HELP", request_id=request_id)
    if rule_ids is not None:
        from banking_rules import rules_version, select_rules

        if len(rule_ids) > 2000:
            raise HTTPException(400, {'code': 'INVALID_RULE_IDS', 'message': '规则编号超过限制'})
        try:
            documents = select_rules(rule_ids.split(','))
        except ValueError as exc:
            raise HTTPException(400, {'code': str(exc), 'message': '请使用有效规则编号'}) from exc
        audit_event('RULES_QUERY', user_id=actor, request_id=request_id, risk_level='GREEN', decision='ALLOW', outcome='SUCCESS',
                    metadata={'count': len(documents), 'knowledge_version': KNOWLEDGE_VERSION})
        return {'code': 200, 'user_id': actor, 'version': 1, 'knowledge_version': KNOWLEDGE_VERSION,
                'rules_version': rules_version(), 'documents': documents, 'facts': [item['text'] for item in documents]}
    safe_topic = topic if topic in TOPICS else "general"
    documents = knowledge_documents(safe_topic)
    return {"code": 200, "user_id": actor, "topic": safe_topic, "facts": [item["text"] for item in documents], "version": 1,
            "knowledge_version": KNOWLEDGE_VERSION, "documents": documents}


@router.get("/api/v1/assistant/capabilities")
def assistant_capabilities(x_user_id: Optional[str] = Header(None, alias="X-User-Id")):
    actor = get_actor(x_user_id, x_user_id, action="CAPABILITY_QUERY", request_id=server_request_id(None))
    return {"code": 200, "user_id": actor, "version": KNOWLEDGE_VERSION, "capabilities": capability_catalog()}


@router.get("/api/v1/assistant/context")
def assistant_context(x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
                      x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """Policy explanation snapshot: never precheck, mint authorization or purge state."""
    request_id = server_request_id(x_request_id)
    actor = get_actor(x_user_id, x_user_id, action="ASSISTANT_CONTEXT", request_id=request_id)
    with STATE_LOCK:
        account = ACCOUNTS.get(actor)
        if account is None:
            raise HTTPException(404, {"code": "ACCOUNT_NOT_FOUND", "message": "账户不存在"})
        now = time.monotonic()
        timeline = (BEHAVIOR_STATE.get(actor) or {}).get("timeline") or []
        names = {row.get("name") for row in timeline if row.get("kind") == "SIGNAL"
                 and 0 <= now - float(row.get("at", 0)) <= behavior_window_seconds()}
        reasons = [row["description"] for row in BEHAVIOR_STEP_UP_RULES if all(name in names for name in row["requires"])]
        labels = {"ACTIVE": "正常", "FROZEN": "已冻结", "LOST": "已挂失"}
        facts = [{"id": "context.daily_spent", "text": f"当前账户今日已转出 ¥{daily_spent(actor):,.2f}。"},
                 {"id": "context.limit_max", "text": f"当前演示卡片日额度最多可设置为 ¥{CARD_MAX_DAILY_LIMIT:,.2f}，不支持无限额度。"}]
        for index, card in enumerate(user_cards(actor)):
            facts.append({"id": f"context.card.{index}", "text": f"卡片 {card['card_id']} 当前状态为{labels.get(str(card.get('status')), '未知')}，日额度 ¥{float(card.get('daily_limit') or 0):,.2f}。",
                          "entity_id": card["card_id"], "card_id": card["card_id"], "entity_names": [card["card_id"]]})
        facts.append({"id": "context.step_up", "text": "当前存在转账强验证升级条件：" + "；".join(reasons) + "。" if reasons
                      else "当前查询时点没有命中额度调整后的转账验证升级条件；实际要求以本次操作预检与审批卡为准。"})
    audit_event("ASSISTANT_CONTEXT", user_id=actor, request_id=request_id, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS")
    return {"code": 200, "user_id": actor, "version": KNOWLEDGE_VERSION, "facts": facts}


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
