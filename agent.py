"""LangGraph orchestration for the AI Banking Agent.

Design rule: the model may extract an intent, but it never chooses an
arbitrary tool and never writes ledger state.  ``planner.py`` validates the
model result and the FastAPI core performs the final authorization check.
When no model key is configured, a conservative rule parser keeps the demo
usable offline; ambiguous or incomplete requests are sent to clarification.

This module builds the default graph; the code lives in ``bank_agent``:
``runtime`` (config, model client, Core transport), text ``extraction``,
``parsing`` and multi-turn ``dialogue``, ``settlement`` bookkeeping, Core
response ``verification`` and ``rendering``, the five graph ``nodes`` and the
``graph`` wiring. To replace the Core transport or the model in tests, patch
``bank_agent.runtime._http`` / ``bank_agent.runtime.llm``.
"""

from __future__ import annotations

import requests  # noqa: F401  (re-exported: tests raise ``agent.requests`` errors)

from planner import build_task_plan  # noqa: F401

# Re-exported: other Gateway modules, tests and scripts use these through ``agent``.
from bank_agent.state import (  # noqa: F401
    BankingAgentState,
    extract_json,
)
from bank_agent.runtime import (  # noqa: F401
    INJECTION_LOCKED_NOTICE,
    _CHANNEL_SESSION,
    channel_session,
    report_security_signal,
)
from bank_agent.extraction import (  # noqa: F401
    _amount_from_text,
    _target_from_text,
)
from bank_agent.parsing import (  # noqa: F401
    BILL_CATEGORY_ALIASES,
    _local_parse,
    _model_parse,
)
from bank_agent.settlement import (  # noqa: F401
    _advance_plan,
)
from bank_agent.dialogue import (  # noqa: F401
    _resolve_ellipsis,
)
from bank_agent.nodes.intent import (  # noqa: F401
    intent_parser_node,
)
from bank_agent.nodes.risk import (  # noqa: F401
    human_review_node,
    risk_checker_node,
)
from bank_agent.nodes.responder import (  # noqa: F401
    responder_node,
)
from bank_agent.nodes.executor import (  # noqa: F401
    executor_node,
)
from bank_agent.graph import (  # noqa: F401
    build_graph,
)

app = build_graph()

__all__ = [
    "app",
    "build_graph",
    "channel_session",
    "BankingAgentState",
    "extract_json",
    "intent_parser_node",
    "risk_checker_node",
    "human_review_node",
    "executor_node",
    "responder_node",
    "build_task_plan",
]
