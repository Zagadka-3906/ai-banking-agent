"""Rollback of operations that have not moved money yet.

Only reservations can be undone: a scheduled transfer before it settles, an AA
request nobody has paid, and a fund lock (with its reminder and order drafts).
A settled transfer or purchase is final - there is no reverse payment here.

The task page (``/tasks/{id}/cancel``), unlock (``/accounts/unlock_fund``) and
the conversational ``UNDO_TASK`` operation share these functions, so the three
paths cannot drift apart. Callers hold ``_ledger_transaction``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bank_core.ledger import ACCOUNTS, LOCKS, SCHEDULED_TRANSFERS, SPLIT_REQUESTS
from scheduler import TASK_STORE, TaskState
from security import STATE_LOCK, SecurityError

UNDOABLE_TASK_TYPES = {"SCHEDULED_TRANSFER": "预约转账", "SPLIT_BILL": "AA 收款请求", "FUND_RESERVATION": "资金锁定"}
# HUMAN_TAKEOVER is terminal: a task handed to a person is undone by that person.
UNDOABLE_STATES = {TaskState.PENDING, TaskState.WAITING_USER, TaskState.FAILED}


def cancel_task_branch(actor: str, task_id: str):
    """Cancel a task, its schedule/AA reservation and its pending dependents."""

    task = TASK_STORE.cancel(task_id, owner_id=actor)
    # Keep money-movement reservations in sync with the task state so a
    # cancelled scheduled/AA request no longer contributes to the daily risk
    # accumulator. The task store remains the source of ownership
    # authorization; this only updates the corresponding domain fixture after
    # cancellation succeeds.
    with STATE_LOCK:
        schedule_id = task.payload.get("schedule_id")
        split_id = task.payload.get("split_id")
        if schedule_id:
            for row in SCHEDULED_TRANSFERS:
                if row.get("schedule_id") == schedule_id:
                    row["status"] = "CANCELLED"
                    row["cancelled_at"] = datetime.now().isoformat()
        if split_id:
            for row in SPLIT_REQUESTS:
                if row.get("split_id") == split_id:
                    row["status"] = "CANCELLED"
                    row["cancelled_at"] = datetime.now().isoformat()
        # A cancelled prerequisite must not leave dependent reminder or
        # order-draft nodes runnable. Cascade only to non-terminal tasks owned
        # by the same principal; terminal records remain immutable for
        # auditability.
        cancelled_lock_id = task.payload.get("lock_id") if isinstance(task.payload, dict) and task.task_type in {"REMINDER", "FUND_RESERVATION"} else None
        for dependent in TASK_STORE.list(owner_id=actor):
            same_reservation = bool(cancelled_lock_id and dependent.payload.get("lock_id") == cancelled_lock_id)
            if (task_id in dependent.depends_on or same_reservation) and dependent.status in {TaskState.PENDING, TaskState.WAITING_USER, TaskState.FAILED}:
                try:
                    TASK_STORE.transition(dependent.task_id, TaskState.CANCELLED, owner_id=actor)
                except (KeyError, ValueError):
                    pass
    return task


def release_locks(actor: str, user_id: str, candidates: list[dict[str, Any]], amount: float) -> list[str]:
    """Release ``amount`` from ``candidates`` (oldest first) and withdraw their pending tasks."""

    account = ACCOUNTS[user_id]
    left = amount
    released_ids: list[str] = []
    for row in candidates:
        if left <= 0:
            break
        release = min(left, float(row["amount"]))
        row["amount"] = round(float(row["amount"]) - release, 2)
        left = round(left - release, 2)
        released_ids.append(row["lock_id"])
        if row["amount"] <= 0:
            row["status"] = "RELEASED"
            row["released_at"] = datetime.now().isoformat()
    account["locked_amount"] = round(max(0.0, float(account["locked_amount"]) - amount), 2)
    released_set = set(released_ids)
    for queued in TASK_STORE.list(owner_id=actor):
        if queued.payload.get("lock_id") in released_set and queued.status in {TaskState.PENDING, TaskState.WAITING_USER}:
            try:
                TASK_STORE.transition(queued.task_id, TaskState.CANCELLED, owner_id=actor)
            except (KeyError, ValueError):
                pass
    return released_ids


def undo_candidate(owner: str, task_id: str):
    """The owner's task if it can still be undone, otherwise a clear refusal."""

    task = TASK_STORE.get(task_id, owner_id=owner) if task_id else None
    if task is None:
        raise SecurityError("没有找到可撤销的操作", 404, "UNDO_TARGET_NOT_FOUND")
    if task.task_type not in UNDOABLE_TASK_TYPES:
        raise SecurityError("该操作不支持撤销", 409, "UNDO_NOT_SUPPORTED")
    payload = task.payload if isinstance(task.payload, dict) else {}
    # A reservation task is SUCCEEDED once the funds are locked (an immutable
    # record of that fact); whether it can be undone depends on the lock.
    if task.task_type != "FUND_RESERVATION" and task.status not in UNDOABLE_STATES:
        raise SecurityError("该操作已执行或已结束，无法撤销", 409, "UNDO_TOO_LATE")
    if task.task_type == "SPLIT_BILL":
        split = next((row for row in SPLIT_REQUESTS if row.get("split_id") == payload.get("split_id")), None)
        if split is None or float(split.get("collected_amount") or 0) > 0:
            raise SecurityError("已有参与人付款，AA 请求无法整体撤回", 409, "UNDO_TOO_LATE")
    if task.task_type == "FUND_RESERVATION":
        lock = LOCKS.get(str(payload.get("lock_id") or ""))
        if lock is None or lock.get("user_id") != owner or lock.get("status") != "LOCKED":
            raise SecurityError("该笔资金已使用或已解除锁定", 409, "UNDO_TOO_LATE")
    return task


def undo_summary(task) -> str:
    """One line for the approval card, built from Core's own record."""

    payload = task.payload if isinstance(task.payload, dict) else {}
    kind = UNDOABLE_TASK_TYPES.get(task.task_type, task.task_type)
    if task.task_type == "SCHEDULED_TRANSFER":
        return f"撤销{kind}：向 {payload.get('target') or '收款人'} 转账 ¥{float(payload.get('amount') or 0):,.2f}，原定 {payload.get('scheduled_at') or '指定时间'}"
    if task.task_type == "SPLIT_BILL":
        return f"撤回{kind}：共 ¥{float(payload.get('amount') or 0):,.2f}，参与人 {'、'.join(map(str, payload.get('participants') or [])) or '未记录'}"
    lock = LOCKS.get(str(payload.get("lock_id") or "")) or {}
    return f"解除{kind}：释放 ¥{float(lock.get('amount') or payload.get('amount') or 0):,.2f}，同时取消关联提醒与订单草稿"


def undo_task(actor: str, task_id: str) -> dict[str, Any]:
    """Undo one confirmed task; the caller verified the single-use approval."""

    task = undo_candidate(actor, task_id)
    if task.task_type == "FUND_RESERVATION":
        lock = LOCKS[str(task.payload["lock_id"])]
        released = release_locks(actor, actor, [lock], float(lock["amount"]))
        return {"status": "UNDONE", "task": task.to_dict(), "kind": task.task_type, "released_lock_ids": released,
                "locked_amount": ACCOUNTS[actor]["locked_amount"]}
    task = cancel_task_branch(actor, task.task_id)
    return {"status": "UNDONE", "task": task.to_dict(), "kind": task.task_type}
