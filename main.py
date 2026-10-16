"""Mock core-banking API with an explicit security boundary.

The ledger is intentionally in memory for a competition demo.  Mutating
operations still require a risk precheck, a signed short-lived one-time
nonce, and (for red operations) a server-validated MFA proof.  Every decision
and outcome is appended to a tamper-evident audit trail.

This module only assembles the service; the code lives in ``bank_core``:
domain state and rules (``ledger``, ``cards``, ``wealth_data``, ``contacts``,
``payments``, ``operations``), cross-cutting guards (``access``,
``idempotency``, ``persistence``, ``http_edge``) and the HTTP routers in
``bank_core.routes``, grouped by risk tier.
"""

from __future__ import annotations

import logging
import os

# Name this process before ``security`` is imported: it selects Core's own
# audit file, separate from the Gateway's chain (see ``audit_log_path``).
os.environ["BANK_AUDIT_SERVICE"] = "core"

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from bank_core import persistence
from bank_core.http_edge import API_VERSION, request_safety_middleware
from bank_core.routes import (
    accounts,
    approvals,
    cards,
    contacts,
    devices,
    execution,
    monitoring,
    sessions,
    subscriptions,
    tasks,
    wealth,
)
from security import SecurityError

# Re-exported: tests and scripts address the live state and a few helpers
# through ``main``. These names are the same objects the routers use.
from bank_core.ledger import (  # noqa: F401
    ACCOUNTS,
    CONTACTS,
    DAILY_TRANSFER_DATE,
    DAILY_TRANSFERS,
    DISPUTE_CASES,
    LINK_UNVERIFIED,
    LOCKS,
    MOCK_ORDERS,
    SCHEDULED_TRANSFERS,
    SPLIT_REQUESTS,
    SUBSCRIPTIONS,
    TRANSACTIONS,
    USER_CONTACTS,
)
from bank_core.cards import (  # noqa: F401
    CARD_DAILY_SPENT,
    CARD_DAILY_SPENT_DATE,
    CARD_MAX_DAILY_LIMIT,
    CARDS,
    MOCK_CARD_VAULT,
)
from bank_core.wealth_data import (  # noqa: F401
    RISK_ATTEMPTS,
    WEALTH_HOLDINGS,
    WEALTH_PRODUCTS,
    WEALTH_RISK_PROFILES,
    wealth_product_view,
)
from bank_core.persistence import (  # noqa: F401
    OPERATION_RECEIPTS,
    _ledger_store_digest,
    ledger_store_status,
    load_ledger_store,
    persist_ledger,
)
from bank_core.idempotency import (  # noqa: F401
    IDEMPOTENCY_RECORDS,
    idempotency_begin,
)
from bank_core.models import (  # noqa: F401
    TaskActionRequest,
)
from bank_core.payments import (  # noqa: F401
    resolve_payee,
)
from bank_core.operations import (  # noqa: F401
    fraud_transaction,
    pending_transfer_amount,
)
from bank_core.routes.tasks import (  # noqa: F401
    _task_action,
)
from bank_core.reset import (  # noqa: F401
    reset_state,
)
from scheduler import (  # noqa: F401
    TASK_STORE,
)
from security import (  # noqa: F401
    AUDIT_LOGS,
    AUTH_SECRET_CONFIGURED,
    AUTH_TOKENS,
    DEVICES,
    MFA_PROOFS,
)

logger = logging.getLogger("mock_banking.core")

app = FastAPI(
    title="Mock Bank Core API",
    description="银行 AI 智能体受控沙箱环境（含分级权限与审计）",
    version=API_VERSION,
)
app.middleware("http")(request_safety_middleware)


@app.exception_handler(SecurityError)
async def security_error_handler(_: Request, exc: SecurityError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"code": exc.code, "message": exc.detail}},
        headers={"Retry-After": "30"} if exc.status_code == 423 else None,
    )


# Inclusion order is route-matching order; it follows the original layout.
for _router in (accounts, contacts, wealth, subscriptions, cards, approvals, sessions, devices, execution, tasks, monitoring):
    app.include_router(_router.router)

__all__ = [
    "app",
    "ACCOUNTS",
    "CONTACTS",
    "USER_CONTACTS",
    "DAILY_TRANSFERS",
    "TRANSACTIONS",
    "DISPUTE_CASES",
    "LOCKS",
    "WEALTH_PRODUCTS",
    "WEALTH_HOLDINGS",
    "WEALTH_RISK_PROFILES",
    "CARDS",
    "SUBSCRIPTIONS",
    "SCHEDULED_TRANSFERS",
    "SPLIT_REQUESTS",
    "AUTH_TOKENS",
    "MFA_PROOFS",
    "AUDIT_LOGS",
    "DEVICES",
    "reset_state",
]


# Opt-in durability: when a path is configured, the committed ledger snapshot is
# replayed at import time so accounts, transactions and reservations survive a
# process restart.  With no path configured the in-memory demo stays
# deterministic and side-effect free.  This runs after ``bank_core.reset`` has
# captured the seed state, which is why it sits at the end of the module.
persistence._LEDGER_STORE_LOAD_SUMMARY = load_ledger_store()
if persistence._LEDGER_STORE_LOAD_SUMMARY.get("rejected"):
    logger.error(
        "账本文件未通过校验，已拒绝加载并冻结写入（将使用种子账本）: %s",
        persistence._LEDGER_STORE_LOAD_SUMMARY.get("error"),
    )
