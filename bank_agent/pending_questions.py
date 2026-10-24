"""Answer read-only side questions without resuming or replacing a paused approval."""
from __future__ import annotations

from typing import Any, Optional, cast

from bank_agent import runtime
from bank_agent.parsing import model_parse
from bank_agent.state import BankingAgentState, empty_state
from bank_agent.understanding import READ_INTENTS, understanding_context, unresolved_transaction_reference
from planner import needs_clarification, validate_intent_payload
from security import audit_event


def answer_pending_question(state: dict[str, Any], text: str, request_id: str) -> Optional[dict[str, Any]]:
    # No checkpoint update, risk precheck, authorization, quiz, or graph resume.
    # The allow-list and HTTP guard are independent of the model's speech-act.
    side = cast(BankingAgentState, {**state, **empty_state(), "user_input": text, "request_id": request_id, "context": {},
                                  "sub_requests": None, "profile_result": None, "risk_quiz": None, "completed_workflow": None})
    with runtime.read_only_calls():
        with understanding_context(side):
            parsed = validate_intent_payload(model_parse(text))
        act = (parsed.get("parse_meta") or {}).get("dialogue_act")
        if act in {"SOCIAL", "UNCLEAR"}:
            kind = (parsed.get("parse_meta") or {}).get("social_kind")
            side["intent"] = "UNKNOWN"
            side["final_response"] = ("这次的问题还不明确，可以说明想查询的记录或要了解的步骤。" if act == "UNCLEAR" else
                                      {"thanks": "不客气。", "goodbye": "好的，有需要可以继续告诉我。"}.get(str(kind), "我在，可以先问业务问题，再决定是否确认原操作。"))
            return dict(side)
        if (parsed.get("parse_meta") or {}).get("dialogue_act") == "UNSUPPORTED":
            side["final_response"] = "这项能力目前没有接入，无法查询或办理。原待确认操作保持不变。"
            return dict(side)
        if parsed["intent"] not in READ_INTENTS or needs_clarification(parsed):
            return None
        side = cast(BankingAgentState, {**side, **parsed})
        if unresolved_transaction_reference(side):
            side["final_response"] = "还不能确定您说的是哪几笔交易。请提供流水号，或先查询账单后指定记录。"
            return dict(side)
        side["risk_level"] = "GREEN"
        try:
            from bank_agent.nodes.executor import _execute_green
            from bank_agent.nodes.responder import responder_node

            side["execution_result"] = _execute_green(side, parsed["intent"])
            side = cast(BankingAgentState, {**side, **responder_node(side)})
        except Exception:
            side["final_response"] = "这次查询暂时没有取得可核验的结果，原待确认操作保持不变。"
            side["business_status"] = "FAILED"
            side["execution_result"] = None
        audit_event("PENDING_INQUIRY", user_id=side.get("user_id"), request_id=request_id, decision="ALLOW", outcome="READ_ONLY",
                    metadata={"intent": parsed["intent"]})
        return dict(side)
