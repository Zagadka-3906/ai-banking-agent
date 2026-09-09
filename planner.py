"""Deterministic policy and task-planning helpers for the banking agent.

The language model is deliberately *not* allowed to decide which Python
function to call or to mutate an account.  It may propose a small JSON
payload; this module validates that payload, maps it to an allow-listed
intent, and compiles complex requests into a dependency-aware task plan.

The helpers are framework independent so they can be used by LangGraph,
FastAPI endpoints, a CLI demo, and unit tests alike.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import math
from typing import Any, Iterable, Mapping, Optional

try:  # security.py is available in the application, but keep this module reusable.
    from security import InjectionError, sanitize_text
except Exception:  # pragma: no cover - only used when imported in isolation
    class InjectionError(ValueError):
        pass

    def sanitize_text(value: Any, *, field: str = "文本", max_length: int = 2000) -> str:
        text = str(value or "").strip()
        if len(text) > max_length:
            raise InjectionError(f"{field}长度超过安全限制")
        return text


class Intent(str, Enum):
    BALANCE_QUERY = "BALANCE_QUERY"
    BILL_QUERY = "BILL_QUERY"
    BILL_ANALYSIS = "BILL_ANALYSIS"
    TRANSFER = "TRANSFER"
    SCHEDULED_TRANSFER = "SCHEDULED_TRANSFER"
    SPLIT_BILL = "SPLIT_BILL"
    CROSS_SCENE_LOCK = "CROSS_SCENE_LOCK"
    WEALTH_QUERY = "WEALTH_QUERY"
    WEALTH_RECOMMEND = "WEALTH_RECOMMEND"
    WEALTH_RISK_ASSESSMENT = "WEALTH_RISK_ASSESSMENT"
    WEALTH_COMPARE = "WEALTH_COMPARE"
    WEALTH_PURCHASE = "WEALTH_PURCHASE"
    WEALTH_REDEEM = "WEALTH_REDEEM"
    CARD_APPLY = "CARD_APPLY"
    CARD_QUERY = "CARD_QUERY"
    CARD_LIMIT = "CARD_LIMIT"
    CARD_FREEZE = "CARD_FREEZE"
    CARD_UNFREEZE = "CARD_UNFREEZE"
    CARD_REPORT_LOST = "CARD_REPORT_LOST"
    CARD_UNREPORT_LOST = "CARD_UNREPORT_LOST"
    PASSWORD_CHANGE = "PASSWORD_CHANGE"
    SUBSCRIPTION_QUERY = "SUBSCRIPTION_QUERY"
    SUBSCRIPTION_CANCEL = "SUBSCRIPTION_CANCEL"
    UNKNOWN = "UNKNOWN"


GREEN_INTENTS = {
    Intent.BALANCE_QUERY.value,
    Intent.BILL_QUERY.value,
    Intent.BILL_ANALYSIS.value,
    Intent.WEALTH_QUERY.value,
    Intent.WEALTH_RECOMMEND.value,
    Intent.WEALTH_RISK_ASSESSMENT.value,
    Intent.WEALTH_COMPARE.value,
    Intent.SUBSCRIPTION_QUERY.value,
    Intent.CARD_QUERY.value,
}
YELLOW_INTENTS = {
    Intent.TRANSFER.value,
    Intent.SCHEDULED_TRANSFER.value,
    Intent.SPLIT_BILL.value,
    Intent.CROSS_SCENE_LOCK.value,
    Intent.SUBSCRIPTION_CANCEL.value,
    Intent.CARD_APPLY.value,
}
RED_INTENTS = {
    Intent.WEALTH_PURCHASE.value,
    Intent.WEALTH_REDEEM.value,
    Intent.CARD_LIMIT.value,
    Intent.CARD_FREEZE.value,
    Intent.CARD_UNFREEZE.value,
    Intent.CARD_REPORT_LOST.value,
    Intent.CARD_UNREPORT_LOST.value,
    Intent.PASSWORD_CHANGE.value,
}
ALL_INTENTS = {item.value for item in Intent}


def risk_for_intent(intent: str, *, amount: Any = 0, daily_total: Any = 0) -> str:
    """Return the server-side baseline risk class for an allow-listed intent.

    Transfer risk is cumulative: a transfer is yellow while the resulting
    same-day total is at most 1,000 yuan and red above that threshold.  The
    core API remains the final authority; this function is for planning/UI
    previews and must never replace the API precheck.
    """

    normalized = str(intent or Intent.UNKNOWN.value).upper()
    if normalized in GREEN_INTENTS:
        return "GREEN"
    if normalized in RED_INTENTS:
        return "RED"
    if normalized in {Intent.TRANSFER.value, Intent.SCHEDULED_TRANSFER.value, Intent.SPLIT_BILL.value}:
        try:
            total = Decimal(str(daily_total)) + Decimal(str(amount))
        except (InvalidOperation, ValueError, TypeError):
            return "RED"
        return "YELLOW" if total <= Decimal("1000") else "RED"
    if normalized in YELLOW_INTENTS:
        return "YELLOW"
    return "UNKNOWN"


def parse_amount(value: Any, *, allow_zero: bool = False) -> Optional[float]:
    """Parse a finite yuan amount and normalize it to cents precision."""

    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value).replace(",", "").replace("￥", "").replace("¥", "").strip())
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("金额格式无效")
    if not amount.is_finite() or (amount < 0 if allow_zero else amount <= 0):
        raise ValueError("金额必须为有限的正数")
    amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount > Decimal("100000000"):
        raise ValueError("金额超过安全上限")
    return float(amount)


def validate_intent_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a model-produced intent object.

    Unknown keys are discarded, unknown intents become ``UNKNOWN`` and
    missing high-risk slots are left empty so the UI can ask a clarification
    question.  No fallback recipient is ever invented.
    """

    if not isinstance(payload, Mapping):
        raise ValueError("意图结果必须是 JSON 对象")
    raw_intent = str(payload.get("intent") or Intent.UNKNOWN.value).upper().strip()
    intent = raw_intent if raw_intent in ALL_INTENTS else Intent.UNKNOWN.value
    target = payload.get("target")
    if target is not None:
        target = sanitize_text(target, field="收款人", max_length=120)
        target = target or None
    extra = payload.get("extra_info")
    if extra is not None:
        extra = sanitize_text(extra, field="补充说明", max_length=500)
        extra = extra or None
    scope = str(payload.get("date_scope") or "ALL").upper().strip()
    aliases = {"今天": "TODAY", "今日": "TODAY", "本月": "MONTH", "上月": "LAST_MONTH", "今年": "YEAR", "年度": "YEAR", "全年": "YEAR"}
    scope = aliases.get(scope, scope)
    if scope not in {"TODAY", "MONTH", "LAST_MONTH", "YEAR", "ALL"}:
        scope = "ALL"
    amount = parse_amount(payload.get("amount"), allow_zero=True)
    confidence = payload.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(confidence))) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None
    slots = payload.get("slots")
    if not isinstance(slots, Mapping):
        slots = {}
    # Keep only JSON-friendly scalar slot values; large/model-controlled
    # objects must not be passed into an execution tool.
    safe_slots: dict[str, Any] = {}
    for key, value in slots.items():
        if not isinstance(key, str) or not isinstance(value, (str, int, float, bool, type(None))):
            continue
        if isinstance(value, float) and not math.isfinite(value):
            continue
        if isinstance(value, str):
            try:
                value = sanitize_text(value, field="意图槽位", max_length=500)
            except InjectionError:
                # A suspicious optional slot should be dropped rather than
                # allowed to influence a later prompt or tool call.
                continue
        safe_slots[key] = value
    raw_participants = payload.get("participants")
    if not isinstance(raw_participants, list):
        raw_participants = []
    participants = []
    for value in raw_participants[:20]:
        if isinstance(value, (str, int)):
            try:
                item = sanitize_text(value, field="分摊参与人", max_length=120)
            except InjectionError:
                continue
            if item and item not in participants:
                participants.append(item)
    raw_product_ids = payload.get("product_ids")
    if not isinstance(raw_product_ids, list):
        raw_product_ids = []
    product_ids = []
    for value in raw_product_ids[:10]:
        if isinstance(value, (str, int)):
            try:
                item = sanitize_text(value, field="理财产品编号", max_length=64)
            except InjectionError:
                continue
            item = item.upper()
            if item and item not in product_ids:
                product_ids.append(item)
    scheduled_at = payload.get("scheduled_at")
    if scheduled_at is not None:
        try:
            scheduled_at = sanitize_text(scheduled_at, field="计划时间", max_length=80) or None
        except InjectionError:
            scheduled_at = None
    event_at = payload.get("event_at")
    if event_at is not None:
        try:
            event_at = sanitize_text(event_at, field="事件时间", max_length=80) or None
        except InjectionError:
            event_at = None
    # The model may call the cross-scene birthday slot ``event_at`` while the
    # lightweight scheduler historically used ``scheduled_at``. Keep both
    # representations in sync for downstream policy checks.
    if event_at and not scheduled_at and intent == Intent.CROSS_SCENE_LOCK.value:
        scheduled_at = event_at
    if scheduled_at and not event_at and intent == Intent.CROSS_SCENE_LOCK.value:
        event_at = scheduled_at
    return {
        "intent": intent,
        "target": target,
        "amount": amount,
        "date_scope": scope,
        "extra_info": extra,
        "confidence": confidence,
        "slots": safe_slots,
        "participants": participants,
        "product_ids": product_ids,
        "scheduled_at": scheduled_at,
        "event_at": event_at,
    }


