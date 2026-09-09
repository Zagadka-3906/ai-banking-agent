"""Minimal durable-shaped task queue for the competition sandbox.

The demo keeps tasks in memory, but models the fields a production scheduler
would persist: owner, dependencies, idempotency key, status, retry budget and a
compensation action.  It never executes arbitrary model-generated code.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from copy import deepcopy
import threading
import uuid
from typing import Any, Optional


class TaskState:
    PENDING = "PENDING"
    WAITING_USER = "WAITING_USER"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    HUMAN_TAKEOVER = "HUMAN_TAKEOVER"


@dataclass
class ScheduledTask:
    task_id: str
    owner_id: str
    task_type: str
    payload: dict[str, Any]
    run_at: Optional[str] = None
    depends_on: list[str] = field(default_factory=list)
    idempotency_key: str = ""
    compensation: Optional[str] = None
    status: str = TaskState.PENDING
    attempts: int = 0
    max_attempts: int = 3
    error: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaskStore:
    """Thread-safe in-memory store with dependency-aware state transitions."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, ScheduledTask] = {}
        self._by_key: dict[tuple[str, str], str] = {}

    def create(
        self,
        owner_id: str,
        task_type: str,
        payload: dict[str, Any],
        *,
        run_at: Optional[str] = None,
        depends_on: Optional[list[str]] = None,
        idempotency_key: Optional[str] = None,
        compensation: Optional[str] = None,
        max_attempts: int = 3,
    ) -> ScheduledTask:
        key = idempotency_key or f"{owner_id}:{task_type}:{uuid.uuid4().hex}"
        scoped = (owner_id, key)
        with self._lock:
            previous_id = self._by_key.get(scoped)
            if previous_id and previous_id in self._tasks:
                return self._tasks[previous_id]
            task = ScheduledTask(
                task_id=f"TSK{uuid.uuid4().hex[:14].upper()}",
                owner_id=owner_id,
                task_type=task_type,
                payload=dict(payload),
                run_at=run_at,
                depends_on=list(depends_on or []),
                idempotency_key=key,
                compensation=compensation,
                max_attempts=max(1, min(int(max_attempts), 10)),
            )
            self._tasks[task.task_id] = task
            self._by_key[scoped] = task.task_id
            return task

    def get(self, task_id: str, *, owner_id: Optional[str] = None) -> Optional[ScheduledTask]:
        with self._lock:
            task = self._tasks.get(task_id)
            # ``None`` means an explicitly privileged/internal lookup.  An
            # empty string is still a supplied owner and must not degrade into
            # an unscoped read.
            if task and owner_id is not None and task.owner_id != owner_id:
                return None
            return task

    def list(self, *, owner_id: Optional[str] = None) -> list[ScheduledTask]:
        with self._lock:
            values = list(self._tasks.values())
            if owner_id is not None:
                values = [task for task in values if task.owner_id == owner_id]
            return sorted(values, key=lambda task: task.created_at)

    def transition(self, task_id: str, status: str, *, error: Optional[str] = None, owner_id: Optional[str] = None) -> ScheduledTask:
        allowed = {value for value in vars(TaskState).values() if isinstance(value, str) and value.isupper()}
        if status not in allowed:
            raise ValueError("unknown task status")
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or (owner_id is not None and task.owner_id != owner_id):
                raise KeyError(task_id)
            # Terminal task records are immutable from the public task
            # actions.  Allowing a completed/cancelled task to be moved back
            # into RUNNING would make compensation and risk reservations
            # ambiguous, and could create a second execution on retry.
            if task.status in {TaskState.SUCCEEDED, TaskState.CANCELLED, TaskState.HUMAN_TAKEOVER} and status != task.status:
                raise ValueError(f"cannot transition terminal task from {task.status} to {status}")
            if status == TaskState.RUNNING:
                if task.attempts >= task.max_attempts:
                    raise ValueError("retry budget exhausted")
                unmet = [dep for dep in task.depends_on if not self._tasks.get(dep) or self._tasks[dep].status != TaskState.SUCCEEDED]
                if unmet:
                    raise ValueError(f"dependencies not complete: {','.join(unmet)}")
                task.attempts += 1
            task.status = status
            task.error = str(error)[:500] if error is not None else None
            task.updated_at = datetime.now(timezone.utc).isoformat()
            return task

    def cancel(self, task_id: str, *, owner_id: Optional[str] = None) -> ScheduledTask:
        return self.transition(task_id, TaskState.CANCELLED, owner_id=owner_id)

    def takeover(self, task_id: str, *, owner_id: Optional[str] = None, reason: str = "人工接管") -> ScheduledTask:
        return self.transition(task_id, TaskState.HUMAN_TAKEOVER, error=reason, owner_id=owner_id)

    def reset(self) -> None:
        with self._lock:
            self._tasks.clear()
            self._by_key.clear()

    def snapshot(self) -> tuple[dict[str, ScheduledTask], dict[tuple[str, str], str]]:
        """Return a deep snapshot for the core's in-memory transaction shim.

        The competition core uses an in-memory ledger, but a failed operation
        must still be all-or-nothing.  Keeping snapshot/restore here avoids
        reaching into the store's private dictionaries from endpoint code and
        gives the production migration a clear replacement seam.
        """

        with self._lock:
            return deepcopy(self._tasks), deepcopy(self._by_key)

    def restore(self, snapshot: tuple[dict[str, ScheduledTask], dict[tuple[str, str], str]]) -> None:
        with self._lock:
            tasks, by_key = snapshot
            self._tasks.clear()
            self._tasks.update(deepcopy(tasks))
            self._by_key.clear()
            self._by_key.update(deepcopy(by_key))

    def ready(self, *, now: Optional[datetime] = None) -> list[ScheduledTask]:
        """Return pending tasks whose dependencies and due time are satisfied."""

        current = now or datetime.now(timezone.utc)
        with self._lock:
            result: list[ScheduledTask] = []
            for task in self._tasks.values():
                if task.status != TaskState.PENDING:
                    continue
                if any(not self._tasks.get(dep) or self._tasks[dep].status != TaskState.SUCCEEDED for dep in task.depends_on):
                    continue
                if task.run_at:
                    try:
                        due = datetime.fromisoformat(task.run_at.replace("Z", "+00:00"))
                        if due.tzinfo is None:
                            due = due.replace(tzinfo=timezone.utc)
                        if due > current:
                            continue
                    except ValueError:
                        continue
                result.append(task)
            return result


TASK_STORE = TaskStore()
