"""Tamper-evident audit trail: hash chain, per-service file and request traces.

The persisted chain is replayed when this module is imported.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from bank_security.config import STATE_LOCK, _env_int, logger
from bank_security.sanitize import redact


AUDIT_LOGS: list[dict[str, Any]] = []
AUDIT_MAX_EVENTS = _env_int("BANK_AUDIT_MAX_EVENTS", 5000, 100, 100_000)


_AUDIT_PREVIOUS_HASH = "0" * 64
# ``AUDIT_LOGS`` is intentionally bounded for the in-memory demo.  Retain the
# predecessor hash of the first retained event so verification remains valid
# after log rotation instead of restarting from genesis and reporting a false
# tamper signal.
_AUDIT_CHAIN_ANCHOR = "0" * 64


def audit_event(
    action: str,
    *,
    user_id: Optional[str] = None,
    risk_level: Optional[str] = None,
    decision: Optional[str] = None,
    outcome: Optional[str] = None,
    reason: Optional[str] = None,
    request_id: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Append a tamper-evident, redacted audit event and return it."""

    global _AUDIT_PREVIOUS_HASH, _AUDIT_CHAIN_ANCHOR
    now = datetime.now(timezone.utc)
    with STATE_LOCK:
        event: dict[str, Any] = {
            "event_id": f"AUD{secrets.token_hex(8).upper()}",
            "timestamp": now.isoformat(),
            "action": action,
            "user_id": redact(user_id),
            "risk_level": risk_level,
            "decision": decision,
            "outcome": outcome,
            "reason": redact(reason),
            "request_id": redact(request_id),
            "metadata": redact(dict(metadata or {})),
        }
        canonical = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        event["integrity_hash"] = hashlib.sha256(
            (_AUDIT_PREVIOUS_HASH + canonical).encode("utf-8")
        ).hexdigest()
        _AUDIT_PREVIOUS_HASH = event["integrity_hash"]
        AUDIT_LOGS.append(event)
        if len(AUDIT_LOGS) > AUDIT_MAX_EVENTS:
            excess = len(AUDIT_LOGS) - AUDIT_MAX_EVENTS
            _AUDIT_CHAIN_ANCHOR = AUDIT_LOGS[excess - 1]["integrity_hash"]
            del AUDIT_LOGS[:excess]

        # Core and Gateway each append their own chain (see ``audit_log_path``);
        # events are redacted before they reach the file.
        audit_path = audit_log_path()
        if audit_path:
            try:
                os.makedirs(os.path.dirname(audit_path) or ".", exist_ok=True)
                with open(audit_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            except OSError:
                logger.exception("Unable to append audit event")
        # Return an isolated snapshot so callers cannot mutate the stored event
        # (including nested metadata) and thereby invalidate or rewrite the
        # in-memory audit trail after it has been hashed.
        return deepcopy(event)


def get_audit_logs(*, limit: int = 100, user_id: Optional[str] = None) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 1000))
    with STATE_LOCK:
        selected = [e for e in AUDIT_LOGS if user_id is None or e.get("user_id") == user_id]
        return deepcopy(selected[-limit:])


def get_audit_trace(request_id: str) -> dict[str, Any]:
    """Return the ordered decision chain for one request_id.

    The core writes every precheck, confirmation, MFA, settlement and token
    consumption event with the caller's X-Request-Id, so one id reconstructs
    the full authoritative decision path of a business request.  Agent-side
    events (intent parse, responder validation) live in the agent process and
    are not part of this core-side chain in the demo deployment.
    """

    with STATE_LOCK:
        events = [deepcopy(event) for event in AUDIT_LOGS if event.get("request_id") == request_id]
    if not events:
        return {"request_id": request_id, "principal": None, "stage_count": 0, "stages": [], "events": []}
    return {
        "request_id": request_id,
        "principal": events[0].get("user_id"),
        "stage_count": len(events),
        "stages": [event.get("action") for event in events],
        "risk_levels": [event.get("risk_level") for event in events],
        "first_at": events[0].get("timestamp"),
        "last_at": events[-1].get("timestamp"),
        "terminal_outcome": next((event.get("outcome") for event in reversed(events) if event.get("action") != "WORKFLOW_STAGE"), None),
        "events": events,
    }


def verify_audit_chain() -> dict[str, Any]:
    """Verify the in-memory audit hash chain without exposing credentials."""

    checked = 0
    with STATE_LOCK:
        previous = _AUDIT_CHAIN_ANCHOR
        for event in AUDIT_LOGS:
            supplied = event.get("integrity_hash")
            body = {key: value for key, value in event.items() if key != "integrity_hash"}
            canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            expected = hashlib.sha256((previous + canonical).encode("utf-8")).hexdigest()
            if not isinstance(supplied, str) or not hmac.compare_digest(expected, supplied):
                return {"valid": False, "checked_events": checked, "failed_event_id": event.get("event_id")}
            previous = supplied
            checked += 1
    return {"valid": True, "checked_events": checked, "failed_event_id": None}


