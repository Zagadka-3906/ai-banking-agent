"""YELLOW/RED execution gates: transfers, fund locks and generic operations."""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request

from bank_core.undo import release_locks, undo_candidate, undo_task
from bank_core.access import (
    _get_actor,
    _presented_session,
    _record_failure_and_raise,
    _request_id,
)
from bank_core.cards import (
    _card_summary,
    card_channels,
    CARD_MAX_DAILY_LIMIT,
    CARD_TRANSITIONS,
    _card_source_states,
    _issue_card_locked,
    _select_card,
)
from bank_core.idempotency import (
    _idempotency_abort,
    _idempotency_begin,
    _idempotency_complete,
    _idempotency_completed_request,
    _idempotency_fingerprint,
)
from bank_core.ledger import (
    ACCOUNTS,
    DAILY_TRANSFERS,
    DISPUTE_CASES,
    LOCKS,
    SCHEDULED_TRANSFERS,
    SPLIT_REQUESTS,
    SUBSCRIPTIONS,
    TRANSACTIONS,
    _ledger_transaction,
)
from bank_core.models import (
    LockFundRequest,
    OperationExecuteRequest,
    TransferExecuteRequest,
    UnlockFundRequest,
)
from bank_core.operations import (
    _card_channel_args,
    _BEHAVIOR_OPERATION_SIGNALS,
    GENERIC_OPERATION_RISK,
    _birthday_times,
    _context_hash,
    _ensure_product_suitable,
    _fraud_transaction,
    _normalize_datetime,
    _operation_amount,
    _operation_context,
    _operation_target,
    _resolve_subscription,
    _validate_operation_name,
    _validate_split_participants,
)
from bank_core.payments import (
    _charge_card_and_balance,
    _daily_spent,
    _ensure_account,
    _prepare_split_shares,
    _record_card_spend,
    _record_split_payment,
    _resolve_target,
    _split_id_from_remark,
)
from bank_core.persistence import (
    persist_ledger,
)
from bank_core.routes.subscriptions import (
    _subscription_query_result,
)
from bank_core.wealth_data import (
    WEALTH_HOLDINGS,
    WEALTH_PRODUCTS,
    WEALTH_RISK_PROFILES,
)
from mock_merchant import quote_cents
from notification_outbox import enqueue as enqueue_notification
from scheduler import TASK_STORE, TaskState
from security import (
    SecurityError,
    amount_to_cents,
    audit_event,
    authorize_execution,
    behavior_large_amount_cents,
    hash_password,
    record_behavior_signal,
    record_security_success,
    revoke_sessions_for,
    sanitize_text,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Yellow/red execution gates
# ---------------------------------------------------------------------------
@router.post("/api/v1/transfers/execute")
def execute_transfer(req: TransferExecuteRequest, request: Request = None, x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"), x_mfa_proof: Optional[str] = Header(None, alias="X-MFA-Proof"), x_user_confirmation: Optional[str] = Header(None, alias="X-User-Confirmation"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(req.from_user, x_user_id, action="TRANSFER_EXECUTE", request_id=request_id)
    token = x_auth_token
    # Do not trust a boolean supplied by an agent/browser.  Only
    # ``/risk/confirm`` (which validates the signed nonce) can set the
    # server-side ``record.confirmed`` bit.  The header is retained solely for
    # correlation/legacy clients and is deliberately ignored for authorization.
    confirmation = False
    proof = req.mfa_proof or x_mfa_proof
    idem_payload = {"from_user": req.from_user, "target": req.target_name_or_id, "amount": req.amount, "remark": req.remark or "智能转账", "card_id": req.card_id}
    cached = _idempotency_begin(actor, "transfer_execute", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    try:
        remark = sanitize_text(req.remark or "智能转账", field="转账备注", max_length=200)
        if not token:
            raise SecurityError("缺少安全授权凭证，严禁直接执行", 403, "TOKEN_REQUIRED")
        with _ledger_transaction():
            # Rollback restores ledger records with fresh objects. Resolve
            # both accounts under the transaction lock so no failed parallel
            # request can leave this settlement holding a detached payee.
            _ensure_account(req.from_user)
            target_id, receiver = _resolve_target(req.target_name_or_id, from_user=req.from_user)
            with authorize_execution(token, from_user=req.from_user, target_id=target_id, amount=req.amount, remark=remark, confirmation=confirmation, mfa_proof=proof, operation="TRANSFER", presented_session=_presented_session(request), request_id=request_id) as (payload, _record):
                sender = ACCOUNTS[req.from_user]
                amount = amount_to_cents(req.amount) / 100.0
                # The paying card's status and daily limit are enforced on
                # settlement, and the same card records the spend.
                paying_card = _charge_card_and_balance(req.from_user, amount, card_id=req.card_id)
                available = sender["balance"] - sender["locked_amount"]
                if available < amount:
                    raise SecurityError("可用余额不足（部分资金已被锁定）", 400, "INSUFFICIENT_FUNDS")
                sender["balance"] = round(sender["balance"] - amount, 2)
                receiver["balance"] = round(receiver["balance"] + amount, 2)
                DAILY_TRANSFERS[req.from_user] = round(_daily_spent(req.from_user) + amount, 2)
                _record_card_spend(req.from_user, amount, card=paying_card)
                if int(round(amount * 100)) >= behavior_large_amount_cents():
                    record_behavior_signal(req.from_user, "LARGE_TRANSFER", metadata={"amount_cents": int(round(amount * 100)), "target_id": target_id}, request_id=request_id)
                txn_id = f"TXN{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:4].upper()}"
                TRANSACTIONS.append({"tx_id": txn_id, "account_id": req.from_user, "merchant": f"转账给 {receiver['name']}", "category": "转账支出", "amount": amount, "remark": remark, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                split_id = _split_id_from_remark(remark)
                if split_id:
                    _record_split_payment(split_id=split_id, payer_id=req.from_user, recipient_id=target_id, amount=amount, transaction_id=txn_id)
                    TRANSACTIONS.append({"tx_id": f"TXN{uuid.uuid4().hex[:12].upper()}", "account_id": target_id, "merchant": f"AA 收款 - {sender['name']}", "category": "AA 收款", "amount": amount, "remark": remark, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                    enqueue_notification(f"AA_PAID:{split_id}:{req.from_user}", owner_id=target_id, recipient_id=req.from_user, split_id=split_id, amount=amount, outcome="COLLECTED", message=f"AA 份额已支付 {amount:.2f} 元")
                risk_level = payload.get("risk_level")
        record_security_success(actor)
    except SecurityError as exc:
        _idempotency_abort(actor, "transfer_execute", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="TRANSFER_EXECUTE", request_id=request_id)
    except Exception as exc:
        _idempotency_abort(actor, "transfer_execute", idempotency_key)
        audit_event("TRANSFER_EXECUTE", user_id=actor, decision="ERROR", outcome="FAILURE", reason="INTERNAL_ERROR", request_id=request_id)
        raise HTTPException(status_code=500, detail={"code": "CORE_ERROR", "message": "核心记账失败，请稍后重试"}) from exc
    audit_event("TRANSFER_EXECUTE", user_id=actor, risk_level=risk_level, decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"transaction_id": txn_id, "target_id": target_id, "amount_cents": int(round(amount * 100))})
    response = {"code": 200, "status": "SUCCESS", "transaction_id": txn_id, "transferred_amount": amount, "sender_balance": ACCOUNTS[req.from_user]["balance"], "receiver_name": receiver["name"], "risk_level": risk_level}
    _idempotency_complete(actor, "transfer_execute", idempotency_key, idem_payload, response)
    persist_ledger()
    return response


@router.post("/api/v1/accounts/lock_fund")
def lock_fund(req: LockFundRequest, request: Request = None, x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"), x_mfa_proof: Optional[str] = Header(None, alias="X-MFA-Proof"), x_user_confirmation: Optional[str] = Header(None, alias="X-User-Confirmation"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(req.user_id, x_user_id, action="LOCK_FUND", request_id=request_id)
    # ``user_confirmed`` and ``X-User-Confirmation`` are advisory input only;
    # authorization_execution checks the confirmation bit set by the signed
    # nonce confirmation endpoint.
    confirmation = False
    token = x_auth_token or req.auth_token
    proof = x_mfa_proof or req.mfa_proof
    idem_payload = {"user_id": req.user_id, "amount": req.amount, "reason": req.reason, "event_at": req.event_at, "reminder_at": req.reminder_at}
    cached = _idempotency_begin(actor, "lock_fund", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    try:
        reason = sanitize_text(req.reason, field="锁定原因", max_length=300)
        event_at, reminder_at = _birthday_times(req.event_at, req.reminder_at)
        if not token:
            raise SecurityError("资金锁定需要先完成风险预检并确认", 403, "TOKEN_REQUIRED")
        with _ledger_transaction():
            account = _ensure_account(req.user_id)
            context_hash = _context_hash({"event_at": event_at, "reminder_at": reminder_at})
            with authorize_execution(token, from_user=req.user_id, target_id="__LOCK_FUND__", amount=req.amount, remark=reason, confirmation=confirmation, mfa_proof=proof, operation="LOCK_FUND", context_hash=context_hash, presented_session=_presented_session(request), request_id=request_id) as (_payload, _record):
                amount = amount_to_cents(req.amount) / 100.0
                if account["balance"] - account["locked_amount"] < amount:
                    raise SecurityError("可用余额不足以锁定此金额", 400, "INSUFFICIENT_FUNDS")
                account["locked_amount"] = round(account["locked_amount"] + amount, 2)
                remaining = round(account["balance"] - account["locked_amount"], 2)
                lock_id = f"LCK{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}"
                LOCKS[lock_id] = {"lock_id": lock_id, "user_id": req.user_id, "amount": amount, "reason": reason, "event_at": event_at, "reminder_at": reminder_at, "status": "LOCKED", "created_at": datetime.now().isoformat()}
                task = TASK_STORE.create(actor, "FUND_RESERVATION", {"lock_id": lock_id, "amount": amount, "reason": reason, "event_at": event_at, "reminder_at": reminder_at}, compensation="UNLOCK_FUND", idempotency_key=f"lock:{lock_id}")
                TASK_STORE.transition(task.task_id, TaskState.SUCCEEDED, owner_id=actor)
                reminder_task = None
                order_task_ids: list[str] = []
                if event_at and reminder_at:
                    reminder_task = TASK_STORE.create(actor, "REMINDER", {"lock_id": lock_id, "message": "生日前 2 天提醒订购鲜花与蛋糕", "event_at": event_at, "reminder_at": reminder_at}, run_at=reminder_at, depends_on=[task.task_id], idempotency_key=f"reminder:{lock_id}")
                    # Orders wait for a fresh approval at the due time. Only
                    # an allow-listed local merchant can receive mock funds.
                    for item in ("鲜花", "蛋糕"):
                        order_task = TASK_STORE.create(
                            actor,
                            "ORDER_DRAFT",
                            {"lock_id": lock_id, "item": item, "amount": quote_cents(item) / 100, "event_at": event_at, "reminder_at": reminder_at},
                            run_at=reminder_at,
                            depends_on=[task.task_id],
                            compensation="CANCEL_ORDER_DRAFT",
                            idempotency_key=f"order-draft:{lock_id}:{item}",
                        )
                        order_task_ids.append(order_task.task_id)
        if int(round(amount * 100)) >= behavior_large_amount_cents():
            record_behavior_signal(req.user_id, "LARGE_LOCK", metadata={"amount_cents": int(round(amount * 100)), "lock_id": lock_id}, request_id=request_id)
        record_security_success(actor)
    except SecurityError as exc:
        _idempotency_abort(actor, "lock_fund", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="LOCK_FUND", request_id=request_id)
    except Exception as exc:
        _idempotency_abort(actor, "lock_fund", idempotency_key)
        audit_event("LOCK_FUND", user_id=actor, decision="ERROR", outcome="FAILURE", reason="INTERNAL_ERROR", request_id=request_id)
        raise HTTPException(status_code=500, detail={"code": "CORE_ERROR", "message": "核心记账失败，请稍后重试"}) from exc
    audit_event("LOCK_FUND", user_id=actor, risk_level=_payload.get("risk_level", "YELLOW"), decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"amount_cents": int(round(amount * 100)), "reason": reason, "lock_id": lock_id})
    response = {"code": 200, "status": "LOCKED", "lock_id": lock_id, "task_id": task.task_id, "reminder_task_id": reminder_task.task_id if reminder_task else None, "order_task_ids": order_task_ids, "event_at": event_at, "reminder_at": reminder_at, "locked_amount": account["locked_amount"], "remaining_available": remaining, "reason": reason}
    _idempotency_complete(actor, "lock_fund", idempotency_key, idem_payload, response)
    persist_ledger()
    return response


@router.post("/api/v1/accounts/unlock_fund")
@router.post("/api/v1/accounts/rollback_lock")
def unlock_fund(req: UnlockFundRequest, request: Request = None, x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"), x_mfa_proof: Optional[str] = Header(None, alias="X-MFA-Proof"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    """Release reserved funds through the same confirmation gate.

    A lock id, when supplied, must belong to the authenticated user.  Without
    one, the oldest matching locks are released FIFO; the signed precheck
    token binds the amount and reason.
    """
    request_id = _request_id(x_request_id)
    actor = _get_actor(req.user_id, x_user_id, action="UNLOCK_FUND", request_id=request_id)
    token = x_auth_token or req.auth_token
    proof = x_mfa_proof or req.mfa_proof
    idem_payload = {"user_id": req.user_id, "amount": req.amount, "lock_id": req.lock_id, "reason": req.reason}
    cached = _idempotency_begin(actor, "unlock_fund", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    try:
        reason = sanitize_text(req.reason or "解除资金隔离", field="解除原因", max_length=300)
        if not token:
            raise SecurityError("解除资金隔离需要先完成风险预检并确认", 403, "TOKEN_REQUIRED")
        lock_id = sanitize_text(req.lock_id, field="锁定编号", max_length=100) if req.lock_id else None
        target = lock_id or "__UNLOCK_FUND__"
        with _ledger_transaction():
            account = _ensure_account(req.user_id)
            candidates = sorted(
                [v for v in LOCKS.values() if v.get("user_id") == req.user_id and v.get("status") == "LOCKED" and (not lock_id or v.get("lock_id") == lock_id)],
                key=lambda row: row.get("created_at", ""),
            )
            requested_amount = amount_to_cents(req.amount) / 100.0
            if lock_id and (not candidates or float(candidates[0].get("amount", 0)) < requested_amount):
                raise SecurityError("锁定编号或可解除金额不匹配", 400, "LOCK_NOT_FOUND")
            if not lock_id and sum(float(v.get("amount", 0)) for v in candidates) < requested_amount:
                raise SecurityError("可解除的锁定金额不足", 400, "LOCK_NOT_FOUND")
            with authorize_execution(token, from_user=req.user_id, target_id=target, amount=req.amount, remark=reason, confirmation=False, mfa_proof=proof, operation="UNLOCK_FUND", presented_session=_presented_session(request), request_id=request_id) as (_payload, _record):
                released_ids = release_locks(actor, req.user_id, candidates, requested_amount)
                remaining = round(float(account["balance"]) - float(account["locked_amount"]), 2)
        record_security_success(actor)
    except SecurityError as exc:
        _idempotency_abort(actor, "unlock_fund", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="UNLOCK_FUND", request_id=request_id)
    except Exception as exc:
        _idempotency_abort(actor, "unlock_fund", idempotency_key)
        audit_event("UNLOCK_FUND", user_id=actor, decision="ERROR", outcome="FAILURE", reason="INTERNAL_ERROR", request_id=request_id)
        raise HTTPException(status_code=500, detail={"code": "CORE_ERROR", "message": "解除资金失败，请稍后重试"}) from exc
    audit_event("UNLOCK_FUND", user_id=actor, risk_level=_payload.get("risk_level", "YELLOW"), decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"amount_cents": int(round(requested_amount * 100)), "lock_ids": released_ids})
    response = {"code": 200, "status": "RELEASED", "released_amount": requested_amount, "lock_ids": released_ids, "locked_amount": account["locked_amount"], "remaining_available": remaining}
    _idempotency_complete(actor, "unlock_fund", idempotency_key, idem_payload, response)
    persist_ledger()
    return response


@router.post("/api/v1/operations/execute")
@router.post("/api/v1/transfers/schedule")
@router.post("/api/v1/transfers/split")
@router.post("/api/v1/subscriptions/cancel")
@router.post("/api/v1/cards/apply")
@router.post("/api/v1/cards/limit")
@router.post("/api/v1/cards/freeze")
@router.post("/api/v1/cards/unfreeze")
@router.post("/api/v1/cards/report-lost")
@router.post("/api/v1/cards/unreport-lost")
@router.post("/api/v1/wealth/purchase")
@router.post("/api/v1/wealth/redeem")
@router.post("/api/v1/auth/password/change")
def execute_operation(
    req: OperationExecuteRequest,
    x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"),
    x_mfa_proof: Optional[str] = Header(None, alias="X-MFA-Proof"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    user_id: Optional[str] = None,
    request: Request = None,
):
    """Execute an allow-listed card/wealth/subscription/task operation.

    A single endpoint keeps the policy boundary uniform.  Convenience routes
    in clients may map to this endpoint, but no operation can bypass the
    signed nonce and (for red actions) the MFA proof.
    """

    request_id = _request_id(x_request_id)
    if user_id and user_id != req.user_id:
        raise HTTPException(status_code=403, detail={"code": "IDENTITY_MISMATCH", "message": "路径用户与请求用户不一致"})
    if request is not None:
        path_operation = {
            "/cards/apply": "CARD_APPLY",
            "/cards/limit": "CARD_LIMIT",
            "/cards/freeze": "CARD_FREEZE",
            "/cards/unfreeze": "CARD_UNFREEZE",
            "/cards/report-lost": "CARD_REPORT_LOST",
            "/cards/unreport-lost": "CARD_UNREPORT_LOST",
            "/wealth/purchase": "WEALTH_PURCHASE",
            "/wealth/redeem": "WEALTH_REDEEM",
            "/auth/password/change": "PASSWORD_CHANGE",
            "/transfers/schedule": "SCHEDULED_TRANSFER",
            "/transfers/split": "SPLIT_BILL",
            "/subscriptions/cancel": "SUBSCRIPTION_CANCEL",
        }
        matched_operation = None
        for suffix, operation_name in path_operation.items():
            if request.url.path.endswith(suffix):
                matched_operation = operation_name
                break
        if matched_operation:
            supplied_operation = str(req.operation or "").strip().upper()
            if supplied_operation and supplied_operation != matched_operation:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "OPERATION_ROUTE_MISMATCH", "message": "请求路径与操作类型不一致"},
                )
            req.operation = matched_operation
    actor = _get_actor(req.user_id, x_user_id, action="OPERATION_EXECUTE", request_id=request_id)
    # Credential fields authorize the first execution, not a read of its
    # completed receipt. Never store credential material in replay metadata.
    submitted_intent = req.model_dump(exclude={"auth_token", "mfa_proof", "user_confirmed", "new_password"})
    submitted_intent["operation"] = str(req.operation or "").strip().upper()
    # Password rotation retains its existing authorization/context path; the
    # new submitted-intent replay shortcut must not equate different secrets.
    request_fingerprint = None if submitted_intent["operation"] == "PASSWORD_CHANGE" else _idempotency_fingerprint(submitted_intent)
    if request_fingerprint is not None:
        cached = _idempotency_completed_request(actor, "operation_execute", idempotency_key, request_fingerprint, request_id=request_id)
        if cached is not None:
            return cached
    token = x_auth_token or req.auth_token
    proof = x_mfa_proof or req.mfa_proof
    operation_risk = GENERIC_OPERATION_RISK.get(str(req.operation or "").upper(), "UNKNOWN")
    resolved_subscription_id: Optional[str] = None
    fraud_tx: Optional[dict[str, Any]] = None
    try:
        operation = _validate_operation_name(req.operation)
        _ensure_account(req.user_id)
        target = _operation_target(req)
        card_channel, channel_enabled = _card_channel_args(operation, req.channel, req.enable)
        if operation == "UNDO_TASK":
            target = undo_candidate(req.user_id, sanitize_text(req.target or "", field="任务编号", max_length=100)).task_id
        if operation == "FRAUD_PROTECT":
            fraud_tx = _fraud_transaction(req.user_id, req.transaction_id)
            target = str(fraud_tx["card_id"])
            if req.transaction_snapshot_hash and req.transaction_snapshot_hash != _context_hash(fraud_tx):
                raise SecurityError("交易信息已变化，请重新预检", 409, "TRANSACTION_CONTEXT_CHANGED")
        if operation == "PASSWORD_CHANGE" and any(value for value in (req.target, req.product_id, req.card_id, req.merchant)):
            raise SecurityError("密码修改不接受外部目标编号", 400, "TARGET_NOT_ALLOWED")
        amount_input = req.new_limit if operation == "CARD_LIMIT" and req.amount in (None, 0) and req.new_limit is not None else req.amount
        amount = _operation_amount(amount_input, operation=operation)
        reason = sanitize_text(req.reason or ("客户否认境外消费，申请反诈保护" if operation == "FRAUD_PROTECT" else "智能银行操作"), field="操作原因", max_length=300)
        if operation in {"CARD_APPLY", "CARD_FREEZE", "CARD_UNFREEZE", "CARD_REPORT_LOST", "CARD_UNREPORT_LOST", "SUBSCRIPTION_QUERY", "SUBSCRIPTION_CANCEL", "PASSWORD_CHANGE", "FRAUD_PROTECT", "CARD_CHANNEL", "UNDO_TASK"} and amount_input not in (None, 0):
            raise SecurityError("该操作不接受金额字段", 400, "AMOUNT_NOT_ALLOWED")
        if operation == "CARD_LIMIT":
            effective_limit = req.new_limit if req.new_limit is not None else req.amount
            if effective_limit is None or not math.isfinite(float(effective_limit)) or float(effective_limit) <= 0:
                raise SecurityError("卡片额度必须为正数", 400, "INVALID_CARD_LIMIT")
        if operation == "WEALTH_PURCHASE" and req.user_id not in WEALTH_RISK_PROFILES:
            raise SecurityError("申购前请先完成风险评估", 428, "RISK_ASSESSMENT_REQUIRED")
        if operation.startswith("CARD_"):
            # CARD_APPLY opens a *new* card, so it is the one operation with no
            # card to resolve; everything else must name an unambiguous one.
            card = None if operation == "CARD_APPLY" else _select_card(req.user_id, req.card_id, allowed_states=_card_source_states(operation))
            if card:
                current_status = str(card.get("status") or "")
                # Same table the executor and the selector use, so a card can no
                # longer be validated against one copy of the rules and executed
                # against another.
                required_states = _card_source_states(operation)
                if required_states and current_status not in required_states:
                    raise SecurityError("卡片当前状态不允许该操作", 409, "INVALID_CARD_STATE")
            if operation != "CARD_APPLY" and card:
                target = req.card_id or card.get("card_id")
        if not token:
            raise SecurityError("该操作需要先完成风险预检", 403, "TOKEN_REQUIRED")
        validated_participants = _validate_split_participants(req.user_id, req.participants) if operation == "SPLIT_BILL" else list(req.participants or [])
        if operation == "SUBSCRIPTION_CANCEL":
            resolved_subscription_id = _resolve_subscription(req.user_id, req.target, req.merchant).get("subscription_id")
        operation_context = _operation_context(
            operation=operation,
            target=target,
            amount=amount,
            reason=reason,
            new_limit=req.new_limit,
            merchant=req.merchant,
            participants=validated_participants,
            product_ids=req.product_ids,
            risk_profile=(WEALTH_RISK_PROFILES.get(req.user_id) or {}).get("risk_profile") if operation == "WEALTH_PURCHASE" else None,
            subscription_id=resolved_subscription_id,
            scheduled_at=_normalize_datetime(req.scheduled_at, field="计划时间", require_future=True) if operation == "SCHEDULED_TRANSFER" else req.scheduled_at,
            transaction_id=fraud_tx.get("tx_id") if fraud_tx else req.transaction_id,
            transaction_snapshot_hash=_context_hash(fraud_tx) if fraud_tx else req.transaction_snapshot_hash,
            card_channel=card_channel,
            channel_enabled=channel_enabled,
        )
        context_hash = _context_hash(operation_context)
        idem_payload = {"user_id": req.user_id, "operation": operation, "context": operation_context}
        cached = _idempotency_begin(actor, "operation_execute", idempotency_key, idem_payload, request_id=request_id)
        if cached is not None:
            return cached
        with _ledger_transaction():
            with authorize_execution(token, from_user=req.user_id, target_id=target, amount=amount, remark=reason, confirmation=False, mfa_proof=proof, operation=operation, context_hash=context_hash, presented_session=_presented_session(request), request_id=request_id) as (authorization_payload, _record):
                operation_risk = authorization_payload.get("risk_level", operation_risk)
                account = ACCOUNTS[req.user_id]
                result: dict[str, Any] = {"operation": operation}
                if operation in {"WEALTH_PURCHASE", "WEALTH_REDEEM"}:
                    product_id = req.product_id or target
                    product = _ensure_product_suitable(req.user_id, product_id) if operation == "WEALTH_PURCHASE" else next((p for p in WEALTH_PRODUCTS if p["product_id"] == product_id), None)
                    if not product:
                        raise SecurityError("理财产品不存在", 404, "PRODUCT_NOT_FOUND")
                    if operation == "WEALTH_PURCHASE":
                        if amount < float(product["min_amount"]):
                            raise SecurityError("申购金额低于产品起购金额", 400, "MIN_AMOUNT_NOT_MET")
                        if account["balance"] - account["locked_amount"] < amount:
                            raise SecurityError("可用余额不足", 400, "INSUFFICIENT_FUNDS")
                        # A purchase is money leaving the account, so the card's
                        # daily payment limit applies here too.  Enforcing it
                        # only on transfers left an obvious second outflow that
                        # ignored the limit entirely.
                        paying_card = _charge_card_and_balance(req.user_id, amount, card_id=req.card_id)
                        account["balance"] = round(account["balance"] - amount, 2)
                        _record_card_spend(req.user_id, amount, card=paying_card)
                        holding = {"holding_id": f"HLD{uuid.uuid4().hex[:10].upper()}", "product_id": product_id, "product_name": product["name"], "principal": amount, "estimated_value": amount, "purchased_at": datetime.now().isoformat()}
                        WEALTH_HOLDINGS.setdefault(req.user_id, []).append(holding)
                        TRANSACTIONS.append({"tx_id": f"TXN{uuid.uuid4().hex[:12].upper()}", "account_id": req.user_id, "merchant": product["name"], "category": "理财申购", "amount": amount, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                        result.update({"status": "PURCHASED", "product": product, "holding": holding})
                    else:
                        holdings = WEALTH_HOLDINGS.setdefault(req.user_id, [])
                        # A product can have several purchase lots. Redeem the
                        # requested total across them in their existing order,
                        # atomically and in cents, without changing other products.
                        lots = [
                            (holding, int(round(float(holding.get("principal", 0)) * 100)))
                            for holding in holdings
                            if holding.get("product_id") == product_id and float(holding.get("principal", 0)) > 0
                        ]
                        remaining_cents = amount_to_cents(amount)
                        if sum(cents for _, cents in lots) < remaining_cents:
                            raise SecurityError("没有足够的持仓可赎回", 400, "HOLDING_NOT_FOUND")
                        for holding, principal_cents in lots:
                            if remaining_cents == 0:
                                break
                            redeemed_cents = min(principal_cents, remaining_cents)
                            residual_cents = principal_cents - redeemed_cents
                            if residual_cents == 0:
                                holdings.remove(holding)
                            else:
                                holding["principal"] = residual_cents / 100.0
                                if "estimated_value" in holding:
                                    holding["estimated_value"] = round(float(holding["estimated_value"]) * residual_cents / principal_cents, 2)
                            remaining_cents -= redeemed_cents
                        account["balance"] = round(account["balance"] + amount, 2)
                        result.update({"status": "REDEEMED", "product": product, "redeemed_amount": amount})
                elif operation == "CARD_APPLY":
                    # Same issuance path (and the same per-user ceiling) as the
                    # convenience endpoint - asking the assistant must not be a
                    # way around the wallet cap.
                    card = _issue_card_locked(req.user_id, card_type="VIRTUAL")
                    result.update({"status": "ISSUED", "card": card})
                elif operation == "CARD_LIMIT":
                    # ``amount`` is already normalized to cents before the
                    # authorization context is checked.  Persist that same
                    # canonical value so a sub-cent input cannot be approved
                    # as one value and stored with a different float rounding.
                    new_limit = amount
                    if not math.isfinite(float(new_limit)) or float(new_limit) <= 0:
                        raise SecurityError("卡片额度必须为正数", 400, "INVALID_CARD_LIMIT")
                    if float(new_limit) > CARD_MAX_DAILY_LIMIT:
                        raise SecurityError(
                            f"卡片日限额不得超过 {CARD_MAX_DAILY_LIMIT:.0f} 元",
                            400,
                            "CARD_LIMIT_OUT_OF_RANGE",
                        )
                    card = _select_card(req.user_id, req.card_id)
                    card["daily_limit"] = round(float(new_limit), 2)
                    result.update({"status": "LIMIT_UPDATED", "card": card})
                elif operation == "FRAUD_PROTECT":
                    tx = _fraud_transaction(req.user_id, operation_context.get("transaction_id"))
                    if operation_context.get("transaction_snapshot_hash") != _context_hash(tx) or target != tx.get("card_id"):
                        raise SecurityError("交易或卡片信息已变化，请重新预检", 409, "FRAUD_CONTEXT_CHANGED")
                    card = _select_card(req.user_id, str(tx["card_id"]), allowed_states={"ACTIVE", "FROZEN"})
                    if card.get("status") == "ACTIVE":
                        card["status"] = "FROZEN"
                    case = next((item for item in DISPUTE_CASES if item.get("owner_id") == req.user_id and item.get("transaction_id") == tx.get("tx_id") and item.get("status") in {"OPEN", "PENDING_REVIEW"}), None)
                    if case is None:
                        created_at = datetime.now(timezone.utc).isoformat()
                        case = {"case_id": f"CASE-{uuid.uuid4().hex[:10].upper()}", "owner_id": req.user_id, "transaction_id": tx["tx_id"], "transaction_snapshot_hash": operation_context["transaction_snapshot_hash"], "card_id": card["card_id"], "status": "PENDING_REVIEW", "protection_status": "CARD_PROTECTED", "statement": reason, "risk_reasons": ["境外交易", "交易类别标记为可疑交易", "用户明确否认该消费"], "created_at": created_at, "updated_at": created_at}
                        DISPUTE_CASES.append(case)
                    result.update({"status": "CARD_PROTECTED", "card": card, "dispute_case": case, "transaction": tx})
                elif operation in {"CARD_FREEZE", "CARD_UNFREEZE", "CARD_REPORT_LOST", "CARD_UNREPORT_LOST"}:
                    card = _select_card(req.user_id, req.card_id)
                    current_status = str(card.get("status") or "")
                    next_status = CARD_TRANSITIONS[operation].get(current_status)
                    if not next_status:
                        raise SecurityError("卡片当前状态不允许该操作", 409, "INVALID_CARD_STATE")
                    card["status"] = next_status
                    result.update({"status": next_status, "card": card})
                elif operation == "PASSWORD_CHANGE":
                    # Now that logins are password-based, "change my password"
                    # has to actually change it - otherwise the old credential
                    # keeps working while the audit trail claims a rotation.
                    # The new password is NOT bound into the signed approval
                    # context on purpose: that context is serialized into the
                    # authorization nonce the client holds, and credential
                    # material has no business inside a bearer token.  What
                    # authorizes the change is the single-use, MFA-gated nonce.
                    if not req.new_password:
                        raise SecurityError("修改密码需要提供新口令", 400, "NEW_PASSWORD_REQUIRED")
                    account["password_hash"] = hash_password(req.new_password)
                    current_version = int(account.get("credential_version", 1))
                    account["credential_version"] = current_version + 1
                    account["credential_rotated_at"] = datetime.now(timezone.utc).isoformat()
                    # A rotated credential must invalidate the sessions it was
                    # used to obtain - including this caller's, which is why the
                    # client is expected to re-login afterwards.
                    revoked = revoke_sessions_for(req.user_id, reason="password_changed", request_id=request_id)
                    result.update({"status": "PASSWORD_CHANGED", "credential_version": account["credential_version"], "revoked_sessions": revoked})
                elif operation == "SUBSCRIPTION_CANCEL":
                    subscriptions = SUBSCRIPTIONS.setdefault(req.user_id, [])
                    selected = _resolve_subscription(req.user_id, req.target, req.merchant)
                    if operation_context.get("subscription_id") and selected.get("subscription_id") != operation_context.get("subscription_id"):
                        raise SecurityError("订阅状态在确认后发生变化，请重新预检", 409, "SUBSCRIPTION_CONTEXT_CHANGED")
                    selected["status"] = "CANCELLED"
                    # A cancelled subscription must not keep nagging: withdraw
                    # its pending renewal reminders as part of the same step.
                    for queued in TASK_STORE.list(owner_id=actor):
                        if queued.task_type != "RENEWAL_REMINDER" or queued.payload.get("subscription_id") != selected.get("subscription_id"):
                            continue
                        if queued.status in {TaskState.PENDING, TaskState.WAITING_USER}:
                            try:
                                TASK_STORE.transition(queued.task_id, TaskState.CANCELLED, owner_id=actor)
                            except (KeyError, ValueError):
                                pass
                    result.update({"status": "CANCELLED", "subscription": selected})
                elif operation == "CARD_CHANNEL":
                    card = _select_card(req.user_id, req.card_id, allowed_states={"ACTIVE"})
                    card["channels"] = {**card_channels(card), card_channel: bool(channel_enabled)}
                    result.update({"status": "CHANNEL_UPDATED", "card": _card_summary(card), "channel": card_channel, "enabled": bool(channel_enabled)})
                elif operation == "UNDO_TASK":
                    result.update(undo_task(actor, target))
                elif operation == "SUBSCRIPTION_QUERY":
                    result.update(_subscription_query_result(req.user_id))
                elif operation == "SCHEDULED_TRANSFER":
                    scheduled_at = operation_context.get("scheduled_at")
                    schedule = {"schedule_id": f"SCH{uuid.uuid4().hex[:10].upper()}", "user_id": req.user_id, "target": req.target, "amount": amount, "card_id": req.card_id, "scheduled_at": scheduled_at, "status": "SCHEDULED"}
                    SCHEDULED_TRANSFERS.append(schedule)
                    # A signed nonce identifies one approved intent.  Use its
                    # jti for the internal task key so two deliberate,
                    # identical schedules are independent unless the caller
                    # supplies an HTTP Idempotency-Key; reusing only the
                    # context digest would alias distinct approvals and leave
                    # duplicate domain rows pointing at one task.
                    task = TASK_STORE.create(actor, "SCHEDULED_TRANSFER", schedule, run_at=scheduled_at, idempotency_key=f"schedule:{req.user_id}:{authorization_payload.get('jti')}")
                    result.update(schedule, task_id=task.task_id)
                elif operation == "SPLIT_BILL":
                    participants = list(operation_context.get("participants") or [])
                    # The initiator creates requests only. Each participant
                    # must authorize an ordinary transfer from their account.
                    collection = _prepare_split_shares(req.user_id, amount, participants, req.target)
                    split = {
                        "split_id": f"SPL{uuid.uuid4().hex[:10].upper()}",
                        "user_id": req.user_id,
                        "amount": amount,
                        "participants": participants,
                        "per_person": collection["participant_share"],
                        "initiator_share": collection["initiator_share"],
                        "participants_total": len(participants),
                        "target": req.target,
                        "status": collection["status"],
                        "collected_amount": collection["collected_amount"],
                        "outstanding_amount": collection["outstanding_amount"],
                        "collection": collection["collection"],
                        "created_at": collection["created_at"],
                    }
                    SPLIT_REQUESTS.append(split)
                    # Scope the task to this signed approval.  In particular,
                    # two AA requests for the same total but different
                    # participants must never share one task.
                    task = TASK_STORE.create(actor, "SPLIT_BILL", split, depends_on=[], idempotency_key=f"split:{req.user_id}:{authorization_payload.get('jti')}")
                    for item in collection["collection"]:
                        outcome = str(item.get("status") or "UNKNOWN")
                        enqueue_notification(f"AA:{split['split_id']}:{item['account_id']}", owner_id=actor, recipient_id=item["account_id"], split_id=split["split_id"], amount=item["share"], outcome=outcome, message=f"AA 待付款 {item['share']:.2f} 元；请用本人账户确认转账，备注 AA:{split['split_id']}")
                    result.update(split, task_id=task.task_id)
                else:  # guarded by _validate_operation_name; defensive fallback
                    raise SecurityError("操作未实现", 501, "OPERATION_NOT_IMPLEMENTED")
        behavior_signal = _BEHAVIOR_OPERATION_SIGNALS.get(operation)
        if behavior_signal:
            record_behavior_signal(actor, behavior_signal, metadata={"operation": operation}, request_id=request_id)
        record_security_success(actor)
    except SecurityError as exc:
        if 'idem_payload' in locals():
            _idempotency_abort(actor, "operation_execute", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="OPERATION_EXECUTE", request_id=request_id)
    except Exception as exc:
        if 'idem_payload' in locals():
            _idempotency_abort(actor, "operation_execute", idempotency_key)
        audit_event("OPERATION_EXECUTE", user_id=actor, decision="ERROR", outcome="FAILURE", reason="INTERNAL_ERROR", request_id=request_id)
        raise HTTPException(status_code=500, detail={"code": "CORE_ERROR", "message": "操作执行失败，请稍后重试"}) from exc
    response = {"code": 200, "status": "SUCCESS", "operation": operation, "data": result}
    audit_event("OPERATION_EXECUTE", user_id=actor, risk_level=operation_risk, decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"operation": operation, "target": target})
    _idempotency_complete(actor, "operation_execute", idempotency_key, idem_payload, response, request_fingerprint=request_fingerprint)
    persist_ledger()
    return response
