"""Deterministic reset of the in-memory sandbox for tests and demos (not an HTTP route)."""

from __future__ import annotations

from copy import deepcopy

from bank_core import persistence
from bank_core.cards import (
    CARD_DAILY_SPENT,
    CARD_DAILY_SPENT_DATE,
    CARD_DISCLOSURE_FAILURES,
    CARDS,
)
from bank_core.http_edge import (
    RATE_LIMIT_STATE,
)
from bank_core.idempotency import (
    IDEMPOTENCY_RECORDS,
)
from bank_core.ledger import (
    ACCOUNTS,
    CONTACTS,
    DAILY_TRANSFER_DATE,
    DAILY_TRANSFERS,
    DISPUTE_CASES,
    LOCKS,
    MOCK_ORDERS,
    SCHEDULED_TRANSFERS,
    SPLIT_REQUESTS,
    SUBSCRIPTIONS,
    TRANSACTIONS,
    USER_CONTACTS,
)
from bank_core.persistence import (
    OPERATION_RECEIPTS,
)
from bank_core.wealth_data import (
    RISK_ATTEMPTS,
    WEALTH_HOLDINGS,
    WEALTH_RISK_PROFILES,
)
from notification_outbox import OUTBOX
from scheduler import TASK_STORE
from security import (
    DEVICES,
    STATE_LOCK,
)

# Test/demo reset helper.  It is intentionally not exposed as an HTTP route;
# production callers must use a transactional rollback workflow instead.
_INITIAL_ACCOUNTS = deepcopy(ACCOUNTS)
_INITIAL_CONTACTS = deepcopy(CONTACTS)
_INITIAL_TRANSACTIONS = deepcopy(TRANSACTIONS)
_INITIAL_WEALTH_HOLDINGS = deepcopy(WEALTH_HOLDINGS)
_INITIAL_WEALTH_RISK_PROFILES = deepcopy(WEALTH_RISK_PROFILES)
_INITIAL_CARDS = deepcopy(CARDS)
_INITIAL_SUBSCRIPTIONS = deepcopy(SUBSCRIPTIONS)
_INITIAL_SCHEDULED_TRANSFERS = deepcopy(SCHEDULED_TRANSFERS)
_INITIAL_SPLIT_REQUESTS = deepcopy(SPLIT_REQUESTS)
_INITIAL_MOCK_ORDERS = deepcopy(MOCK_ORDERS)
_INITIAL_DISPUTE_CASES = deepcopy(DISPUTE_CASES)


def reset_state() -> None:
    """Restore the deterministic in-memory sandbox and clear security state.

    Deliberately does not touch the durable ledger file: the reset helper is a
    test/demo affordance that never goes through the HTTP authorization path,
    so rewriting the audited snapshot from it would let an out-of-band call
    rewrite what a deployment believes was committed.  Point a deployment's
    ``BANK_LEDGER_STORE_PATH`` at a scratch file when testing resets.
    """

    persistence._LEDGER_STORE_BLOCKED = False
    with STATE_LOCK:
        ACCOUNTS.clear()
        ACCOUNTS.update(deepcopy(_INITIAL_ACCOUNTS))
        CONTACTS.clear()
        CONTACTS.update(deepcopy(_INITIAL_CONTACTS))
        USER_CONTACTS.clear()
        TRANSACTIONS[:] = deepcopy(_INITIAL_TRANSACTIONS)
        DAILY_TRANSFERS.clear()
        DAILY_TRANSFERS["user_zhang"] = 0.0
        DAILY_TRANSFER_DATE.clear()
        CARD_DAILY_SPENT.clear()
        CARD_DAILY_SPENT_DATE.clear()
        RATE_LIMIT_STATE.clear()
        LOCKS.clear()
        TASK_STORE.reset()
        OUTBOX.clear()
        IDEMPOTENCY_RECORDS.clear()
        OPERATION_RECEIPTS.clear()
        WEALTH_HOLDINGS.clear()
        WEALTH_HOLDINGS.update(deepcopy(_INITIAL_WEALTH_HOLDINGS))
        WEALTH_RISK_PROFILES.clear()
        WEALTH_RISK_PROFILES.update(deepcopy(_INITIAL_WEALTH_RISK_PROFILES))
        RISK_ATTEMPTS.clear()
        CARDS.clear()
        CARDS.update(deepcopy(_INITIAL_CARDS))
        CARD_DISCLOSURE_FAILURES.clear()
        SUBSCRIPTIONS.clear()
        SUBSCRIPTIONS.update(deepcopy(_INITIAL_SUBSCRIPTIONS))
        SCHEDULED_TRANSFERS[:] = deepcopy(_INITIAL_SCHEDULED_TRANSFERS)
        SPLIT_REQUESTS[:] = deepcopy(_INITIAL_SPLIT_REQUESTS)
        MOCK_ORDERS[:] = deepcopy(_INITIAL_MOCK_ORDERS)
        DISPUTE_CASES[:] = deepcopy(_INITIAL_DISPUTE_CASES)
        try:
            from security import reset_security_state

            reset_security_state()
        except Exception:
            pass
        # Device enrollment is cleared by ``reset_security_state`` above; this is
        # belt-and-braces so a reset never leaves a test (or demo) trusting a
        # stale key even if that call raised.
        DEVICES.clear()
