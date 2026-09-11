"""Mock core-banking API with an explicit security boundary.

The ledger is intentionally in memory for a competition demo.  Mutating
operations still require a risk precheck, a signed short-lived one-time
nonce, and (for red operations) a server-validated MFA proof.  Every decision
and outcome is appended to a tamper-evident audit trail.
"""

from __future__ import annotations

import hmac
import hashlib
import json
import logging
import math
import os
import time
import uuid
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from security import (
    AUTH_SECRET_CONFIGURED,
    AUTH_TOKENS,
    AUDIT_LOGS,
    DEVICES,
    MFA_PROOFS,
    SecurityError,
    amount_to_cents,
    attach_push_token,
    audit_event,
    authorize_execution,
    behavior_large_amount_cents,
    behavior_status,
    circuit_status,
    confirm_auth_token,
    demo_mode,
    ensure_circuit_closed,
    enroll_device,
    get_audit_logs,
    get_audit_trace,
    hash_password,
    inspect_auth_token,
    issue_auth_token,
    issue_face_challenge,
    issue_session_token,
    list_devices,
    record_security_failure,
    record_security_success,
    record_behavior_signal,
    resolve_session,
    revoke_device,
    revoke_session,
    revoke_sessions_for,
    security_health,
    sanitize_text,
    store_integrity_digest,
    validate_actor,
    verify_login_otp,
    verify_mfa,
    verify_password,
    verify_audit_chain,
)
from security import STATE_LOCK
from notifications import notify_waiting_user
from scheduler import TASK_STORE, TaskState
from planner import analyze_transactions, filter_transactions
from sandbox import sandbox_health


logger = logging.getLogger("mock_banking.core")

app = FastAPI(
    title="Mock Bank Core API",
    description="银行 AI 智能体受控沙箱环境（含分级权限与审计）",
    version="2.0.0",
)
_cors_origins = [origin.strip() for origin in os.getenv("BANK_CORS_ORIGINS", "http://localhost:8501,http://127.0.0.1:8501").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-User-Id", "X-Session-Token", "X-Auth-Token", "X-MFA-Proof", "X-User-Confirmation", "X-Request-Id", "Idempotency-Key", "X-Admin-Token"],
)

# Keep the HTTP edge defensive even when the mock is run directly under
# Uvicorn (without a reverse proxy/WAF).  The body limit is deliberately
# generous for normal JSON requests but prevents an untrusted channel from
# turning prompt/metadata fields into an unbounded memory allocation.
try:
    _MAX_REQUEST_BYTES = max(16_384, min(int(os.getenv("BANK_MAX_REQUEST_BYTES", "1048576")), 10_000_000))
except (TypeError, ValueError):
    _MAX_REQUEST_BYTES = 1_048_576

# Paths that must stay reachable without a session and without rate limiting
# (container probes and the interactive API docs).
_HEALTH_PATHS = {"/healthz", "/api/v1/security/health"}
_DOCS_PREFIXES = ("/docs", "/redoc", "/openapi.json")
_SESSION_ISSUE_PATH = "/api/v1/auth/session"
# Endpoints that must stay reachable *before* a session exists.  Without this,
# strict session mode would refuse the login request itself and no client could
# ever obtain the credential it is being asked for.
_SESSION_BOOTSTRAP_PATHS = {_SESSION_ISSUE_PATH, "/api/v1/auth/login"}
RATE_LIMIT_WINDOW_SECONDS = 60
# Per-principal request windows.  The demo has no reverse proxy, so a simple
# in-process fixed window is the cheapest way to blunt accidental or hostile
# floods; production should also apply an edge/WAF limit.
RATE_LIMIT_STATE: dict[str, dict[str, Any]] = {}


def _rate_limit_exempt(path: str) -> bool:
    return path in _HEALTH_PATHS or path.startswith(_DOCS_PREFIXES)


def _strict_session_required() -> bool:
    return os.getenv("BANK_REQUIRE_SESSION", "0").lower() in {"1", "true", "yes", "on"}


def _rate_limit_check(key: str) -> Optional[int]:
    """Return ``Retry-After`` seconds when over the per-minute limit, else None."""

    try:
        limit = int(os.getenv("BANK_RATE_LIMIT_PER_MINUTE", "240"))
    except (TypeError, ValueError):
        limit = 240
    if limit <= 0:
        return None
    now = time.monotonic()
    record = RATE_LIMIT_STATE.get(key)
    if not record or now - float(record.get("start", 0.0)) >= RATE_LIMIT_WINDOW_SECONDS:
        RATE_LIMIT_STATE[key] = {"start": now, "count": 1, "notified": False}
        return None
    record["count"] = int(record.get("count", 0)) + 1
    if record["count"] > limit:
        if not record.get("notified"):
            record["notified"] = True
            audit_event("RATE_LIMITED", user_id=key, decision="BLOCK", outcome="RATE_LIMITED", reason="rate limit exceeded", metadata={"limit": limit, "window_seconds": RATE_LIMIT_WINDOW_SECONDS})
        return max(1, int(RATE_LIMIT_WINDOW_SECONDS - (now - float(record.get("start", now)))))
    return None


def _apply_security_headers(response: JSONResponse, path: str) -> JSONResponse:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def request_safety_middleware(request: Request, call_next):
    path = request.url.path
    # 1) A session token is authoritative over a self-asserted X-User-Id: it is
    #    verified first and its subject replaces any client-supplied header, so
    #    a caller can never claim another principal alongside a valid session.
    session_token = request.headers.get("X-Session-Token")
    if session_token:
        try:
            principal = resolve_session(session_token)
        except SecurityError as exc:
            return _apply_security_headers(
                JSONResponse(status_code=exc.status_code, content={"detail": {"code": exc.code, "message": exc.detail}}),
                path,
            )
        forwarded = [(name, value) for name, value in request.scope["headers"] if name.lower() != b"x-user-id"]
        forwarded.append((b"x-user-id", principal.encode("utf-8")))
        request.scope["headers"] = forwarded
    elif _strict_session_required() and request.method in {"POST", "PUT", "PATCH", "DELETE"} and not _rate_limit_exempt(path) and path not in _SESSION_BOOTSTRAP_PATHS:
        return _apply_security_headers(
            JSONResponse(status_code=401, content={"detail": {"code": "SESSION_REQUIRED", "message": "写操作需要会话凭证"}}),
            path,
        )
    # 2) Per-principal request window (probes and docs are exempt).
    if not _rate_limit_exempt(path):
        rate_key = request.headers.get("X-User-Id") or (request.client.host if request.client else "anonymous")
        retry_after = _rate_limit_check(str(rate_key))
        if retry_after is not None:
            response = _apply_security_headers(
                JSONResponse(status_code=429, content={"detail": {"code": "RATE_LIMITED", "message": "请求过于频繁，请稍后重试"}}),
                path,
            )
            response.headers["Retry-After"] = str(retry_after)
            return response
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_REQUEST_BYTES:
                return _apply_security_headers(
                    JSONResponse(status_code=413, content={"detail": {"code": "REQUEST_TOO_LARGE", "message": "请求体超过安全限制"}}),
                    path,
                )
        except ValueError:
            return _apply_security_headers(
                JSONResponse(status_code=400, content={"detail": {"code": "INVALID_CONTENT_LENGTH", "message": "请求长度字段无效"}}),
                path,
            )
    response = await call_next(request)
    return _apply_security_headers(response, request.url.path)


# ---------------------------------------------------------------------------
# In-memory demo ledger (public names retained for backwards compatibility)
# ---------------------------------------------------------------------------
# Every demo account shares one password.  The argon2id digest is baked in
# rather than computed at import: hashing costs ~33 ms per call, and a fixed
# digest keeps start-up (and therefore every test run) deterministic.  A real
# deployment provisions credentials through an enrolment flow, never a source
# constant.  The plaintext (`Bank@2026`) is documented in .env.example.
DEMO_PASSWORD_HASH = "$argon2id$v=19$m=65536,t=3,p=4$01eEUN2Ii59Smsmuoc9mhg$O+kWnd/qcEgxrcodomCtjb5PgtkprfAfvHOfDqFEyi8"

ACCOUNTS: dict[str, dict[str, Any]] = {
    "user_zhang": {"card_no": "622202001", "name": "张三", "phone": "13800138000", "balance": 15000.0, "locked_amount": 0.0, "status": "ACTIVE", "credential_version": 1, "password_hash": DEMO_PASSWORD_HASH},
    "user_li": {"card_no": "622202002", "name": "李四", "phone": "13900139000", "balance": 2000.0, "locked_amount": 0.0, "status": "ACTIVE", "credential_version": 1, "password_hash": DEMO_PASSWORD_HASH},
    "user_wife": {"card_no": "622202003", "name": "王美（爱人）", "phone": "13700137000", "balance": 5000.0, "locked_amount": 0.0, "status": "ACTIVE", "credential_version": 1, "password_hash": DEMO_PASSWORD_HASH},
}

CONTACTS: dict[str, str] = {
    "李四": "user_li",
    "爱人": "user_wife",
    "老婆": "user_wife",
    "王美": "user_wife",
    "王美（爱人）": "user_wife",
    "13900139000": "user_li",
    "13700137000": "user_wife",
}

DAILY_TRANSFERS: dict[str, float] = {"user_zhang": 0.0}
DAILY_TRANSFER_DATE: dict[str, str] = {}
# Individual lock records make rollback/unlock auditable and prevent a caller
# from releasing another user's reserved funds.
LOCKS: dict[str, dict[str, Any]] = {}
# Additional mock products make the other competition scenarios executable
# without pretending that a real bank is connected.  These records are
# deterministic fixtures; all writes still pass through the same authorization
# gateway used by transfers.
WEALTH_PRODUCTS: list[dict[str, Any]] = [
    {"product_id": "MMF001", "name": "稳健现金管理", "risk_level": "R1", "annualized_yield": 0.021, "min_amount": 100.0, "liquidity": "T+0"},
    {"product_id": "BOND002", "name": "短债增强", "risk_level": "R2", "annualized_yield": 0.034, "min_amount": 1000.0, "liquidity": "T+1"},
    {"product_id": "INDEX003", "name": "宽基指数基金", "risk_level": "R3", "annualized_yield": None, "min_amount": 100.0, "liquidity": "T+2"},
]
WEALTH_HOLDINGS: dict[str, list[dict[str, Any]]] = {"user_zhang": []}
# A demo risk profile is stored separately from the account and contains no
# sensitive questionnaire answers.  It lets the purchase flow demonstrate the
# dependency while keeping the profile queryable and auditable.
WEALTH_RISK_PROFILES: dict[str, dict[str, Any]] = {
    "user_zhang": {"risk_profile": "CONSERVATIVE", "assessed_at": "2026-09-01T00:00:00+00:00", "source": "DEMO_SEED"}
}
CARDS: dict[str, dict[str, Any]] = {
    "user_zhang": {"card_id": "CARD-001", "type": "VIRTUAL", "status": "ACTIVE", "daily_limit": 5000.0}
}
# Per-card, per-day settled spend.  The card's ``daily_limit`` is enforced on
# settlement, so lowering a limit (CARD_LIMIT) immediately constrains further
# payments instead of only being a display field.
CARD_DAILY_SPENT: dict[str, float] = {}
CARD_DAILY_SPENT_DATE: dict[str, str] = {}
SUBSCRIPTIONS: dict[str, list[dict[str, Any]]] = {
    "user_zhang": [
        {"subscription_id": "SUB-APPLE", "merchant": "Apple 订阅", "amount": 15.0, "next_charge": "2026-10-03", "status": "ACTIVE"},
        {"subscription_id": "SUB-VIDEO", "merchant": "视频会员", "amount": 25.0, "next_charge": "2026-09-15", "status": "ACTIVE"},
    ]
}
SCHEDULED_TRANSFERS: list[dict[str, Any]] = []
SPLIT_REQUESTS: list[dict[str, Any]] = []
TRANSACTIONS: list[dict[str, Any]] = [
    {"tx_id": "TX1000", "account_id": "user_zhang", "merchant": "咖啡店", "category": "餐饮", "amount": 28.0, "time": f"{datetime.now().strftime('%Y-%m-%d')} 09:15:00"},
    {"tx_id": "TX1001", "account_id": "user_zhang", "merchant": "美团外卖", "category": "餐饮", "amount": 45.0, "time": "2026-08-01 12:30:00"},
    {"tx_id": "TX1002", "account_id": "user_zhang", "merchant": "Apple 订阅", "category": "订阅代扣", "amount": 15.0, "time": "2026-08-03 08:00:00"},
    {"tx_id": "TX1003", "account_id": "user_zhang", "merchant": "海底捞火锅", "category": "餐饮", "amount": 320.0, "time": "2026-08-05 19:20:00"},
    {"tx_id": "TX1004", "account_id": "user_zhang", "merchant": "境外未知商户", "category": "可疑交易", "amount": 999.0, "time": "2026-08-07 03:15:00"},
]


@contextmanager
def _ledger_transaction():
    """Best-effort atomicity for the in-memory demo ledger.

    Production deployments must replace this shim with a database transaction
    and an outbox.  Keeping the snapshot boundary here nevertheless prevents
    a task-store or validation failure from leaving half-applied balance,
    lock, schedule, holding, or subscription mutations in the competition
    sandbox.
    """

    names = (
        "ACCOUNTS",
        "DAILY_TRANSFERS",
        "DAILY_TRANSFER_DATE",
        "CARD_DAILY_SPENT",
        "CARD_DAILY_SPENT_DATE",
        "LOCKS",
        "WEALTH_HOLDINGS",
        "CARDS",
        "SUBSCRIPTIONS",
        "SCHEDULED_TRANSFERS",
        "SPLIT_REQUESTS",
        "TRANSACTIONS",
        "WEALTH_RISK_PROFILES",
    )
    with STATE_LOCK:
        snapshots = {name: deepcopy(globals()[name]) for name in names}
        task_snapshot = TASK_STORE.snapshot()
        try:
            yield
        except Exception:
            for name, snapshot in snapshots.items():
                current = globals()[name]
                if isinstance(current, dict):
                    current.clear()
                    current.update(deepcopy(snapshot))
                else:
                    current[:] = deepcopy(snapshot)
            TASK_STORE.restore(task_snapshot)
            raise

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
# the evidence.  Owned by the core process, which is the only writer; the
# gateway and the Streamlit demo never touch it.
# ---------------------------------------------------------------------------

LEDGER_STORE_VERSION = 1
_LEDGER_STORE_BLOCKED = False
_LEDGER_STORE_LOAD_SUMMARY: dict[str, Any] = {}

