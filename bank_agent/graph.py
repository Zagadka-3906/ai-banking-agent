"""Graph wiring: conditional routing between nodes and ``build_graph``."""

from __future__ import annotations

from typing import Any


from workflow_runtime import instrument
from bank_agent.state import (
    BankingAgentState,
)
from bank_agent.runtime import (
    END,
    MemorySaver,
    StateGraph,
)
from bank_agent.nodes.intent import (
    intent_parser_node,
    workflow_next_node,
)
from bank_agent.nodes.risk import (
    human_review_node,
    risk_checker_node,
)
from bank_agent.nodes.responder import (
    responder_node,
)
from bank_agent.nodes.executor import (
    executor_node,
)


def route_after_risk_check(state: BankingAgentState) -> str:
    if state.get("final_response") or state.get("clarification_required"):
        return END
    if state.get("risk_level") in {"YELLOW", "RED"}:
        return "human_review"
    if state.get("risk_level") == "GREEN":
        return "executor"
    return END


def route_after_human_review(state: BankingAgentState) -> str:
    return END if state.get("final_response") or not state.get("user_confirmed") else "executor"


def route_after_workflow_next(state: BankingAgentState) -> str:
    return END if state.get("final_response") else "risk_checker"


def build_graph(checkpointer: Any = None) -> Any:
    """Compile the banking agent graph against an explicit checkpointer.

    ``agent.app`` - the module-level graph used by unit tests and local
    evaluators - keeps the in-process :class:`MemorySaver`.  A channel service
    that must survive a restart, or that has to answer an approval arriving on
    a *later* HTTP request (possibly served by another worker), injects a
    durable checkpointer instead.  See ``conversation.py``.

    Returns ``None`` when LangGraph is not installed, matching the historical
    behaviour of ``agent.app`` so a minimal installation still imports.
    """

    if StateGraph is None:  # pragma: no cover - dependency guidance path
        return None
    workflow = StateGraph(BankingAgentState)
    workflow.add_node("intent_parser", instrument("intent_parser", intent_parser_node))
    workflow.add_node("risk_checker", instrument("risk_checker", risk_checker_node))
    workflow.add_node("human_review", instrument("human_review", human_review_node))
    workflow.add_node("executor", instrument("executor", executor_node))
    workflow.add_node("responder", instrument("responder", responder_node))
    workflow.add_node("workflow_next", instrument("workflow_next", workflow_next_node))
    workflow.set_entry_point("intent_parser")
    workflow.add_edge("intent_parser", "risk_checker")
    workflow.add_conditional_edges("risk_checker", route_after_risk_check)
    workflow.add_conditional_edges("human_review", route_after_human_review)
    workflow.add_edge("executor", "responder")
    workflow.add_conditional_edges("responder", lambda state: "workflow_next" if (state.get("context") or {}).get("compound_workflow") else END)
    workflow.add_conditional_edges("workflow_next", route_after_workflow_next)
    return workflow.compile(checkpointer=checkpointer if checkpointer is not None else MemorySaver())
