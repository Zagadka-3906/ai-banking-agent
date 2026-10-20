"""Durable ledger snapshot: integrity-checked save and load of every domain collection."""

from __future__ import annotations

import hmac
import json
import logging
import math
import os
import re
import time
from copy import deepcopy
from typing import Any, Optional

from bank_core.ledger import (
    CONTACT_PHONE_RE,
    LEDGER_COLLECTIONS,
    register_collections,
)
from scheduler import TASK_STORE
from security import (
    AUTH_SECRET_CONFIGURED,
    STATE_LOCK,
    resolve_store_digest_algorithm,
    store_integrity_digest,
)

logger = logging.getLogger("mock_banking.core")


# ---------------------------------------------------------------------------
# Durable ledger store
#
# The business ledger is in memory because this is a competition sandbox, but
# a restart that silently empties every balance is a failure mode the device
# registry and the audit file already refuse.  Point ``BANK_LEDGER_STORE_PATH``
# at a file and every committed mutation is persisted atomically and replayed
# at start-up, so a container recreate keeps the accounts, transactions and
# reservations it had.
#
# What is stored is security-sensitive - balances and password digests are a
# fraud target - so the file carries an integrity digest: keyed (HMAC) when
# ``BANK_AUTH_SECRET`` is configured, otherwise a plain checksum that only
# detects corruption, and ``ledger_store_status`` says which one is in force
# rather than implying a protection that is not there.  A file that fails its
# digest, or that holds a structurally invalid record, is neither loaded nor
# overwritten: writes are frozen so the next mutation cannot quietly destroy
# the evidence. Owned by the core process, which is the only writer; the
# Android/IM gateway never touches it.
# ---------------------------------------------------------------------------

LEDGER_STORE_VERSION = 1
OPERATION_RECEIPTS: dict[str, dict[str, Any]] = {}
_LEDGER_STORE_BLOCKED = False
# A failed *write* is transient (a locked file, a full disk being cleaned up):
# it is reported and retried on the next mutation.  Only a file that fails
# validation on *load* freezes writes, to keep the evidence.
_LEDGER_WRITE_FAILURES = 0
_LEDGER_LAST_WRITE_FAILED = False
_LEDGER_STORE_LOAD_SUMMARY: dict[str, Any] = {}
register_collections(OPERATION_RECEIPTS=OPERATION_RECEIPTS)

# Every domain collection whose committed state a restart must restore.  The
# daily counters are included so risk accumulation survives a restart instead
# of giving a fresh 1,000-yuan yellow allowance after every bounce.
LEDGER_STATE_NAMES = (
    "ACCOUNTS",
    "CONTACTS",
    "USER_CONTACTS",
    "DAILY_TRANSFERS",
    "DAILY_TRANSFER_DATE",
    "CARD_DAILY_SPENT",
    "CARD_DAILY_SPENT_DATE",
    "LOCKS",
    "WEALTH_PRODUCTS",
    "WEALTH_HOLDINGS",
    "WEALTH_RISK_PROFILES",
    "RISK_ATTEMPTS",
    "CARDS",
    "SUBSCRIPTIONS",
    "SCHEDULED_TRANSFERS",
    "SPLIT_REQUESTS",
    "MOCK_ORDERS",
    "DISPUTE_CASES",
    "TRANSACTIONS",
    "OUTBOX",
    "OPERATION_RECEIPTS",
)


def _ledger_store_target(path: Optional[str] = None) -> Optional[str]:
    """The configured store path, or None when durability is off."""

    target = (path if path is not None else os.getenv("BANK_LEDGER_STORE_PATH")) or ""
    return target.strip() or None


def _ledger_store_digest(canonical: str, *, keyed: bool) -> dict[str, str]:
    return store_integrity_digest(canonical, keyed=keyed)


def _finite_nonnegative(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value)) and float(value) >= 0


