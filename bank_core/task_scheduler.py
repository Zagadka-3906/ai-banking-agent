"""Background scheduler that advances due tasks without an external caller.

Renewal reminders, birthday order drafts and scheduled transfers carry a
``run_at``.  Before this loop existed only ``POST /tasks/run-due`` moved them,
and nothing in a deployment called it, so a due reminder never fired and a
birthday order never reached the owner's confirmation.  The loop reuses the
endpoint's logic: reminders complete, money movement stops at WAITING_USER.

``BANK_TASK_SCHEDULER_SECONDS`` sets the period (default 60, ``0`` disables);
the test suite disables it so task timing stays deterministic.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from security import audit_event

logger = logging.getLogger("mock_banking.scheduler")

_STOP = threading.Event()
_THREAD: Optional[threading.Thread] = None


def scheduler_interval_seconds() -> float:
    try:
        value = float(os.getenv("BANK_TASK_SCHEDULER_SECONDS", "60"))
    except (TypeError, ValueError):
        value = 60.0
    if value <= 0:
        return 0.0
    return max(5.0, min(value, 3600.0))


def run_scheduler_once() -> int:
    """Advance every owner's due tasks once; returns how many changed."""

    from bank_core.routes.tasks import advance_due_tasks

    changed = advance_due_tasks(owner=None)
    if changed:
        audit_event("TASK_SCHEDULER_RUN", decision="ALLOW", outcome="SUCCESS",
                    metadata={"changed": len(changed), "task_ids": [row["task_id"] for row in changed][:20]})
    return len(changed)


def _loop(interval: float) -> None:
    while not _STOP.wait(interval):
        try:
            run_scheduler_once()
        except Exception:  # pragma: no cover - a failed tick must not kill the loop
            logger.exception("Due-task scheduler tick failed")


def start_scheduler() -> bool:
    """Start the loop once per process; returns whether it is running."""

    global _THREAD
    interval = scheduler_interval_seconds()
    if not interval:
        return False
    if _THREAD is not None and _THREAD.is_alive():
        return True
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, args=(interval,), name="due-task-scheduler", daemon=True)
    _THREAD.start()
    return True


def stop_scheduler() -> None:
    global _THREAD
    _STOP.set()
    if _THREAD is not None:
        _THREAD.join(timeout=5)
    _THREAD = None


def scheduler_status() -> dict[str, object]:
    return {"interval_seconds": scheduler_interval_seconds(), "running": bool(_THREAD and _THREAD.is_alive())}
