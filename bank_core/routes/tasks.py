"""Task centre: AA inbox, receipts, scheduling, cancellation and human takeover."""

from __future__ import annotations

import hmac
import json
import os
import time
import uuid
from copy import deepcopy
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request

from bank_core.undo import UNDOABLE_TASK_TYPES, cancel_task_branch, undo_candidate, undo_summary
from bank_core.access import (
    _get_actor,
    _presented_session,
    _record_failure_and_raise,
    _request_id,
)
from bank_core.idempotency import (
    IDEMPOTENCY_RECORDS,
    IDEMPOTENCY_TTL_SECONDS,
    _idempotency_abort,
    _idempotency_begin,
    _idempotency_complete,
)
from bank_core.ledger import (
    ACCOUNTS,
    DAILY_TRANSFERS,
    LOCKS,
    MOCK_ORDERS,
    SCHEDULED_TRANSFERS,
    SPLIT_REQUESTS,
    TRANSACTIONS,
    _ledger_transaction,
)
from bank_core.models import (
    TaskActionRequest,
    TaskExecuteRequest,
    TaskPrecheckRequest,
)
from bank_core.operations import (
    _context_hash,
    _pending_transfer_amount,
)
from bank_core.payments import (
    _charge_card_and_balance,
    _daily_spent,
    _payment_card,
    _record_card_spend,
    _refresh_split_status,
    _resolve_target,
)
from bank_core.persistence import (
    OPERATION_RECEIPTS,
    persist_ledger,
)
from mock_merchant import CATALOG as MOCK_MERCHANT_CATALOG
from mock_merchant import place_order, quote_cents
from notification_outbox import enqueue as enqueue_notification
from notification_outbox import visible as visible_notifications
from scheduler import TASK_STORE, TaskState
from security import (
    STATE_LOCK,
    SecurityError,
    amount_to_cents,
    audit_event,
    authorize_execution,
    inspect_auth_token,
    issue_auth_token,
    sanitize_text,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Task centre: scheduling, cancellation and human takeover
# ---------------------------------------------------------------------------
@router.get("/api/v1/splits/inbox")
def split_inbox(x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    actor = _get_actor(x_user_id, x_user_id, action="SPLIT_INBOX", request_id=_request_id(x_request_id))
    with STATE_LOCK:
        rows = [
            {"split_id": split["split_id"], "from_user": split["user_id"],
             "from_name": ACCOUNTS[split["user_id"]]["name"], "share": item["share"],
             "status": item["status"], "target": split.get("target"),
             "payment_remark": f"AA:{split['split_id']}"}
            for split in SPLIT_REQUESTS for item in split.get("collection", [])
            if item.get("account_id") == actor
        ]
    audit_event("SPLIT_INBOX", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=_request_id(x_request_id), metadata={"count": len(rows)})
    return {"code": 200, "data": rows}


@router.post("/api/v1/splits/{split_id}/decline")
def decline_split(split_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    actor = _get_actor(x_user_id, x_user_id, action="SPLIT_DECLINE", request_id=_request_id(x_request_id))
    safe_id = sanitize_text(split_id, field="AA 编号", max_length=100)
    with _ledger_transaction():
        split = next((row for row in SPLIT_REQUESTS if row.get("split_id") == safe_id), None)
        item = next((row for row in split.get("collection", []) if row.get("account_id") == actor), None) if split else None
        if not item:
            raise HTTPException(status_code=404, detail={"code": "SPLIT_NOT_FOUND", "message": "AA 请求不存在"})
        if item.get("status") != "PENDING":
            raise HTTPException(status_code=409, detail={"code": "SPLIT_SHARE_NOT_PENDING", "message": "该份额已处理"})
        item["status"] = "DECLINED"
        _refresh_split_status(split)
        enqueue_notification(f"AA_DECLINED:{safe_id}:{actor}", owner_id=split["user_id"], recipient_id=actor, split_id=safe_id, amount=item["share"], outcome="DECLINED", message="AA 付款请求已拒绝")
    audit_event("SPLIT_DECLINE", user_id=actor, risk_level="YELLOW", decision="ALLOW", outcome="SUCCESS", request_id=_request_id(x_request_id), metadata={"split_id": safe_id})
    persist_ledger()
    return {"code": 200, "status": "DECLINED", "split_id": safe_id}


@router.get("/api/v1/notifications/outbox")
def notification_outbox_list(split_id: Optional[str] = None, x_session_token: Optional[str] = Header(None, alias="X-Session-Token"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    if not x_session_token:
        raise HTTPException(status_code=401, detail={"code": "SESSION_REQUIRED", "message": "查询 AA 事件需要会话凭证"})
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    actor = _get_actor(x_user_id, x_user_id, action="NOTIFICATION_QUERY", request_id=_request_id(x_request_id))
    return {"code": 200, "data": visible_notifications(actor, split_id=split_id)}


@router.get("/api/v1/operations/{operation_id}")
def operation_status(operation_id: str, endpoint: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    request_id = _request_id(x_request_id)
    actor = _get_actor(x_user_id, x_user_id, action="OPERATION_QUERY", request_id=request_id)
    if endpoint not in {"transfer_execute", "lock_fund", "operation_execute", "task_execute"}:
        raise HTTPException(status_code=400, detail={"code": "INVALID_OPERATION_KIND", "message": "操作类型无效"})
    key = sanitize_text(operation_id, field="操作编号", max_length=128)
    with STATE_LOCK:
        stored = OPERATION_RECEIPTS.get(json.dumps([actor, endpoint, key]))
        entry = IDEMPOTENCY_RECORDS.get((actor, endpoint, key))
        if stored and time.time() - stored["completed_at"] <= IDEMPOTENCY_TTL_SECONDS:
            result = {"status": "COMPLETED", "receipt": deepcopy(stored["response"])}
        elif entry and time.time() - entry["created_at"] <= IDEMPOTENCY_TTL_SECONDS:
            result = {"status": entry["status"], "receipt": deepcopy(entry.get("response"))}
        else:
            result = {"status": "UNKNOWN", "receipt": None}
    audit_event("OPERATION_QUERY", user_id=actor, request_id=request_id, risk_level="GREEN", decision="ALLOW", outcome=result["status"])
    return {"code": 200, "operation_id": key, **result}


@router.get("/api/v1/tasks")
def list_tasks(x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    request_id = _request_id(x_request_id)
    actor = _get_actor(x_user_id, x_user_id, action="TASK_QUERY", request_id=request_id)
    rows = [task.to_dict() for task in TASK_STORE.list(owner_id=actor)]
    audit_event("TASK_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"count": len(rows)})
    return {"code": 200, "data": rows}


# Registered before ``/tasks/{task_id}`` so "undoable" is not read as a task id.
@router.get("/api/v1/tasks/undoable")
def list_undoable_tasks(x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """The caller's reservations that can still be undone, newest first.

    Uses the same rules as the ``UNDO_TASK`` precheck, so a channel never has to
    guess which task (or which lock) is still reversible.
    """

    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    request_id = _request_id(x_request_id)
    actor = _get_actor(x_user_id, x_user_id, action="TASK_UNDO_QUERY", request_id=request_id)
    rows = []
    for task in reversed(TASK_STORE.list(owner_id=actor)):
        if task.task_type not in UNDOABLE_TASK_TYPES:
            continue
        try:
            undo_candidate(actor, task.task_id)
        except SecurityError:
            continue
        rows.append({"task_id": task.task_id, "task_type": task.task_type, "summary": undo_summary(task), "created_at": task.created_at})
    audit_event("TASK_UNDO_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"count": len(rows)})
    return {"code": 200, "data": rows}


@router.post("/api/v1/tasks/run-due")
def run_due_tasks(x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """Advance due reminder tasks in the demo scheduler.

    Scheduled money movement is deliberately not auto-settled here: it is
    moved to WAITING_USER so a fresh approval/MFA can be collected at run
    time.  Reminder tasks can be marked delivered automatically.
    """

    # Bound once: calling ``_request_id`` at each use site minted a *different*
    # correlation id per event when the caller supplied none, so one scheduler
    # run could not be traced as a single chain.
    request_id = _request_id(x_request_id)
    expected = os.getenv("BANK_ADMIN_AUDIT_TOKEN")
    if expected:
        if not x_admin_token or not hmac.compare_digest(expected, x_admin_token):
            raise HTTPException(status_code=403, detail={"code": "ADMIN_AUTH_REQUIRED", "message": "需要调度管理员凭证"})
        owner = None
    else:
        if not x_user_id:
            raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
        owner = x_user_id
        _get_actor(owner, owner, action="TASK_RUN", request_id=request_id)
    changed = []
    with STATE_LOCK:
        for task in TASK_STORE.ready():
            if owner and task.owner_id != owner:
                continue
            try:
                running = TASK_STORE.transition_if_status(task.task_id, TaskState.PENDING, TaskState.RUNNING, owner_id=owner)
                if running is None:
                    continue
                # Due money movement pauses for a fresh confirmation/MFA.
                final_status = TaskState.WAITING_USER if task.task_type in {"SCHEDULED_TRANSFER", "ORDER_DRAFT"} else TaskState.SUCCEEDED
                updated = TASK_STORE.transition(task.task_id, final_status, owner_id=owner)
                changed.append(updated.to_dict())
            except (KeyError, ValueError):
                continue
        if changed:
            persist_ledger()
    audit_event("TASK_RUN", user_id=owner, decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"changed": len(changed)})
    return {"code": 200, "data": changed}


@router.get("/api/v1/tasks/{task_id}")
def get_task(task_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    request_id = _request_id(x_request_id)
    actor = _get_actor(x_user_id, x_user_id, action="TASK_QUERY", request_id=request_id)
    task = TASK_STORE.get(sanitize_text(task_id, field="任务编号", max_length=100), owner_id=actor)
    if not task:
        raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "任务不存在"})
    return {"code": 200, "data": task.to_dict()}


@router.get("/api/v1/mock-merchants/orders/{order_id}")
def get_mock_merchant_order(order_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    actor = _get_actor(x_user_id, x_user_id, action="MOCK_ORDER_QUERY", request_id=_request_id(x_request_id))
    safe_id = sanitize_text(order_id, field="模拟订单编号", max_length=100)
    with STATE_LOCK:
        order = next((row for row in MOCK_ORDERS if row.get("mock_order_id") == safe_id and row.get("user_id") == actor), None)
        if not order:
            raise HTTPException(status_code=404, detail={"code": "MOCK_ORDER_NOT_FOUND", "message": "订单不存在"})
        result = dict(order)
    audit_event("MOCK_ORDER_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=_request_id(x_request_id), metadata={"order_id": safe_id})
    return {"code": 200, "data": result}


def _task_settlement_context(task: Any, *, target_id: str, amount: float, card_id: Optional[str] = None) -> dict[str, Any]:
    payload = task.payload
    return {
        "task_id": task.task_id,
        "task_updated_at": task.updated_at,
        "schedule_id": payload.get("schedule_id"),
        "owner_id": task.owner_id,
        "target_id": target_id,
        "amount_cents": amount_to_cents(amount),
        "card_id": payload.get("card_id") if card_id is None else card_id,
        "scheduled_at": payload.get("scheduled_at"),
        "lock_id": payload.get("lock_id"),
        "item": payload.get("item"),
    }


@router.post("/api/v1/tasks/{task_id}/precheck")
def task_precheck(task_id: str, req: TaskPrecheckRequest, request: Request = None, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(req.user_id, x_user_id, action="TASK_PRECHECK", request_id=request_id)
    safe_id = sanitize_text(task_id, field="任务编号", max_length=100)
    task = TASK_STORE.get(safe_id, owner_id=actor)
    if not task:
        raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "任务不存在"})
    if task.status != TaskState.WAITING_USER or task.task_type not in {"SCHEDULED_TRANSFER", "ORDER_DRAFT"}:
        raise HTTPException(status_code=409, detail={"code": "TASK_NOT_READY", "message": "任务当前不等待授权"})
    payload = task.payload
    amount = float(payload.get("amount", 0))
    if task.task_type == "SCHEDULED_TRANSFER":
        target_id, receiver = _resolve_target(payload.get("target"), from_user=actor)
        target_display = receiver.get("name", target_id)
        paying_card = _payment_card(actor, payload.get("card_id"))
        if paying_card is None:
            raise SecurityError("预约转账缺少可用付款卡", 409, "CARD_NOT_AVAILABLE")
        bound_card_id = str(paying_card.get("card_id") or "")
        # Persist the concrete choice on the task so the signed context cannot
        # silently fall back to a different default card at execution time.
        payload["card_id"] = bound_card_id
        schedule_row = next((row for row in SCHEDULED_TRANSFERS if row.get("schedule_id") == payload.get("schedule_id")), None)
        if schedule_row is not None and not schedule_row.get("card_id"):
            schedule_row["card_id"] = bound_card_id
        operation = "SCHEDULED_TRANSFER_SETTLE"
        risk = "YELLOW" if round(_daily_spent(actor) + _pending_transfer_amount(actor, exclude_schedule_id=payload.get("schedule_id")) + amount, 2) <= 1000 else "RED"
    else:
        target_id, target_display, operation, risk = f"__ORDER_DRAFT__:{task.task_id}", str(payload.get("item") or "provider-mock"), "ORDER_DRAFT_SETTLE", "YELLOW"
        if target_display not in MOCK_MERCHANT_CATALOG or amount_to_cents(amount) != quote_cents(target_display):
            raise SecurityError("模拟商户报价已变化", 409, "ORDER_QUOTE_CHANGED")
        lock = LOCKS.get(str(payload.get("lock_id") or ""))
        if not lock or lock.get("user_id") != actor or lock.get("status") != "LOCKED" or float(lock.get("amount", 0)) < amount:
            raise SecurityError("预留预算不足以订购商品", 409, "ORDER_BUDGET_UNAVAILABLE")
    context = _task_settlement_context(task, target_id=target_id, amount=amount, card_id=bound_card_id if task.task_type == "SCHEDULED_TRANSFER" else None)
    context_hash = _context_hash(context)
    required = ["USER_CLICK_CONFIRM"] if risk == "YELLOW" else ["USER_CLICK_CONFIRM", "SMS_OTP", "FACE_RECOGNITION"]
    idem_payload = {"task_id": safe_id, "user_id": actor, "context_hash": context_hash}
    cached = _idempotency_begin(actor, "task_precheck", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    token, token_payload = issue_auth_token(from_user=actor, target_id=target_id, target_display=target_display, amount=amount, remark=f"任务结算:{safe_id}", risk_level=risk, required_actions=required, operation=operation, context_hash=context_hash, session_token=_presented_session(request))
    response = {"code": 200, "risk_level": risk, "operation": operation, "auth_token": token, "token_type": "Bearer-Auth-Nonce", "expires_at": token_payload["exp"], "required_actions": required, "mfa_required": risk == "RED", "task_id": safe_id, "context_hash": context_hash, "amount": amount, "item": payload.get("item")}
    _idempotency_complete(actor, "task_precheck", idempotency_key, idem_payload, response)
    audit_event("TASK_PRECHECK", user_id=actor, risk_level=risk, decision="CHALLENGE", outcome="TOKEN_ISSUED", request_id=request_id, metadata={"task_id": safe_id, "jti": token_payload["jti"]})
    return response


@router.post("/api/v1/tasks/{task_id}/execute")
def execute_task(task_id: str, req: TaskExecuteRequest, request: Request = None, x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"), x_mfa_proof: Optional[str] = Header(None, alias="X-MFA-Proof"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(req.user_id, x_user_id, action="TASK_EXECUTE", request_id=request_id)
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(status_code=400, detail={"code": "IDEMPOTENCY_KEY_REQUIRED", "message": "任务执行需要 Idempotency-Key"})
    safe_id = sanitize_text(task_id, field="任务编号", max_length=100)
    idem_payload = {"task_id": safe_id, "user_id": actor}
    cached = _idempotency_begin(actor, "task_execute", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    token = req.auth_token or x_auth_token
    proof = req.mfa_proof or x_mfa_proof
    try:
        if not token:
            raise SecurityError("缺少安全授权凭证", 403, "TOKEN_REQUIRED")
        with _ledger_transaction():
            # Task payloads and account records must stay attached to the live
            # ledger from validation through debit/credit. Another rollback
            # can replace all of these objects while this lock is not held.
            task = TASK_STORE.get(safe_id, owner_id=actor)
            if not task:
                raise SecurityError("任务不存在", 404, "TASK_NOT_FOUND")
            if task.status != TaskState.WAITING_USER or task.task_type not in {"SCHEDULED_TRANSFER", "ORDER_DRAFT"}:
                raise SecurityError("任务当前不等待授权", 409, "TASK_NOT_READY")
            payload = task.payload
            if task.task_type == "SCHEDULED_TRANSFER":
                target_id, receiver = _resolve_target(payload.get("target"), from_user=actor)
                amount = float(payload.get("amount", 0))
                bound_card_id = payload.get("card_id")
                schedule = next((row for row in SCHEDULED_TRANSFERS if row.get("schedule_id") == payload.get("schedule_id")), None)
                if not schedule or any(schedule.get(key) != payload.get(key) for key in ("schedule_id", "target", "amount", "card_id", "scheduled_at")):
                    raise SecurityError("预约任务详情已变化，请重新预检", 409, "SCHEDULE_CONTEXT_CHANGED")
                operation = "SCHEDULED_TRANSFER_SETTLE"
                current_risk = "YELLOW" if round(_daily_spent(actor) + _pending_transfer_amount(actor, exclude_schedule_id=payload.get("schedule_id")) + amount, 2) <= 1000 else "RED"
            else:
                target_id, amount, operation, current_risk = f"__ORDER_DRAFT__:{task.task_id}", float(payload.get("amount", 0)), "ORDER_DRAFT_SETTLE", "YELLOW"
                if str(payload.get("item") or "") not in MOCK_MERCHANT_CATALOG or amount_to_cents(amount) != quote_cents(str(payload.get("item") or "")):
                    raise SecurityError("模拟商户报价已变化", 409, "ORDER_QUOTE_CHANGED")
            context_hash = _context_hash(_task_settlement_context(task, target_id=target_id, amount=amount, card_id=bound_card_id if task.task_type == "SCHEDULED_TRANSFER" else None))
            token_payload, _ = inspect_auth_token(token)
            if token_payload.get("risk_level") == "YELLOW" and current_risk == "RED":
                raise SecurityError("风险等级已升高，请重新授权", 409, "RISK_CHANGED_REAUTHORIZE")
            with authorize_execution(token, from_user=actor, target_id=target_id, amount=amount, remark=f"任务结算:{safe_id}", operation=operation, context_hash=context_hash, mfa_proof=proof, presented_session=_presented_session(request), request_id=request_id):
                running = TASK_STORE.transition_if_status(safe_id, TaskState.WAITING_USER, TaskState.RUNNING, owner_id=actor)
                if running is None:
                    raise SecurityError("任务已被取消或正在执行", 409, "TASK_STATE_CHANGED")
                if task.task_type == "ORDER_DRAFT":
                    lock = LOCKS.get(str(payload.get("lock_id") or ""))
                    account = ACCOUNTS[actor]
                    if not lock or lock.get("user_id") != actor or lock.get("status") != "LOCKED" or float(lock.get("amount", 0)) < amount or float(account.get("locked_amount", 0)) < amount:
                        raise SecurityError("预留预算不足以订购商品", 409, "ORDER_BUDGET_UNAVAILABLE")
                    if float(account["balance"]) < amount:
                        raise SecurityError("账户余额不足", 400, "INSUFFICIENT_FUNDS")
                    # A merchant order is an online card payment.
                    paying_card = _charge_card_and_balance(actor, amount, channel="ONLINE")
                    order = place_order(user_id=actor, task_id=safe_id, item=str(payload["item"]), amount_cents=amount_to_cents(amount))
                    account["balance"] = round(float(account["balance"]) - amount, 2)
                    account["locked_amount"] = round(float(account["locked_amount"]) - amount, 2)
                    lock["amount"] = round(float(lock["amount"]) - amount, 2)
                    if lock["amount"] == 0:
                        lock["status"] = "SPENT"
                    _record_card_spend(actor, amount, card=paying_card)
                    MOCK_ORDERS.append(order)
                    TRANSACTIONS.append({"tx_id": f"TXN{uuid.uuid4().hex[:12].upper()}", "account_id": actor, "merchant": "本地模拟商户", "category": "跨场景订购", "amount": amount, "remark": f"{payload['item']} {order['mock_order_id']}", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                    payload["mock_order_id"] = order["mock_order_id"]
                    result = {"execution_mode": "MOCK_MERCHANT", "funds_debited": True, "mock_order_id": order["mock_order_id"], "order_status": "PLACED", "item": payload["item"], "amount": amount}
                else:
                    schedule = next((row for row in SCHEDULED_TRANSFERS if row.get("schedule_id") == payload.get("schedule_id")), None)
                    if not schedule or schedule.get("status") not in {"WAITING_USER", "SCHEDULED"}:
                        raise SecurityError("预约转账状态已变化", 409, "SCHEDULE_CONTEXT_CHANGED")
                    paying_card = _charge_card_and_balance(actor, amount, card_id=payload.get("card_id"))
                    account = ACCOUNTS[actor]
                    if account["balance"] - account["locked_amount"] < amount:
                        raise SecurityError("可用余额不足（部分资金已被锁定）", 400, "INSUFFICIENT_FUNDS")
                    account["balance"] = round(account["balance"] - amount, 2)
                    receiver["balance"] = round(receiver["balance"] + amount, 2)
                    DAILY_TRANSFERS[actor] = round(_daily_spent(actor) + amount, 2)
                    _record_card_spend(actor, amount, card=paying_card)
                    txn_id = f"TXN{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:4].upper()}"
                    TRANSACTIONS.append({"tx_id": txn_id, "account_id": actor, "merchant": f"转账给 {receiver['name']}", "category": "转账支出", "amount": amount, "remark": "预约转账", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                    schedule["status"] = "SETTLED"
                    schedule["settled_at"] = datetime.now().isoformat()
                    result = {"execution_mode": "LEDGER", "transaction_id": txn_id, "transferred_amount": amount, "sender_balance": account["balance"]}
                TASK_STORE.transition(safe_id, TaskState.SUCCEEDED, owner_id=actor)
            response = {"code": 200, "status": "SUCCESS", "task_id": safe_id, **result}
            _idempotency_complete(actor, "task_execute", idempotency_key, idem_payload, response)
            persist_ledger()
        audit_event("TASK_EXECUTE", user_id=actor, decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"task_id": safe_id})
        return response
    except SecurityError as exc:
        _idempotency_abort(actor, "task_execute", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="TASK_EXECUTE", request_id=request_id)
    except Exception as exc:
        _idempotency_abort(actor, "task_execute", idempotency_key)
        raise HTTPException(status_code=500, detail={"code": "CORE_ERROR", "message": "任务执行失败，请稍后重试"}) from exc


def _task_action(task_id: str, action: str, req: TaskActionRequest, x_user_id: Optional[str], x_request_id: Optional[str]):
    # Task state and its schedule/split reservation form one ledger mutation.
    # Hold the same lock used by settlement so cancellation cannot win only
    # half of that operation or be snapshotted between those changes.
    request_id = _request_id(x_request_id)
    with _ledger_transaction():
        response = _task_action_locked(task_id, action, req, x_user_id, request_id)
        try:
            persist_ledger()
        except Exception:
            audit_event(action, user_id=x_user_id, decision="BLOCK", outcome="PERSISTENCE_FAILED_ROLLBACK",
                        request_id=request_id, metadata={"task_id": task_id})
            raise
        return response


def _task_action_locked(task_id: str, action: str, req: TaskActionRequest, x_user_id: Optional[str], x_request_id: Optional[str]):
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    request_id = _request_id(x_request_id)
    actor = _get_actor(x_user_id, x_user_id, action=action, request_id=request_id)
    try:
        safe_id = sanitize_text(task_id, field="任务编号", max_length=100)
        reason = sanitize_text(req.reason, field="操作原因", max_length=300)
        if action == "TASK_CANCEL":
            task = cancel_task_branch(actor, safe_id)
        else:
            task = TASK_STORE.get(safe_id, owner_id=actor)
            if task is None:
                raise KeyError(safe_id)
            if task.status not in {TaskState.PENDING, TaskState.WAITING_USER, TaskState.FAILED, TaskState.HUMAN_TAKEOVER}:
                raise ValueError("task cannot be taken over in its current state")
            # Pause the entire dependent branch. A takeover must not leave
            # sibling orders sharing this reservation eligible for execution.
            paused = {safe_id}
            lock_id = task.payload.get("lock_id")
            while True:
                newly_paused = []
                for dependent in TASK_STORE.list(owner_id=actor):
                    if dependent.task_id in paused:
                        continue
                    related = bool(set(dependent.depends_on) & paused) or bool(lock_id and dependent.payload.get("lock_id") == lock_id)
                    if related and dependent.status == TaskState.RUNNING:
                        raise ValueError("related task is currently running; reconcile before takeover")
                    if related and dependent.status in {TaskState.PENDING, TaskState.WAITING_USER, TaskState.FAILED}:
                        newly_paused.append(dependent)
                if not newly_paused:
                    break
                for dependent in newly_paused:
                    paused.add(dependent.task_id)
            # Validate every node first so a rejection cannot partially pause
            # the branch. The enclosing STATE_LOCK also serializes execution.
            for paused_id in paused:
                TASK_STORE.takeover(paused_id, owner_id=actor, reason=reason)
    except (SecurityError, KeyError, ValueError) as exc:
        if isinstance(exc, SecurityError):
            _record_failure_and_raise(exc, user_id=actor, action=action, request_id=request_id)
        raise HTTPException(status_code=409 if isinstance(exc, ValueError) else 404, detail={"code": "TASK_STATE_INVALID", "message": str(exc)})
    audit_event(action, user_id=actor, decision="ALLOW", outcome=task.status, request_id=request_id, metadata={"task_id": task.task_id, "reason": reason})
    return {"code": 200, "data": task.to_dict()}


@router.post("/api/v1/tasks/{task_id}/cancel")
def cancel_task(task_id: str, req: Optional[TaskActionRequest] = None, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    return _task_action(task_id, "TASK_CANCEL", req or TaskActionRequest(), x_user_id, x_request_id)


@router.post("/api/v1/tasks/{task_id}/takeover")
def takeover_task(task_id: str, req: Optional[TaskActionRequest] = None, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    return _task_action(task_id, "TASK_TAKEOVER", req or TaskActionRequest(), x_user_id, x_request_id)
