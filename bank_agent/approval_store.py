"""Pending approvals: the record kept per approval handle and its optional durable store."""

from __future__ import annotations

import hmac
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from security import AUTH_SECRET_CONFIGURED, resolve_store_digest_algorithm, store_integrity_digest

try:  # The graph is optional so this module stays importable in minimal setups.
    from agent import build_graph, channel_session
except Exception:  # pragma: no cover - dependency guidance path
    build_graph = None  # type: ignore[assignment]

    @contextmanager
    def channel_session(token: Optional[str]):
        """Fallback when the graph module is unavailable: present nothing."""

        yield

try:
    from langgraph.checkpoint.memory import MemorySaver
except Exception:  # pragma: no cover
    MemorySaver = None  # type: ignore[assignment,misc]

try:
    from langgraph.types import Command
except Exception:  # pragma: no cover
    Command = None  # type: ignore[assignment,misc]
from bank_agent.conversation_base import (  # noqa: F401
    logger,
)


@dataclass
class Approval:
    """A pending human approval, held server-side.

    ``auth_token`` is the signed authorization nonce issued by the core during
    the precheck.  It is never serialized to a client.
    """

    approval_id: str
    principal: str
    thread_id: str
    conversation_id: str
    request_id: str
    risk_level: str
    required_actions: list[str]
    summary: str
    auth_token: Optional[str]
    expires_at: float
    attempts: int = 0
    resolved: bool = False

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    def public_view(self) -> dict[str, Any]:
        """The only projection a client is allowed to see."""

        return {
            "approval_id": self.approval_id,
            "risk_level": self.risk_level,
            "required_actions": list(self.required_actions),
            "summary": self.summary,
            "mfa_required": self.risk_level == "RED",
            "expires_in": max(0, int(self.expires_at - time.time())),
        }

    def to_store_record(self) -> dict[str, Any]:
        """The durable projection: everything needed to resume, nothing extra."""

        return {
            "approval_id": self.approval_id,
            "principal": self.principal,
            "thread_id": self.thread_id,
            "conversation_id": self.conversation_id,
            "request_id": self.request_id,
            "risk_level": self.risk_level,
            "required_actions": list(self.required_actions),
            "summary": self.summary,
            "auth_token": self.auth_token,
            "expires_at": self.expires_at,
            "attempts": self.attempts,
            "resolved": self.resolved,
        }

    @classmethod
    def from_store_record(cls, record: Mapping[str, Any]) -> "Approval":
        return cls(
            approval_id=str(record["approval_id"]),
            principal=str(record["principal"]),
            thread_id=str(record["thread_id"]),
            conversation_id=str(record["conversation_id"]),
            request_id=str(record.get("request_id") or ""),
            risk_level=str(record.get("risk_level") or "YELLOW"),
            required_actions=[str(item) for item in (record.get("required_actions") or [])],
            summary=str(record.get("summary") or "该操作需要您的确认。"),
            auth_token=record.get("auth_token"),
            expires_at=float(record.get("expires_at") or 0.0),
            attempts=int(record.get("attempts") or 0),
            resolved=bool(record.get("resolved")),
        )


# ---------------------------------------------------------------------------
# Durable approval registry
#
# The conversation checkpointer already survives a restart, but an approval
# handle lived only in this process: after a gateway recreate the client kept
# an ``approval_id`` the new process had never heard of, and the paused turn
# it pointed at was unreachable until the nonce expired.  With a file-backed
# checkpointer the pending approvals are persisted next to it (or wherever
# ``BANK_APPROVAL_STORE_PATH`` points) and reloaded at start-up, so a pending
# approval can be resolved across a restart.
#
# The file necessarily carries the signed authorization nonce while it waits -
# that is what the handle resolves to - so it gets the same treatment as the
# device registry: an integrity digest keyed by ``BANK_AUTH_SECRET`` when one
# is configured (a plain checksum otherwise, reported honestly by
# ``approval_store_status``), an atomic replace-on-write, and a hard freeze on
# any file that fails validation so a later approval cannot overwrite the
# evidence.  The caller's session token is *not* stored: the resumed turn runs
# with whatever credential the approver presents, so a restart plus a fresh
# login is all it takes to continue.
# ---------------------------------------------------------------------------

APPROVAL_STORE_VERSION = 1
_APPROVAL_STORE_BLOCKED = False
_APPROVAL_STORE_LOAD_SUMMARY: dict[str, Any] = {}


def _approval_store_target() -> Optional[str]:
    """Where the approval registry persists, or None for memory only.

    Defaults to sitting beside the conversation checkpointer: approvals are
    only resumable when the paused graph state they point into survives too,
    so the two live and die together.
    """

    explicit = (os.getenv("BANK_APPROVAL_STORE_PATH") or "").strip()
    if explicit:
        # An explicit "memory" opts out of durability entirely - what the test
        # suite and the offline demo want.
        if explicit.lower() in {"memory", ":memory:"}:
            return None
        return explicit
    db = (os.getenv("BANK_CONVERSATION_DB", "agent_conversations.sqlite3") or "").strip()
    if not db or db.lower() in {"memory", ":memory:"}:
        return None
    return f"{db}.approvals.json"