def _account_record_valid(user_id: str, record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    if not user_id or not isinstance(record.get("name"), str) or not isinstance(record.get("status"), str):
        return False
    if not isinstance(record.get("password_hash"), str) or not record["password_hash"]:
        return False
    return _finite_nonnegative(record.get("balance")) and _finite_nonnegative(record.get("locked_amount", 0.0))


def _ledger_state_valid(state: dict[str, Any]) -> bool:
    """Re-validate a persisted snapshot before it is trusted again.

    A digest only proves the file is the one that was written; these checks
    prove the *shape* is one this version understands, so a schema drift can
    never silently resurrect half a ledger.
    """

    accounts = state.get("ACCOUNTS")
    if not isinstance(accounts, dict) or not accounts:
        return False
    for user_id, record in accounts.items():
        if not _account_record_valid(str(user_id), record):
            return False
    for name in ("CONTACTS", "DAILY_TRANSFER_DATE", "CARD_DAILY_SPENT_DATE"):
        container = state.get(name)
        if not isinstance(container, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in container.items()):
            return False
    for name in ("DAILY_TRANSFERS", "CARD_DAILY_SPENT"):
        container = state.get(name)
        if not isinstance(container, dict) or any(not _finite_nonnegative(v) for v in container.values()):
            return False
    locks = state.get("LOCKS")
    if not isinstance(locks, dict):
        return False
    for lock_id, record in locks.items():
        if not isinstance(record, dict) or record.get("lock_id") != lock_id or not isinstance(record.get("user_id"), str):
            return False
        if not _finite_nonnegative(record.get("amount")):
            return False
    products = state.get("WEALTH_PRODUCTS")
    if not isinstance(products, list) or any(not isinstance(item, dict) or not isinstance(item.get("product_id"), str) for item in products):
        return False
    holdings = state.get("WEALTH_HOLDINGS")
    if not isinstance(holdings, dict) or any(not isinstance(rows, list) for rows in holdings.values()):
        return False
    profiles = state.get("WEALTH_RISK_PROFILES")
    if not isinstance(profiles, dict):
        return False
    valid_profiles = {"CONSERVATIVE", "BALANCED", "AGGRESSIVE", "C1", "C2", "C3", "C4", "C5"}
    for record in profiles.values():
        if not isinstance(record, dict) or record.get("risk_profile") not in valid_profiles:
            return False
    cards = state.get("CARDS")
    if not isinstance(cards, dict) or any(not isinstance(rows, list) for rows in cards.values()):
        return False
    for rows in cards.values():
        for record in rows:
            if not isinstance(record, dict) or not isinstance(record.get("card_id"), str) or not record["card_id"]:
                return False
    for name in ("SUBSCRIPTIONS", "WEALTH_HOLDINGS"):
        container = state.get(name)
        if not isinstance(container, dict):
            return False
    for name in ("SCHEDULED_TRANSFERS", "SPLIT_REQUESTS", "TRANSACTIONS", "MOCK_ORDERS"):
        rows = state.get(name, [] if name == "MOCK_ORDERS" else None)
        if not isinstance(rows, list):
            return False
    for row in state.get("TRANSACTIONS") or []:
        if not isinstance(row, dict) or not isinstance(row.get("account_id"), str) or not _finite_nonnegative(row.get("amount", 0.0)):
            return False
    outbox = state.get("OUTBOX", [])
    receipts = state.get("OPERATION_RECEIPTS", {})
    if not isinstance(receipts, dict):
        return False
    for key, receipt in receipts.items():
        try:
            scoped = json.loads(key)
        except (TypeError, ValueError):
            return False
        if (not isinstance(scoped, list) or len(scoped) != 3 or not all(isinstance(item, str) for item in scoped)
                or scoped[0] not in accounts or scoped[1] not in {"transfer_execute", "lock_fund", "operation_execute", "task_execute"}
                or not isinstance(receipt, dict) or not _finite_nonnegative(receipt.get("completed_at"))
                or not isinstance(receipt.get("response"), dict)):
            return False
        if receipt.get("fingerprint") is not None and not re.fullmatch(r"[0-9a-f]{64}", str(receipt["fingerprint"])):
            return False
        if receipt.get("request_fingerprint") is not None and not re.fullmatch(r"[0-9a-f]{64}", str(receipt["request_fingerprint"])):
            return False
    if not isinstance(outbox, list):
        return False
    for row in outbox:
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) and row.get(k) for k in ("event_id", "owner_id", "recipient_id", "split_id")):
            return False
        if not _finite_nonnegative(row.get("amount", 0.0)):
            return False
    # Contact book rows feed the payee allow-list, so a half-written row must
    # not come back from a restart: name/phone/contact_id are the resolution
    # keys and every row needs all three.
    book = state.get("USER_CONTACTS")
    if not isinstance(book, dict):
        return False
    for rows in book.values():
        if not isinstance(rows, list):
            return False
        for row in rows:
            if not isinstance(row, dict):
                return False
            if not all(isinstance(row.get(key), str) and row.get(key) for key in ("contact_id", "name", "phone")):
                return False
            if not CONTACT_PHONE_RE.fullmatch(str(row.get("phone"))):
                return False
            # ``owner_id``/``link_verification`` are consent bookkeeping added
            # later; snapshots written before them must still load (consent is
            # recomputed on read anyway), so only validate the shape when present.
            for key in ("owner_id", "link_verification"):
                if key in row and not isinstance(row.get(key), str):
                    return False
    try:
        tasks, _ = TASK_STORE.decode_records(state.get("TASK_STORE", []))
    except (TypeError, ValueError):
        return False
    if any(task.owner_id not in accounts for task in tasks.values()):
        return False
    return True