AUDIT_SERVICE = os.getenv("BANK_AUDIT_SERVICE", "").strip().lower() or None


def _default_audit_dir() -> str:
    base = os.getenv("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "bank-agent", "audit")


def audit_log_path() -> Optional[str]:
    """The append-only file this process writes, or None for memory only.

    Core and Gateway read the same ``.env``. Two processes appending separate
    hash chains to one file broke each other's links, so a restart rejected
    genuine events as tampered. A service process (``BANK_AUDIT_SERVICE``, set
    by its entry module) therefore always writes its own file: by default
    ``<BANK_AUDIT_LOG_DIR>/<service>-audit.jsonl`` outside the repository, and
    an explicit ``BANK_AUDIT_LOG_PATH`` gains the service name before its
    suffix. ``BANK_AUDIT_LOG_DIR=off`` keeps the events in memory only.
    """

    explicit = os.getenv("BANK_AUDIT_LOG_PATH", "").strip()
    if explicit:
        if not AUDIT_SERVICE:
            return explicit
        stem, suffix = os.path.splitext(explicit)
        return f"{stem}.{AUDIT_SERVICE}{suffix or '.jsonl'}"
    if not AUDIT_SERVICE:
        return None
    directory = os.getenv("BANK_AUDIT_LOG_DIR", "").strip()
    if directory.lower() == "off":
        return None
    return os.path.join(directory or _default_audit_dir(), f"{AUDIT_SERVICE}-audit.jsonl")


def load_audit_file(path: Optional[str] = None) -> dict[str, Any]:
    """Replay an append-only audit file into the in-memory hash chain.

    Each persisted line is re-hashed against the running chain, so a tampered
    or truncated file is detected instead of being silently trusted.  Runs
    once per process at import, for the file ``audit_log_path`` selects.
    """

    global _AUDIT_PREVIOUS_HASH, _AUDIT_CHAIN_ANCHOR
    target = path or audit_log_path()
    if not target:
        return {"loaded_events": 0, "path": None, "message": "audit file disabled"}
    loaded = 0
    with STATE_LOCK:
        try:
            handle = open(target, "r", encoding="utf-8")
        except FileNotFoundError:
            return {"loaded_events": 0, "path": target, "message": "审计文件不存在，从空链开始"}
        except OSError as exc:
            return {"loaded_events": 0, "path": target, "error": str(exc)}
        with handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed audit line")
                    continue
                if not isinstance(event, dict) or not isinstance(event.get("integrity_hash"), str):
                    logger.warning("Skipping audit line without integrity hash")
                    continue
                body = {key: value for key, value in event.items() if key != "integrity_hash"}
                canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                expected = hashlib.sha256((_AUDIT_PREVIOUS_HASH + canonical).encode("utf-8")).hexdigest()
                if not hmac.compare_digest(expected, event["integrity_hash"]):
                    return {
                        "loaded_events": loaded,
                        "path": target,
                        "tampered": True,
                        "error": f"审计文件哈希链校验失败于 {event.get('event_id')}",
                    }
                _AUDIT_PREVIOUS_HASH = event["integrity_hash"]
                AUDIT_LOGS.append(event)
                loaded += 1
                if len(AUDIT_LOGS) > AUDIT_MAX_EVENTS:
                    excess = len(AUDIT_LOGS) - AUDIT_MAX_EVENTS
                    _AUDIT_CHAIN_ANCHOR = AUDIT_LOGS[excess - 1]["integrity_hash"]
                    del AUDIT_LOGS[:excess]
    return {"loaded_events": loaded, "path": target, "tampered": False}


def audit_persistence_status() -> dict[str, Any]:
    """Describe where audit events are stored, without leaking the path secret."""

    configured = audit_log_path() is not None
    return {
        "mode": "memory+append_only_file" if configured else "memory_only",
        "file_configured": configured,
        "service": AUDIT_SERVICE,
        "file_loaded_events": _AUDIT_LOAD_SUMMARY.get("loaded_events", 0),
        "file_tampered": bool(_AUDIT_LOAD_SUMMARY.get("tampered")),
        "in_memory_events": len(AUDIT_LOGS),
    }


def _reset_audit_chain_locked() -> None:
    """Empty the in-memory chain back to genesis.  Assumes ``STATE_LOCK`` is held."""

    global _AUDIT_PREVIOUS_HASH, _AUDIT_CHAIN_ANCHOR
    AUDIT_LOGS.clear()
    _AUDIT_PREVIOUS_HASH = "0" * 64
    _AUDIT_CHAIN_ANCHOR = "0" * 64


# The persisted chain is replayed at import time so verification and traces
# survive a process restart. Without a service or explicit path (tests, CLI
# helpers) the in-memory log stays side-effect free.
_AUDIT_LOAD_SUMMARY: dict[str, Any] = load_audit_file()
if _AUDIT_LOAD_SUMMARY.get("tampered"):
    logger.error("审计文件哈希链校验失败，已保留已校验前缀: %s", _AUDIT_LOAD_SUMMARY.get("error"))
