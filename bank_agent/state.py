"""The LangGraph state schema, its empty value and tolerant JSON extraction from model output."""

from __future__ import annotations

import json
import re
from typing import Any, Optional, TypedDict


from planner import (
    Intent,
)


class BankingAgentState(TypedDict, total=False):
    dialogue_state: dict[str, Any]
    memory_principal: Optional[str]
    dialogue_history: list[dict[str, str]]
    observations: dict[str, dict[str, Any]]
    pending_operation: Optional[dict[str, Any]]
    answer_presentation: Optional[dict[str, Any]]
    user_id: str
    user_input: str
    intent: Optional[str]
    target: Optional[str]
    amount: Optional[float]
    date_scope: Optional[str]
    extra_info: Optional[str]
    risk_level: Optional[str]
    auth_token: Optional[str]
    mfa_proof: Optional[str]
    user_confirmed: bool
    request_id: Optional[str]
    action_requirements: list[str]
    execution_result: Optional[dict[str, Any]]
    execution_results: Optional[list[dict[str, Any]]]
    chart_data: Optional[list[dict[str, Any]]]
    todo_card: Optional[dict[str, Any]]
    final_response: Optional[str]
    plan: Optional[dict[str, Any]]
    clarification_required: bool
    context: Optional[dict[str, Any]]
    # 顶层问卷载荷（题目结构），渠道信封据此打开逐题作答组件；完成后为 None。
    risk_quiz: Optional[dict[str, Any]]
    # 测评完成后的档案结果（类型/特点/匹配级别）与快捷输入建议，随信封下发。
    profile_result: Optional[dict[str, Any]]
    suggestions: Optional[list[str]]
    participants: Optional[list[str]]
    product_ids: Optional[list[str]]
    slots: Optional[dict[str, Any]]
    risk_profile: Optional[str]
    scheduled_at: Optional[str]
    event_at: Optional[str]
    reminder_at: Optional[str]
    sub_requests: Optional[list[dict[str, Any]]]
    transaction: Optional[dict[str, Any]]
    risk_reasons: Optional[list[str]]
    business_status: Optional[str]
    reconciliation: Optional[dict[str, Any]]
    parallel_reads: bool
    completed_workflow: Optional[dict[str, Any]]
    continue_workflow: bool
    date_range: Optional[dict[str, str]]
    parse_meta: Optional[dict[str, Any]]

def empty_state(*, final_response: Optional[str] = None) -> dict[str, Any]:
    return {
        "intent": Intent.UNKNOWN.value,
        "target": None,
        "amount": None,
        "date_scope": "ALL",
        "date_range": None,
        "parse_meta": None,
        "extra_info": None,
        "answer_presentation": None,
        "risk_level": None,
        "auth_token": None,
        "mfa_proof": None,
        "user_confirmed": False,
        "request_id": None,
        "action_requirements": [],
        "execution_result": None,
        "execution_results": None,
        "business_status": "NEEDS_INPUT" if final_response else None,
        "reconciliation": None,
        "chart_data": None,
        "todo_card": None,
        "final_response": final_response,
        "plan": None,
        "clarification_required": bool(final_response),
        "participants": [],
        "product_ids": [],
        "slots": {},
        "risk_profile": None,
        "scheduled_at": None,
        "event_at": None,
        "reminder_at": None,
    }


def extract_json(text: str) -> dict[str, Any]:
    """Parse one JSON object from a model response without greedy regexes."""

    if not isinstance(text, str) or len(text) > 10000:
        raise ValueError("model output is too large")
    candidate = text.strip()
    candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.IGNORECASE).strip()
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        if start < 0:
            raise ValueError("model output is not JSON")
        value, _ = decoder.raw_decode(candidate[start:])
    if not isinstance(value, dict):
        raise ValueError("model output must be an object")
    return value
