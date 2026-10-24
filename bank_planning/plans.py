"""Task plans: the per-request step ledger the graph advances and the client displays."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional


from bank_planning.intents import (
    Intent,
    risk_for_intent,
)


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

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaskPlan":
        """Rebuild a plan from its serialized form (round-trip with to_dict)."""

        tasks: list[Task] = []
        for item in value.get("tasks") or []:
            tasks.append(
                Task(
                    task_id=str(item["task_id"]),
                    action=str(item.get("action") or ""),
                    depends_on=list(item.get("depends_on") or []),
                    risk_level=str(item.get("risk_level") or "GREEN"),
                    status=TaskStatus(str(item.get("status") or "PENDING")),
                    idempotency_key=item.get("idempotency_key"),
                    compensation=item.get("compensation"),
                    details=dict(item.get("details") or {}),
                    error=item.get("error"),
                )
            )
        return cls(
            plan_id=str(value.get("plan_id") or "plan-demo"),
            intent=str(value.get("intent") or Intent.UNKNOWN.value),
            tasks=tasks,
            policy_version=str(value.get("policy_version") or "2026-09-09"),
        )

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


class PlanExecutor:
    """Deterministic executor for a :class:`TaskPlan`.

    The agent advances the DAG through this class so the plan is a truthful
    execution ledger: only tasks whose dependencies are SUCCEEDED are eligible,
    a settled step is marked SUCCEEDED/FAILED, downstream steps that are owned
    by the core task center stay PENDING with an honest note, and a failed
    step exposes its compensation action to the caller.
    """

    @staticmethod
    def find(plan: TaskPlan, task_id: str) -> Task:
        for task in plan.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(task_id)

    @staticmethod
    def next_ready(plan: TaskPlan) -> Optional[Task]:
        ready = plan.ready_tasks()
        return ready[0] if ready else None

    @staticmethod
    def mark_succeeded(plan: TaskPlan, task_id: str, *, note: Optional[str] = None) -> None:
        task = PlanExecutor.find(plan, task_id)
        task.status = TaskStatus.SUCCEEDED
        task.error = None
        if note:
            task.details["note"] = note

    @staticmethod
    def mark_failed(plan: TaskPlan, task_id: str, *, error: str) -> Optional[str]:
        """Mark a task FAILED and return its compensation action, if any."""

        task = PlanExecutor.find(plan, task_id)
        task.status = TaskStatus.FAILED
        task.error = str(error)[:500]
        return task.compensation

    @staticmethod
    def keep_pending(plan: TaskPlan, task_id: str, *, note: str) -> None:
        """Keep a downstream task PENDING and record why it is not executed yet."""

        task = PlanExecutor.find(plan, task_id)
        task.status = TaskStatus.PENDING
        task.error = None
        task.details["note"] = note


def build_task_plan(parsed: Mapping[str, Any], *, plan_id: str = "plan-demo") -> TaskPlan:
    """Compile an allow-listed intent into a small deterministic DAG."""

    intent = str(parsed.get("intent") or Intent.UNKNOWN.value)
    amount = parsed.get("amount")
    target = parsed.get("target")
    base = {"target": target, "amount": amount, "date_scope": parsed.get("date_scope", "ALL"), "date_range": parsed.get("date_range"), "product_ids": list(parsed.get("product_ids") or []), "scheduled_at": parsed.get("scheduled_at"), "event_at": parsed.get("event_at"), "reminder_at": parsed.get("reminder_at")}
    tasks: list[Task]

    if intent == Intent.CROSS_SCENE_LOCK.value:
        tasks = [
            Task("validate_event", "VALIDATE_EVENT", risk_level="GREEN", details=base),
            Task("reserve_funds", "LOCK_FUND", ["validate_event"], "YELLOW", compensation="UNLOCK_FUND", details=base),
            Task("schedule_orders", "SCHEDULE_REMINDER", ["reserve_funds"], "YELLOW", details=base),
        ]
    elif intent == Intent.FRAUD_DISPUTE.value:
        tasks = [
            Task("verify_transaction", "VERIFY_TRANSACTION", risk_level="GREEN", details=base),
            Task("explain_risk", "EXPLAIN_RISK", ["verify_transaction"], "GREEN", details=base),
            Task("mfa_confirm", "MFA_CONFIRM", ["explain_risk"], "RED", details=base),
            Task("freeze_card", "FREEZE_CARD", ["mfa_confirm"], "RED", details=base),
            Task("create_dispute_case", "CREATE_DISPUTE_CASE", ["freeze_card"], "RED", details=base),
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
    elif intent in {Intent.DISPUTE_QUERY.value, Intent.BANKING_HELP.value}:
        tasks = [Task("read_service", "READ_DISPUTES" if intent == Intent.DISPUTE_QUERY.value else "READ_SERVICE_GUIDE", risk_level="GREEN", details=base)]
    elif intent in {Intent.BILL_ANALYSIS.value, Intent.BILL_QUERY.value}:
        tasks = [
            Task("fetch_transactions", "FETCH_TRANSACTIONS", risk_level="GREEN", details=base),
            Task("compute_facts", "COMPUTE_BILL_FACTS", ["fetch_transactions"], "GREEN", details=base),
        ]
    elif intent in {Intent.WEALTH_RECOMMEND.value, Intent.WEALTH_QUERY.value}:
        tasks = [Task("fetch_products", "FETCH_WEALTH_DATA", risk_level="GREEN", details=base)]
    elif intent == Intent.WEALTH_HOLDINGS.value:
        tasks = [Task("fetch_holdings", "FETCH_WEALTH_HOLDINGS", risk_level="GREEN", details=base)]
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
    elif intent == Intent.UNDO_OPERATION.value:
        tasks = [Task("undo_operation", "UNDO_TASK", risk_level="YELLOW", details=base)]
    elif intent.startswith("CARD_"):
        level = risk_for_intent(intent)
        tasks = [Task("card_operation", intent, risk_level=level, details=base)]
    elif intent.startswith("SUBSCRIPTION_"):
        level = risk_for_intent(intent)
        slots = parsed.get("slots") or {}
        action = "SUBSCRIPTION_REMINDER" if intent == Intent.SUBSCRIPTION_QUERY.value and slots.get("renewal_reminder") is True else intent
        details = {**base, "days_before": slots.get("days_before", 2)} if action == "SUBSCRIPTION_REMINDER" else base
        tasks = [Task("subscription_operation", action, risk_level=level, details=details)]
    elif intent in {Intent.BALANCE_QUERY.value}:
        tasks = [Task("fetch_balance", "FETCH_BALANCE", risk_level="GREEN", details=base)]
    else:
        tasks = [Task("clarify_request", "CLARIFY", risk_level="UNKNOWN", details=base)]

    for task in tasks:
        task.idempotency_key = f"{plan_id}:{task.task_id}"
    return TaskPlan(plan_id=plan_id, intent=intent, tasks=tasks)
