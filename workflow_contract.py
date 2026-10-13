"""Versioned, credential-free replies shared by all conversation channels."""
from __future__ import annotations

from typing import Any

_SECRETS = {"auth_token", "mfa_proof", "session_token", "otp", "face_attestation", "nonce", "password"}


def public_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: public_data(v) for k, v in value.items() if k.lower() not in _SECRETS}
    if isinstance(value, list):
        return [public_data(v) for v in value]
    return value


def business_status(state: dict) -> str:
    if state.get("business_status"):
        return state["business_status"]
    quiz = state.get("risk_quiz")
    if state.get("clarification_required") or isinstance(quiz, dict) and quiz.get("active") is not False:
        return "NEEDS_INPUT"
    if state.get("risk_level") == "ERROR":
        return "BLOCKED"
    if state.get("execution_result") or state.get("profile_result"):
        return "SUCCEEDED"
    return "FAILED"


def workflow_plan(state: dict) -> dict | None:
    flow = (state.get("context") or {}).get("compound_workflow") or state.get("completed_workflow")
    if not flow:
        return None
    current_status = "WAITING_APPROVAL" if state.get("auth_token") and not state.get("user_confirmed") and not state.get("final_response") else business_status(state)
    return {"id": flow["id"], "current_step": flow["index"] + 1,
            "steps": [{"index": index + 1, "label": item["text"],
                       "intent": item["payload"].get("intent"), "status": current_status if index == flow["index"] else item.get("status", "PENDING")}
                      for index, item in enumerate(flow["steps"])]}


def reply_contract(state: dict, summary: str, status: str) -> dict:
    prior = (state.get("context") or {}).get("compound_workflow") or state.get("completed_workflow") or {}
    results = list(prior.get("results") or [])
    current = state.get("execution_results") or ([state["execution_result"]] if state.get("execution_result") else [])
    for result in current:
        if result not in results:
            results.append(result)
    sections = []
    for result in results:
        sections.append(public_data({"type": result.get("type"),
                                    "status": result.get("business_status", "SUCCEEDED"), "data": result}))
    reconciliation = state.get("reconciliation") or {}
    return {"version": 1, "status": status, "summary": summary, "sections": sections,
            "workflow_plan": workflow_plan(state),
            "operation_id": reconciliation.get("operation_id"),
            "actions": ([{"id": "reconcile", "label": "核对结果", "operation_id": reconciliation["operation_id"]}]
                        if status == "PENDING_RECONCILIATION" and reconciliation else [])}