def needs_clarification(parsed: Mapping[str, Any]) -> bool:
    """Whether a request lacks a required slot or is too uncertain to execute."""

    intent = str(parsed.get("intent") or Intent.UNKNOWN.value)
    confidence = parsed.get("confidence")
    if intent == Intent.UNKNOWN.value:
        return True
    if confidence is not None:
        try:
            if float(confidence) < 0.65:
                return True
        except (TypeError, ValueError):
            return True
    if intent == Intent.WEALTH_RISK_ASSESSMENT.value:
        # A risk file is a suitability control, not a cosmetic label.  Never
        # silently fill a missing answer with LOW/NONE (or any other safe
        # looking default), because that would both mislead the customer and
        # make a later purchase authorization depend on guessed facts.  The
        # conversational layer asks for the missing questionnaire answers and
        # the Core endpoint enforces the same requirement independently.
        slots = parsed.get("slots") if isinstance(parsed.get("slots"), Mapping) else {}
        tolerance = str(slots.get("loss_tolerance") or "").upper().strip()
        experience = str(slots.get("experience") or "").upper().strip()
        horizon = slots.get("horizon_years")
        try:
            horizon_value = int(horizon) if horizon is not None and not isinstance(horizon, bool) else None
        except (TypeError, ValueError):
            horizon_value = None
        return (
            tolerance not in {"LOW", "MEDIUM", "HIGH"}
            or experience not in {"NONE", "BASIC", "PROFESSIONAL"}
            or horizon_value is None
            or horizon_value < 0
            or horizon_value > 100
        )
    if intent == Intent.SPLIT_BILL.value:
        # AA 收款不是向一个既定收款账户转账。它必须明确总金额和至少一位
        # 参与人；强制要求 ``target`` 会让正常的“和 A、B AA 300 元”永远
        # 停在澄清分支。
        participants = parsed.get("participants")
        return parsed.get("amount") in (None, 0) or not isinstance(participants, list) or not participants
    if intent == Intent.CROSS_SCENE_LOCK.value:
        # A reservation that promises a birthday reminder must carry an
        # explicit, parseable event date; never silently assume “next week”.
        return parsed.get("amount") in (None, 0) or not (parsed.get("scheduled_at") or parsed.get("event_at"))
    if intent == Intent.SCHEDULED_TRANSFER.value and not parsed.get("scheduled_at"):
        return True
    if intent == Intent.WEALTH_COMPARE.value:
        product_ids = parsed.get("product_ids")
        return not isinstance(product_ids, list) or len(product_ids) < 2
    if intent in {
        Intent.TRANSFER.value,
        Intent.SCHEDULED_TRANSFER.value,
        Intent.SUBSCRIPTION_CANCEL.value,
        Intent.WEALTH_PURCHASE.value,
        Intent.WEALTH_REDEEM.value,
        Intent.CARD_LIMIT.value,
        Intent.CROSS_SCENE_LOCK.value,
    }:
        if intent not in {Intent.CROSS_SCENE_LOCK.value, Intent.CARD_LIMIT.value} and not parsed.get("target"):
            return True
        if intent not in {Intent.SUBSCRIPTION_CANCEL.value} and parsed.get("amount") in (None, 0):
            # Some operations (card/report-lost) do not have an amount.
            return True
    return False


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_USER = "WAITING_USER"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class Task:
    task_id: str
    action: str
    depends_on: list[str] = field(default_factory=list)
    risk_level: str = "GREEN"
    status: TaskStatus = TaskStatus.PENDING
    idempotency_key: Optional[str] = None
    compensation: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value


