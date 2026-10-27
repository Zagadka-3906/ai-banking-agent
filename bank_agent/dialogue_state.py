"""Principal-scoped discussion state, separate from immutable execution approvals."""
from __future__ import annotations

from typing import Any, Mapping


def dialogue_view(state: Mapping[str, Any]) -> dict[str, Any]:
    value = state.get("dialogue_state")
    if not isinstance(value, dict) or value.get("principal") != state.get("user_id"):
        return {"version": 1, "principal": state.get("user_id"), "active_topic": "general", "tasks": []}
    return {**value, "tasks": list(value.get("tasks") or [])[-4:]}


def remember_discussion(state: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, Any]:
    from bank_agent.understanding import _safe_text, help_topic

    view = dialogue_view(state)
    context = state.get("context") or {}
    pending = context.get("pending_clarification")
    tasks = list(view["tasks"])
    intent = state.get("intent")
    if isinstance(pending, dict):
        tasks = [task for task in tasks if task.get("intent") != pending.get("intent")]
        payload = {key: value for key, value in (pending.get("payload") or {}).items()
                   if key in {"intent", "target", "amount", "date_scope", "date_range", "participants", "product_ids", "slots", "scheduled_at", "event_at"}}
        tasks.append({"id": str(pending.get("intent")), "intent": pending.get("intent"), "pending": {**pending, "payload": payload}})
    if state.get("business_status") in {"SUCCEEDED", "CANCELLED"} or response.get("business_status") in {"SUCCEEDED", "CANCELLED"}:
        tasks = [task for task in tasks if task.get("intent") != intent]
    topic = help_topic(str(state.get("user_input") or ""))
    selected = (state.get("slots") or {}).get("help_topic")
    if (topic == "general" or intent == "BANKING_HELP") and selected:
        topic = selected
    from bank_planning.requests import request_goals
    goal_topics = {goal['topic'] for goal in request_goals(state) if goal['act'] != 'UNSUPPORTED'}
    if intent == 'BANKING_HELP' and len(goal_topics) == 1:
        topic = next(iter(goal_topics))
    relation = (state.get("parse_meta") or {}).get("topic_relation")
    if topic == "general" and relation == "CONTINUE":
        topic = view.get("active_topic", "general")
    result = {**view, "active_topic": topic, "question": _safe_text(state.get("user_input"), 500), "tasks": tasks[-4:]}
    # A finite-limit explanation can ask for a value without pretending that
    # a write was requested. This typed, principal-bound input expires on the
    # next topic/turn; the value itself must be stated anew, then re-approved.
    execution = state.get("execution_result") or {}
    result["next_input"] = ({"intent": "CARD_LIMIT", "field": "amount"}
                            if intent == "BANKING_HELP" and topic == "limits"
                            and execution.get("type") == "BANKING_HELP" and (execution.get("data") or {}).get("documents")
                            else None)
    if result["next_input"]:
        import re
        from intent_language import business_text

        text = business_text(str(state.get("user_input") or ""))
        cards = set(re.findall(r"(?i)(?<![A-Za-z0-9])CARD-[0-9a-z]{2,}(?![A-Za-z0-9])", text))
        if len(cards) == 1:
            # This is the user's literal selector, not proof of ownership.
            # Core validates it again before issuing the new approval.
            result["next_input"]["target"] = next(iter(cards)).upper()
        elif len(cards) > 1 or re.search(r"尾号|末四位|后四位|那张|这张", text):
            # A bare amount must not silently choose a different primary card.
            result["next_input"] = None
    return result


def resume_task(state: Mapping[str, Any], parsed: Mapping[str, Any], text: str) -> dict[str, Any] | None:
    import re

    # The model may identify a known task, never manufacture its stored slots.
    # Resuming a discussion is not resuming/approving a transaction nonce.
    task_id = (parsed.get("parse_meta") or {}).get("resume_task_id")
    if not task_id or not re.search(r"继续|接着|恢复|刚才|之前|上次", text):
        return None
    for task in dialogue_view(state)["tasks"]:
        if task.get("id") == task_id and isinstance(task.get("pending"), dict):
            return dict(task["pending"])
    return None