def _persist_ledger_locked() -> None:
    """Write the ledger snapshot atomically.  Assumes ``STATE_LOCK`` is held."""

    global _LEDGER_WRITE_FAILURES, _LEDGER_LAST_WRITE_FAILED
    target = _ledger_store_target()
    if not target or _LEDGER_STORE_BLOCKED:
        return
    state = {name: deepcopy(LEDGER_COLLECTIONS[name]) for name in LEDGER_STATE_NAMES}
    state["TASK_STORE"] = TASK_STORE.export_records()
    canonical = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    snapshot = {
        "version": LEDGER_STORE_VERSION,
        "updated_at": int(time.time()),
        "state": state,
        "integrity": _ledger_store_digest(canonical, keyed=AUTH_SECRET_CONFIGURED),
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
        # Replace, never truncate: a crash mid-write must not leave a half file
        # that the next start would refuse as tampered.
        os.replace(temporary, target)
    except OSError:
        # The committed mutation stands; the next one rewrites the whole
        # snapshot, so nothing is lost once the disk recovers.  The health
        # endpoint reports the failure, so a failing disk still surfaces to an
        # operator.  Freezing here (as before) silently stopped persisting for
        # the rest of the process after one transient error.
        _LEDGER_WRITE_FAILURES += 1
        _LEDGER_LAST_WRITE_FAILED = True
        logger.exception("Unable to persist the ledger store; will retry on the next change")
    else:
        _LEDGER_LAST_WRITE_FAILED = False


def persist_ledger() -> dict[str, Any]:
    """Persist on demand; a no-op when durability is off or writes are frozen."""

    with STATE_LOCK:
        _persist_ledger_locked()
    return ledger_store_status()


def load_ledger_store(path: Optional[str] = None) -> dict[str, Any]:
    """Load a persisted ledger, refusing anything that fails validation.

    Opt-in and re-callable (tests re-point the path): with no
    ``BANK_LEDGER_STORE_PATH`` the in-memory demo stays deterministic and
    side-effect free.  Only a validated snapshot is written into the globals;
    a rejected file freezes writes instead of letting the next mutation
    overwrite the evidence.
    """

    global _LEDGER_STORE_BLOCKED, _LEDGER_STORE_LOAD_SUMMARY
    target = _ledger_store_target(path)
    if not target:
        _LEDGER_STORE_LOAD_SUMMARY = {"loaded_state": False, "path": None, "message": "BANK_LEDGER_STORE_PATH not configured"}
        return _LEDGER_STORE_LOAD_SUMMARY
    try:
        with open(target, "r", encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except FileNotFoundError:
        _LEDGER_STORE_LOAD_SUMMARY = {"loaded_state": False, "path": target, "message": "账本文件不存在，从种子账本开始"}
        return _LEDGER_STORE_LOAD_SUMMARY
    except OSError as exc:
        _LEDGER_STORE_BLOCKED = True
        _LEDGER_STORE_LOAD_SUMMARY = {"loaded_state": False, "path": target, "rejected": True, "error": f"账本文件无法读取: {type(exc).__name__}"}
        return _LEDGER_STORE_LOAD_SUMMARY
    except json.JSONDecodeError:
        _LEDGER_STORE_BLOCKED = True
        _LEDGER_STORE_LOAD_SUMMARY = {"loaded_state": False, "path": target, "rejected": True, "error": "账本文件不是合法 JSON"}
        return _LEDGER_STORE_LOAD_SUMMARY

    if not isinstance(snapshot, dict) or snapshot.get("version") != LEDGER_STORE_VERSION:
        _LEDGER_STORE_BLOCKED = True
        _LEDGER_STORE_LOAD_SUMMARY = {"loaded_state": False, "path": target, "rejected": True, "error": "账本文件版本不受支持"}
        return _LEDGER_STORE_LOAD_SUMMARY
    state = snapshot.get("state")
    integrity = snapshot.get("integrity")
    if not isinstance(state, dict) or not isinstance(integrity, dict):
        _LEDGER_STORE_BLOCKED = True
        _LEDGER_STORE_LOAD_SUMMARY = {"loaded_state": False, "path": target, "rejected": True, "error": "账本文件结构无效"}
        return _LEDGER_STORE_LOAD_SUMMARY

    canonical = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # The algorithm comes from our configuration, never from the file being
    # verified: see ``resolve_store_digest_algorithm``.
    keyed, algorithm_error = resolve_store_digest_algorithm(integrity)
    if algorithm_error:
        _LEDGER_STORE_BLOCKED = True
        _LEDGER_STORE_LOAD_SUMMARY = {"loaded_state": False, "path": target, "rejected": True, "error": algorithm_error}
        return _LEDGER_STORE_LOAD_SUMMARY
    expected = _ledger_store_digest(canonical, keyed=bool(keyed))["value"]
    if not hmac.compare_digest(expected, str(integrity.get("value") or "")):
        _LEDGER_STORE_BLOCKED = True
        _LEDGER_STORE_LOAD_SUMMARY = {"loaded_state": False, "path": target, "rejected": True, "error": "账本文件完整性校验失败"}
        return _LEDGER_STORE_LOAD_SUMMARY
    if not _ledger_state_valid(state):
        _LEDGER_STORE_BLOCKED = True
        _LEDGER_STORE_LOAD_SUMMARY = {"loaded_state": False, "path": target, "rejected": True, "error": "账本文件记录无效"}
        return _LEDGER_STORE_LOAD_SUMMARY

    with STATE_LOCK:
        for name in LEDGER_STATE_NAMES:
            container = LEDGER_COLLECTIONS[name]
            restored = deepcopy(state.get(name, [] if name in {"OUTBOX", "MOCK_ORDERS"} else None))
            if isinstance(container, dict):
                container.clear()
                if isinstance(restored, dict):
                    container.update(restored)
            elif isinstance(container, list):
                container[:] = restored if isinstance(restored, list) else []
        # Older snapshots did not contain a queue. Keep them readable, but do
        # not invent approvals or replay merchant/reminder actions whose prior
        # task state was never saved.
        TASK_STORE.restore(TASK_STORE.decode_records(state.get("TASK_STORE", [])))
        # The product shelf is configuration, not account data: a snapshot saved
        # before the catalogue changed must not bring retired products back.
        from bank_core.wealth_data import WEALTH_CATALOGUE

        LEDGER_COLLECTIONS["WEALTH_PRODUCTS"][:] = deepcopy(WEALTH_CATALOGUE)
    _LEDGER_STORE_LOAD_SUMMARY = {
        "loaded_state": True,
        "path": target,
        "rejected": False,
        "keyed": bool(keyed),
        "accounts": len(state.get("ACCOUNTS") or {}),
        "transactions": len(state.get("TRANSACTIONS") or []),
    }
    return _LEDGER_STORE_LOAD_SUMMARY


def ledger_store_status() -> dict[str, Any]:
    """Describe ledger durability without leaking the configured path."""

    configured = bool(_ledger_store_target())
    return {
        "mode": "memory+file" if configured else "memory_only",
        "file_configured": configured,
        "file_loaded": bool(_LEDGER_STORE_LOAD_SUMMARY.get("loaded_state")),
        "file_rejected": bool(_LEDGER_STORE_LOAD_SUMMARY.get("rejected")),
        "writes_frozen": _LEDGER_STORE_BLOCKED,
        "last_write_failed": _LEDGER_LAST_WRITE_FAILED,
        "write_failures": _LEDGER_WRITE_FAILURES,
        # False means the file carries a plain checksum: whoever can write it can
        # also forge it.  Configure BANK_AUTH_SECRET to make the digest keyed.
        "integrity_keyed": AUTH_SECRET_CONFIGURED,
    }