@dataclass
class TaskPlan:
    plan_id: str
    intent: str
    tasks: list[Task]
    policy_version: str = "2026-09-09"

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "intent": self.intent,
            "policy_version": self.policy_version,
            "tasks": [task.to_dict() for task in self.tasks],
        }

    def ready_tasks(self) -> list[Task]:
        completed = {task.task_id for task in self.tasks if task.status == TaskStatus.SUCCEEDED}
        return [
            task
            for task in self.tasks
            if task.status == TaskStatus.PENDING and set(task.depends_on).issubset(completed)
        ]

    def mark(self, task_id: str, status: TaskStatus, *, error: Optional[str] = None) -> None:
        for task in self.tasks:
            if task.task_id == task_id:
                task.status = status
                task.error = error
                return
        raise KeyError(task_id)


def build_task_plan(parsed: Mapping[str, Any], *, plan_id: str = "plan-demo") -> TaskPlan:
    """Compile an allow-listed intent into a small deterministic DAG."""

    intent = str(parsed.get("intent") or Intent.UNKNOWN.value)
    amount = parsed.get("amount")
    target = parsed.get("target")
    base = {"target": target, "amount": amount, "date_scope": parsed.get("date_scope", "ALL"), "product_ids": list(parsed.get("product_ids") or []), "scheduled_at": parsed.get("scheduled_at"), "event_at": parsed.get("event_at"), "reminder_at": parsed.get("reminder_at")}
    tasks: list[Task]

    if intent == Intent.CROSS_SCENE_LOCK.value:
        tasks = [
            Task("validate_event", "VALIDATE_EVENT", risk_level="GREEN", details=base),
            Task("reserve_funds", "LOCK_FUND", ["validate_event"], "YELLOW", compensation="UNLOCK_FUND", details=base),
            Task("schedule_orders", "SCHEDULE_REMINDER", ["reserve_funds"], "YELLOW", details=base),
        ]
    elif intent == Intent.SCHEDULED_TRANSFER.value:
        tasks = [
            Task("validate_transfer", "VALIDATE_TRANSFER", risk_level=risk_for_intent(intent, amount=amount), details=base),
            Task("schedule_transfer", "SCHEDULE_TRANSFER", ["validate_transfer"], risk_for_intent(intent, amount=amount), details=base),
        ]
    elif intent == Intent.SPLIT_BILL.value:
        tasks = [
            Task("validate_split", "VALIDATE_SPLIT", risk_level="GREEN", details=base),
            Task("create_split_request", "CREATE_SPLIT_REQUEST", ["validate_split"], "YELLOW", details=base),
            Task("notify_participants", "NOTIFY_PARTICIPANTS", ["create_split_request"], "GREEN", details=base),
        ]
    elif intent == Intent.TRANSFER.value:
        level = risk_for_intent(intent, amount=amount)
        tasks = [
            Task("validate_transfer", "VALIDATE_TRANSFER", risk_level="GREEN", details=base),
            Task("execute_transfer", "TRANSFER", ["validate_transfer"], level, details=base),
        ]
    elif intent in {Intent.BILL_ANALYSIS.value, Intent.BILL_QUERY.value}:
        tasks = [
            Task("fetch_transactions", "FETCH_TRANSACTIONS", risk_level="GREEN", details=base),
            Task("compute_facts", "COMPUTE_BILL_FACTS", ["fetch_transactions"], "GREEN", details=base),
        ]
    elif intent in {Intent.WEALTH_RECOMMEND.value, Intent.WEALTH_QUERY.value}:
        tasks = [Task("fetch_products", "FETCH_WEALTH_DATA", risk_level="GREEN", details=base)]
    elif intent == Intent.WEALTH_RISK_ASSESSMENT.value:
        tasks = [Task("risk_assessment", "WEALTH_RISK_ASSESSMENT", risk_level="GREEN", details=base)]
    elif intent == Intent.WEALTH_COMPARE.value:
        tasks = [Task("compare_products", "WEALTH_COMPARE", risk_level="GREEN", details=base)]
    elif intent in {Intent.WEALTH_PURCHASE.value, Intent.WEALTH_REDEEM.value}:
        tasks = [
            Task("risk_assessment", "WEALTH_RISK_ASSESSMENT", risk_level="GREEN", details=base),
            Task("mfa_and_confirm", "MFA_CONFIRM", ["risk_assessment"], "RED", details=base),
            Task("settle_wealth", "WEALTH_SETTLE", ["mfa_and_confirm"], "RED", compensation="ROLLBACK_WEALTH", details=base),
        ]
    elif intent == Intent.CARD_QUERY.value:
        tasks = [Task("fetch_cards", "FETCH_CARDS", risk_level="GREEN", details=base)]
    elif intent == Intent.PASSWORD_CHANGE.value:
        tasks = [Task("password_change", "PASSWORD_CHANGE", risk_level="RED", details=base)]
    elif intent.startswith("CARD_"):
        level = risk_for_intent(intent)
        tasks = [Task("card_operation", intent, risk_level=level, details=base)]
    elif intent.startswith("SUBSCRIPTION_"):
        level = risk_for_intent(intent)
        tasks = [Task("subscription_operation", intent, risk_level=level, details=base)]
    elif intent in {Intent.BALANCE_QUERY.value}:
        tasks = [Task("fetch_balance", "FETCH_BALANCE", risk_level="GREEN", details=base)]
    else:
        tasks = [Task("clarify_request", "CLARIFY", risk_level="UNKNOWN", details=base)]

    for task in tasks:
        task.idempotency_key = f"{plan_id}:{task.task_id}"
    return TaskPlan(plan_id=plan_id, intent=intent, tasks=tasks)


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
    return None


