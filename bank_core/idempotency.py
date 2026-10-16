"""Idempotency records that make committed operations safely retryable."""

from __future__ import annotations

import hashlib
import json
import os
import time
from copy import deepcopy
from typing import Any, Optional

from bank_core.access import (
    record_failure_and_raise,
)
from bank_core.persistence import (
    OPERATION_RECEIPTS,
)
from security import (
    STATE_LOCK,
    SecurityError,
    audit_event,
    sanitize_text,
)

# Idempotency records make a committed operation safely retryable when a
# client loses the HTTP response.  Keys are scoped to the authenticated
# principal and endpoint; values never contain credentials.
IDEMPOTENCY_RECORDS: dict[tuple[str, str, str], dict[str, Any]] = {}
IDEMPOTENCY_TTL_SECONDS = max(300, int(os.getenv("BANK_IDEMPOTENCY_TTL_SECONDS", "86400")))
IDEMPOTENCY_MAX_RECORDS = max(100, int(os.getenv("BANK_IDEMPOTENCY_MAX_RECORDS", "10000")))


def idempotency_fingerprint(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def idempotency_begin(actor: str, endpoint: str, key: Optional[str], payload: Any, *, request_id: str) -> Optional[dict[str, Any]]:
    """Reserve a key, or return a previously completed response.

    ``None`` means the caller owns a new reservation.  A pending reservation
    is rejected to prevent concurrent duplicate ledger mutations.
    """
    if not key:
        return None
    try:
        safe_key = sanitize_text(key, field="幂等键", max_length=128)
    except SecurityError as exc:
        record_failure_and_raise(exc, user_id=actor, action="IDEMPOTENCY", request_id=request_id)
    if not safe_key or len(safe_key) < 8:
        exc = SecurityError("幂等键长度不足", 400, "INVALID_IDEMPOTENCY_KEY")
        record_failure_and_raise(exc, user_id=actor, action="IDEMPOTENCY", request_id=request_id)
    fingerprint = idempotency_fingerprint(payload)
    now = time.time()
    with STATE_LOCK:
        # Opportunistically expire old entries and cap memory.
        expired = [k for k, v in IDEMPOTENCY_RECORDS.items() if now - float(v.get("created_at", now)) > IDEMPOTENCY_TTL_SECONDS]
        for old_key in expired:
            IDEMPOTENCY_RECORDS.pop(old_key, None)
        scoped = (actor, endpoint, safe_key)
        committed = OPERATION_RECEIPTS.get(json.dumps([actor, endpoint, safe_key]))
        if committed and committed.get("fingerprint") and now - committed["completed_at"] <= IDEMPOTENCY_TTL_SECONDS:
            if committed["fingerprint"] != fingerprint:
                raise SecurityError("幂等键已用于不同请求", 409, "IDEMPOTENCY_KEY_REUSE")
            audit_event("IDEMPOTENCY_REPLAY", user_id=actor, decision="ALLOW", outcome="CACHED_RESPONSE", request_id=request_id, metadata={"endpoint": endpoint})
            return deepcopy(committed["response"])
        existing = IDEMPOTENCY_RECORDS.get(scoped)
        if existing:
            if existing.get("fingerprint") != fingerprint:
                raise SecurityError("幂等键已用于不同请求", 409, "IDEMPOTENCY_KEY_REUSE")
            if existing.get("status") == "COMPLETED":
                audit_event("IDEMPOTENCY_REPLAY", user_id=actor, decision="ALLOW", outcome="CACHED_RESPONSE", request_id=request_id, metadata={"endpoint": endpoint})
                return deepcopy(existing.get("response") or {})
            raise SecurityError("相同请求正在处理中，请勿重复提交", 409, "IDEMPOTENCY_IN_PROGRESS")
        if len(IDEMPOTENCY_RECORDS) >= IDEMPOTENCY_MAX_RECORDS:
            # Remove the oldest item; this is bounded housekeeping, not a
            # correctness path because nonce replay protection remains active.
            oldest = min(IDEMPOTENCY_RECORDS, key=lambda item: IDEMPOTENCY_RECORDS[item].get("created_at", now))
            IDEMPOTENCY_RECORDS.pop(oldest, None)
        IDEMPOTENCY_RECORDS[scoped] = {"fingerprint": fingerprint, "status": "PENDING", "created_at": now}
    return None


def idempotency_completed_request(actor: str, endpoint: str, key: Optional[str], request_fingerprint: str, *, request_id: str) -> Optional[dict[str, Any]]:
    """Replay a settled request before mutable business prerequisites change.

    The ordinary fingerprint still binds the validated execution context. A
    second fingerprint records the submitted intent so a completed cancellation
    or card transition can be retried after its own success changed that state.
    Legacy receipts without this field retain their original validation path.
    """

    if not key:
        return None
    safe_key = sanitize_text(key, field="幂等键", max_length=128)
    with STATE_LOCK:
        entry = OPERATION_RECEIPTS.get(json.dumps([actor, endpoint, safe_key]))
        if entry is None:
            candidate = IDEMPOTENCY_RECORDS.get((actor, endpoint, safe_key))
            entry = candidate if candidate and candidate.get("status") == "COMPLETED" else None
        if not entry or time.time() - float(entry.get("completed_at", 0)) > IDEMPOTENCY_TTL_SECONDS:
            return None
        previous = entry.get("request_fingerprint")
        if previous is None:
            return None
        if previous != request_fingerprint:
            raise SecurityError("幂等键已用于不同请求", 409, "IDEMPOTENCY_KEY_REUSE")
        audit_event("IDEMPOTENCY_REPLAY", user_id=actor, decision="ALLOW", outcome="CACHED_RESPONSE", request_id=request_id, metadata={"endpoint": endpoint})
        return deepcopy(entry["response"])


def idempotency_complete(actor: str, endpoint: str, key: Optional[str], payload: Any, response: dict[str, Any], *, request_fingerprint: Optional[str] = None) -> None:
    if not key:
        return
    safe_key = sanitize_text(key, field="幂等键", max_length=128)
    scoped = (actor, endpoint, safe_key)
    with STATE_LOCK:
        entry = IDEMPOTENCY_RECORDS.get(scoped)
        if entry and entry.get("fingerprint") == idempotency_fingerprint(payload):
            entry.update({"status": "COMPLETED", "response": deepcopy(response), "completed_at": time.time()})
            if request_fingerprint is not None:
                entry["request_fingerprint"] = request_fingerprint
            if endpoint in {"transfer_execute", "lock_fund", "operation_execute", "task_execute"}:
                receipt_key = json.dumps([actor, endpoint, safe_key])
                OPERATION_RECEIPTS[receipt_key] = {"completed_at": time.time(), "fingerprint": entry["fingerprint"], "response": deepcopy(response)}
                if request_fingerprint is not None:
                    OPERATION_RECEIPTS[receipt_key]["request_fingerprint"] = request_fingerprint
                if len(OPERATION_RECEIPTS) > IDEMPOTENCY_MAX_RECORDS:
                    oldest = min(OPERATION_RECEIPTS, key=lambda item: OPERATION_RECEIPTS[item]["completed_at"])
                    OPERATION_RECEIPTS.pop(oldest, None)


def idempotency_abort(actor: str, endpoint: str, key: Optional[str]) -> None:
    if not key:
        return
    try:
        safe_key = sanitize_text(key, field="幂等键", max_length=128)
    except SecurityError:
        return
    with STATE_LOCK:
        IDEMPOTENCY_RECORDS.pop((actor, endpoint, safe_key), None)
