"""Generic operation policy: risk table, signed operation context and target validation."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from bank_core.access import (
    _record_failure_and_raise,
)
from bank_core.contacts import (
    _aa_request_eligible,
)
from bank_core.ledger import (
    SCHEDULED_TRANSFERS,
    SUBSCRIPTIONS,
    TRANSACTIONS,
)
from bank_core.models import (
    OperationExecuteRequest,
    OperationPrecheckRequest,
)
from bank_core.payments import (
    _daily_spent,
    _resolve_target,
)
from bank_core.wealth_data import (
    WEALTH_PRODUCTS,
    WEALTH_RISK_PROFILES,
    profile_rank,
)
from security import (
    AUTH_TOKENS,
    STATE_LOCK,
    SecurityError,
    amount_to_cents,
    ensure_circuit_closed,
    inspect_auth_token,
    sanitize_text,
)

# Sensitive operations that feed the behaviour-combination breaker.  The
# signal names line up with ``security.BEHAVIOR_SIGNAL_WEIGHTS``.
_BEHAVIOR_OPERATION_SIGNALS: dict[str, str] = {
    "CARD_REPORT_LOST": "CARD_REPORT_LOST",
    "CARD_FREEZE": "CARD_STATE_CHANGE",
    "CARD_UNFREEZE": "CARD_STATE_CHANGE",
    "CARD_LIMIT": "CARD_LIMIT_CHANGE",
    "PASSWORD_CHANGE": "PASSWORD_CHANGE",
    "WEALTH_PURCHASE": "WEALTH_PURCHASE",
    "WEALTH_REDEEM": "WEALTH_REDEEM",
}

GENERIC_OPERATION_RISK: dict[str, str] = {
    # Yellow: a reversible/low-value action still needs an explicit click.
    "CARD_APPLY": "YELLOW",
    # Subscription/debit lists are yellow by contest policy: the user confirms
    # before Core releases them, and the confirmation is a single-use nonce.
    "SUBSCRIPTION_QUERY": "YELLOW",
    "SUBSCRIPTION_CANCEL": "YELLOW",
    "SCHEDULED_TRANSFER": "YELLOW",
    "SPLIT_BILL": "YELLOW",
    # Red: money investment, card security and limit changes require MFA.
    "WEALTH_PURCHASE": "RED",
    "WEALTH_REDEEM": "RED",
    "CARD_LIMIT": "RED",
    "CARD_FREEZE": "RED",
    "CARD_UNFREEZE": "RED",
    "CARD_REPORT_LOST": "RED",
    "CARD_UNREPORT_LOST": "RED",
    "PASSWORD_CHANGE": "RED",
    "FRAUD_PROTECT": "RED",
}


def _pending_transfer_amount(user_id: str, *, exclude_schedule_id: Optional[str] = None) -> float:
    """Return unconsumed transfer reservations for cumulative risk checks.

    A precheck is a reservation, not a settlement.  Counting active signed
    nonces prevents two concurrent 600-yuan prechecks from both being shown as
    yellow and bypassing the 1,000-yuan daily threshold.
    """

    now = time.time()
    total = 0.0
    # Callers normally hold STATE_LOCK already; RLock keeps this helper safe
    # when used independently (for example by a concurrent risk probe).
    with STATE_LOCK:
        for record in AUTH_TOKENS.values():
            if record.get("used") or float(record.get("expires_at", 0)) <= now:
                continue
            payload = record.get("payload") or {}
            if payload.get("from_user") == user_id and payload.get("operation", "TRANSFER") in {"TRANSFER", "SCHEDULED_TRANSFER"}:
                try:
                    total += float(payload.get("amount_cents", 0)) / 100.0
                except (TypeError, ValueError):
                    continue
        # Once a scheduled approval nonce is consumed, the resulting task
        # remains an outstanding money-movement reservation. Keep it in the
        # cumulative risk calculation until cancelled or settled, preventing a
        # user from consuming a yellow nonce and immediately obtaining another
        # yellow nonce that exceeds the daily threshold.
        total += sum(
            float(row.get("amount", 0) or 0)
            for row in SCHEDULED_TRANSFERS
            if row.get("user_id") == user_id and row.get("schedule_id") != exclude_schedule_id and row.get("status") in {"SCHEDULED", "WAITING_USER"}
        )
    return round(total, 2)


def _operation_target(req: OperationPrecheckRequest | OperationExecuteRequest) -> str:
    """Stable token binding for a generic operation."""

    candidate = req.product_id or req.card_id or req.target or getattr(req, "merchant", None)
    if candidate:
        return sanitize_text(candidate, field="操作目标", max_length=128)
    return f"__{req.operation.upper()}__"


def _operation_amount(value: Optional[float], *, operation: str = "") -> float:
    """Normalize an operation amount, or return the non-financial sentinel.

    The signed token format uses cents and requires a positive value, so
    operations that carry no money (freeze, cancel, password change) use 0.01
    as an internal sentinel that is never debited.

    A supplied amount of exactly zero is treated as "no amount" rather than as
    a one-cent operation: the executor defaults ``amount`` to 0, so absent and
    explicit zero are not distinguishable at this boundary, and every operation
    that actually moves money is separately required to carry a positive amount
    (``AMOUNT_REQUIRED``).  What must never happen is a zero being quietly
    rounded up into an instruction the caller did not give, which is why
    ``CARD_LIMIT`` - where zero would mean "stop this card paying" - gets a
    precise rejection instead of the sentinel.
    """

    if value is None or (not isinstance(value, bool) and isinstance(value, (int, float)) and float(value) == 0):
        if operation == "CARD_LIMIT" and value is not None:
            raise SecurityError(
                "卡片日限额必须大于 0；如需停用该卡请使用冻结或挂失",
                400,
                "INVALID_CARD_LIMIT",
            )
        return 0.01
    # Return the canonical cent-rounded value as well as validating it.  Using
    # the raw binary float here would let a value such as 0.005 be signed as
    # one cent but persisted as a sub-cent amount in scheduled/split/wealth
    # records, making risk totals and ledger facts disagree.
    return amount_to_cents(value) / 100.0


def _context_hash(context: dict[str, Any]) -> str:
    """Return a deterministic digest for approval-bound operation details."""

    canonical = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_datetime(value: Optional[str], *, field: str, require_future: bool = False) -> Optional[str]:
    """Normalize an ISO/date value for token binding and scheduler use.

    A date without a time is interpreted at 09:00 in UTC for deterministic
    demos.  The normalized UTC string is what gets signed, so equivalent input
    representations cannot produce an authorization mismatch at execution.
    """

    if value is None or not str(value).strip():
        return None
    text = sanitize_text(value, field=field, max_length=80).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d")
        except ValueError as exc:
            raise SecurityError(f"{field}格式无效", 400, "INVALID_DATETIME") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc).replace(microsecond=0)
    if require_future and parsed <= datetime.now(timezone.utc):
        raise SecurityError(f"{field}必须是未来时间", 400, "DATETIME_NOT_FUTURE")
    return parsed.isoformat()


def _birthday_times(event_at: Optional[str], reminder_at: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """Normalize a cross-scene event and derive the auditable reminder time."""

    normalized_event = _normalize_datetime(event_at, field="事件日期", require_future=True) if event_at else None
    if not normalized_event:
        if reminder_at:
            raise SecurityError("缺少生日日期，无法计算提醒时间", 400, "EVENT_DATE_REQUIRED")
        return None, None
    event_dt = datetime.fromisoformat(normalized_event)
    expected_reminder = (event_dt - timedelta(days=2)).isoformat()
    if reminder_at:
        normalized_reminder = _normalize_datetime(reminder_at, field="提醒日期")
        if normalized_reminder != expected_reminder:
            raise SecurityError("提醒时间必须是事件日期前 2 天", 400, "INVALID_REMINDER_TIME")
    return normalized_event, expected_reminder


def _operation_context(
    *,
    operation: str,
    target: str,
    amount: float,
    reason: str,
    new_limit: Optional[float] = None,
    merchant: Optional[str] = None,
    participants: Optional[list[str]] = None,
    product_ids: Optional[list[str]] = None,
    risk_profile: Optional[str] = None,
    subscription_id: Optional[str] = None,
    scheduled_at: Optional[str] = None,
    transaction_id: Optional[str] = None,
    transaction_snapshot_hash: Optional[str] = None,
) -> dict[str, Any]:
    """Normalize every field that can alter a generic operation's outcome."""

    safe_participants: list[str] = []
    for value in participants or []:
        safe_participants.append(sanitize_text(value, field="分摊参与人", max_length=120))
    if len(safe_participants) > 20 or len(set(safe_participants)) != len(safe_participants):
        raise SecurityError("分摊参与人数量或内容无效", 400, "INVALID_PARTICIPANTS")
    safe_product_ids: list[str] = []
    for value in product_ids or []:
        item = sanitize_text(value, field="理财产品编号", max_length=64)
        if item and item not in safe_product_ids:
            safe_product_ids.append(item)
    if len(safe_product_ids) > 10:
        raise SecurityError("理财产品数量超过限制", 400, "INVALID_PRODUCTS")
    # Cancellation clients may identify the same subscription as either
    # ``target`` during precheck or ``merchant`` during execution.  Canonicalize
    # that alias so adding the explicit merchant field at execution cannot
    # invalidate an otherwise unchanged approval; a genuinely different
    # merchant still changes the digest and is rejected.
    merchant_value = merchant or (target if operation == "SUBSCRIPTION_CANCEL" else None)
    safe_merchant = sanitize_text(merchant_value, field="商户", max_length=120) if merchant_value else None
    normalized_schedule = _normalize_datetime(
        scheduled_at,
        field="计划时间",
        require_future=operation == "SCHEDULED_TRANSFER",
    ) if scheduled_at else None
    # ``amount`` is the backwards-compatible CARD_LIMIT field.  Bind the
    # effective limit rather than only the optional ``new_limit`` so a client
    # cannot approve one value and inject another representation at execution;
    # explicit and implicit forms carrying the same value remain equivalent.
    effective_new_limit = amount if operation == "CARD_LIMIT" and new_limit is None else new_limit
    normalized_new_limit = amount_to_cents(effective_new_limit) if effective_new_limit not in (None, 0) else None
    return {
        "operation": operation,
        "target": target,
        "amount_cents": amount_to_cents(amount),
        "reason": reason,
        "new_limit_cents": normalized_new_limit,
        "merchant": safe_merchant,
        "participants": safe_participants,
        "product_ids": safe_product_ids,
        "risk_profile": sanitize_text(risk_profile, field="风险档案", max_length=32).upper() if risk_profile else None,
        "subscription_id": sanitize_text(subscription_id, field="订阅编号", max_length=128) if subscription_id else None,
        "scheduled_at": normalized_schedule,
        "transaction_id": sanitize_text(transaction_id, field="交易编号", max_length=128) if transaction_id else None,
        "transaction_snapshot_hash": sanitize_text(transaction_snapshot_hash, field="交易快照摘要", max_length=128) if transaction_snapshot_hash else None,
    }