# Every domain collection whose committed state a restart must restore.  The
# daily counters are included so risk accumulation survives a restart instead
# of giving a fresh 1,000-yuan yellow allowance after every bounce.
LEDGER_STATE_NAMES = (
    "ACCOUNTS",
    "CONTACTS",
    "DAILY_TRANSFERS",
    "DAILY_TRANSFER_DATE",
    "CARD_DAILY_SPENT",
    "CARD_DAILY_SPENT_DATE",
    "LOCKS",
    "WEALTH_PRODUCTS",
    "WEALTH_HOLDINGS",
    "WEALTH_RISK_PROFILES",
    "CARDS",
    "SUBSCRIPTIONS",
    "SCHEDULED_TRANSFERS",
    "SPLIT_REQUESTS",
    "TRANSACTIONS",
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
    for record in profiles.values():
        if not isinstance(record, dict) or record.get("risk_profile") not in {"CONSERVATIVE", "BALANCED", "AGGRESSIVE"}:
            return False
    cards = state.get("CARDS")
    if not isinstance(cards, dict) or any(not isinstance(record, dict) for record in cards.values()):
        return False
    for name in ("SUBSCRIPTIONS", "WEALTH_HOLDINGS"):
        container = state.get(name)
        if not isinstance(container, dict):
            return False
    for name in ("SCHEDULED_TRANSFERS", "SPLIT_REQUESTS", "TRANSACTIONS"):
        rows = state.get(name)
        if not isinstance(rows, list):
            return False
    for row in state.get("TRANSACTIONS") or []:
        if not isinstance(row, dict) or not isinstance(row.get("account_id"), str) or not _finite_nonnegative(row.get("amount", 0.0)):
            return False
    return True


def _persist_ledger_locked() -> None:
    """Write the ledger snapshot atomically.  Assumes ``STATE_LOCK`` is held."""

    global _LEDGER_STORE_BLOCKED
    target = _ledger_store_target()
    if not target or _LEDGER_STORE_BLOCKED:
        return
    state = {name: deepcopy(globals()[name]) for name in LEDGER_STATE_NAMES}
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
        # The committed mutation stands; the next one retries the write.  The
        # health endpoint reports the store, so a silently failing disk still
        # surfaces to an operator.
        _LEDGER_STORE_BLOCKED = True
        logger.exception("Unable to persist the ledger store")


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
    algorithm = str(integrity.get("alg") or "")
    if algorithm == "hmac-sha256":
        expected = _ledger_store_digest(canonical, keyed=True)["value"]
    elif algorithm == "sha256":
        expected = _ledger_store_digest(canonical, keyed=False)["value"]
    else:
        _LEDGER_STORE_BLOCKED = True
        _LEDGER_STORE_LOAD_SUMMARY = {"loaded_state": False, "path": target, "rejected": True, "error": f"账本文件摘要算法未知: {algorithm}"}
        return _LEDGER_STORE_LOAD_SUMMARY
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
            container = globals()[name]
            restored = deepcopy(state.get(name))
            if isinstance(container, dict):
                container.clear()
                if isinstance(restored, dict):
                    container.update(restored)
            elif isinstance(container, list):
                container[:] = restored if isinstance(restored, list) else []
    _LEDGER_STORE_LOAD_SUMMARY = {
        "loaded_state": True,
        "path": target,
        "rejected": False,
        "keyed": algorithm == "hmac-sha256",
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
        # False means the file carries a plain checksum: whoever can write it can
        # also forge it.  Configure BANK_AUTH_SECRET to make the digest keyed.
        "integrity_keyed": AUTH_SECRET_CONFIGURED,
    }


# Idempotency records make a committed operation safely retryable when a
# client loses the HTTP response.  Keys are scoped to the authenticated
# principal and endpoint; values never contain credentials.
IDEMPOTENCY_RECORDS: dict[tuple[str, str, str], dict[str, Any]] = {}
IDEMPOTENCY_TTL_SECONDS = max(300, int(os.getenv("BANK_IDEMPOTENCY_TTL_SECONDS", "86400")))
IDEMPOTENCY_MAX_RECORDS = max(100, int(os.getenv("BANK_IDEMPOTENCY_MAX_RECORDS", "10000")))


def _idempotency_fingerprint(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _idempotency_begin(actor: str, endpoint: str, key: Optional[str], payload: Any, *, request_id: str) -> Optional[dict[str, Any]]:
    """Reserve a key, or return a previously completed response.

    ``None`` means the caller owns a new reservation.  A pending reservation
    is rejected to prevent concurrent duplicate ledger mutations.
    """
    if not key:
        return None
    try:
        safe_key = sanitize_text(key, field="幂等键", max_length=128)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=actor, action="IDEMPOTENCY", request_id=request_id)
    if not safe_key or len(safe_key) < 8:
        exc = SecurityError("幂等键长度不足", 400, "INVALID_IDEMPOTENCY_KEY")
        _record_failure_and_raise(exc, user_id=actor, action="IDEMPOTENCY", request_id=request_id)
    fingerprint = _idempotency_fingerprint(payload)
    now = time.time()
    with STATE_LOCK:
        # Opportunistically expire old entries and cap memory.
        expired = [k for k, v in IDEMPOTENCY_RECORDS.items() if now - float(v.get("created_at", now)) > IDEMPOTENCY_TTL_SECONDS]
        for old_key in expired:
            IDEMPOTENCY_RECORDS.pop(old_key, None)
        scoped = (actor, endpoint, safe_key)
        existing = IDEMPOTENCY_RECORDS.get(scoped)
        if existing:
            if existing.get("fingerprint") != fingerprint:
                raise SecurityError("幂等键已用于不同请求", 409, "IDEMPOTENCY_KEY_REUSE")
            if existing.get("status") == "COMPLETED":
                audit_event("IDEMPOTENCY_REPLAY", user_id=actor, decision="ALLOW", outcome="CACHED_RESPONSE", request_id=request_id, metadata={"endpoint": endpoint})
                return dict(existing.get("response") or {})
            raise SecurityError("相同请求正在处理中，请勿重复提交", 409, "IDEMPOTENCY_IN_PROGRESS")
        if len(IDEMPOTENCY_RECORDS) >= IDEMPOTENCY_MAX_RECORDS:
            # Remove the oldest item; this is bounded housekeeping, not a
            # correctness path because nonce replay protection remains active.
            oldest = min(IDEMPOTENCY_RECORDS, key=lambda item: IDEMPOTENCY_RECORDS[item].get("created_at", now))
            IDEMPOTENCY_RECORDS.pop(oldest, None)
        IDEMPOTENCY_RECORDS[scoped] = {"fingerprint": fingerprint, "status": "PENDING", "created_at": now}
    return None


def _idempotency_complete(actor: str, endpoint: str, key: Optional[str], payload: Any, response: dict[str, Any]) -> None:
    if not key:
        return
    safe_key = sanitize_text(key, field="幂等键", max_length=128)
    scoped = (actor, endpoint, safe_key)
    with STATE_LOCK:
        entry = IDEMPOTENCY_RECORDS.get(scoped)
        if entry and entry.get("fingerprint") == _idempotency_fingerprint(payload):
            entry.update({"status": "COMPLETED", "response": dict(response), "completed_at": time.time()})


def _idempotency_abort(actor: str, endpoint: str, key: Optional[str]) -> None:
    if not key:
        return
    try:
        safe_key = sanitize_text(key, field="幂等键", max_length=128)
    except SecurityError:
        return
    with STATE_LOCK:
        IDEMPOTENCY_RECORDS.pop((actor, endpoint, safe_key), None)


class StrictModel(BaseModel):
    # Unknown model fields are ignored; only explicit allowlisted fields below
    # can reach a business operation.
    model_config = ConfigDict(extra="ignore")


class PrecheckRequest(StrictModel):
    from_user: str
    target_name_or_id: str
    amount: float = Field(...)
    remark: Optional[str] = "智能转账"


class TransferExecuteRequest(StrictModel):
    from_user: str
    target_name_or_id: str
    amount: float = Field(...)
    remark: Optional[str] = "智能转账"
    mfa_proof: Optional[str] = None
    user_confirmed: bool = False


class ConfirmationRequest(StrictModel):
    auth_token: Optional[str] = None
    user_id: Optional[str] = None


class MFAVerifyRequest(StrictModel):
    auth_token: Optional[str] = None
    otp: Optional[str] = None
    sms_code: Optional[str] = None
    # Kept as StrictBool so attacker-controlled strings such as ``"yes"``
    # still fail schema validation.  The field alone no longer satisfies the
    # biometric factor: a signed, single-use face attestation ticket bound to
    # a server-issued challenge is required (``face_attestation``).
    face_verified: StrictBool = False
    face_attestation: Optional[str] = None
    user_id: Optional[str] = None


class FaceChallengeRequest(StrictModel):
    auth_token: str
    user_id: Optional[str] = None


class SessionRequest(StrictModel):
    user_id: str
    ttl_seconds: Optional[int] = None


class LoginRequest(StrictModel):
    user_id: str
    password: str
    # Optional second factor at login, required only when the deployment asks
    # for it (BANK_LOGIN_REQUIRE_OTP=1).
    otp: Optional[str] = None


class DeviceRegisterRequest(StrictModel):
    """Device enrollment: the client proves possession of a P-256 private key."""

    public_key: str = Field(..., description="Base64(DER SubjectPublicKeyInfo) 的公钥")
    label: Optional[str] = None
    platform: Optional[str] = None
    app_version: Optional[str] = None


class PushTokenRequest(StrictModel):
    """Delivery token for notifications (an FCM registration token)."""

    token: str
    platform: Optional[str] = None


class LockPrecheckRequest(StrictModel):
    user_id: str
    amount: float = Field(...)
    reason: str = "跨场景联动储备"
    event_at: Optional[str] = None
    reminder_at: Optional[str] = None


class LockFundRequest(StrictModel):
    user_id: str
    amount: float = Field(...)
    reason: str
    auth_token: Optional[str] = None
    mfa_proof: Optional[str] = None
    user_confirmed: bool = False
    event_at: Optional[str] = None
    reminder_at: Optional[str] = None


class UnlockPrecheckRequest(StrictModel):
    user_id: str
    amount: float
    lock_id: Optional[str] = None
    reason: str = "解除资金隔离"


class UnlockFundRequest(StrictModel):
    user_id: str
    amount: float
    lock_id: Optional[str] = None
    reason: str = "解除资金隔离"
    auth_token: Optional[str] = None
    mfa_proof: Optional[str] = None
    user_confirmed: bool = False


class OperationPrecheckRequest(StrictModel):
    """Common policy request for card, wealth and subscription actions."""

    user_id: str
    operation: str
    amount: Optional[float] = 0.0
    target: Optional[str] = None
    product_id: Optional[str] = None
    card_id: Optional[str] = None
    new_limit: Optional[float] = None
    merchant: Optional[str] = None
    participants: Optional[list[str]] = None
    product_ids: Optional[list[str]] = None
    scheduled_at: Optional[str] = None
    reason: Optional[str] = "智能银行操作"


class OperationExecuteRequest(StrictModel):
    user_id: str
    operation: str = ""
    amount: Optional[float] = 0.0
    target: Optional[str] = None
    product_id: Optional[str] = None
    card_id: Optional[str] = None
    new_limit: Optional[float] = None
    merchant: Optional[str] = None
    participants: Optional[list[str]] = None
    product_ids: Optional[list[str]] = None
    scheduled_at: Optional[str] = None
    reason: Optional[str] = "智能银行操作"
    # Only meaningful for PASSWORD_CHANGE, and deliberately not part of the
    # signed approval context: credential material must never travel inside a
    # bearer token the client holds.
    new_password: Optional[str] = None
    auth_token: Optional[str] = None
    mfa_proof: Optional[str] = None
    user_confirmed: bool = False


class TaskActionRequest(StrictModel):
    reason: str = "用户请求"


class RiskAssessmentRequest(StrictModel):
    user_id: str
    # Questionnaire answers are intentionally optional at the transport
    # boundary so the API can return a precise 400/428-style clarification
    # instead of Pydantic silently applying a suitability-affecting default.
    horizon_years: Optional[int] = None
    loss_tolerance: Optional[str] = None
    experience: Optional[str] = None


def _request_id(value: Optional[str]) -> str:
    if value:
        try:
            return sanitize_text(value, field="请求编号", max_length=80)
        except SecurityError:
            pass
    return f"REQ{uuid.uuid4().hex}"


def _raise_security(exc: SecurityError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.detail},
        headers={"Retry-After": "30"} if exc.status_code == 423 else None,
    )


def _record_failure_and_raise(exc: SecurityError, *, user_id: Optional[str], action: str, request_id: str) -> None:
    security_codes = {
        "TOKEN_REQUIRED", "INVALID_TOKEN", "INVALID_TOKEN_SIGNATURE", "TOKEN_NOT_FOUND", "TOKEN_REPLAY", "TOKEN_EXPIRED",
        "TOKEN_BINDING_MISMATCH", "TOKEN_CONTEXT_MISMATCH", "TOKEN_INVALID", "TOKEN_OPERATION_MISMATCH", "MFA_FAILED", "MFA_PROOF_INVALID",
        "MFA_PROOF_REPLAY", "MFA_PROOF_MISMATCH", "MFA_PROOF_EXPIRED", "MFA_REQUIRED", "MFA_PROOF_REQUIRED",
        "IDENTITY_MISMATCH", "PROMPT_INJECTION_BLOCKED", "CIRCUIT_OPEN",
        # Online password guessing counts towards the same per-account breaker as
        # a faulty nonce: the point of the breaker is to stop repetition.
        "INVALID_CREDENTIALS", "INVALID_LOGIN_OTP",
    }
    if exc.code in security_codes:
        record_security_failure(user_id, exc.code, request_id=request_id)
    audit_event(action, user_id=user_id, decision="BLOCK", outcome="FAILURE", reason=exc.code, request_id=request_id, metadata={"message": exc.detail})
    _raise_security(exc)


_WRITE_ACTIONS_REQUIRING_HEADER = {
    "RISK_PRECHECK",
    "LOCK_PRECHECK",
    "UNLOCK_PRECHECK",
    "OPERATION_PRECHECK",
    "TRANSFER_EXECUTE",
    "LOCK_FUND",
    "UNLOCK_FUND",
    "OPERATION_EXECUTE",
    # Risk assessment persists a suitability profile.  Although the resulting
    # profile is a green/read-style fact, the questionnaire write must still
    # be attributed to an authenticated principal so another account cannot be
    # overwritten by a forged body user id.
    "WEALTH_RISK_ASSESSMENT",
}


def _get_actor(requested_user: str, presented_user: Optional[str], *, action: str, request_id: str) -> str:
    # A body/path user id is an object selector, not authentication.  Require
    # a presented principal for every operation that can mint a nonce or
    # mutate state; otherwise a caller could deliberately attribute repeated
    # bad-token failures to another account while demo compatibility remains
    # enabled for read-only queries.
    if action in _WRITE_ACTIONS_REQUIRING_HEADER and not (presented_user or "").strip():
        exc = SecurityError("写操作需要登录身份", 401, "AUTHENTICATION_REQUIRED")
        _record_failure_and_raise(exc, user_id=None, action=action, request_id=request_id)
        raise AssertionError("unreachable")
    try:
        return validate_actor(requested_user, presented_user)
    except SecurityError as exc:
        # Attribute an authentication/identity failure to the presented
        # principal (or anonymous), never to the account named in an
        # untrusted request body/path.  Otherwise an attacker could repeatedly
        # submit ``requested_user=victim`` with a mismatching header and open
        # the victim's circuit breaker as a denial-of-service primitive.
        failure_user = presented_user or (None if exc.code == "AUTHENTICATION_REQUIRED" else requested_user)
        _record_failure_and_raise(exc, user_id=failure_user, action=action, request_id=request_id)
        raise AssertionError("unreachable")


def _resolve_target(value: str, *, from_user: Optional[str] = None) -> tuple[str, dict[str, Any]]:
    safe_value = sanitize_text(value, field="收款人", max_length=128)
    target_id = CONTACTS.get(safe_value, safe_value)
    receiver = ACCOUNTS.get(target_id)
    if not receiver:
        raise SecurityError("收款方账户不存在或未登记为联系人", 404, "TARGET_NOT_FOUND")
    if from_user and target_id == from_user:
        raise SecurityError("不允许向本人账户转账", 400, "SELF_TRANSFER")
    if receiver.get("status") != "ACTIVE":
        raise SecurityError("收款方账户当前不可用", 409, "TARGET_INACTIVE")
    return target_id, receiver


def _ensure_account(user_id: str) -> dict[str, Any]:
    account = ACCOUNTS.get(user_id)
    if not account:
        raise SecurityError("账户不存在", 404, "ACCOUNT_NOT_FOUND")
    if account.get("status") != "ACTIVE":
        raise SecurityError("账户已被安全锁定或不可用", 423, "ACCOUNT_LOCKED")
    return account


def _daily_spent(user_id: str) -> float:
    today = datetime.now().strftime("%Y-%m-%d")
    # Initialize the date bucket without discarding a deliberately seeded
    # demo counter; only a known prior date is reset on rollover.
    if user_id not in DAILY_TRANSFER_DATE:
        DAILY_TRANSFER_DATE[user_id] = today
        DAILY_TRANSFERS.setdefault(user_id, 0.0)
    elif DAILY_TRANSFER_DATE.get(user_id) != today:
        DAILY_TRANSFER_DATE[user_id] = today
        DAILY_TRANSFERS[user_id] = 0.0
    return float(DAILY_TRANSFERS.get(user_id, 0.0))


