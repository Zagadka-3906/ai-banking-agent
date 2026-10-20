"""Risk prechecks, explicit confirmation, cancellation and MFA challenges."""

from __future__ import annotations

import math
from typing import Any, Optional

from fastapi import APIRouter, Header, Request

from bank_core.undo import undo_candidate, undo_summary
from bank_core.access import (
    get_actor,
    request_session_token,
    record_failure_and_raise,
    server_request_id,
)
from bank_core.cards import (
    card_source_states,
    select_card,
)
from bank_core.idempotency import (
    idempotency_abort,
    idempotency_begin,
    idempotency_complete,
)
from bank_core.ledger import (
    LOCKS,
)
from bank_core.models import (
    ConfirmationRequest,
    FaceChallengeRequest,
    LockPrecheckRequest,
    MFAVerifyRequest,
    OperationPrecheckRequest,
    PrecheckRequest,
    UnlockPrecheckRequest,
)
from bank_core.operations import (
    card_channel_args,
    birthday_times,
    hash_operation_context,
    ensure_product_suitable,
    fraud_transaction,
    fund_lock_risk_level,
    normalize_datetime,
    operation_amount,
    build_operation_context,
    operation_risk_level,
    operation_target,
    pending_transfer_amount,
    require_token_subject,
    resolve_subscription,
    transfer_risk_level,
    validate_operation_name,
    validate_split_participants,
)
from bank_core.payments import (
    daily_spent,
    enforce_card_limit,
    ensure_account,
    payment_card,
    resolve_payee,
)
from bank_core.wealth_data import (
    WEALTH_HOLDINGS,
    WEALTH_PRODUCTS,
    WEALTH_RISK_PROFILES,
)
from security import (
    STATE_LOCK,
    SecurityError,
    amount_to_cents,
    audit_event,
    cancel_auth_token,
    confirm_auth_token,
    issue_auth_token,
    issue_demo_otp,
    issue_face_challenge,
    sanitize_text,
    verify_mfa,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Risk prechecks, explicit confirmation and MFA
# ---------------------------------------------------------------------------
@router.post("/api/v1/risk/precheck")
def risk_precheck(req: PrecheckRequest, request: Request = None, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    request_id = server_request_id(x_request_id)
    actor = get_actor(req.from_user, x_user_id, action="RISK_PRECHECK", request_id=request_id)
    idem_payload = {"from_user": req.from_user, "target": req.target_name_or_id, "amount": req.amount, "remark": req.remark or "智能转账"}
    cached = idempotency_begin(actor, "risk_precheck", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    try:
        ensure_account(req.from_user)
        target_id, receiver = resolve_payee(req.target_name_or_id, from_user=req.from_user)
        remark = sanitize_text(req.remark or "智能转账", field="转账备注", max_length=200)
        cents = amount_to_cents(req.amount)
        if not math.isfinite(float(req.amount)):
            raise SecurityError("金额格式无效", 400, "INVALID_AMOUNT")
        # Resolve the paying card before a credential is issued, so an
        # ambiguous or unusable wallet is reported now rather than after the
        # user has approved and passed MFA.  Execution re-checks it.
        payer_card = payment_card(req.from_user, req.card_id)
        if req.card_id or payer_card is not None:
            enforce_card_limit(req.from_user, cents / 100.0, card=payer_card)
    except SecurityError as exc:
        idempotency_abort(actor, "risk_precheck", idempotency_key)
        record_failure_and_raise(exc, user_id=actor, action="RISK_PRECHECK", request_id=request_id)
    from security import STATE_LOCK
    with STATE_LOCK:
        current_spent = daily_spent(req.from_user)
        pending = pending_transfer_amount(req.from_user)
        accumulated_after = round(current_spent + pending + cents / 100.0, 2)
        risk_level = transfer_risk_level(req.from_user, accumulated_after, request_id=request_id)
        required_actions = ["USER_CLICK_CONFIRM"] if risk_level == "YELLOW" else ["USER_CLICK_CONFIRM", "SMS_OTP", "FACE_RECOGNITION"]
        token, payload = issue_auth_token(from_user=req.from_user, target_id=target_id, target_display=receiver["name"], amount=req.amount, remark=remark, risk_level=risk_level, required_actions=required_actions, operation="TRANSFER", session_token=request_session_token(request))
    audit_event("RISK_PRECHECK", user_id=actor, risk_level=risk_level, decision="CHALLENGE", outcome="TOKEN_ISSUED", request_id=request_id, metadata={"jti": payload["jti"], "target_id": target_id, "amount_cents": cents, "daily_accumulated_after_cents": int(round(accumulated_after * 100)), "required_actions": required_actions})
    response = {"risk_level": risk_level, "daily_accumulated_now": current_spent, "daily_pending_reserved": pending, "daily_accumulated_after": accumulated_after, "auth_token": token, "token_type": "Bearer-Auth-Nonce", "expires_at": payload["exp"], "required_actions": required_actions, "target_id": target_id, "target_name": receiver["name"], "mfa_required": risk_level == "RED"}
    idempotency_complete(actor, "risk_precheck", idempotency_key, idem_payload, response)
    return response


@router.post("/api/v1/risk/precheck/lock")
def lock_precheck(req: LockPrecheckRequest, request: Request = None, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    request_id = server_request_id(x_request_id)
    actor = get_actor(req.user_id, x_user_id, action="LOCK_PRECHECK", request_id=request_id)
    idem_payload = {"user_id": req.user_id, "amount": req.amount, "reason": req.reason, "event_at": req.event_at, "reminder_at": req.reminder_at}
    cached = idempotency_begin(actor, "lock_precheck", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    try:
        account = ensure_account(req.user_id)
        reason = sanitize_text(req.reason, field="锁定原因", max_length=300)
        cents = amount_to_cents(req.amount)
        event_at, reminder_at = birthday_times(req.event_at, req.reminder_at)
        if account["balance"] - account["locked_amount"] < cents / 100.0:
            raise SecurityError("可用余额不足以锁定此金额", 400, "INSUFFICIENT_FUNDS")
    except SecurityError as exc:
        idempotency_abort(actor, "lock_precheck", idempotency_key)
        record_failure_and_raise(exc, user_id=actor, action="LOCK_PRECHECK", request_id=request_id)
    lock_context_hash = hash_operation_context({"event_at": event_at, "reminder_at": reminder_at})
    risk_level = fund_lock_risk_level(req.amount)
    required_actions = ["USER_CLICK_CONFIRM"] if risk_level == "YELLOW" else ["USER_CLICK_CONFIRM", "SMS_OTP", "FACE_RECOGNITION"]
    token, payload = issue_auth_token(from_user=req.user_id, target_id="__LOCK_FUND__", target_display="资金隔离池", amount=req.amount, remark=reason, risk_level=risk_level, required_actions=required_actions, operation="LOCK_FUND", context_hash=lock_context_hash, session_token=request_session_token(request))
    audit_event("LOCK_PRECHECK", user_id=actor, risk_level=risk_level, decision="CHALLENGE", outcome="TOKEN_ISSUED", request_id=request_id, metadata={"jti": payload["jti"], "amount_cents": cents})
    response = {"risk_level": risk_level, "auth_token": token, "token_type": "Bearer-Auth-Nonce", "expires_at": payload["exp"], "required_actions": required_actions, "mfa_required": risk_level == "RED", "event_at": event_at, "reminder_at": reminder_at}
    idempotency_complete(actor, "lock_precheck", idempotency_key, idem_payload, response)
    return response


@router.post("/api/v1/risk/precheck/unlock")
def unlock_precheck(req: UnlockPrecheckRequest, request: Request = None, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    """Issue a yellow-zone nonce for releasing a previously locked amount."""
    request_id = server_request_id(x_request_id)
    actor = get_actor(req.user_id, x_user_id, action="UNLOCK_PRECHECK", request_id=request_id)
    try:
        ensure_account(req.user_id)
        reason = sanitize_text(req.reason or "解除资金隔离", field="解除原因", max_length=300)
        cents = amount_to_cents(req.amount)
        lock_id = sanitize_text(req.lock_id, field="锁定编号", max_length=100) if req.lock_id else None
        with STATE_LOCK:
            candidates = [v for v in LOCKS.values() if v.get("user_id") == req.user_id and v.get("status") == "LOCKED" and (not lock_id or v.get("lock_id") == lock_id)]
            available_locked = sum(float(v.get("amount", 0)) for v in candidates)
        if available_locked < cents / 100.0:
            raise SecurityError("可解除的锁定金额不足", 400, "LOCK_NOT_FOUND")
        target = lock_id or "__UNLOCK_FUND__"
    except SecurityError as exc:
        record_failure_and_raise(exc, user_id=actor, action="UNLOCK_PRECHECK", request_id=request_id)
    idem_payload = {"user_id": req.user_id, "amount": req.amount, "lock_id": req.lock_id, "reason": reason}
    cached = idempotency_begin(actor, "unlock_precheck", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    risk_level = fund_lock_risk_level(req.amount)
    required_actions = ["USER_CLICK_CONFIRM"] if risk_level == "YELLOW" else ["USER_CLICK_CONFIRM", "SMS_OTP", "FACE_RECOGNITION"]
    token, payload = issue_auth_token(from_user=req.user_id, target_id=target, target_display="资金隔离池", amount=req.amount, remark=reason, risk_level=risk_level, required_actions=required_actions, operation="UNLOCK_FUND", session_token=request_session_token(request))
    audit_event("UNLOCK_PRECHECK", user_id=actor, risk_level=risk_level, decision="CHALLENGE", outcome="TOKEN_ISSUED", request_id=request_id, metadata={"jti": payload["jti"], "amount_cents": cents, "lock_id": lock_id})
    response = {"risk_level": risk_level, "auth_token": token, "token_type": "Bearer-Auth-Nonce", "expires_at": payload["exp"], "required_actions": required_actions, "mfa_required": risk_level == "RED", "lock_id": lock_id}
    idempotency_complete(actor, "unlock_precheck", idempotency_key, idem_payload, response)
    return response


@router.post("/api/v1/risk/precheck/operation")
def operation_precheck(
    req: OperationPrecheckRequest,
    request: Request = None,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Create a short-lived approval nonce for non-transfer scenarios.

    This endpoint is intentionally generic so adding a new card/wealth
    operation cannot accidentally create a second, unprotected authorization
    path.  The operation name is checked against ``GENERIC_OPERATION_RISK``.
    """

    request_id = server_request_id(x_request_id)
    actor = get_actor(req.user_id, x_user_id, action="OPERATION_PRECHECK", request_id=request_id)
    resolved_subscription_id: Optional[str] = None
    fraud_tx: Optional[dict[str, Any]] = None
    try:
        ensure_account(req.user_id)
        operation = validate_operation_name(req.operation)
        target = operation_target(req)
        card_channel, channel_enabled = card_channel_args(operation, req.channel, req.enable)
        undo_preview = None
        if operation == "UNDO_TASK":
            undo = undo_candidate(req.user_id, sanitize_text(req.target or "", field="任务编号", max_length=100))
            target, undo_preview = undo.task_id, {"task_type": undo.task_type, "summary": undo_summary(undo)}
        if operation == "FRAUD_PROTECT":
            fraud_tx = fraud_transaction(req.user_id, req.transaction_id)
            target = str(fraud_tx["card_id"])
            reason = sanitize_text(req.reason or "客户否认境外消费，申请反诈保护", field="操作原因", max_length=300)
            if req.transaction_snapshot_hash and req.transaction_snapshot_hash != hash_operation_context(fraud_tx):
                raise SecurityError("交易信息已变化，请重新核验", 409, "TRANSACTION_CONTEXT_CHANGED")
        if operation == "PASSWORD_CHANGE" and any(value for value in (req.target, req.product_id, req.card_id, req.merchant)):
            raise SecurityError("密码修改不接受外部目标编号", 400, "TARGET_NOT_ALLOWED")
        reason = sanitize_text(req.reason or ("客户否认境外消费，申请反诈保护" if operation == "FRAUD_PROTECT" else "智能银行操作"), field="操作原因", max_length=300)
        amount_input = req.new_limit if operation == "CARD_LIMIT" and req.amount in (None, 0) and req.new_limit is not None else req.amount
        amount = operation_amount(amount_input, operation=operation)
        if operation in {"CARD_APPLY", "CARD_FREEZE", "CARD_UNFREEZE", "CARD_REPORT_LOST", "CARD_UNREPORT_LOST", "SUBSCRIPTION_QUERY", "SUBSCRIPTION_CANCEL", "PASSWORD_CHANGE", "FRAUD_PROTECT", "CARD_CHANNEL", "UNDO_TASK"} and amount_input not in (None, 0):
            raise SecurityError("该操作不接受金额字段", 400, "AMOUNT_NOT_ALLOWED")
        if operation in {"SCHEDULED_TRANSFER", "SPLIT_BILL", "WEALTH_PURCHASE", "WEALTH_REDEEM", "CARD_LIMIT"} and (amount_input is None or float(amount_input) <= 0):
            raise SecurityError("该操作需要明确的正数金额", 400, "AMOUNT_REQUIRED")
        if operation == "CARD_LIMIT":
            effective_limit = req.new_limit if req.new_limit is not None else req.amount
            if effective_limit is None or not math.isfinite(float(effective_limit)) or float(effective_limit) <= 0:
                raise SecurityError("卡片额度必须为正数", 400, "INVALID_CARD_LIMIT")
        if operation == "SCHEDULED_TRANSFER":
            if not req.target:
                raise SecurityError("预约转账缺少收款人", 400, "TARGET_REQUIRED")
            resolve_payee(req.target, from_user=req.user_id)
            if not req.scheduled_at:
                raise SecurityError("预约转账缺少执行时间", 400, "SCHEDULE_REQUIRED")
            # A scheduled transfer must remain a future task.  Normalize it
            # before signing so execution and scheduler storage use the same
            # canonical instant and cannot silently turn into an immediate
            # debit after approval.
            normalized_scheduled_at = normalize_datetime(req.scheduled_at, field="计划时间", require_future=True)
        else:
            normalized_scheduled_at = req.scheduled_at
        validated_participants = validate_split_participants(req.user_id, req.participants) if operation == "SPLIT_BILL" else list(req.participants or [])
        if operation == "SUBSCRIPTION_CANCEL" and not (req.target or req.merchant):
            raise SecurityError("取消订阅缺少商户或订阅编号", 400, "TARGET_REQUIRED")
        if operation == "SUBSCRIPTION_CANCEL":
            resolved_subscription_id = resolve_subscription(req.user_id, req.target, req.merchant).get("subscription_id")
        if operation in {"WEALTH_PURCHASE", "WEALTH_REDEEM"} and not (req.product_id or req.target):
            raise SecurityError("理财操作缺少产品编号", 400, "PRODUCT_REQUIRED")
        if operation == "WEALTH_PURCHASE":
            if req.user_id not in WEALTH_RISK_PROFILES:
                raise SecurityError("申购前请先完成风险评估", 428, "RISK_ASSESSMENT_REQUIRED")
            product = ensure_product_suitable(req.user_id, req.product_id or req.target or "")
            if amount < float(product["min_amount"]):
                raise SecurityError("申购金额低于产品起购金额", 400, "MIN_AMOUNT_NOT_MET")
            with STATE_LOCK:
                account = ensure_account(req.user_id)
                if account["balance"] - account["locked_amount"] < amount:
                    raise SecurityError("可用余额不足以申购此金额", 400, "INSUFFICIENT_FUNDS")
                # Settlement charges the paying card; refuse now rather than after the
                # user has gone through SMS and device verification for nothing.
                enforce_card_limit(req.user_id, amount, card=payment_card(req.user_id, req.card_id))
        if operation == "WEALTH_COMPARE":
            product_ids = list(req.product_ids or [])
            if len(product_ids) < 2:
                raise SecurityError("产品对比至少需要两个产品编号", 400, "PRODUCTS_REQUIRED")
            if len(product_ids) > 10 or len(set(product_ids)) != len(product_ids):
                raise SecurityError("产品编号列表无效", 400, "INVALID_PRODUCTS")
            if any(not any(p["product_id"] == item for p in WEALTH_PRODUCTS) for item in product_ids):
                raise SecurityError("理财产品不存在", 404, "PRODUCT_NOT_FOUND")
        # Validate a product/card target before issuing a credential where
        # possible.  Unknown targets are rejected, never silently remapped.
        if operation == "WEALTH_REDEEM":
            product_id = req.product_id or req.target
            if not any(p["product_id"] == product_id for p in WEALTH_PRODUCTS):
                raise SecurityError("理财产品不存在", 404, "PRODUCT_NOT_FOUND")
            with STATE_LOCK:
                available_cents = sum(
                    max(0, int(round(float(holding.get("principal", 0)) * 100)))
                    for holding in WEALTH_HOLDINGS.get(req.user_id, [])
                    if holding.get("product_id") == product_id
                )
            if available_cents < amount_to_cents(amount):
                raise SecurityError("没有足够的持仓可赎回", 400, "HOLDING_NOT_FOUND")
        if operation.startswith("CARD_"):
            # CARD_APPLY opens a *new* card, so it is the one operation with no
            # card to resolve; everything else must name an unambiguous one.
            card = None if operation == "CARD_APPLY" else select_card(req.user_id, req.card_id, allowed_states=card_source_states(operation))
            if card:
                current_status = str(card.get("status") or "")
                # Same table the executor and the selector use, so a card can no
                # longer be validated against one copy of the rules and executed
                # against another.
                required_states = card_source_states(operation)
                if required_states and current_status not in required_states:
                    raise SecurityError("卡片当前状态不允许该操作", 409, "INVALID_CARD_STATE")
            if operation != "CARD_APPLY" and card:
                # Bind the signed approval to the concrete card even when the
                # conversational request omitted a card id.  This prevents a
                # card replacement between precheck and execution from
                # redirecting the action.
                target = str(req.card_id or card.get("card_id"))
    except SecurityError as exc:
        record_failure_and_raise(exc, user_id=actor, action="OPERATION_PRECHECK", request_id=request_id)
    # Risk calculation, nonce reservation and issuance share one lock. This
    # prevents concurrent scheduled/AA prechecks from both observing the same
    # daily total and receiving yellow approvals that collectively exceed the
    # threshold.
    with STATE_LOCK:
        risk_level = operation_risk_level(req.user_id, operation, amount, channel_enabled=channel_enabled)
        required = ["USER_CLICK_CONFIRM"] if risk_level == "YELLOW" else ["USER_CLICK_CONFIRM", "SMS_OTP", "FACE_RECOGNITION"]
        operation_context = build_operation_context(
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
            scheduled_at=normalized_scheduled_at,
            transaction_id=fraud_tx.get("tx_id") if fraud_tx else req.transaction_id,
            transaction_snapshot_hash=hash_operation_context(fraud_tx) if fraud_tx else req.transaction_snapshot_hash,
            card_channel=card_channel,
            channel_enabled=channel_enabled,
        )
        context_hash = hash_operation_context(operation_context)
        idem_payload = {"user_id": req.user_id, "operation": operation, "context": operation_context}
        cached = idempotency_begin(actor, "operation_precheck", idempotency_key, idem_payload, request_id=request_id)
        if cached is not None:
            return cached
        token, payload = issue_auth_token(
            from_user=req.user_id,
            target_id=target,
            target_display=target,
            amount=amount,
            remark=reason,
            risk_level=risk_level,
            required_actions=required,
            operation=operation,
            context_hash=context_hash,
            session_token=request_session_token(request),
        )
    response = {"code": 200, "risk_level": risk_level, "operation": operation, "auth_token": token, "token_type": "Bearer-Auth-Nonce", "expires_at": payload["exp"], "required_actions": required, "mfa_required": risk_level == "RED", "target": target, "context_hash": context_hash}
    if undo_preview:
        response["undo"] = undo_preview
    if fraud_tx:
        response["transaction"] = dict(fraud_tx)
        response["risk_reasons"] = ["境外交易", "交易类别标记为可疑交易", "用户明确否认该消费"]
    idempotency_complete(actor, "operation_precheck", idempotency_key, idem_payload, response)
    audit_event("OPERATION_PRECHECK", user_id=actor, risk_level=risk_level, decision="CHALLENGE", outcome="TOKEN_ISSUED", request_id=request_id, metadata={"operation": operation, "jti": payload["jti"], "target": target})
    return response


@router.post("/api/v1/auth/confirm")
@router.post("/api/v1/auth/confirmation")
@router.post("/api/v1/risk/confirm")
def confirm_operation(req: ConfirmationRequest, x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = server_request_id(x_request_id)
    auth_token = req.auth_token or x_auth_token
    # Do not treat a body user id as an authenticated principal.  Until the
    # token subject and X-User-Id are verified, failures must be attributed only
    # to the presented header (or anonymous), otherwise this endpoint becomes
    # a cross-account circuit-breaker DoS primitive.
    actor = x_user_id
    try:
        if not auth_token:
            raise SecurityError("缺少安全授权凭证", 403, "TOKEN_REQUIRED")
        actor = require_token_subject(auth_token, body_user=req.user_id, header_user=x_user_id, action="USER_CONFIRMATION", request_id=request_id)
        result = confirm_auth_token(auth_token, user_id=actor, request_id=request_id)
    except SecurityError as exc:
        record_failure_and_raise(exc, user_id=actor, action="USER_CONFIRMATION", request_id=request_id)
    return {"code": 200, **result}


@router.post("/api/v1/risk/cancel")
def cancel_operation(
    req: ConfirmationRequest,
    request: Request,
    x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
):
    """Cancel a nonce owned by the presented principal and original session."""

    request_id = server_request_id(x_request_id)
    try:
        if not x_user_id:
            raise SecurityError("需要用户身份", 401, "AUTHENTICATION_REQUIRED")
        if req.user_id and req.user_id != x_user_id:
            raise SecurityError("请求用户与登录身份不一致", 403, "IDENTITY_MISMATCH")
        auth_token = req.auth_token or x_auth_token
        if not auth_token:
            raise SecurityError("缺少安全授权凭证", 403, "TOKEN_REQUIRED")
        result = cancel_auth_token(
            auth_token, user_id=x_user_id, presented_session=request_session_token(request),
            request_id=request_id,
        )
    except SecurityError as exc:
        record_failure_and_raise(exc, user_id=x_user_id, action="AUTH_TOKEN_CANCEL", request_id=request_id)
    return {"code": 200, **result}


@router.post("/api/v1/auth/mfa/verify")
@router.post("/api/v1/auth/mfa")
@router.post("/api/v1/risk/mfa/verify")
def mfa_verify(req: MFAVerifyRequest, x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = server_request_id(x_request_id)
    otp = req.otp if req.otp is not None else req.sms_code
    auth_token = req.auth_token or x_auth_token
    # The request body is untrusted until the signed token/header binding has
    # succeeded; see ``confirm_operation`` above.
    actor = x_user_id
    try:
        if not auth_token:
            raise SecurityError("缺少安全授权凭证", 403, "TOKEN_REQUIRED")
        actor = require_token_subject(auth_token, body_user=req.user_id, header_user=x_user_id, action="MFA_VERIFY", request_id=request_id)
        result = verify_mfa(auth_token, otp=otp, face_attestation=req.face_attestation, user_id=actor, request_id=request_id)
    except SecurityError as exc:
        record_failure_and_raise(exc, user_id=actor, action="MFA_VERIFY", request_id=request_id)
    return {"code": 200, **result}


@router.post("/api/v1/auth/mfa/otp")
def mfa_demo_otp(req: FaceChallengeRequest, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = server_request_id(x_request_id)
    try:
        actor = require_token_subject(req.auth_token, body_user=req.user_id, header_user=x_user_id, action="DEMO_OTP", request_id=request_id)
        return {"code": 200, **issue_demo_otp(req.auth_token, user_id=actor, request_id=request_id)}
    except SecurityError as exc:
        record_failure_and_raise(exc, user_id=x_user_id, action="DEMO_OTP", request_id=request_id)


@router.post("/api/v1/auth/mfa/face/challenge")
@router.post("/api/v1/risk/mfa/face/challenge")
def mfa_face_challenge(req: FaceChallengeRequest, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """Issue a liveness challenge the demo biometric SDK must sign."""

    request_id = server_request_id(x_request_id)
    actor = x_user_id
    try:
        actor = require_token_subject(req.auth_token, body_user=req.user_id, header_user=x_user_id, action="FACE_CHALLENGE", request_id=request_id)
        result = issue_face_challenge(req.auth_token, user_id=actor, request_id=request_id)
    except SecurityError as exc:
        record_failure_and_raise(exc, user_id=actor, action="FACE_CHALLENGE", request_id=request_id)
    return {"code": 200, **result}