def _fraud_transaction(user_id: str, transaction_id: Optional[str] = None) -> dict[str, Any]:
    """Resolve exactly one owned suspicious overseas transaction."""
    rows = [row for row in TRANSACTIONS if row.get("account_id") == user_id and (row.get("category") == "可疑交易" or row.get("country"))]
    if transaction_id:
        row = next((item for item in rows if item.get("tx_id") == transaction_id), None)
        if row is None:
            raise SecurityError("交易不存在或不属于当前用户", 404, "TRANSACTION_NOT_FOUND")
    elif not rows:
        raise SecurityError("未找到可疑交易，请指定交易编号以便核对", 404, "TRANSACTION_NOT_FOUND")
    elif len(rows) != 1:
        raise SecurityError("匹配到多笔可疑交易，请指定交易编号", 409, "TRANSACTION_ID_REQUIRED")
    else:
        row = rows[0]
    if not row.get("card_id"):
        raise SecurityError("该交易未关联卡片，无法安全冻结", 409, "CARD_LINK_MISSING")
    return row


def _validate_split_participants(user_id: str, participants: Optional[list[str]]) -> list[str]:
    """Resolve AA participants against the Core contact allow-list.

    Participant names are used for the local split-event record,
    but they are still security-sensitive routing data.  Accepting arbitrary
    model-generated strings would allow an event to target an unregistered
    account (or make the audit trail impossible to reconcile).
    Return the original display values so the UI remains human-readable while
    proving that each value resolves to an active, non-self account.
    """

    values = list(participants or [])
    if not values:
        raise SecurityError("AA 分摊至少需要一位参与人", 400, "PARTICIPANTS_REQUIRED")
    if len(values) > 20 or len(set(values)) != len(values):
        raise SecurityError("分摊参与人数量或内容无效", 400, "INVALID_PARTICIPANTS")
    resolved: list[str] = []
    resolved_ids: set[str] = set()
    for value in values:
        display = sanitize_text(value, field="分摊参与人", max_length=120)
        target_id, _ = _resolve_target(display, from_user=user_id)
        # An AA request only names a verified contact. Money moves later only
        # when the participant authenticates and approves an ordinary transfer.
        if not _aa_request_eligible(user_id, display):
            raise SecurityError(
                f"“{display}”尚未确认关联账户，AA 请求只能发给已确认的联系人",
                403,
                "PARTICIPANT_NOT_AUTHORIZED",
            )
        if target_id in resolved_ids:
            raise SecurityError("分摊参与人不能重复指向同一账户", 400, "INVALID_PARTICIPANTS")
        resolved_ids.add(target_id)
        resolved.append(display)
    return resolved