def _split_shares(total_cents: int, participant_count: int) -> tuple[int, int]:
    """Split cents so the parts always add up exactly (no rounding leakage).

    Participants pay an equal floor share; the initiator bears the remainder,
    so ``participant_share * n + initiator_share == total`` for every amount
    (for example 100.00 / 3 -> 33.33 + 33.33 + 33.34).
    """

    divisor = participant_count + 1
    participant_share = total_cents // divisor
    initiator_share = total_cents - participant_share * participant_count
    return participant_share, initiator_share


def _collect_split_shares(actor: str, user_id: str, total: float, participants: list[str], target: Optional[str]) -> dict[str, Any]:
    """Collect each participant's AA share into the initiator's account.

    The demo settles AA requests against the mock accounts of the resolved
    contacts: each participant's share is debited, the initiator is credited,
    and both sides get an itemised transaction.  A participant with
    insufficient funds is reported as failed instead of blocking the others,
    and the request ends as SETTLED or PARTIAL accordingly.
    """

    total_cents = int(round(total * 100))
    participant_share_cents, initiator_share_cents = _split_shares(total_cents, len(participants))
    initiator = _ensure_account(user_id)
    collected_cents = 0
    rows: list[dict[str, Any]] = []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for name in participants:
        target_id, account = _resolve_target(name, from_user=user_id)
        share = participant_share_cents / 100.0
        status = "COLLECTED"
        reason: Optional[str] = None
        if target_id == user_id:
            status, reason = "SKIPPED", "SELF_PARTICIPANT"
        elif account.get("status") != "ACTIVE":
            status, reason = "FAILED", "ACCOUNT_LOCKED"
        elif round(float(account["balance"]) - float(account.get("locked_amount", 0.0)), 2) < share:
            status, reason = "FAILED", "INSUFFICIENT_FUNDS"
        if status == "COLLECTED":
            account["balance"] = round(float(account["balance"]) - share, 2)
            initiator["balance"] = round(float(initiator["balance"]) + share, 2)
            collected_cents += participant_share_cents
            TRANSACTIONS.append({"tx_id": f"TXN{uuid.uuid4().hex[:12].upper()}", "account_id": target_id, "merchant": f"AA 分摊给 {initiator['name']}", "category": "AA 支出", "amount": share, "time": timestamp})
            TRANSACTIONS.append({"tx_id": f"TXN{uuid.uuid4().hex[:12].upper()}", "account_id": user_id, "merchant": f"AA 收款 - {account['name']}", "category": "AA 收款", "amount": share, "time": timestamp})
        rows.append({"name": name, "account_id": target_id, "display_name": account.get("name"), "share": share, "status": status, "reason": reason})
    collected = collected_cents / 100.0
    # Outstanding is what the participants still owe: the initiator's own
    # share is never "collected", so it must not be reported as arrears.
    outstanding_cents = max(0, total_cents - collected_cents - initiator_share_cents)
    outstanding = outstanding_cents / 100.0
    return {
        "collection": rows,
        "collected_amount": collected,
        "outstanding_amount": outstanding,
        "participant_share": participant_share_cents / 100.0,
        "initiator_share": initiator_share_cents / 100.0,
        "status": "SETTLED" if outstanding <= 0 else "PARTIAL",
        "collected_at": timestamp,
        "target": target,
    }


def _card_daily_spent(card_id: str) -> float:
    today = datetime.now().strftime("%Y-%m-%d")
    if card_id not in CARD_DAILY_SPENT_DATE:
        CARD_DAILY_SPENT_DATE[card_id] = today
        CARD_DAILY_SPENT.setdefault(card_id, 0.0)
    elif CARD_DAILY_SPENT_DATE.get(card_id) != today:
        CARD_DAILY_SPENT_DATE[card_id] = today
        CARD_DAILY_SPENT[card_id] = 0.0
    return float(CARD_DAILY_SPENT.get(card_id, 0.0))


def _card_id_for(user_id: str) -> Optional[str]:
    card = CARDS.get(user_id)
    if not card:
        return None
    return str(card.get("card_id") or user_id)


def _enforce_card_limit(user_id: str, amount: float) -> None:
    """Refuse a settlement that would exceed the card's daily payment limit."""

    card = CARDS.get(user_id)
    card_id = _card_id_for(user_id)
    if not card or not card_id:
        return
    limit = card.get("daily_limit")
    if limit is None:
        return
    try:
        limit_value = float(limit)
    except (TypeError, ValueError):
        return
    spent = _card_daily_spent(card_id)
    if round(spent + amount, 2) > round(limit_value, 2):
        raise SecurityError("超出卡片当日支付限额，请调整额度或明日再试", 400, "CARD_LIMIT_EXCEEDED")


def _record_card_spend(user_id: str, amount: float) -> None:
    card_id = _card_id_for(user_id)
    if not card_id:
        return
    CARD_DAILY_SPENT[card_id] = round(_card_daily_spent(card_id) + amount, 2)


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
}


def _pending_transfer_amount(user_id: str) -> float:
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
            if payload.get("from_user") == user_id and payload.get("operation", "TRANSFER") in {"TRANSFER", "SCHEDULED_TRANSFER", "SPLIT_BILL"}:
                try:
                    total += float(payload.get("amount_cents", 0)) / 100.0
                except (TypeError, ValueError):
                    continue
        # Once a scheduled/AA approval nonce is consumed, the resulting task
        # remains an outstanding money-movement reservation. Keep it in the
        # cumulative risk calculation until cancelled or settled, preventing a
        # user from consuming a yellow nonce and immediately obtaining another
        # yellow nonce that exceeds the daily threshold.
        total += sum(
            float(row.get("amount", 0) or 0)
            for row in SCHEDULED_TRANSFERS
            if row.get("user_id") == user_id and row.get("status") in {"SCHEDULED", "WAITING_USER"}
        )
        total += sum(
            float(row.get("amount", 0) or 0)
            for row in SPLIT_REQUESTS
            if row.get("user_id") == user_id and row.get("status") in {"OPEN", "WAITING_USER"}
        )
    return round(total, 2)


def _operation_target(req: OperationPrecheckRequest | OperationExecuteRequest) -> str:
    """Stable token binding for a generic operation."""

    candidate = req.product_id or req.card_id or req.target or getattr(req, "merchant", None)
    if candidate:
        return sanitize_text(candidate, field="操作目标", max_length=128)
    return f"__{req.operation.upper()}__"


def _operation_amount(value: Optional[float]) -> float:
    # The signed token format uses cents and requires a positive value.  For
    # non-financial card/subscription actions 0.01 is an internal sentinel;
    # it is never debited from an account.
    if value in (None, 0):
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
    }


def _validate_split_participants(user_id: str, participants: Optional[list[str]]) -> list[str]:
    """Resolve AA participants against the Core contact allow-list.

    Participant names are used only for the split-request notification flow,
    but they are still security-sensitive routing data.  Accepting arbitrary
    model-generated strings would allow notifications to be sent to an
    unregistered account (or make the audit trail impossible to reconcile).
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
        if target_id in resolved_ids:
            raise SecurityError("分摊参与人不能重复指向同一账户", 400, "INVALID_PARTICIPANTS")
        resolved_ids.add(target_id)
        resolved.append(display)
    return resolved


def _operation_risk_level(user_id: str, operation: str, amount: float) -> str:
    """Return the core-owned risk class for a generic operation.

    Scheduled transfers and AA requests are still money movement. Their
    cumulative same-day total therefore uses the same 1,000-yuan boundary as
    immediate transfers instead of inheriting a permanently yellow class.
    """

    if operation not in {"SCHEDULED_TRANSFER", "SPLIT_BILL"}:
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
    if profile not in {"CONSERVATIVE", "BALANCED", "AGGRESSIVE"}:
        raise SecurityError("申购前请先完成风险评估", 428, "RISK_ASSESSMENT_REQUIRED")
    try:
        product_rank = int(str(product.get("risk_level", "R9")).lstrip("R"))
    except (TypeError, ValueError):
        raise SecurityError("理财产品风险等级无效", 500, "PRODUCT_RISK_INVALID")
    max_rank = {"CONSERVATIVE": 1, "BALANCED": 2, "AGGRESSIVE": 3}[profile]
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


@app.exception_handler(SecurityError)
async def security_error_handler(_: Request, exc: SecurityError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"code": exc.code, "message": exc.detail}},
        headers={"Retry-After": "30"} if exc.status_code == 423 else None,
    )


# ---------------------------------------------------------------------------
# Green zone: read-only operations
# ---------------------------------------------------------------------------
@app.get("/api/v1/accounts/{user_id}/balance")
def get_balance(user_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = _request_id(x_request_id)
    # Return a stable not-found result for an unknown resource before doing
    # object-level identity comparison; this avoids leaking whether a valid
    # caller is associated with a different account.
    if user_id not in ACCOUNTS:
        audit_event("BALANCE_QUERY", user_id=x_user_id, decision="BLOCK", outcome="NOT_FOUND", reason="ACCOUNT_NOT_FOUND", request_id=request_id)
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": "账户不存在"})
    actor = _get_actor(user_id, x_user_id, action="BALANCE_QUERY", request_id=request_id)
    try:
        account = _ensure_account(user_id)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=actor, action="BALANCE_QUERY", request_id=request_id)
    result = {
        "code": 200,
        "user_name": account["name"],
        "total_balance": account["balance"],
        "locked_amount": account["locked_amount"],
        "available_balance": account["balance"] - account["locked_amount"],
        "status": account["status"],
    }
    audit_event("BALANCE_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id)
    return result


@app.get("/api/v1/accounts/{user_id}/transactions")
def get_transactions(user_id: str, date_scope: str = "ALL", x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = _request_id(x_request_id)
    if user_id not in ACCOUNTS:
        audit_event("TRANSACTION_QUERY", user_id=x_user_id, decision="BLOCK", outcome="NOT_FOUND", reason="ACCOUNT_NOT_FOUND", request_id=request_id)
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": "账户不存在"})
    actor = _get_actor(user_id, x_user_id, action="TRANSACTION_QUERY", request_id=request_id)
    try:
        _ensure_account(user_id)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=actor, action="TRANSACTION_QUERY", request_id=request_id)
    scope = str(date_scope or "ALL").upper()
    scope = {"今年": "YEAR", "年度": "YEAR", "全年": "YEAR"}.get(scope, scope)
    if scope not in {"TODAY", "MONTH", "LAST_MONTH", "YEAR", "ALL"}:
        raise HTTPException(status_code=400, detail={"code": "INVALID_DATE_SCOPE", "message": "不支持的时间范围"})
    records = [tx.copy() for tx in TRANSACTIONS if tx.get("account_id") == user_id]
    records = filter_transactions(records, scope)
    audit_event("TRANSACTION_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"record_count": len(records), "date_scope": scope})
    return {"code": 200, "total_records": len(records), "data": records}


@app.get("/api/v1/contacts/{user_id}")
def get_contacts(user_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(user_id, x_user_id, action="CONTACT_QUERY", request_id=request_id)
    _ensure_account(user_id)
    # Only expose the caller's allow-listed contacts; phone numbers are
    # masked so this endpoint cannot become an account-enumeration oracle.
    rows = []
    seen = set()
    for alias, target_id in CONTACTS.items():
        if target_id in seen:
            continue
        receiver = ACCOUNTS.get(target_id)
        if not receiver:
            continue
        phone = str(receiver.get("phone", ""))
        rows.append({"contact_id": target_id, "display_name": receiver.get("name"), "phone_masked": phone[:3] + "****" + phone[-4:] if len(phone) >= 7 else "****"})
        seen.add(target_id)
    audit_event("CONTACT_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"count": len(rows)})
    return {"code": 200, "data": rows}


@app.get("/api/v1/accounts/{user_id}/bill-analysis")
def get_bill_analysis(user_id: str, date_scope: str = "ALL", x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """GREEN: return source-bound bill facts for a report or chart."""

    request_id = _request_id(x_request_id)
    actor = _get_actor(user_id, x_user_id, action="BILL_ANALYSIS", request_id=request_id)
    _ensure_account(user_id)
    scope = str(date_scope or "ALL").upper()
    scope = {"今年": "YEAR", "年度": "YEAR", "全年": "YEAR"}.get(scope, scope)
    if scope not in {"TODAY", "MONTH", "LAST_MONTH", "YEAR", "ALL"}:
        raise HTTPException(status_code=400, detail={"code": "INVALID_DATE_SCOPE", "message": "不支持的时间范围"})
    rows = filter_transactions([tx.copy() for tx in TRANSACTIONS if tx.get("account_id") == user_id], scope)
    facts = analyze_transactions(rows)
    audit_event("BILL_ANALYSIS", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"date_scope": scope, "record_count": facts["record_count"]})
    return {"code": 200, "date_scope": scope, "facts": facts, "records": rows}


@app.get("/api/v1/wealth/products")
def get_wealth_products(user_id: Optional[str] = None, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """GREEN: list product facts; no recommendation is fabricated by the API."""

    requested = user_id or x_user_id
    if not requested:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    request_id = _request_id(x_request_id)
    actor = _get_actor(requested, x_user_id, action="WEALTH_QUERY", request_id=request_id)
    _ensure_account(requested)
    audit_event("WEALTH_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id)
    return {"code": 200, "data": [dict(product) for product in WEALTH_PRODUCTS]}


@app.get("/api/v1/wealth/holdings/{user_id}")
def get_wealth_holdings(user_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(user_id, x_user_id, action="WEALTH_HOLDINGS_QUERY", request_id=request_id)
    _ensure_account(user_id)
    rows = [dict(row) for row in WEALTH_HOLDINGS.get(user_id, [])]
    audit_event("WEALTH_HOLDINGS_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"count": len(rows)})
    return {"code": 200, "data": rows}


@app.get("/api/v1/wealth/recommend")
def recommend_wealth(user_id: Optional[str] = None, risk_profile: str = "CONSERVATIVE", x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    requested = user_id or x_user_id
    if not requested:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    request_id = _request_id(x_request_id)
    actor = _get_actor(requested, x_user_id, action="WEALTH_RECOMMEND", request_id=request_id)
    _ensure_account(requested)
    profile = str(risk_profile or "CONSERVATIVE").upper()
    if profile not in {"CONSERVATIVE", "BALANCED", "AGGRESSIVE"}:
        raise HTTPException(status_code=400, detail={"code": "INVALID_RISK_PROFILE", "message": "风险偏好无效"})
    max_risk = {"CONSERVATIVE": 1, "BALANCED": 2, "AGGRESSIVE": 3}[profile]
    products = [p for p in WEALTH_PRODUCTS if int(str(p.get("risk_level", "R3")).lstrip("R")) <= max_risk]
    audit_event("WEALTH_RECOMMEND", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"risk_profile": profile, "count": len(products)})
    return {"code": 200, "risk_profile": profile, "disclaimer": "推荐仅基于演示资料，不构成投资建议。", "data": [dict(p) for p in products]}


@app.post("/api/v1/wealth/risk-assessment")
def wealth_risk_assessment(
    req: RiskAssessmentRequest,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Record an explicit suitability questionnaire for the authenticated user.

    The profile is a persisted (in this demo, in-memory) authorization input,
    so this endpoint is treated as a write even though its risk tier is green.
    Missing answers are rejected rather than replaced with a conservative
    looking default.  An optional idempotency key makes client retries safe.
    """

    request_id = _request_id(x_request_id)
    actor = _get_actor(req.user_id, x_user_id, action="WEALTH_RISK_ASSESSMENT", request_id=request_id)
    try:
        _ensure_account(req.user_id)
        if req.horizon_years is None or isinstance(req.horizon_years, bool) or req.horizon_years < 0 or req.horizon_years > 100:
            raise SecurityError("请填写 0 到 100 年之间的投资期限", 400, "INVALID_HORIZON")
        if req.loss_tolerance is None or req.experience is None:
            raise SecurityError("风险承受程度和投资经验不能为空", 400, "ASSESSMENT_INCOMPLETE")
        tolerance_raw = sanitize_text(req.loss_tolerance, field="风险承受程度", max_length=40).upper().strip()
        experience_raw = sanitize_text(req.experience, field="投资经验", max_length=40).upper().strip()
        # Accept a small, explicit set of Chinese labels at the API boundary;
        # arbitrary model-generated labels are still rejected.
        tolerance = {
            "低": "LOW", "低风险": "LOW", "保守": "LOW",
            "中": "MEDIUM", "中风险": "MEDIUM", "中等风险": "MEDIUM", "平衡": "MEDIUM",
            "高": "HIGH", "高风险": "HIGH", "激进": "HIGH",
        }.get(tolerance_raw, tolerance_raw)
        experience = {
            "无": "NONE", "无经验": "NONE", "没有经验": "NONE", "新手": "NONE",
            "基础": "BASIC", "基础经验": "BASIC", "一般": "BASIC",
            "专业": "PROFESSIONAL", "专业经验": "PROFESSIONAL",
        }.get(experience_raw, experience_raw)
        if tolerance not in {"LOW", "MEDIUM", "HIGH"} or experience not in {"NONE", "BASIC", "PROFESSIONAL"}:
            raise SecurityError("风险评估选项无效", 400, "INVALID_ASSESSMENT")
        # Suitability is bounded by both loss tolerance and demonstrated
        # experience.  A high tolerance from a novice cannot silently unlock a
        # high-risk product.
        tolerance_rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}[tolerance]
        experience_cap = {"NONE": 1, "BASIC": 2, "PROFESSIONAL": 3}[experience]
        effective_rank = min(tolerance_rank, experience_cap)
        profile = {1: "CONSERVATIVE", 2: "BALANCED", 3: "AGGRESSIVE"}[effective_rank]
        idem_payload = {
            "user_id": req.user_id,
            "horizon_years": int(req.horizon_years),
            "loss_tolerance": tolerance,
            "experience": experience,
        }
        cached = _idempotency_begin(actor, "wealth_risk_assessment", idempotency_key, idem_payload, request_id=request_id)
        if cached is not None:
            return cached
        with STATE_LOCK:
            WEALTH_RISK_PROFILES[req.user_id] = {
                "risk_profile": profile,
                "assessed_at": datetime.now(timezone.utc).isoformat(),
                "horizon_years": int(req.horizon_years),
                "loss_tolerance": tolerance,
                "experience": experience,
                "source": "AGENT_OR_API",
            }
    except SecurityError as exc:
        _idempotency_abort(actor, "wealth_risk_assessment", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="WEALTH_RISK_ASSESSMENT", request_id=request_id)
    response = {
        "code": 200,
        "risk_profile": profile,
        "horizon_years": int(req.horizon_years),
        "disclaimer": "风险评估仅供演示，不构成投资建议。",
    }
    _idempotency_complete(actor, "wealth_risk_assessment", idempotency_key, idem_payload, response)
    persist_ledger()
    audit_event("WEALTH_RISK_ASSESSMENT", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"profile": profile, "horizon_years": int(req.horizon_years)})
    return response


