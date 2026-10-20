"""Per-principal failure circuit breaker and behaviour-combination risk rules."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Mapping, Optional

from bank_security.audit import audit_event
from bank_security.config import (
    FAILURE_WINDOW_SECONDS,
    LOCKOUT_SECONDS,
    MAX_FAILURES,
    STATE_LOCK,
    env_int,
)
from bank_security.errors import SecurityError
from bank_security.sanitize import redact


# Per-principal failure windows.  deque values are (monotonic timestamp,
# reason), which avoids clock rollback issues for lockout decisions.
SECURITY_STATE: dict[str, dict[str, Any]] = defaultdict(
    lambda: {"failures": deque(), "locked_until": 0.0, "lock_reason": None}
)


def _purge_failures(state: dict[str, Any], now: Optional[float] = None) -> None:
    current = time.monotonic() if now is None else now
    failures: deque = state["failures"]
    while failures and current - failures[0][0] > FAILURE_WINDOW_SECONDS:
        failures.popleft()


def _principal_key(user_id: Optional[str]) -> str:
    """Canonical key for per-principal security state."""

    value = str(user_id).strip() if user_id else ""
    return value or "anonymous"


def circuit_status(user_id: Optional[str]) -> dict[str, Any]:
    principal = _principal_key(user_id)
    now = time.monotonic()
    with STATE_LOCK:
        state = SECURITY_STATE[principal]
        _purge_failures(state, now)
        locked_until = float(state.get("locked_until", 0.0))
        if locked_until and locked_until <= now:
            state["locked_until"] = 0.0
            state["lock_reason"] = None
            locked_until = 0.0
        return {
            "principal": principal,
            "locked": locked_until > now,
            "locked_until_epoch": locked_until if locked_until > now else None,
            "failure_count": len(state["failures"]),
            "lock_reason": state.get("lock_reason"),
        }


def ensure_circuit_closed(user_id: Optional[str]) -> None:
    status = circuit_status(user_id)
    if status["locked"]:
        retry = max(1, int(status["locked_until_epoch"] - time.monotonic()))
        reason = status.get("lock_reason") or "连续失败"
        raise SecurityError(
            f"安全熔断已开启，请在约 {retry} 秒后重试（原因：{reason}）",
            423,
            "CIRCUIT_OPEN",
        )


def record_security_failure(user_id: Optional[str], reason: str, *, request_id: Optional[str] = None) -> dict[str, Any]:
    # Attribute failures only to a principal actually presented by the caller
    # (or to the anonymous bucket).  Callers must not pass an untrusted
    # request-body ``user_id`` here: doing so would let an attacker open
    # somebody else's circuit with forged payloads.
    principal = _principal_key(user_id)
    reason_text = str(reason)[:120]
    now = time.monotonic()
    with STATE_LOCK:
        state = SECURITY_STATE[principal]
        _purge_failures(state, now)
        # Do not extend an already active lockout on every blocked retry.  A
        # client polling faster than LOCKOUT_SECONDS must not be able to keep
        # an account locked indefinitely.  Each retry is still audited.
        was_locked = float(state.get("locked_until", 0.0)) > now
        if not was_locked:
            state["failures"].append((now, reason_text))
        if not was_locked and len(state["failures"]) >= MAX_FAILURES:
            state["locked_until"] = now + LOCKOUT_SECONDS
            state["lock_reason"] = reason_text
            audit_event(
                "CIRCUIT_BREAKER_OPEN",
                user_id=user_id,
                decision="BLOCK",
                outcome="LOCKED",
                reason=reason_text,
                request_id=request_id,
                metadata={"failure_count": len(state["failures"]), "lockout_seconds": LOCKOUT_SECONDS},
            )
        audit_event(
            "SECURITY_FAILURE",
            user_id=user_id,
            decision="BLOCK",
            outcome="FAILURE",
            reason=reason_text,
            request_id=request_id,
            metadata={"failure_count": len(state["failures"])},
        )
        status = circuit_status(principal)
    # Failed credentials also feed the behaviour engine (credential stuffing).
    record_behavior_signal(user_id, "AUTH_FAILURE", metadata={"reason": reason_text}, request_id=request_id)
    return status


def record_security_success(user_id: Optional[str]) -> None:
    principal = _principal_key(user_id)
    with STATE_LOCK:
        state = SECURITY_STATE[principal]
        _purge_failures(state)
        # A successful authenticated operation clears transient failures but
        # never silently clears an active lockout.
        if float(state.get("locked_until", 0.0)) <= time.monotonic():
            state["failures"].clear()
    # Every successful sensitive operation is also a behaviour signal so that
    # combinations such as "three failed attempts then one success" can be
    # recognised.  The signal itself carries no weight.
    record_behavior_signal(user_id, "SENSITIVE_SUCCESS")


# ---------------------------------------------------------------------------
# Behaviour combination circuit breaker
#
# The failure counter above only reacts to *rejected* credentials.  Real fraud
# often uses valid credentials in a suspicious sequence (transfer then report
# the card lost, raise the limit then drain the account, change the password
# right after a large transfer).  Each sensitive operation therefore emits a
# weighted signal, named combination rules fire on the recent timeline, and a
# score above the threshold opens the same per-principal lockout used by the
# failure breaker (423 + Retry-After) with an auditable reason.
# ---------------------------------------------------------------------------
BEHAVIOR_WINDOW_SECONDS = env_int("BANK_BEHAVIOR_WINDOW_SECONDS", 600, 60, 3600)
BEHAVIOR_RISK_THRESHOLD = env_int("BANK_BEHAVIOR_RISK_THRESHOLD", 80, 10, 1000)
BEHAVIOR_LARGE_AMOUNT_CENTS = env_int("BANK_BEHAVIOR_LARGE_AMOUNT_CENTS", 200_000, 100, 10_000_000_000)
BEHAVIOR_MAX_SIGNALS = env_int("BANK_BEHAVIOR_MAX_SIGNALS", 200, 20, 5000)

BEHAVIOR_SIGNAL_WEIGHTS: dict[str, int] = {
    "SENSITIVE_SUCCESS": 0,
    # The failure counter above already locks at MAX_FAILURES; this weight only
    # contributes to the "failures then success" combination.
    "AUTH_FAILURE": 15,
    "LARGE_TRANSFER": 30,
    "LARGE_LOCK": 20,
    "WEALTH_PURCHASE": 15,
    "WEALTH_REDEEM": 10,
    "CARD_LIMIT_CHANGE": 15,
    "CARD_STATE_CHANGE": 25,
    "CARD_REPORT_LOST": 30,
    "PASSWORD_CHANGE": 35,
}

BEHAVIOR_RULES: tuple[dict[str, Any], ...] = (
    {
        "rule_id": "LOST_AFTER_LARGE_TRANSFER",
        "description": "大额转账后短时间内挂失卡片（典型盗刷后逃逸特征）",
        "trigger": "CARD_REPORT_LOST",
        "requires": ("LARGE_TRANSFER",),
        "weight": 50,
    },
    {
        "rule_id": "PASSWORD_CHANGE_AFTER_LARGE_TRANSFER",
        "description": "大额转账后短时间内修改密码（账户接管特征）",
        "trigger": "PASSWORD_CHANGE",
        "requires": ("LARGE_TRANSFER",),
        "weight": 50,
    },
    {
        "rule_id": "BURST_LARGE_TRANSFERS",
        "description": "窗口内连续多笔大额转账",
        "trigger": "LARGE_TRANSFER",
        "min_count": 3,
        "weight": 40,
    },
    {
        "rule_id": "BURST_CARD_CHANGES",
        "description": "窗口内连续多次卡片安全状态变更",
        "trigger": "CARD_STATE_CHANGE",
        "min_count": 3,
        "weight": 30,
    },
    {
        "rule_id": "FAILURES_THEN_SUCCESS",
        "description": "多次凭证失败后紧接着一次成功操作（撞库/暴力尝试特征）",
        "trigger": "SENSITIVE_SUCCESS",
        "requires_count": {"AUTH_FAILURE": 3},
        "weight": 40,
    },
)

# Combinations that call for stronger verification rather than a lockout.
# Raising a card limit is itself a RED, MFA-verified action, and the card's own
# "limit exceeded" message tells the user to do exactly that before a larger
# payment.  Scoring "limit change then transfer" towards a lockout therefore
# locked the genuine owner right after following the app's advice.  Instead,
# money leaving the account inside the window is escalated to the RED tier:
# whoever raised the limit must pass OTP and the device signature again.
BEHAVIOR_STEP_UP_RULES: tuple[dict[str, Any], ...] = (
    {
        "rule_id": "TRANSFER_AFTER_LIMIT_CHANGE",
        "description": "调整卡片额度后的窗口内转出资金，升级为强验证",
        "requires": ("CARD_LIMIT_CHANGE",),
    },
)

BEHAVIOR_STATE: dict[str, dict[str, Any]] = defaultdict(lambda: {"timeline": deque(maxlen=BEHAVIOR_MAX_SIGNALS), "rule_fires": {}})


def behavior_window_seconds() -> int:
    """Effective signal window (read per call so it stays configurable)."""

    return env_int("BANK_BEHAVIOR_WINDOW_SECONDS", BEHAVIOR_WINDOW_SECONDS, 60, 3600)


def behavior_risk_threshold() -> int:
    """Effective lockout threshold for the accumulated behaviour score."""

    return env_int("BANK_BEHAVIOR_RISK_THRESHOLD", BEHAVIOR_RISK_THRESHOLD, 10, 1000)


def behavior_large_amount_cents() -> int:
    """Amount above which a settlement counts as a large-transfer signal."""

    return env_int("BANK_BEHAVIOR_LARGE_AMOUNT_CENTS", BEHAVIOR_LARGE_AMOUNT_CENTS, 100, 10_000_000_000)


def _purge_behavior(state: dict[str, Any], now: float) -> None:
    window = behavior_window_seconds()
    timeline: deque = state["timeline"]
    while timeline and now - float(timeline[0].get("at", 0.0)) > window:
        timeline.popleft()
    for rule_id, fired_at in list(state["rule_fires"].items()):
        if now - float(fired_at) > window:
            state["rule_fires"].pop(rule_id, None)


def _behavior_score(state: dict[str, Any]) -> int:
    return int(sum(int(entry.get("weight", 0)) for entry in state["timeline"]))


def _evaluate_behavior_rules(state: dict[str, Any], trigger: str, now: float) -> list[str]:
    """Fire named combination rules against the recent signal timeline."""

    fired: list[str] = []
    timeline: deque = state["timeline"]
    signals = [entry for entry in timeline if entry.get("kind") == "SIGNAL"]
    for rule in BEHAVIOR_RULES:
        if rule["trigger"] != trigger or rule["rule_id"] in state["rule_fires"]:
            continue
        if "min_count" in rule and sum(1 for entry in signals if entry.get("name") == trigger) < int(rule["min_count"]):
            continue
        if not all(any(entry.get("name") == required for entry in signals) for required in rule.get("requires", ())):
            continue
        if not all(
            sum(1 for entry in signals if entry.get("name") == name) >= int(minimum)
            for name, minimum in (rule.get("requires_count") or {}).items()
        ):
            continue
        timeline.append({"at": now, "kind": "RULE", "name": rule["rule_id"], "weight": int(rule["weight"]), "metadata": {"description": rule["description"]}})
        state["rule_fires"][rule["rule_id"]] = now
        fired.append(rule["rule_id"])
    return fired


def record_behavior_signal(user_id: Optional[str], signal: str, *, metadata: Optional[Mapping[str, Any]] = None, request_id: Optional[str] = None) -> dict[str, Any]:
    """Record a behaviour signal, evaluate combination rules, maybe lock."""

    principal = _principal_key(user_id)
    now = time.monotonic()
    weight = int(BEHAVIOR_SIGNAL_WEIGHTS.get(signal, 0))
    locked_now = False
    lock_reason: Optional[str] = None
    with STATE_LOCK:
        state = BEHAVIOR_STATE[principal]
        _purge_behavior(state, now)
        # Several layers can report the same rejected request (the endpoint
        # wrapper and the primitive both record a failure).  Deduplicate per
        # request so one attempt never counts as two signals.
        if request_id and any(
            entry.get("kind") == "SIGNAL" and entry.get("name") == signal and entry.get("request_id") == request_id
            for entry in state["timeline"]
        ):
            return {
                "principal": principal,
                "signal": signal,
                "weight": weight,
                "score": _behavior_score(state),
                "threshold": behavior_risk_threshold(),
                "fired_rules": [],
                "locked": float(SECURITY_STATE[principal].get("locked_until", 0.0)) > now,
                "just_locked": False,
                "duplicate": True,
                "lock_reason": None,
            }
        state["timeline"].append({"at": now, "kind": "SIGNAL", "name": signal, "weight": weight, "request_id": redact(request_id), "metadata": redact(dict(metadata or {}))})
        fired = _evaluate_behavior_rules(state, signal, now)
        score = _behavior_score(state)
        security_state = SECURITY_STATE[principal]
        if score >= behavior_risk_threshold() and float(security_state.get("locked_until", 0.0)) <= now:
            security_state["locked_until"] = now + LOCKOUT_SECONDS
            security_state["lock_reason"] = f"BEHAVIOR_RISK:{','.join(fired) or signal}"
            locked_now = True
            lock_reason = str(security_state["lock_reason"])
            audit_event(
                "BEHAVIOR_RISK_LOCK",
                user_id=principal,
                risk_level="RED",
                decision="BLOCK",
                outcome="LOCKED",
                reason=lock_reason,
                request_id=request_id,
                metadata={
                    "score": score,
                    "threshold": behavior_risk_threshold(),
                    "rules": fired,
                    "signals": [entry.get("name") for entry in state["timeline"] if entry.get("kind") == "SIGNAL"],
                    "lockout_seconds": LOCKOUT_SECONDS,
                },
            )
        elif fired:
            audit_event(
                "BEHAVIOR_RULE_FIRED",
                user_id=principal,
                risk_level="RED",
                decision="ALLOW",
                outcome="OBSERVED",
                reason=",".join(fired),
                request_id=request_id,
                metadata={"score": score, "threshold": behavior_risk_threshold(), "signal": signal},
            )
        elif weight >= 20:
            audit_event(
                "BEHAVIOR_SIGNAL",
                user_id=principal,
                risk_level="YELLOW",
                decision="ALLOW",
                outcome="OBSERVED",
                reason=signal,
                request_id=request_id,
                metadata={"signal": signal, "weight": weight, "score": score},
            )
        active_lock = float(security_state.get("locked_until", 0.0)) > now
        return {
            "principal": principal,
            "signal": signal,
            "weight": weight,
            "score": score,
            "threshold": behavior_risk_threshold(),
            "fired_rules": fired,
            "locked": active_lock,
            "just_locked": locked_now,
            "lock_reason": str(security_state.get("lock_reason") or "") if active_lock else None,
        }


def behavior_status(user_id: Optional[str]) -> dict[str, Any]:
    """Explain the behaviour risk score of one principal (for review/UI)."""

    principal = _principal_key(user_id)
    now = time.monotonic()
    with STATE_LOCK:
        state = BEHAVIOR_STATE.get(principal)
        security_state = SECURITY_STATE[principal]
        locked_until = float(security_state.get("locked_until", 0.0))
        locked = locked_until > now
        base: dict[str, Any] = {
            "principal": principal,
            "score": 0,
            "threshold": behavior_risk_threshold(),
            "window_seconds": behavior_window_seconds(),
            "signals": [],
            "fired_rules": [],
            "locked": locked,
            "lock_reason": security_state.get("lock_reason") if locked else None,
            "remaining_lock_seconds": max(0, int(locked_until - now)) if locked else 0,
        }
        if not state:
            return base
        _purge_behavior(state, now)
        base["score"] = _behavior_score(state)
        base["fired_rules"] = sorted(state["rule_fires"])
        base["signals"] = [
            {
                "name": entry.get("name"),
                "kind": entry.get("kind"),
                "weight": entry.get("weight"),
                "age_seconds": round(max(0.0, now - float(entry.get("at", now))), 1),
                **({"metadata": entry.get("metadata")} if entry.get("kind") == "SIGNAL" else {}),
            }
            for entry in state["timeline"]
        ]
        return base


def behavior_step_up(user_id: Optional[str]) -> Optional[dict[str, Any]]:
    """Return the step-up rule that applies to an outgoing payment, if any.

    Read-only: it never adds a signal or a lock.  Callers raise the payment to
    the RED tier and audit the returned rule id.
    """

    principal = _principal_key(user_id)
    now = time.monotonic()
    with STATE_LOCK:
        state = BEHAVIOR_STATE.get(principal)
        if not state:
            return None
        _purge_behavior(state, now)
        names = {entry.get("name") for entry in state["timeline"] if entry.get("kind") == "SIGNAL"}
    for rule in BEHAVIOR_STEP_UP_RULES:
        if all(required in names for required in rule["requires"]):
            return {"rule_id": rule["rule_id"], "description": rule["description"]}
    return None