def _operation_risk_level(user_id: str, operation: str, amount: float) -> str:
    """Return the core-owned risk class for a generic operation.

    An AA request does not debit anyone. Each participant's eventual payment
    uses the ordinary transfer risk check on that participant's daily total.
    """

    if operation not in {"SCHEDULED_TRANSFER"}:
        return GENERIC_OPERATION_RISK[operation]
    with STATE_LOCK:
        accumulated_after = round(_daily_spent(user_id) + _pending_transfer_amount(user_id) + float(amount), 2)
    return "YELLOW" if accumulated_after <= 1000.0 else "RED"


def _fund_lock_risk_level(amount: Any) -> str:
    """Escalate a large reservation/release to the red verification tier."""

    normalized = amount_to_cents(amount) / 100.0
    return "YELLOW" if normalized <= 1000.0 else "RED"


def _validate_operation_name(operation: str) -> str:
    try:
        normalized = sanitize_text(operation, field="操作类型", max_length=64).upper()
    except SecurityError:
        raise
    if normalized not in GENERIC_OPERATION_RISK:
        raise SecurityError("不支持或未登记的操作类型", 400, "OPERATION_NOT_ALLOWED")
    return normalized


def _ensure_product_suitable(user_id: str, product_id: str) -> dict[str, Any]:
    """Return a product only when it is compatible with the user's risk file."""

    product_id = sanitize_text(product_id, field="理财产品编号", max_length=64).upper()
    product = next((item for item in WEALTH_PRODUCTS if item.get("product_id") == product_id), None)
    if not product:
        raise SecurityError("理财产品不存在", 404, "PRODUCT_NOT_FOUND")
    profile = (WEALTH_RISK_PROFILES.get(user_id) or {}).get("risk_profile")
    # Gate on the rank helper rather than a hand-written set of codes.  The
    # 8-question questionnaire grades C1-C5 while the older form produced
    # CONSERVATIVE/BALANCED/AGGRESSIVE, and a literal set here silently answered
    # "please complete the assessment first" to every user who had *just*
    # completed it - the purchase path was unreachable for them.  ``profile_rank``
    # already knows both vocabularies; unknown/absent means "not assessed".
    max_rank = profile_rank(profile)
    if max_rank is None:
        raise SecurityError("申购前请先完成风险评估", 428, "RISK_ASSESSMENT_REQUIRED")
    try:
        product_rank = int(str(product.get("risk_level", "R9")).lstrip("R"))
    except (TypeError, ValueError):
        raise SecurityError("理财产品风险等级无效", 500, "PRODUCT_RISK_INVALID")
    if product_rank > max_rank:
        raise SecurityError("当前风险档案不适合该产品，请先重新评估", 403, "RISK_PROFILE_MISMATCH")
    return product