@app.get("/api/v1/wealth/compare")
def compare_wealth(
    user_id: Optional[str] = None,
    product_ids: str = "",
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
):
    """GREEN: compare two or more allow-listed product facts."""

    requested = user_id or x_user_id
    if not requested:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    request_id = _request_id(x_request_id)
    actor = _get_actor(requested, x_user_id, action="WEALTH_COMPARE", request_id=request_id)
    _ensure_account(requested)
    values = [sanitize_text(item, field="理财产品编号", max_length=64).upper() for item in str(product_ids or "").split(",") if str(item).strip()]
    if len(values) < 2 or len(values) > 10 or len(set(values)) != len(values):
        raise HTTPException(status_code=400, detail={"code": "INVALID_PRODUCTS", "message": "产品对比至少需要两个不同的产品编号"})
    rows = []
    for product_id in values:
        product = next((item for item in WEALTH_PRODUCTS if item.get("product_id") == product_id), None)
        if not product:
            raise HTTPException(status_code=404, detail={"code": "PRODUCT_NOT_FOUND", "message": "理财产品不存在"})
        rows.append(dict(product))
    audit_event("WEALTH_COMPARE", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"product_count": len(rows)})
    return {"code": 200, "disclaimer": "对比仅基于演示资料，不构成投资建议。", "data": rows}