def date_bounds(scope: str, *, now: Optional[datetime] = None) -> tuple[Optional[date], Optional[date]]:
    """Return inclusive start/exclusive end dates for a bill query."""

    current = now or datetime.now()
    today = current.date()
    normalized = str(scope or "ALL").upper()
    if normalized == "TODAY":
        return today, today + timedelta(days=1)
    first_this_month = today.replace(day=1)
    if normalized == "MONTH":
        if first_this_month.month == 12:
            next_month = date(first_this_month.year + 1, 1, 1)
        else:
            next_month = date(first_this_month.year, first_this_month.month + 1, 1)
        return first_this_month, next_month
    if normalized == "LAST_MONTH":
        last_end = first_this_month
        last_start = (first_this_month - timedelta(days=1)).replace(day=1)
        return last_start, last_end
    if normalized == "YEAR":
        return date(today.year, 1, 1), date(today.year + 1, 1, 1)
    return None, None


def filter_transactions(records: Iterable[Mapping[str, Any]], scope: str, *, now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Filter transactions without trusting model-generated dates."""

    start, end = date_bounds(scope, now=now)
    output: list[dict[str, Any]] = []
    for record in records:
        parsed = _parse_timestamp(record.get("time"))
        if start is not None:
            if parsed is None or not (start <= parsed.date() < end):
                continue
        output.append(dict(record))
    return output


def analyze_transactions(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Compute auditable bill facts and conservative anomaly flags.

    The result contains only values derived from the supplied records.  A
    generative model may phrase these facts, but it must not invent numbers or
    transaction IDs.
    """

    rows = [dict(r) for r in records]
    by_category: dict[str, float] = {}
    total = 0.0
    anomalies: list[dict[str, Any]] = []
    for row in rows:
        try:
            amount = parse_amount(row.get("amount"), allow_zero=True) or 0.0
        except ValueError:
            amount = 0.0
        total += amount
        category = str(row.get("category") or "其他")
        by_category[category] = round(by_category.get(category, 0.0) + amount, 2)
        timestamp = _parse_timestamp(row.get("time"))
        merchant = str(row.get("merchant") or "")
        reasons: list[str] = []
        if timestamp and (timestamp.hour < 6 or timestamp.hour >= 23) and amount >= 500:
            reasons.append("深夜大额")
        if any(word in merchant for word in ("境外", "海外", "国外")):
            reasons.append("境外商户")
        if category in {"可疑交易", "异常", "盗刷"}:
            reasons.append("标记为可疑类别")
        if reasons:
            anomalies.append({"tx_id": row.get("tx_id"), "amount": amount, "merchant": merchant, "reasons": reasons})
    denominator = total or 1.0
    category_breakdown = [
        {"category": category, "amount": amount, "share": round(amount / denominator, 4)}
        for category, amount in sorted(by_category.items(), key=lambda item: item[1], reverse=True)
    ]
    return {
        "record_count": len(rows),
        "total_spent": round(total, 2),
        "category_breakdown": category_breakdown,
        "anomalies": anomalies,
        "source_tx_ids": [row.get("tx_id") for row in rows if row.get("tx_id")],
    }


def safe_analysis_text(facts: Mapping[str, Any]) -> str:
    """Render a deterministic Chinese bill summary from verified facts."""

    total = float(facts.get("total_spent") or 0)
    count = int(facts.get("record_count") or 0)
    lines = [f"共核验 {count} 笔交易，支出合计 ¥{total:,.2f}。"]
    breakdown = facts.get("category_breakdown") or []
    if breakdown:
        items = "、".join(f"{x['category']} ¥{float(x['amount']):,.2f}（{float(x['share'])*100:.1f}%）" for x in breakdown)
        lines.append(f"分类统计：{items}。")
    anomalies = facts.get("anomalies") or []
    if anomalies:
        refs = "、".join(str(x.get("tx_id")) for x in anomalies if x.get("tx_id"))
        lines.append(f"风险提示：发现 {len(anomalies)} 笔需复核的交易（流水号：{refs}）。")
    else:
        lines.append("未在已提供流水中发现规则命中的明显异常；如有疑问请联系银行客服。")
    return "\n".join(lines)