def _resolve_subscription(user_id: str, target: Optional[str], merchant: Optional[str]) -> dict[str, Any]:
    """Resolve one subscription deterministically before signing approval."""

    candidate = sanitize_text(target or merchant, field="订阅商户或编号", max_length=128)
    if not candidate:
        raise SecurityError("取消订阅缺少商户或订阅编号", 400, "TARGET_REQUIRED")
    rows = list(SUBSCRIPTIONS.get(user_id, []))
    exact = [
        row for row in rows
        if str(row.get("subscription_id", "")).casefold() == candidate.casefold()
        or str(row.get("merchant", "")).casefold() == candidate.casefold()
    ]
    matches = exact or [
        row for row in rows
        if candidate.casefold() in str(row.get("merchant", "")).casefold()
    ]
    if not matches:
        raise SecurityError("订阅不存在", 404, "SUBSCRIPTION_NOT_FOUND")
    if len(matches) != 1:
        raise SecurityError("匹配到多个订阅，请提供订阅编号", 409, "SUBSCRIPTION_AMBIGUOUS")
    if str(matches[0].get("status")) != "ACTIVE":
        raise SecurityError("该订阅已取消或不可用", 409, "SUBSCRIPTION_NOT_ACTIVE")
    return matches[0]


def _require_token_subject(
    auth_token: str,
    *,
    body_user: Optional[str],
    header_user: Optional[str],
    action: str,
    request_id: str,
) -> str:
    """Bind confirmation/MFA to both the signed subject and login header."""

    try:
        payload, _ = inspect_auth_token(auth_token)
        subject = str(payload.get("from_user") or "").strip()
        if not subject:
            raise SecurityError("安全凭证缺少用户主体", 403, "TOKEN_INVALID")
        # Approval and MFA are authorization endpoints.  Require the login
        # principal even in local demo mode so a bearer nonce alone cannot be
        # replayed by a different channel; strict deployments additionally
        # enforce the same rule on every read/write endpoint.
        if not header_user:
            raise SecurityError("请先完成登录认证", 401, "AUTHENTICATION_REQUIRED")
        # Re-use the same per-principal circuit gate as normal API calls;
        # otherwise an already locked account could continue issuing
        # confirmations/MFA challenges with an outstanding nonce.
        ensure_circuit_closed(header_user)
        if subject != header_user:
            raise SecurityError("安全凭证与登录身份不一致", 403, "IDENTITY_MISMATCH")
        if body_user and subject != body_user:
            raise SecurityError("安全凭证与请求身份不一致", 403, "IDENTITY_MISMATCH")
        return header_user or body_user or subject
    except SecurityError as exc:
        # ``body_user`` is untrusted until both the signed token and the
        # authenticated header have been checked.  Recording failures against
        # it here would let an unauthenticated caller repeatedly submit
        # ``{"user_id": "victim"}`` and open the victim's circuit breaker.
        # Only a presented header principal is eligible for attribution; when
        # no header exists, use the anonymous bucket in the security module.
        _record_failure_and_raise(exc, user_id=header_user, action=action, request_id=request_id)
        raise AssertionError("unreachable")