@app.get("/api/v1/subscriptions/{user_id}")
def get_subscriptions(user_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(user_id, x_user_id, action="SUBSCRIPTION_QUERY", request_id=request_id)
    _ensure_account(user_id)
    rows = [dict(row) for row in SUBSCRIPTIONS.get(user_id, [])]
    audit_event("SUBSCRIPTION_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"count": len(rows)})
    return {"code": 200, "data": rows}


@app.get("/api/v1/subscriptions/{user_id}/detect")
def detect_subscriptions(user_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """GREEN: identify recurring debit candidates from verified records."""

    request_id = _request_id(x_request_id)
    actor = _get_actor(user_id, x_user_id, action="SUBSCRIPTION_DETECT", request_id=request_id)
    _ensure_account(user_id)
    candidates = [dict(row) for row in SUBSCRIPTIONS.get(user_id, []) if row.get("status") == "ACTIVE"]
    audit_event("SUBSCRIPTION_DETECT", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"count": len(candidates)})
    return {"code": 200, "data": candidates, "renewal_reminders": [{"subscription_id": row["subscription_id"], "next_charge": row["next_charge"], "message": f"{row['merchant']} 将于 {row['next_charge']} 扣款 ¥{row['amount']:.2f}"} for row in candidates]}


@app.post("/api/v1/subscriptions/{user_id}/reminders")
def create_renewal_reminders(user_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """Create idempotent renewal reminders (two days before the charge).

    No money moves here, so this is a green write: it only materialises the
    detected renewals as auditable task-center items.  Repeating the call for
    the same subscription and charge date returns the existing task instead of
    duplicating it.
    """

    request_id = _request_id(x_request_id)
    actor = _get_actor(user_id, x_user_id, action="SUBSCRIPTION_REMINDER", request_id=request_id)
    _ensure_account(user_id)
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    with STATE_LOCK:
        with _ledger_transaction():
            for row in SUBSCRIPTIONS.get(user_id, []):
                if row.get("status") != "ACTIVE":
                    continue
                try:
                    charge_date = datetime.strptime(str(row.get("next_charge")), "%Y-%m-%d")
                except (TypeError, ValueError):
                    skipped.append({"subscription_id": row.get("subscription_id"), "reason": "INVALID_NEXT_CHARGE"})
                    continue
                run_at = (charge_date - timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
                task = TASK_STORE.create(
                    actor,
                    "RENEWAL_REMINDER",
                    {
                        "subscription_id": row["subscription_id"],
                        "merchant": row.get("merchant"),
                        "amount": row.get("amount"),
                        "next_charge": row.get("next_charge"),
                        "message": f"{row.get('merchant')} 将于 {row.get('next_charge')} 扣款 ¥{float(row.get('amount') or 0):,.2f}，需要取消请提前操作",
                    },
                    run_at=run_at,
                    idempotency_key=f"renewal:{row['subscription_id']}:{row.get('next_charge')}",
                )
                created.append(task.to_dict())
    audit_event(
        "SUBSCRIPTION_REMINDER_CREATE",
        user_id=actor,
        risk_level="GREEN",
        decision="ALLOW",
        outcome="SUCCESS",
        request_id=request_id,
        metadata={"created": len(created), "skipped": len(skipped)},
    )
    return {"code": 200, "status": "CREATED", "data": created, "skipped": skipped}


@app.get("/api/v1/cards/{user_id}")
def get_cards(user_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(user_id, x_user_id, action="CARD_QUERY", request_id=request_id)
    _ensure_account(user_id)
    card = CARDS.get(user_id)
    data: list[dict[str, Any]] = []
    if card:
        enriched = dict(card)
        card_id = _card_id_for(user_id)
        if card_id:
            spent = _card_daily_spent(card_id)
            enriched["daily_spent_today"] = spent
            try:
                enriched["daily_remaining"] = round(max(0.0, float(card.get("daily_limit") or 0.0) - spent), 2)
            except (TypeError, ValueError):
                enriched["daily_remaining"] = None
        data.append(enriched)
    audit_event("CARD_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id)
    return {"code": 200, "data": data}


# ---------------------------------------------------------------------------
# Risk prechecks, explicit confirmation and MFA
# ---------------------------------------------------------------------------
@app.post("/api/v1/risk/precheck")
def risk_precheck(req: PrecheckRequest, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(req.from_user, x_user_id, action="RISK_PRECHECK", request_id=request_id)
    idem_payload = {"from_user": req.from_user, "target": req.target_name_or_id, "amount": req.amount, "remark": req.remark or "智能转账"}
    cached = _idempotency_begin(actor, "risk_precheck", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    try:
        _ensure_account(req.from_user)
        target_id, receiver = _resolve_target(req.target_name_or_id, from_user=req.from_user)
        remark = sanitize_text(req.remark or "智能转账", field="转账备注", max_length=200)
        cents = amount_to_cents(req.amount)
        if not math.isfinite(float(req.amount)):
            raise SecurityError("金额格式无效", 400, "INVALID_AMOUNT")
    except SecurityError as exc:
        _idempotency_abort(actor, "risk_precheck", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="RISK_PRECHECK", request_id=request_id)
    from security import STATE_LOCK
    with STATE_LOCK:
        current_spent = _daily_spent(req.from_user)
        pending = _pending_transfer_amount(req.from_user)
        accumulated_after = round(current_spent + pending + cents / 100.0, 2)
        risk_level = "YELLOW" if accumulated_after <= 1000.0 else "RED"
        required_actions = ["USER_CLICK_CONFIRM"] if risk_level == "YELLOW" else ["USER_CLICK_CONFIRM", "SMS_OTP", "FACE_RECOGNITION"]
        token, payload = issue_auth_token(from_user=req.from_user, target_id=target_id, target_display=receiver["name"], amount=req.amount, remark=remark, risk_level=risk_level, required_actions=required_actions, operation="TRANSFER")
    audit_event("RISK_PRECHECK", user_id=actor, risk_level=risk_level, decision="CHALLENGE", outcome="TOKEN_ISSUED", request_id=request_id, metadata={"jti": payload["jti"], "target_id": target_id, "amount_cents": cents, "daily_accumulated_after_cents": int(round(accumulated_after * 100)), "required_actions": required_actions})
    response = {"risk_level": risk_level, "daily_accumulated_now": current_spent, "daily_pending_reserved": pending, "daily_accumulated_after": accumulated_after, "auth_token": token, "token_type": "Bearer-Auth-Nonce", "expires_at": payload["exp"], "required_actions": required_actions, "target_id": target_id, "target_name": receiver["name"], "mfa_required": risk_level == "RED"}
    _idempotency_complete(actor, "risk_precheck", idempotency_key, idem_payload, response)
    return response


@app.post("/api/v1/risk/precheck/lock")
def lock_precheck(req: LockPrecheckRequest, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(req.user_id, x_user_id, action="LOCK_PRECHECK", request_id=request_id)
    idem_payload = {"user_id": req.user_id, "amount": req.amount, "reason": req.reason, "event_at": req.event_at, "reminder_at": req.reminder_at}
    cached = _idempotency_begin(actor, "lock_precheck", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    try:
        account = _ensure_account(req.user_id)
        reason = sanitize_text(req.reason, field="锁定原因", max_length=300)
        cents = amount_to_cents(req.amount)
        event_at, reminder_at = _birthday_times(req.event_at, req.reminder_at)
        if account["balance"] - account["locked_amount"] < cents / 100.0:
            raise SecurityError("可用余额不足以锁定此金额", 400, "INSUFFICIENT_FUNDS")
    except SecurityError as exc:
        _idempotency_abort(actor, "lock_precheck", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="LOCK_PRECHECK", request_id=request_id)
    lock_context_hash = _context_hash({"event_at": event_at, "reminder_at": reminder_at})
    risk_level = _fund_lock_risk_level(req.amount)
    required_actions = ["USER_CLICK_CONFIRM"] if risk_level == "YELLOW" else ["USER_CLICK_CONFIRM", "SMS_OTP", "FACE_RECOGNITION"]
    token, payload = issue_auth_token(from_user=req.user_id, target_id="__LOCK_FUND__", target_display="资金隔离池", amount=req.amount, remark=reason, risk_level=risk_level, required_actions=required_actions, operation="LOCK_FUND", context_hash=lock_context_hash)
    audit_event("LOCK_PRECHECK", user_id=actor, risk_level=risk_level, decision="CHALLENGE", outcome="TOKEN_ISSUED", request_id=request_id, metadata={"jti": payload["jti"], "amount_cents": cents})
    response = {"risk_level": risk_level, "auth_token": token, "token_type": "Bearer-Auth-Nonce", "expires_at": payload["exp"], "required_actions": required_actions, "mfa_required": risk_level == "RED", "event_at": event_at, "reminder_at": reminder_at}
    _idempotency_complete(actor, "lock_precheck", idempotency_key, idem_payload, response)
    return response


@app.post("/api/v1/risk/precheck/unlock")
def unlock_precheck(req: UnlockPrecheckRequest, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    """Issue a yellow-zone nonce for releasing a previously locked amount."""
    request_id = _request_id(x_request_id)
    actor = _get_actor(req.user_id, x_user_id, action="UNLOCK_PRECHECK", request_id=request_id)
    try:
        _ensure_account(req.user_id)
        reason = sanitize_text(req.reason or "解除资金隔离", field="解除原因", max_length=300)
        cents = amount_to_cents(req.amount)
        lock_id = sanitize_text(req.lock_id, field="锁定编号", max_length=100) if req.lock_id else None
        with STATE_LOCK:
            candidates = [v for v in LOCKS.values() if v.get("user_id") == req.user_id and v.get("status") == "LOCKED" and (not lock_id or v.get("lock_id") == lock_id)]
            available_locked = sum(float(v.get("amount", 0)) for v in candidates)
        if available_locked < cents / 100.0:
            raise SecurityError("可解除的锁定金额不足", 400, "LOCK_NOT_FOUND")
        target = lock_id or "__UNLOCK_FUND__"
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=actor, action="UNLOCK_PRECHECK", request_id=request_id)
    idem_payload = {"user_id": req.user_id, "amount": req.amount, "lock_id": req.lock_id, "reason": reason}
    cached = _idempotency_begin(actor, "unlock_precheck", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    risk_level = _fund_lock_risk_level(req.amount)
    required_actions = ["USER_CLICK_CONFIRM"] if risk_level == "YELLOW" else ["USER_CLICK_CONFIRM", "SMS_OTP", "FACE_RECOGNITION"]
    token, payload = issue_auth_token(from_user=req.user_id, target_id=target, target_display="资金隔离池", amount=req.amount, remark=reason, risk_level=risk_level, required_actions=required_actions, operation="UNLOCK_FUND")
    audit_event("UNLOCK_PRECHECK", user_id=actor, risk_level=risk_level, decision="CHALLENGE", outcome="TOKEN_ISSUED", request_id=request_id, metadata={"jti": payload["jti"], "amount_cents": cents, "lock_id": lock_id})
    response = {"risk_level": risk_level, "auth_token": token, "token_type": "Bearer-Auth-Nonce", "expires_at": payload["exp"], "required_actions": required_actions, "mfa_required": risk_level == "RED", "lock_id": lock_id}
    _idempotency_complete(actor, "unlock_precheck", idempotency_key, idem_payload, response)
    return response


@app.post("/api/v1/risk/precheck/operation")
def operation_precheck(
    req: OperationPrecheckRequest,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Create a short-lived approval nonce for non-transfer scenarios.

    This endpoint is intentionally generic so adding a new card/wealth
    operation cannot accidentally create a second, unprotected authorization
    path.  The operation name is checked against ``GENERIC_OPERATION_RISK``.
    """

    request_id = _request_id(x_request_id)
    actor = _get_actor(req.user_id, x_user_id, action="OPERATION_PRECHECK", request_id=request_id)
    resolved_subscription_id: Optional[str] = None
    try:
        _ensure_account(req.user_id)
        operation = _validate_operation_name(req.operation)
        target = _operation_target(req)
        if operation == "PASSWORD_CHANGE" and any(value for value in (req.target, req.product_id, req.card_id, req.merchant)):
            raise SecurityError("密码修改不接受外部目标编号", 400, "TARGET_NOT_ALLOWED")
        reason = sanitize_text(req.reason or "智能银行操作", field="操作原因", max_length=300)
        amount_input = req.new_limit if operation == "CARD_LIMIT" and req.amount in (None, 0) and req.new_limit is not None else req.amount
        amount = _operation_amount(amount_input)
        if operation in {"CARD_APPLY", "CARD_FREEZE", "CARD_UNFREEZE", "CARD_REPORT_LOST", "CARD_UNREPORT_LOST", "SUBSCRIPTION_CANCEL", "PASSWORD_CHANGE"} and amount_input not in (None, 0):
            raise SecurityError("该操作不接受金额字段", 400, "AMOUNT_NOT_ALLOWED")
        if operation in {"SCHEDULED_TRANSFER", "SPLIT_BILL", "WEALTH_PURCHASE", "WEALTH_REDEEM", "CARD_LIMIT"} and (amount_input is None or float(amount_input) <= 0):
            raise SecurityError("该操作需要明确的正数金额", 400, "AMOUNT_REQUIRED")
        if operation == "CARD_LIMIT":
            effective_limit = req.new_limit if req.new_limit is not None else req.amount
            if effective_limit is None or not math.isfinite(float(effective_limit)) or float(effective_limit) <= 0:
                raise SecurityError("卡片额度必须为正数", 400, "INVALID_CARD_LIMIT")
        if operation == "SCHEDULED_TRANSFER":
            if not req.target:
                raise SecurityError("预约转账缺少收款人", 400, "TARGET_REQUIRED")
            _resolve_target(req.target, from_user=req.user_id)
            if not req.scheduled_at:
                raise SecurityError("预约转账缺少执行时间", 400, "SCHEDULE_REQUIRED")
            # A scheduled transfer must remain a future task.  Normalize it
            # before signing so execution and scheduler storage use the same
            # canonical instant and cannot silently turn into an immediate
            # debit after approval.
            normalized_scheduled_at = _normalize_datetime(req.scheduled_at, field="计划时间", require_future=True)
        else:
            normalized_scheduled_at = req.scheduled_at
        validated_participants = _validate_split_participants(req.user_id, req.participants) if operation == "SPLIT_BILL" else list(req.participants or [])
        if operation == "SUBSCRIPTION_CANCEL" and not (req.target or req.merchant):
            raise SecurityError("取消订阅缺少商户或订阅编号", 400, "TARGET_REQUIRED")
        if operation == "SUBSCRIPTION_CANCEL":
            resolved_subscription_id = _resolve_subscription(req.user_id, req.target, req.merchant).get("subscription_id")
        if operation in {"WEALTH_PURCHASE", "WEALTH_REDEEM"} and not (req.product_id or req.target):
            raise SecurityError("理财操作缺少产品编号", 400, "PRODUCT_REQUIRED")
        if operation == "WEALTH_PURCHASE":
            if req.user_id not in WEALTH_RISK_PROFILES:
                raise SecurityError("申购前请先完成风险评估", 428, "RISK_ASSESSMENT_REQUIRED")
            _ensure_product_suitable(req.user_id, req.product_id or req.target or "")
        if operation == "WEALTH_COMPARE":
            product_ids = list(req.product_ids or [])
            if len(product_ids) < 2:
                raise SecurityError("产品对比至少需要两个产品编号", 400, "PRODUCTS_REQUIRED")
            if len(product_ids) > 10 or len(set(product_ids)) != len(product_ids):
                raise SecurityError("产品编号列表无效", 400, "INVALID_PRODUCTS")
            if any(not any(p["product_id"] == item for p in WEALTH_PRODUCTS) for item in product_ids):
                raise SecurityError("理财产品不存在", 404, "PRODUCT_NOT_FOUND")
        # Validate a product/card target before issuing a credential where
        # possible.  Unknown targets are rejected, never silently remapped.
        if operation in {"WEALTH_PURCHASE", "WEALTH_REDEEM"} and req.product_id:
            if operation == "WEALTH_PURCHASE":
                _ensure_product_suitable(req.user_id, req.product_id)
            elif not any(p["product_id"] == req.product_id for p in WEALTH_PRODUCTS):
                raise SecurityError("理财产品不存在", 404, "PRODUCT_NOT_FOUND")
        if operation.startswith("CARD_"):
            card = CARDS.get(req.user_id)
            if operation != "CARD_APPLY" and not card:
                raise SecurityError("当前用户没有可操作的卡片", 404, "CARD_NOT_FOUND")
            if req.card_id and (not card or req.card_id != card.get("card_id")):
                raise SecurityError("卡片不存在或不属于当前用户", 404, "CARD_NOT_FOUND")
            if card:
                current_status = str(card.get("status") or "")
                required_state = {
                    "CARD_FREEZE": "ACTIVE",
                    "CARD_UNFREEZE": "FROZEN",
                    "CARD_REPORT_LOST": {"ACTIVE", "FROZEN"},
                    "CARD_UNREPORT_LOST": "LOST",
                }.get(operation)
                if isinstance(required_state, set):
                    if current_status not in required_state:
                        raise SecurityError("卡片当前状态不允许该操作", 409, "INVALID_CARD_STATE")
                elif required_state and current_status != required_state:
                    raise SecurityError("卡片当前状态不允许该操作", 409, "INVALID_CARD_STATE")
            if operation != "CARD_APPLY" and card:
                # Bind the signed approval to the concrete card even when the
                # conversational request omitted a card id (the demo user has
                # one active card).  This prevents a card replacement between
                # precheck and execution from redirecting the action.
                target = req.card_id or card.get("card_id")
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=actor, action="OPERATION_PRECHECK", request_id=request_id)
    # Risk calculation, nonce reservation and issuance share one lock. This
    # prevents concurrent scheduled/AA prechecks from both observing the same
    # daily total and receiving yellow approvals that collectively exceed the
    # threshold.
    with STATE_LOCK:
        risk_level = _operation_risk_level(req.user_id, operation, amount)
        required = ["USER_CLICK_CONFIRM"] if risk_level == "YELLOW" else ["USER_CLICK_CONFIRM", "SMS_OTP", "FACE_RECOGNITION"]
        operation_context = _operation_context(
            operation=operation,
            target=target,
            amount=amount,
            reason=reason,
            new_limit=req.new_limit,
            merchant=req.merchant,
            participants=validated_participants,
            product_ids=req.product_ids,
            risk_profile=(WEALTH_RISK_PROFILES.get(req.user_id) or {}).get("risk_profile") if operation == "WEALTH_PURCHASE" else None,
            subscription_id=resolved_subscription_id,
            scheduled_at=normalized_scheduled_at,
        )
        context_hash = _context_hash(operation_context)
        idem_payload = {"user_id": req.user_id, "operation": operation, "context": operation_context}
        cached = _idempotency_begin(actor, "operation_precheck", idempotency_key, idem_payload, request_id=request_id)
        if cached is not None:
            return cached
        token, payload = issue_auth_token(
            from_user=req.user_id,
            target_id=target,
            target_display=target,
            amount=amount,
            remark=reason,
            risk_level=risk_level,
            required_actions=required,
            operation=operation,
            context_hash=context_hash,
        )
    response = {"code": 200, "risk_level": risk_level, "operation": operation, "auth_token": token, "token_type": "Bearer-Auth-Nonce", "expires_at": payload["exp"], "required_actions": required, "mfa_required": risk_level == "RED", "target": target, "context_hash": context_hash}
    _idempotency_complete(actor, "operation_precheck", idempotency_key, idem_payload, response)
    audit_event("OPERATION_PRECHECK", user_id=actor, risk_level=risk_level, decision="CHALLENGE", outcome="TOKEN_ISSUED", request_id=request_id, metadata={"operation": operation, "jti": payload["jti"], "target": target})
    return response


def _login_otp_required() -> bool:
    """Whether a login must also present an SMS code.

    Optional by design: a password-only login is the demo default, and a
    deployment that wants the second factor turns it on here.
    """

    return os.getenv("BANK_LOGIN_REQUIRE_OTP", "0").lower() in {"1", "true", "yes", "on"}


@app.post("/api/v1/auth/login")
def login(req: LoginRequest, x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """Authenticate a subject with a password, optionally plus an SMS code.

    Failures answer uniformly: an unknown account, a disabled account and a
    wrong password all return 401 ``INVALID_CREDENTIALS``, so the endpoint is not
    an account-enumeration oracle.  Every failure is attributed to the presented
    subject and counts towards that account's breaker, which blunts online
    guessing regardless of whether the account exists.
    """

    request_id = _request_id(x_request_id)
    principal = sanitize_text(req.user_id, field="用户标识", max_length=128)
    if not principal:
        raise HTTPException(status_code=400, detail={"code": "INVALID_SUBJECT", "message": "缺少用户标识"})
    try:
        ensure_circuit_closed(principal)
    except SecurityError as exc:
        _raise_security(exc)

    account = ACCOUNTS.get(principal)
    if account is None or account.get("status") != "ACTIVE":
        _record_failure_and_raise(
            SecurityError("账号或口令不正确", 401, "INVALID_CREDENTIALS"),
            user_id=principal,
            action="LOGIN",
            request_id=request_id,
        )
    if not verify_password(req.password, account.get("password_hash")):
        _record_failure_and_raise(
            SecurityError("账号或口令不正确", 401, "INVALID_CREDENTIALS"),
            user_id=principal,
            action="LOGIN",
            request_id=request_id,
        )
    if _login_otp_required() and not verify_login_otp(req.otp):
        _record_failure_and_raise(
            SecurityError("短信验证码不正确", 401, "INVALID_LOGIN_OTP"),
            user_id=principal,
            action="LOGIN",
            request_id=request_id,
        )

    record_security_success(principal)
    result = issue_session_token(principal, request_id=request_id)
    audit_event(
        "LOGIN",
        user_id=principal,
        risk_level="GREEN",
        decision="ALLOW",
        outcome="SUCCESS",
        request_id=request_id,
        metadata={"otp_required": _login_otp_required()},
    )
    return {"code": 200, **result}


@app.post("/api/v1/auth/session")
def create_session(req: SessionRequest, x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """Issue a session credential without authenticating anybody.

    Kept for the demo console and the offline test suite, where there is no
    password to present.  It is gated on ``BANK_DEMO_MODE``: an endpoint that
    mints a credential for any claimed identity would make ``/auth/login``
    pointless, so outside a demo it refuses rather than quietly undermines the
    real login path.
    """

    if not demo_mode():
        raise HTTPException(status_code=403, detail={"code": "DEMO_ENDPOINT_DISABLED", "message": "演示会话接口已停用，请使用口令登录"})
    request_id = _request_id(x_request_id)
    try:
        _ensure_account(req.user_id)
        result = issue_session_token(req.user_id, ttl_seconds=req.ttl_seconds, request_id=request_id)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=req.user_id, action="SESSION_ISSUE", request_id=request_id)
    return {"code": 200, **result}


@app.get("/api/v1/auth/session")
def resolve_session_endpoint(
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
):
    """Resolve a presented session credential to its subject.

    A channel gateway must not keep its own copy of the session registry: a
    credential is only meaningful to the authority that issued it, and a second
    copy would drift - or worse, keep honouring a token the core has already
    dropped.  The gateway therefore asks this endpoint instead.

    The middleware has already validated the token and replaced ``X-User-Id``
    with its subject before the request reaches here, so an invalid or expired
    session never gets this far.
    """

    if not x_session_token:
        raise HTTPException(status_code=401, detail={"code": "SESSION_REQUIRED", "message": "缺少会话凭证"})
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    return {"code": 200, "user_id": x_user_id}


@app.post("/api/v1/auth/logout")
def logout(
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
):
    """Revoke exactly the presented session credential.

    A client signing out must be able to kill the credential it holds on the
    server as well; wiping local storage only would leave a copied token valid
    until it expires.  The scope is deliberately this one session - not every
    session of the principal, which is what credential rotation means - so
    logging out on one device never signs the user's other devices out.
    """

    request_id = _request_id(x_request_id)
    try:
        result = revoke_session(x_session_token, request_id=request_id)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=x_user_id, action="LOGOUT", request_id=request_id)
    return {"code": 200, "status": "LOGGED_OUT", "user_id": result["user_id"]}


# ---------------------------------------------------------------------------
# Device trust root: enrolling the key behind the red tier's biometric factor
# ---------------------------------------------------------------------------
@app.post("/api/v1/devices/register")
def register_device(
    req: DeviceRegisterRequest,
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
):
    """Register this device's public key against the authenticated principal.

    Enrollment is a credential-establishing operation, so it demands a session
    credential rather than a self-asserted identity header: once a key is
    enrolled, its signatures satisfy the biometric factor for that account.
    The middleware has already replaced ``X-User-Id`` with the session subject
    when a session is presented, so the two cannot disagree here.
    """

    request_id = _request_id(x_request_id)
    if not x_session_token:
        raise HTTPException(status_code=401, detail={"code": "SESSION_REQUIRED", "message": "设备注册必须出示会话凭证"})
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    try:
        _ensure_account(x_user_id)
        summary = enroll_device(
            req.public_key,
            user_id=x_user_id,
            label=req.label,
            platform=req.platform,
            app_version=req.app_version,
            request_id=request_id,
        )
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=x_user_id, action="DEVICE_REGISTER", request_id=request_id)
    return {"code": 200, **summary}


@app.post("/api/v1/devices/{device_id}/push-token")
def register_push_token(
    device_id: str,
    req: PushTokenRequest,
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
):
    """Attach a notification delivery token to one of the caller's devices.

    Like enrollment this demands a session credential: a token attached to
    somebody else's device would let one account push notifications onto another
    account's phone.
    """

    request_id = _request_id(x_request_id)
    if not x_session_token:
        raise HTTPException(status_code=401, detail={"code": "SESSION_REQUIRED", "message": "推送令牌注册必须出示会话凭证"})
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    try:
        summary = attach_push_token(device_id, req.token, user_id=x_user_id, request_id=request_id)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=x_user_id, action="PUSH_TOKEN_REGISTER", request_id=request_id)
    return {"code": 200, **summary}


@app.get("/api/v1/devices")
def get_devices(x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """List the devices enrolled to the caller.  No key material is returned."""

    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    request_id = _request_id(x_request_id)
    actor = _get_actor(x_user_id, x_user_id, action="DEVICE_QUERY", request_id=request_id)
    rows = list_devices(actor)
    audit_event("DEVICE_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"count": len(rows)})
    return {"code": 200, "data": rows}


@app.post("/api/v1/devices/{device_id}/revoke")
def revoke_registered_device(device_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """Stop accepting attestations from one of the caller's own devices."""

    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    request_id = _request_id(x_request_id)
    actor = _get_actor(x_user_id, x_user_id, action="DEVICE_REVOKE", request_id=request_id)
    try:
        summary = revoke_device(device_id, user_id=actor, request_id=request_id)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=actor, action="DEVICE_REVOKE", request_id=request_id)
    return {"code": 200, **summary}


@app.post("/api/v1/auth/confirm")
@app.post("/api/v1/auth/confirmation")
@app.post("/api/v1/risk/confirm")
def confirm_operation(req: ConfirmationRequest, x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = _request_id(x_request_id)
    auth_token = req.auth_token or x_auth_token
    # Do not treat a body user id as an authenticated principal.  Until the
    # token subject and X-User-Id are verified, failures must be attributed only
    # to the presented header (or anonymous), otherwise this endpoint becomes
    # a cross-account circuit-breaker DoS primitive.
    actor = x_user_id
    try:
        if not auth_token:
            raise SecurityError("缺少安全授权凭证", 403, "TOKEN_REQUIRED")
        actor = _require_token_subject(auth_token, body_user=req.user_id, header_user=x_user_id, action="USER_CONFIRMATION", request_id=request_id)
        result = confirm_auth_token(auth_token, user_id=actor, request_id=request_id)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=actor, action="USER_CONFIRMATION", request_id=request_id)
    return {"code": 200, **result}


@app.post("/api/v1/auth/mfa/verify")
@app.post("/api/v1/auth/mfa")
@app.post("/api/v1/risk/mfa/verify")
def mfa_verify(req: MFAVerifyRequest, x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    request_id = _request_id(x_request_id)
    otp = req.otp if req.otp is not None else req.sms_code
    auth_token = req.auth_token or x_auth_token
    # The request body is untrusted until the signed token/header binding has
    # succeeded; see ``confirm_operation`` above.
    actor = x_user_id
    try:
        if not auth_token:
            raise SecurityError("缺少安全授权凭证", 403, "TOKEN_REQUIRED")
        actor = _require_token_subject(auth_token, body_user=req.user_id, header_user=x_user_id, action="MFA_VERIFY", request_id=request_id)
        result = verify_mfa(auth_token, otp=otp, face_verified=req.face_verified, face_attestation=req.face_attestation, user_id=actor, request_id=request_id)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=actor, action="MFA_VERIFY", request_id=request_id)
    return {"code": 200, **result}


@app.post("/api/v1/auth/mfa/face/challenge")
@app.post("/api/v1/risk/mfa/face/challenge")
def mfa_face_challenge(req: FaceChallengeRequest, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """Issue a liveness challenge the demo biometric SDK must sign."""

    request_id = _request_id(x_request_id)
    actor = x_user_id
    try:
        actor = _require_token_subject(req.auth_token, body_user=req.user_id, header_user=x_user_id, action="FACE_CHALLENGE", request_id=request_id)
        result = issue_face_challenge(req.auth_token, user_id=actor, request_id=request_id)
    except SecurityError as exc:
        _record_failure_and_raise(exc, user_id=actor, action="FACE_CHALLENGE", request_id=request_id)
    return {"code": 200, **result}


# ---------------------------------------------------------------------------
# Yellow/red execution gates
# ---------------------------------------------------------------------------
@app.post("/api/v1/transfers/execute")
def execute_transfer(req: TransferExecuteRequest, x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"), x_mfa_proof: Optional[str] = Header(None, alias="X-MFA-Proof"), x_user_confirmation: Optional[str] = Header(None, alias="X-User-Confirmation"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(req.from_user, x_user_id, action="TRANSFER_EXECUTE", request_id=request_id)
    token = x_auth_token
    # Do not trust a boolean supplied by an agent/browser.  Only
    # ``/risk/confirm`` (which validates the signed nonce) can set the
    # server-side ``record.confirmed`` bit.  The header is retained solely for
    # correlation/legacy clients and is deliberately ignored for authorization.
    confirmation = False
    proof = req.mfa_proof or x_mfa_proof
    idem_payload = {"from_user": req.from_user, "target": req.target_name_or_id, "amount": req.amount, "remark": req.remark or "智能转账"}
    cached = _idempotency_begin(actor, "transfer_execute", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    try:
        _ensure_account(req.from_user)
        target_id, receiver = _resolve_target(req.target_name_or_id, from_user=req.from_user)
        remark = sanitize_text(req.remark or "智能转账", field="转账备注", max_length=200)
        if not token:
            raise SecurityError("缺少安全授权凭证，严禁直接执行", 403, "TOKEN_REQUIRED")
        with _ledger_transaction():
            with authorize_execution(token, from_user=req.from_user, target_id=target_id, amount=req.amount, remark=remark, confirmation=confirmation, mfa_proof=proof, operation="TRANSFER", request_id=request_id) as (payload, _record):
                sender = ACCOUNTS[req.from_user]
                card_state = CARDS.get(req.from_user)
                if card_state and card_state.get("status") != "ACTIVE":
                    raise SecurityError("付款卡片当前不可用于交易", 423, "CARD_BLOCKED")
                amount = amount_to_cents(req.amount) / 100.0
                # The card's daily payment limit is enforced on settlement, not
                # only displayed; lowering the limit constrains later payments.
                _enforce_card_limit(req.from_user, amount)
                available = sender["balance"] - sender["locked_amount"]
                if available < amount:
                    raise SecurityError("可用余额不足（部分资金已被锁定）", 400, "INSUFFICIENT_FUNDS")
                sender["balance"] = round(sender["balance"] - amount, 2)
                receiver["balance"] = round(receiver["balance"] + amount, 2)
                DAILY_TRANSFERS[req.from_user] = round(_daily_spent(req.from_user) + amount, 2)
                _record_card_spend(req.from_user, amount)
                if int(round(amount * 100)) >= behavior_large_amount_cents():
                    record_behavior_signal(req.from_user, "LARGE_TRANSFER", metadata={"amount_cents": int(round(amount * 100)), "target_id": target_id}, request_id=request_id)
                txn_id = f"TXN{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:4].upper()}"
                TRANSACTIONS.append({"tx_id": txn_id, "account_id": req.from_user, "merchant": f"转账给 {receiver['name']}", "category": "转账支出", "amount": amount, "remark": remark, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                risk_level = payload.get("risk_level")
        record_security_success(actor)
    except SecurityError as exc:
        _idempotency_abort(actor, "transfer_execute", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="TRANSFER_EXECUTE", request_id=request_id)
    except Exception as exc:
        _idempotency_abort(actor, "transfer_execute", idempotency_key)
        audit_event("TRANSFER_EXECUTE", user_id=actor, decision="ERROR", outcome="FAILURE", reason="INTERNAL_ERROR", request_id=request_id)
        raise HTTPException(status_code=500, detail={"code": "CORE_ERROR", "message": "核心记账失败，请稍后重试"}) from exc
    audit_event("TRANSFER_EXECUTE", user_id=actor, risk_level=risk_level, decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"transaction_id": txn_id, "target_id": target_id, "amount_cents": int(round(amount * 100))})
    response = {"code": 200, "status": "SUCCESS", "transaction_id": txn_id, "transferred_amount": amount, "sender_balance": ACCOUNTS[req.from_user]["balance"], "receiver_name": receiver["name"], "risk_level": risk_level}
    _idempotency_complete(actor, "transfer_execute", idempotency_key, idem_payload, response)
    persist_ledger()
    return response


@app.post("/api/v1/accounts/lock_fund")
def lock_fund(req: LockFundRequest, x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"), x_mfa_proof: Optional[str] = Header(None, alias="X-MFA-Proof"), x_user_confirmation: Optional[str] = Header(None, alias="X-User-Confirmation"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    request_id = _request_id(x_request_id)
    actor = _get_actor(req.user_id, x_user_id, action="LOCK_FUND", request_id=request_id)
    # ``user_confirmed`` and ``X-User-Confirmation`` are advisory input only;
    # authorization_execution checks the confirmation bit set by the signed
    # nonce confirmation endpoint.
    confirmation = False
    token = x_auth_token or req.auth_token
    proof = x_mfa_proof or req.mfa_proof
    idem_payload = {"user_id": req.user_id, "amount": req.amount, "reason": req.reason, "event_at": req.event_at, "reminder_at": req.reminder_at}
    cached = _idempotency_begin(actor, "lock_fund", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    try:
        account = _ensure_account(req.user_id)
        reason = sanitize_text(req.reason, field="锁定原因", max_length=300)
        event_at, reminder_at = _birthday_times(req.event_at, req.reminder_at)
        if not token:
            raise SecurityError("资金锁定需要先完成风险预检并确认", 403, "TOKEN_REQUIRED")
        with _ledger_transaction():
            context_hash = _context_hash({"event_at": event_at, "reminder_at": reminder_at})
            with authorize_execution(token, from_user=req.user_id, target_id="__LOCK_FUND__", amount=req.amount, remark=reason, confirmation=confirmation, mfa_proof=proof, operation="LOCK_FUND", context_hash=context_hash, request_id=request_id) as (_payload, _record):
                amount = amount_to_cents(req.amount) / 100.0
                if account["balance"] - account["locked_amount"] < amount:
                    raise SecurityError("可用余额不足以锁定此金额", 400, "INSUFFICIENT_FUNDS")
                account["locked_amount"] = round(account["locked_amount"] + amount, 2)
                remaining = round(account["balance"] - account["locked_amount"], 2)
                lock_id = f"LCK{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}"
                LOCKS[lock_id] = {"lock_id": lock_id, "user_id": req.user_id, "amount": amount, "reason": reason, "event_at": event_at, "reminder_at": reminder_at, "status": "LOCKED", "created_at": datetime.now().isoformat()}
                task = TASK_STORE.create(actor, "FUND_RESERVATION", {"lock_id": lock_id, "amount": amount, "reason": reason, "event_at": event_at, "reminder_at": reminder_at}, compensation="UNLOCK_FUND", idempotency_key=f"lock:{lock_id}")
                TASK_STORE.transition(task.task_id, TaskState.SUCCEEDED, owner_id=actor)
                reminder_task = None
                order_task_ids: list[str] = []
                if event_at and reminder_at:
                    reminder_task = TASK_STORE.create(actor, "REMINDER", {"lock_id": lock_id, "message": "生日前 2 天提醒订购鲜花与蛋糕", "event_at": event_at, "reminder_at": reminder_at}, run_at=reminder_at, depends_on=[task.task_id], idempotency_key=f"reminder:{lock_id}")                    # Keep the two requested purchases as explicit, auditable
                    # order drafts.  They are deliberately *not* sent to an
                    # external merchant in the mock; when due they move to
                    # WAITING_USER so a real provider and a fresh approval can
                    # be connected without silently spending money.
                    for item in ("鲜花", "蛋糕"):
                        order_task = TASK_STORE.create(
                            actor,
                            "ORDER_DRAFT",
                            {"lock_id": lock_id, "item": item, "event_at": event_at, "reminder_at": reminder_at},
                            run_at=reminder_at,
                            depends_on=[task.task_id],
                            compensation="CANCEL_ORDER_DRAFT",
                            idempotency_key=f"order-draft:{lock_id}:{item}",
                        )
                        order_task_ids.append(order_task.task_id)
        if int(round(amount * 100)) >= behavior_large_amount_cents():
            record_behavior_signal(req.user_id, "LARGE_LOCK", metadata={"amount_cents": int(round(amount * 100)), "lock_id": lock_id}, request_id=request_id)
        record_security_success(actor)
    except SecurityError as exc:
        _idempotency_abort(actor, "lock_fund", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="LOCK_FUND", request_id=request_id)
    except Exception as exc:
        _idempotency_abort(actor, "lock_fund", idempotency_key)
        audit_event("LOCK_FUND", user_id=actor, decision="ERROR", outcome="FAILURE", reason="INTERNAL_ERROR", request_id=request_id)
        raise HTTPException(status_code=500, detail={"code": "CORE_ERROR", "message": "核心记账失败，请稍后重试"}) from exc
    audit_event("LOCK_FUND", user_id=actor, risk_level=_payload.get("risk_level", "YELLOW"), decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"amount_cents": int(round(amount * 100)), "reason": reason, "lock_id": lock_id})
    response = {"code": 200, "status": "LOCKED", "lock_id": lock_id, "task_id": task.task_id, "reminder_task_id": reminder_task.task_id if reminder_task else None, "order_task_ids": order_task_ids, "event_at": event_at, "reminder_at": reminder_at, "locked_amount": account["locked_amount"], "remaining_available": remaining, "reason": reason}
    _idempotency_complete(actor, "lock_fund", idempotency_key, idem_payload, response)
    persist_ledger()
    return response


@app.post("/api/v1/accounts/unlock_fund")
@app.post("/api/v1/accounts/rollback_lock")
def unlock_fund(req: UnlockFundRequest, x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"), x_mfa_proof: Optional[str] = Header(None, alias="X-MFA-Proof"), x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    """Release reserved funds through the same confirmation gate.

    A lock id, when supplied, must belong to the authenticated user.  Without
    one, the oldest matching locks are released FIFO; the signed precheck
    token binds the amount and reason.
    """
    request_id = _request_id(x_request_id)
    actor = _get_actor(req.user_id, x_user_id, action="UNLOCK_FUND", request_id=request_id)
    token = x_auth_token or req.auth_token
    proof = x_mfa_proof or req.mfa_proof
    idem_payload = {"user_id": req.user_id, "amount": req.amount, "lock_id": req.lock_id, "reason": req.reason}
    cached = _idempotency_begin(actor, "unlock_fund", idempotency_key, idem_payload, request_id=request_id)
    if cached is not None:
        return cached
    try:
        account = _ensure_account(req.user_id)
        reason = sanitize_text(req.reason or "解除资金隔离", field="解除原因", max_length=300)
        if not token:
            raise SecurityError("解除资金隔离需要先完成风险预检并确认", 403, "TOKEN_REQUIRED")
        lock_id = sanitize_text(req.lock_id, field="锁定编号", max_length=100) if req.lock_id else None
        target = lock_id or "__UNLOCK_FUND__"
        with _ledger_transaction():
            candidates = sorted(
                [v for v in LOCKS.values() if v.get("user_id") == req.user_id and v.get("status") == "LOCKED" and (not lock_id or v.get("lock_id") == lock_id)],
                key=lambda row: row.get("created_at", ""),
            )
            requested_amount = amount_to_cents(req.amount) / 100.0
            if lock_id and (not candidates or float(candidates[0].get("amount", 0)) < requested_amount):
                raise SecurityError("锁定编号或可解除金额不匹配", 400, "LOCK_NOT_FOUND")
            if not lock_id and sum(float(v.get("amount", 0)) for v in candidates) < requested_amount:
                raise SecurityError("可解除的锁定金额不足", 400, "LOCK_NOT_FOUND")
            with authorize_execution(token, from_user=req.user_id, target_id=target, amount=req.amount, remark=reason, confirmation=False, mfa_proof=proof, operation="UNLOCK_FUND", request_id=request_id) as (_payload, _record):
                left = requested_amount
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
                account["locked_amount"] = round(max(0.0, float(account["locked_amount"]) - requested_amount), 2)
                remaining = round(float(account["balance"]) - float(account["locked_amount"]), 2)
                released_set = set(released_ids)
                for queued in TASK_STORE.list(owner_id=actor):
                    if queued.payload.get("lock_id") in released_set and queued.status in {TaskState.PENDING, TaskState.WAITING_USER}:
                        try:
                            TASK_STORE.transition(queued.task_id, TaskState.CANCELLED, owner_id=actor)
                        except (KeyError, ValueError):
                            pass
        record_security_success(actor)
    except SecurityError as exc:
        _idempotency_abort(actor, "unlock_fund", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="UNLOCK_FUND", request_id=request_id)
    except Exception as exc:
        _idempotency_abort(actor, "unlock_fund", idempotency_key)
        audit_event("UNLOCK_FUND", user_id=actor, decision="ERROR", outcome="FAILURE", reason="INTERNAL_ERROR", request_id=request_id)
        raise HTTPException(status_code=500, detail={"code": "CORE_ERROR", "message": "解除资金失败，请稍后重试"}) from exc
    audit_event("UNLOCK_FUND", user_id=actor, risk_level=_payload.get("risk_level", "YELLOW"), decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"amount_cents": int(round(requested_amount * 100)), "lock_ids": released_ids})
    response = {"code": 200, "status": "RELEASED", "released_amount": requested_amount, "lock_ids": released_ids, "locked_amount": account["locked_amount"], "remaining_available": remaining}
    _idempotency_complete(actor, "unlock_fund", idempotency_key, idem_payload, response)
    persist_ledger()
    return response


@app.post("/api/v1/operations/execute")
@app.post("/api/v1/transfers/schedule")
@app.post("/api/v1/transfers/split")
@app.post("/api/v1/subscriptions/cancel")
@app.post("/api/v1/cards/apply")
@app.post("/api/v1/cards/limit")
@app.post("/api/v1/cards/freeze")
@app.post("/api/v1/cards/unfreeze")
@app.post("/api/v1/cards/report-lost")
@app.post("/api/v1/cards/unreport-lost")
@app.post("/api/v1/wealth/purchase")
@app.post("/api/v1/wealth/redeem")
@app.post("/api/v1/auth/password/change")
def execute_operation(
    req: OperationExecuteRequest,
    x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"),
    x_mfa_proof: Optional[str] = Header(None, alias="X-MFA-Proof"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    user_id: Optional[str] = None,
    request: Request = None,
):
    """Execute an allow-listed card/wealth/subscription/task operation.

    A single endpoint keeps the policy boundary uniform.  Convenience routes
    in clients may map to this endpoint, but no operation can bypass the
    signed nonce and (for red actions) the MFA proof.
    """

    request_id = _request_id(x_request_id)
    if user_id and user_id != req.user_id:
        raise HTTPException(status_code=403, detail={"code": "IDENTITY_MISMATCH", "message": "路径用户与请求用户不一致"})
    if request is not None:
        path_operation = {
            "/cards/apply": "CARD_APPLY",
            "/cards/limit": "CARD_LIMIT",
            "/cards/freeze": "CARD_FREEZE",
            "/cards/unfreeze": "CARD_UNFREEZE",
            "/cards/report-lost": "CARD_REPORT_LOST",
            "/cards/unreport-lost": "CARD_UNREPORT_LOST",
            "/wealth/purchase": "WEALTH_PURCHASE",
            "/wealth/redeem": "WEALTH_REDEEM",
            "/auth/password/change": "PASSWORD_CHANGE",
            "/transfers/schedule": "SCHEDULED_TRANSFER",
            "/transfers/split": "SPLIT_BILL",
            "/subscriptions/cancel": "SUBSCRIPTION_CANCEL",
        }
        matched_operation = None
        for suffix, operation_name in path_operation.items():
            if request.url.path.endswith(suffix):
                matched_operation = operation_name
                break
        if matched_operation:
            supplied_operation = str(req.operation or "").strip().upper()
            if supplied_operation and supplied_operation != matched_operation:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "OPERATION_ROUTE_MISMATCH", "message": "请求路径与操作类型不一致"},
                )
            req.operation = matched_operation
    actor = _get_actor(req.user_id, x_user_id, action="OPERATION_EXECUTE", request_id=request_id)
    token = x_auth_token or req.auth_token
    proof = x_mfa_proof or req.mfa_proof
    operation_risk = GENERIC_OPERATION_RISK.get(str(req.operation or "").upper(), "UNKNOWN")
    resolved_subscription_id: Optional[str] = None
    try:
        operation = _validate_operation_name(req.operation)
        _ensure_account(req.user_id)
        target = _operation_target(req)
        if operation == "PASSWORD_CHANGE" and any(value for value in (req.target, req.product_id, req.card_id, req.merchant)):
            raise SecurityError("密码修改不接受外部目标编号", 400, "TARGET_NOT_ALLOWED")
        amount_input = req.new_limit if operation == "CARD_LIMIT" and req.amount in (None, 0) and req.new_limit is not None else req.amount
        amount = _operation_amount(amount_input)
        reason = sanitize_text(req.reason or "智能银行操作", field="操作原因", max_length=300)
        if operation in {"CARD_APPLY", "CARD_FREEZE", "CARD_UNFREEZE", "CARD_REPORT_LOST", "CARD_UNREPORT_LOST", "SUBSCRIPTION_CANCEL", "PASSWORD_CHANGE"} and amount_input not in (None, 0):
            raise SecurityError("该操作不接受金额字段", 400, "AMOUNT_NOT_ALLOWED")
        if operation == "CARD_LIMIT":
            effective_limit = req.new_limit if req.new_limit is not None else req.amount
            if effective_limit is None or not math.isfinite(float(effective_limit)) or float(effective_limit) <= 0:
                raise SecurityError("卡片额度必须为正数", 400, "INVALID_CARD_LIMIT")
        if operation == "WEALTH_PURCHASE" and req.user_id not in WEALTH_RISK_PROFILES:
            raise SecurityError("申购前请先完成风险评估", 428, "RISK_ASSESSMENT_REQUIRED")
        if operation.startswith("CARD_"):
            card = CARDS.get(req.user_id)
            if operation != "CARD_APPLY" and not card:
                raise SecurityError("当前用户没有可操作的卡片", 404, "CARD_NOT_FOUND")
            if req.card_id and (not card or req.card_id != card.get("card_id")):
                raise SecurityError("卡片不存在或不属于当前用户", 404, "CARD_NOT_FOUND")
            if card:
                current_status = str(card.get("status") or "")
                required_state = {
                    "CARD_FREEZE": "ACTIVE",
                    "CARD_UNFREEZE": "FROZEN",
                    "CARD_REPORT_LOST": {"ACTIVE", "FROZEN"},
                    "CARD_UNREPORT_LOST": "LOST",
                }.get(operation)
                if isinstance(required_state, set):
                    if current_status not in required_state:
                        raise SecurityError("卡片当前状态不允许该操作", 409, "INVALID_CARD_STATE")
                elif required_state and current_status != required_state:
                    raise SecurityError("卡片当前状态不允许该操作", 409, "INVALID_CARD_STATE")
            if operation != "CARD_APPLY" and card:
                target = req.card_id or card.get("card_id")
        if not token:
            raise SecurityError("该操作需要先完成风险预检", 403, "TOKEN_REQUIRED")
        validated_participants = _validate_split_participants(req.user_id, req.participants) if operation == "SPLIT_BILL" else list(req.participants or [])
        if operation == "SUBSCRIPTION_CANCEL":
            resolved_subscription_id = _resolve_subscription(req.user_id, req.target, req.merchant).get("subscription_id")
        operation_context = _operation_context(
            operation=operation,
            target=target,
            amount=amount,
            reason=reason,
            new_limit=req.new_limit,
            merchant=req.merchant,
            participants=validated_participants,
            product_ids=req.product_ids,
            risk_profile=(WEALTH_RISK_PROFILES.get(req.user_id) or {}).get("risk_profile") if operation == "WEALTH_PURCHASE" else None,
            subscription_id=resolved_subscription_id,
            scheduled_at=_normalize_datetime(req.scheduled_at, field="计划时间", require_future=True) if operation == "SCHEDULED_TRANSFER" else req.scheduled_at,
        )
        context_hash = _context_hash(operation_context)
        idem_payload = {"user_id": req.user_id, "operation": operation, "context": operation_context}
        cached = _idempotency_begin(actor, "operation_execute", idempotency_key, idem_payload, request_id=request_id)
        if cached is not None:
            return cached
        with _ledger_transaction():
            with authorize_execution(token, from_user=req.user_id, target_id=target, amount=amount, remark=reason, confirmation=False, mfa_proof=proof, operation=operation, context_hash=context_hash, request_id=request_id) as (authorization_payload, _record):
                operation_risk = authorization_payload.get("risk_level", operation_risk)
                account = ACCOUNTS[req.user_id]
                result: dict[str, Any] = {"operation": operation}
                if operation in {"WEALTH_PURCHASE", "WEALTH_REDEEM"}:
                    product_id = req.product_id or target
                    product = _ensure_product_suitable(req.user_id, product_id) if operation == "WEALTH_PURCHASE" else next((p for p in WEALTH_PRODUCTS if p["product_id"] == product_id), None)
                    if not product:
                        raise SecurityError("理财产品不存在", 404, "PRODUCT_NOT_FOUND")
                    if operation == "WEALTH_PURCHASE":
                        if amount < float(product["min_amount"]):
                            raise SecurityError("申购金额低于产品起购金额", 400, "MIN_AMOUNT_NOT_MET")
                        if account["balance"] - account["locked_amount"] < amount:
                            raise SecurityError("可用余额不足", 400, "INSUFFICIENT_FUNDS")
                        account["balance"] = round(account["balance"] - amount, 2)
                        holding = {"holding_id": f"HLD{uuid.uuid4().hex[:10].upper()}", "product_id": product_id, "product_name": product["name"], "principal": amount, "estimated_value": amount, "purchased_at": datetime.now().isoformat()}
                        WEALTH_HOLDINGS.setdefault(req.user_id, []).append(holding)
                        TRANSACTIONS.append({"tx_id": f"TXN{uuid.uuid4().hex[:12].upper()}", "account_id": req.user_id, "merchant": product["name"], "category": "理财申购", "amount": amount, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                        result.update({"status": "PURCHASED", "product": product, "holding": holding})
                    else:
                        holdings = WEALTH_HOLDINGS.setdefault(req.user_id, [])
                        index = next((i for i, h in enumerate(holdings) if h.get("product_id") == product_id and float(h.get("principal", 0)) >= amount), None)
                        if index is None:
                            raise SecurityError("没有足够的持仓可赎回", 400, "HOLDING_NOT_FOUND")
                        holding = holdings[index]
                        holding["principal"] = round(float(holding["principal"]) - amount, 2)
                        account["balance"] = round(account["balance"] + amount, 2)
                        if holding["principal"] <= 0:
                            holdings.pop(index)
                        result.update({"status": "REDEEMED", "product": product, "redeemed_amount": amount})
                elif operation == "CARD_APPLY":
                    card = CARDS.setdefault(req.user_id, {"card_id": f"CARD-{uuid.uuid4().hex[:8].upper()}", "type": "VIRTUAL", "status": "ACTIVE", "daily_limit": 5000.0})
                    result.update({"status": "ISSUED", "card": card})
                elif operation == "CARD_LIMIT":
                    # ``amount`` is already normalized to cents before the
                    # authorization context is checked.  Persist that same
                    # canonical value so a sub-cent input cannot be approved
                    # as one value and stored with a different float rounding.
                    new_limit = amount
                    if not math.isfinite(float(new_limit)) or float(new_limit) <= 0:
                        raise SecurityError("卡片额度必须为正数", 400, "INVALID_CARD_LIMIT")
                    card = CARDS.setdefault(req.user_id, {"card_id": "CARD-001", "type": "VIRTUAL", "status": "ACTIVE", "daily_limit": 5000.0})
                    card["daily_limit"] = round(float(new_limit), 2)
                    result.update({"status": "LIMIT_UPDATED", "card": card})
                elif operation in {"CARD_FREEZE", "CARD_UNFREEZE", "CARD_REPORT_LOST", "CARD_UNREPORT_LOST"}:
                    card = CARDS.get(req.user_id)
                    if not card:
                        raise SecurityError("当前用户没有可操作的卡片", 404, "CARD_NOT_FOUND")
                    current_status = str(card.get("status") or "")
                    transitions = {
                        "CARD_FREEZE": {"ACTIVE": "FROZEN"},
                        "CARD_UNFREEZE": {"FROZEN": "ACTIVE"},
                        "CARD_REPORT_LOST": {"ACTIVE": "LOST", "FROZEN": "LOST"},
                        "CARD_UNREPORT_LOST": {"LOST": "ACTIVE"},
                    }
                    next_status = transitions[operation].get(current_status)
                    if not next_status:
                        raise SecurityError("卡片当前状态不允许该操作", 409, "INVALID_CARD_STATE")
                    card["status"] = next_status
                    result.update({"status": next_status, "card": card})
                elif operation == "PASSWORD_CHANGE":
                    # Now that logins are password-based, "change my password"
                    # has to actually change it - otherwise the old credential
                    # keeps working while the audit trail claims a rotation.
                    # The new password is NOT bound into the signed approval
                    # context on purpose: that context is serialized into the
                    # authorization nonce the client holds, and credential
                    # material has no business inside a bearer token.  What
                    # authorizes the change is the single-use, MFA-gated nonce.
                    if not req.new_password:
                        raise SecurityError("修改密码需要提供新口令", 400, "NEW_PASSWORD_REQUIRED")
                    account["password_hash"] = hash_password(req.new_password)
                    current_version = int(account.get("credential_version", 1))
                    account["credential_version"] = current_version + 1
                    account["credential_rotated_at"] = datetime.now(timezone.utc).isoformat()
                    # A rotated credential must invalidate the sessions it was
                    # used to obtain - including this caller's, which is why the
                    # client is expected to re-login afterwards.
                    revoked = revoke_sessions_for(req.user_id, reason="password_changed", request_id=request_id)
                    result.update({"status": "PASSWORD_CHANGED", "credential_version": account["credential_version"], "revoked_sessions": revoked})
                elif operation == "SUBSCRIPTION_CANCEL":
                    subscriptions = SUBSCRIPTIONS.setdefault(req.user_id, [])
                    selected = _resolve_subscription(req.user_id, req.target, req.merchant)
                    if operation_context.get("subscription_id") and selected.get("subscription_id") != operation_context.get("subscription_id"):
                        raise SecurityError("订阅状态在确认后发生变化，请重新预检", 409, "SUBSCRIPTION_CONTEXT_CHANGED")
                    selected["status"] = "CANCELLED"
                    # A cancelled subscription must not keep nagging: withdraw
                    # its pending renewal reminders as part of the same step.
                    for queued in TASK_STORE.list(owner_id=actor):
                        if queued.task_type != "RENEWAL_REMINDER" or queued.payload.get("subscription_id") != selected.get("subscription_id"):
                            continue
                        if queued.status in {TaskState.PENDING, TaskState.WAITING_USER}:
                            try:
                                TASK_STORE.transition(queued.task_id, TaskState.CANCELLED, owner_id=actor)
                            except (KeyError, ValueError):
                                pass
                    result.update({"status": "CANCELLED", "subscription": selected})
                elif operation == "SCHEDULED_TRANSFER":
                    scheduled_at = operation_context.get("scheduled_at")
                    schedule = {"schedule_id": f"SCH{uuid.uuid4().hex[:10].upper()}", "user_id": req.user_id, "target": req.target, "amount": amount, "scheduled_at": scheduled_at, "status": "SCHEDULED"}
                    SCHEDULED_TRANSFERS.append(schedule)
                    # A signed nonce identifies one approved intent.  Use its
                    # jti for the internal task key so two deliberate,
                    # identical schedules are independent unless the caller
                    # supplies an HTTP Idempotency-Key; reusing only the
                    # context digest would alias distinct approvals and leave
                    # duplicate domain rows pointing at one task.
                    task = TASK_STORE.create(actor, "SCHEDULED_TRANSFER", schedule, run_at=scheduled_at, idempotency_key=f"schedule:{req.user_id}:{authorization_payload.get('jti')}")
                    result.update(schedule, task_id=task.task_id)
                elif operation == "SPLIT_BILL":
                    participants = list(operation_context.get("participants") or [])
                    # AA is a real collection in the mock ledger: participants'
                    # shares are debited and the initiator is credited, with the
                    # remainder assigned to the initiator so the cents always
                    # reconcile (100.00 / 3 -> 33.33 + 33.33 + 33.34).
                    collection = _collect_split_shares(actor, req.user_id, amount, participants, req.target)
                    split = {
                        "split_id": f"SPL{uuid.uuid4().hex[:10].upper()}",
                        "user_id": req.user_id,
                        "amount": amount,
                        "participants": participants,
                        "per_person": collection["participant_share"],
                        "initiator_share": collection["initiator_share"],
                        "participants_total": len(participants),
                        "target": req.target,
                        "status": collection["status"],
                        "collected_amount": collection["collected_amount"],
                        "outstanding_amount": collection["outstanding_amount"],
                        "collection": collection["collection"],
                        "collected_at": collection["collected_at"],
                    }
                    SPLIT_REQUESTS.append(split)
                    # Scope the task to this signed approval.  In particular,
                    # two AA requests for the same total but different
                    # participants must never share one task.
                    task = TASK_STORE.create(actor, "SPLIT_BILL", split, depends_on=[], idempotency_key=f"split:{req.user_id}:{authorization_payload.get('jti')}")
                    result.update(split, task_id=task.task_id)
                else:  # guarded by _validate_operation_name; defensive fallback
                    raise SecurityError("操作未实现", 501, "OPERATION_NOT_IMPLEMENTED")
        behavior_signal = _BEHAVIOR_OPERATION_SIGNALS.get(operation)
        if behavior_signal:
            record_behavior_signal(actor, behavior_signal, metadata={"operation": operation}, request_id=request_id)
        record_security_success(actor)
    except SecurityError as exc:
        if 'idem_payload' in locals():
            _idempotency_abort(actor, "operation_execute", idempotency_key)
        _record_failure_and_raise(exc, user_id=actor, action="OPERATION_EXECUTE", request_id=request_id)
    except Exception as exc:
        if 'idem_payload' in locals():
            _idempotency_abort(actor, "operation_execute", idempotency_key)
        audit_event("OPERATION_EXECUTE", user_id=actor, decision="ERROR", outcome="FAILURE", reason="INTERNAL_ERROR", request_id=request_id)
        raise HTTPException(status_code=500, detail={"code": "CORE_ERROR", "message": "操作执行失败，请稍后重试"}) from exc
    response = {"code": 200, "status": "SUCCESS", "operation": operation, "data": result}
    audit_event("OPERATION_EXECUTE", user_id=actor, risk_level=operation_risk, decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"operation": operation, "target": target})
    _idempotency_complete(actor, "operation_execute", idempotency_key, idem_payload, response)
    persist_ledger()
    return response


# ---------------------------------------------------------------------------
# Task centre: scheduling, cancellation and human takeover
# ---------------------------------------------------------------------------
@app.get("/api/v1/tasks")
def list_tasks(x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    request_id = _request_id(x_request_id)
    actor = _get_actor(x_user_id, x_user_id, action="TASK_QUERY", request_id=request_id)
    rows = [task.to_dict() for task in TASK_STORE.list(owner_id=actor)]
    audit_event("TASK_QUERY", user_id=actor, risk_level="GREEN", decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"count": len(rows)})
    return {"code": 200, "data": rows}


@app.post("/api/v1/tasks/run-due")
def run_due_tasks(x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    """Advance due reminder tasks in the demo scheduler.

    Scheduled money movement is deliberately not auto-settled here: it is
    moved to WAITING_USER so a fresh approval/MFA can be collected at run
    time.  Reminder tasks can be marked delivered automatically.
    """

    # Bound once: calling ``_request_id`` at each use site minted a *different*
    # correlation id per event when the caller supplied none, so one scheduler
    # run could not be traced as a single chain.
    request_id = _request_id(x_request_id)
    expected = os.getenv("BANK_ADMIN_AUDIT_TOKEN")
    if expected:
        if not x_admin_token or not hmac.compare_digest(expected, x_admin_token):
            raise HTTPException(status_code=403, detail={"code": "ADMIN_AUTH_REQUIRED", "message": "需要调度管理员凭证"})
        owner = None
    else:
        if not x_user_id:
            raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
        owner = x_user_id
        _get_actor(owner, owner, action="TASK_RUN", request_id=request_id)
    changed = []
    for task in TASK_STORE.ready():
        if owner and task.owner_id != owner:
            continue
        try:
            TASK_STORE.transition(task.task_id, TaskState.RUNNING, owner_id=owner)
            # Money movement and merchant order drafts must never be executed
            # silently by the scheduler.  At due time they pause for a fresh
            # confirmation/MFA; pure reminders can be marked delivered.
            final_status = TaskState.WAITING_USER if task.task_type in {"SCHEDULED_TRANSFER", "ORDER_DRAFT"} else TaskState.SUCCEEDED
            updated = TASK_STORE.transition(task.task_id, final_status, owner_id=owner)
            changed.append(updated.to_dict())
            if final_status == TaskState.WAITING_USER:
                # This is the moment nothing more happens without the user, so it
                # is the moment to tell them.  The dispatch is audited, including
                # when no delivery provider is configured.
                notify_waiting_user(updated, request_id=request_id)
        except (KeyError, ValueError):
            continue
    audit_event("TASK_RUN", user_id=owner, decision="ALLOW", outcome="SUCCESS", request_id=request_id, metadata={"changed": len(changed)})
    return {"code": 200, "data": changed}


@app.get("/api/v1/tasks/{task_id}")
def get_task(task_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    request_id = _request_id(x_request_id)
    actor = _get_actor(x_user_id, x_user_id, action="TASK_QUERY", request_id=request_id)
    task = TASK_STORE.get(sanitize_text(task_id, field="任务编号", max_length=100), owner_id=actor)
    if not task:
        raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "任务不存在"})
    return {"code": 200, "data": task.to_dict()}


def _task_action(task_id: str, action: str, req: TaskActionRequest, x_user_id: Optional[str], x_request_id: Optional[str]):
    if not x_user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "需要用户身份"})
    request_id = _request_id(x_request_id)
    actor = _get_actor(x_user_id, x_user_id, action=action, request_id=request_id)
    try:
        safe_id = sanitize_text(task_id, field="任务编号", max_length=100)
        reason = sanitize_text(req.reason, field="操作原因", max_length=300)
        if action == "TASK_CANCEL":
            task = TASK_STORE.cancel(safe_id, owner_id=actor)
            # Keep money-movement reservations in sync with the task state so
            # a cancelled scheduled/AA request no longer contributes to the
            # daily risk accumulator.  The task store remains the source of
            # ownership authorization; this only updates the corresponding
            # domain fixture after cancellation succeeds.
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
                # A cancelled prerequisite must not leave dependent reminder
                # or order-draft nodes runnable.  Cascade only to non-terminal
                # tasks owned by the same principal; terminal records remain
                # immutable for auditability.
                cancelled_lock_id = task.payload.get("lock_id") if isinstance(task.payload, dict) and task.task_type in {"REMINDER", "FUND_RESERVATION"} else None
                for dependent in TASK_STORE.list(owner_id=actor):
                    same_reservation = bool(cancelled_lock_id and dependent.payload.get("lock_id") == cancelled_lock_id)
                    if (safe_id in dependent.depends_on or same_reservation) and dependent.status in {TaskState.PENDING, TaskState.WAITING_USER, TaskState.FAILED}:
                        try:
                            TASK_STORE.transition(dependent.task_id, TaskState.CANCELLED, owner_id=actor)
                        except (KeyError, ValueError):
                            pass
                # The cancellation rewrote the domain fixtures (a cancelled
                # schedule/split no longer counts towards the daily risk
                # accumulator), so the durable snapshot has to agree.
                persist_ledger()
        else:
            task = TASK_STORE.takeover(safe_id, owner_id=actor, reason=reason)
    except (SecurityError, KeyError, ValueError) as exc:
        if isinstance(exc, SecurityError):
            _record_failure_and_raise(exc, user_id=actor, action=action, request_id=request_id)
        raise HTTPException(status_code=409 if isinstance(exc, ValueError) else 404, detail={"code": "TASK_STATE_INVALID", "message": str(exc)})
    audit_event(action, user_id=actor, decision="ALLOW", outcome=task.status, request_id=request_id, metadata={"task_id": task.task_id, "reason": reason})
    return {"code": 200, "data": task.to_dict()}


@app.post("/api/v1/tasks/{task_id}/cancel")
def cancel_task(task_id: str, req: Optional[TaskActionRequest] = None, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    return _task_action(task_id, "TASK_CANCEL", req or TaskActionRequest(), x_user_id, x_request_id)


@app.post("/api/v1/tasks/{task_id}/takeover")
def takeover_task(task_id: str, req: Optional[TaskActionRequest] = None, x_user_id: Optional[str] = Header(None, alias="X-User-Id"), x_request_id: Optional[str] = Header(None, alias="X-Request-Id")):
    return _task_action(task_id, "TASK_TAKEOVER", req or TaskActionRequest(), x_user_id, x_request_id)


# ---------------------------------------------------------------------------
# Operational observability
# ---------------------------------------------------------------------------
@app.get("/healthz")
def healthz():
    """Liveness probe that does not disclose account or credential data."""

    return {"status": "ok", "service": "mock-banking-core", "version": app.version}


@app.get("/api/v1/security/health")
def security_health_endpoint():
    return {"code": 200, **security_health(), "sandbox": sandbox_health(), "ledger_persistence": ledger_store_status()}


@app.get("/api/v1/security/circuit/{user_id}")
def security_circuit_endpoint(user_id: str, x_user_id: Optional[str] = Header(None, alias="X-User-Id")):
    if not x_user_id or x_user_id != user_id:
        raise HTTPException(status_code=403, detail={"code": "AUTHORIZATION_REQUIRED", "message": "只能查询当前用户的安全状态"})
    return {"code": 200, **circuit_status(user_id)}


@app.get("/api/v1/security/behavior/{user_id}")
def security_behavior_endpoint(user_id: str, x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"), x_user_id: Optional[str] = Header(None, alias="X-User-Id")):
    """Explain the behaviour-risk score behind a combination lockout."""

    expected = os.getenv("BANK_ADMIN_AUDIT_TOKEN")
    admin_ok = bool(expected and x_admin_token and hmac.compare_digest(expected, x_admin_token))
    if not admin_ok and (not x_user_id or x_user_id != user_id):
        raise HTTPException(status_code=403, detail={"code": "AUTHORIZATION_REQUIRED", "message": "只能查询当前用户的行为风险状态"})
    return {"code": 200, **behavior_status(user_id)}


@app.get("/api/v1/security/audit")
@app.get("/api/v1/audit/logs")
def security_audit_endpoint(limit: int = 100, user_id: Optional[str] = None, x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"), x_user_id: Optional[str] = Header(None, alias="X-User-Id")):
    expected = os.getenv("BANK_ADMIN_AUDIT_TOKEN")
    admin_ok = bool(expected and x_admin_token and hmac.compare_digest(expected, x_admin_token))
    # A normal user may inspect only their own redacted events; cross-user or
    # unscoped access remains admin-only.
    if not admin_ok and (not x_user_id or (user_id and user_id != x_user_id)):
        raise HTTPException(status_code=403, detail={"code": "ADMIN_AUTH_REQUIRED", "message": "需要审计管理员凭证"})
    if not admin_ok:
        # Bind the self-service view to a known account and the presented
        # principal.  Merely choosing ``?user_id=...`` is never sufficient.
        try:
            validate_actor(x_user_id or "", x_user_id)
            _ensure_account(x_user_id or "")
        except SecurityError as exc:
            _raise_security(exc)
        user_id = x_user_id
    return {"code": 200, "data": get_audit_logs(limit=limit, user_id=user_id)}


@app.get("/api/v1/security/audit/verify")
def security_audit_verify(x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token")):
    expected = os.getenv("BANK_ADMIN_AUDIT_TOKEN")
    if not expected or not x_admin_token or not hmac.compare_digest(expected, x_admin_token):
        raise HTTPException(status_code=403, detail={"code": "ADMIN_AUTH_REQUIRED", "message": "需要审计管理员凭证"})
    return {"code": 200, **verify_audit_chain()}


@app.get("/api/v1/security/audit/trace/{request_id}")
def security_audit_trace(request_id: str, x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"), x_user_id: Optional[str] = Header(None, alias="X-User-Id")):
    """Return the full decision chain (precheck → confirm → MFA → settle) of one request.

    A user may trace only their own requests; an audit administrator holding
    ``BANK_ADMIN_AUDIT_TOKEN`` may trace any request.  For a normal user an
    unknown request id is indistinguishable from someone else's request (no
    enumeration oracle), while the administrator sees an empty chain.
    """

    expected = os.getenv("BANK_ADMIN_AUDIT_TOKEN")
    admin_ok = bool(expected and x_admin_token and hmac.compare_digest(expected, x_admin_token))
    trace = get_audit_trace(request_id)
    principal = trace.get("principal")
    if not admin_ok:
        if not x_user_id or not principal or x_user_id != principal:
            raise HTTPException(status_code=403, detail={"code": "ADMIN_AUTH_REQUIRED", "message": "只能查看本人请求的审计链路"})
    return {"code": 200, **trace}


__all__ = ["app", "ACCOUNTS", "CONTACTS", "DAILY_TRANSFERS", "TRANSACTIONS", "LOCKS", "WEALTH_PRODUCTS", "WEALTH_HOLDINGS", "WEALTH_RISK_PROFILES", "CARDS", "SUBSCRIPTIONS", "SCHEDULED_TRANSFERS", "SPLIT_REQUESTS", "AUTH_TOKENS", "MFA_PROOFS", "AUDIT_LOGS", "DEVICES", "reset_state"]


# Test/demo reset helper.  It is intentionally not exposed as an HTTP route;
# production callers must use a transactional rollback workflow instead.
_INITIAL_ACCOUNTS = deepcopy(ACCOUNTS)
_INITIAL_CONTACTS = deepcopy(CONTACTS)
_INITIAL_TRANSACTIONS = deepcopy(TRANSACTIONS)
_INITIAL_WEALTH_HOLDINGS = deepcopy(globals().get("WEALTH_HOLDINGS", {}))
_INITIAL_WEALTH_RISK_PROFILES = deepcopy(globals().get("WEALTH_RISK_PROFILES", {}))
_INITIAL_CARDS = deepcopy(globals().get("CARDS", {}))
_INITIAL_SUBSCRIPTIONS = deepcopy(globals().get("SUBSCRIPTIONS", {}))
_INITIAL_SCHEDULED_TRANSFERS = deepcopy(globals().get("SCHEDULED_TRANSFERS", []))
_INITIAL_SPLIT_REQUESTS = deepcopy(globals().get("SPLIT_REQUESTS", []))


def reset_state() -> None:
    """Restore the deterministic in-memory sandbox and clear security state.

    Deliberately does not touch the durable ledger file: the reset helper is a
    test/demo affordance that never goes through the HTTP authorization path,
    so rewriting the audited snapshot from it would let an out-of-band call
    rewrite what a deployment believes was committed.  Point a deployment's
    ``BANK_LEDGER_STORE_PATH`` at a scratch file when testing resets.
    """

    global _LEDGER_STORE_BLOCKED
    _LEDGER_STORE_BLOCKED = False
    with STATE_LOCK:
        ACCOUNTS.clear()
        ACCOUNTS.update(deepcopy(_INITIAL_ACCOUNTS))
        CONTACTS.clear()
        CONTACTS.update(deepcopy(_INITIAL_CONTACTS))
        TRANSACTIONS[:] = deepcopy(_INITIAL_TRANSACTIONS)
        DAILY_TRANSFERS.clear()
        DAILY_TRANSFERS["user_zhang"] = 0.0
        DAILY_TRANSFER_DATE.clear()
        if "CARD_DAILY_SPENT" in globals():
            CARD_DAILY_SPENT.clear()
        if "CARD_DAILY_SPENT_DATE" in globals():
            CARD_DAILY_SPENT_DATE.clear()
        RATE_LIMIT_STATE.clear()
        LOCKS.clear()
        TASK_STORE.reset()
        IDEMPOTENCY_RECORDS.clear()
        if "WEALTH_HOLDINGS" in globals():
            WEALTH_HOLDINGS.clear()
            WEALTH_HOLDINGS.update(deepcopy(_INITIAL_WEALTH_HOLDINGS))
        if "WEALTH_RISK_PROFILES" in globals():
            WEALTH_RISK_PROFILES.clear()
            WEALTH_RISK_PROFILES.update(deepcopy(_INITIAL_WEALTH_RISK_PROFILES))
        if "CARDS" in globals():
            CARDS.clear()
            CARDS.update(deepcopy(_INITIAL_CARDS))
        if "SUBSCRIPTIONS" in globals():
            SUBSCRIPTIONS.clear()
            SUBSCRIPTIONS.update(deepcopy(_INITIAL_SUBSCRIPTIONS))
        if "SCHEDULED_TRANSFERS" in globals():
            SCHEDULED_TRANSFERS[:] = deepcopy(_INITIAL_SCHEDULED_TRANSFERS)
        if "SPLIT_REQUESTS" in globals():
            SPLIT_REQUESTS[:] = deepcopy(_INITIAL_SPLIT_REQUESTS)
        try:
            from security import reset_security_state

            reset_security_state()
        except Exception:
            pass
        # Device enrollment is cleared by ``reset_security_state`` above; this is
        # belt-and-braces so a reset never leaves a test (or demo) trusting a
        # stale key even if that call raised.
        DEVICES.clear()


# Opt-in durability: when a path is configured, the committed ledger snapshot is
# replayed at import time so accounts, transactions and reservations survive a
# process restart.  With no path configured the in-memory demo stays
# deterministic and side-effect free.  This must run after every ledger global
# above is defined, which is why it sits at the end of the module.
_LEDGER_STORE_LOAD_SUMMARY = load_ledger_store()
if _LEDGER_STORE_LOAD_SUMMARY.get("rejected"):
    logger.error(
        "账本文件未通过校验，已拒绝加载并冻结写入（将使用种子账本）: %s",
        _LEDGER_STORE_LOAD_SUMMARY.get("error"),
    )