def persist_approvals_locked(approvals: Mapping[str, Approval]) -> None:
    """Write the live approvals atomically.  Caller holds ``self._lock``."""

    global _APPROVAL_STORE_BLOCKED
    target = _approval_store_target()
    if not target or _APPROVAL_STORE_BLOCKED:
        return
    # Only approvals a restart could still resolve are worth persisting; a
    # resolved or expired handle is dead weight (and a resolved nonce must not
    # look pending again after a reload).
    live = {
        approval_id: record.to_store_record()
        for approval_id, record in sorted(approvals.items())
        if not record.resolved and not record.expired
    }
    canonical = json.dumps(live, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    snapshot = {
        "version": APPROVAL_STORE_VERSION,
        "updated_at": int(time.time()),
        "approvals": live,
        "integrity": store_integrity_digest(canonical, keyed=AUTH_SECRET_CONFIGURED),
    }
    temporary = f"{target}.tmp"
    try:
        directory = os.path.dirname(os.path.abspath(target))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass  # best effort; Windows ACLs are not POSIX modes
        os.replace(temporary, target)
    except OSError:
        _APPROVAL_STORE_BLOCKED = True
        logger.exception("Unable to persist the approval registry")


def load_approval_store() -> dict[str, Any]:
    """Load persisted approvals, refusing anything that fails validation."""

    global _APPROVAL_STORE_BLOCKED
    target = _approval_store_target()
    if not target:
        return {"loaded_approvals": 0, "path": None, "message": "approval store not configured (memory only)"}
    try:
        with open(target, "r", encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except FileNotFoundError:
        return {"loaded_approvals": 0, "path": target, "message": "审批文件不存在，从空注册表开始"}
    except OSError as exc:
        _APPROVAL_STORE_BLOCKED = True
        return {"loaded_approvals": 0, "path": target, "rejected": True, "error": f"审批文件无法读取: {type(exc).__name__}"}
    except json.JSONDecodeError:
        _APPROVAL_STORE_BLOCKED = True
        return {"loaded_approvals": 0, "path": target, "rejected": True, "error": "审批文件不是合法 JSON"}

    if not isinstance(snapshot, dict) or snapshot.get("version") != APPROVAL_STORE_VERSION:
        _APPROVAL_STORE_BLOCKED = True
        return {"loaded_approvals": 0, "path": target, "rejected": True, "error": "审批文件版本不受支持"}
    approvals = snapshot.get("approvals")
    integrity = snapshot.get("integrity")
    if not isinstance(approvals, dict) or not isinstance(integrity, dict):
        _APPROVAL_STORE_BLOCKED = True
        return {"loaded_approvals": 0, "path": target, "rejected": True, "error": "审批文件结构无效"}

    canonical = json.dumps(approvals, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # Algorithm chosen from our configuration, not from the file: see
    # ``resolve_store_digest_algorithm``.
    keyed, algorithm_error = resolve_store_digest_algorithm(integrity)
    if algorithm_error:
        _APPROVAL_STORE_BLOCKED = True
        return {"loaded_approvals": 0, "path": target, "rejected": True, "error": algorithm_error}
    expected = store_integrity_digest(canonical, keyed=bool(keyed))["value"]
    if not hmac.compare_digest(expected, str(integrity.get("value") or "")):
        _APPROVAL_STORE_BLOCKED = True
        return {"loaded_approvals": 0, "path": target, "rejected": True, "error": "审批文件完整性校验失败"}
    for approval_id, record in approvals.items():
        if not isinstance(record, dict) or record.get("approval_id") != approval_id:
            _APPROVAL_STORE_BLOCKED = True
            return {"loaded_approvals": 0, "path": target, "rejected": True, "error": f"审批记录无效: {approval_id}"}
    return {
        "loaded_approvals": len(approvals),
        "path": target,
        "rejected": False,
        "keyed": bool(keyed),
        "records": approvals,
    }


def approval_store_status() -> dict[str, Any]:
    """Describe registry durability without leaking the configured path."""

    configured = bool(_approval_store_target())
    return {
        "mode": "memory+file" if configured else "memory_only",
        "file_configured": configured,
        "file_loaded_approvals": _APPROVAL_STORE_LOAD_SUMMARY.get("loaded_approvals", 0),
        "file_rejected": bool(_APPROVAL_STORE_LOAD_SUMMARY.get("rejected")),
        "writes_frozen": _APPROVAL_STORE_BLOCKED,
        "integrity_keyed": AUTH_SECRET_CONFIGURED,
    }
