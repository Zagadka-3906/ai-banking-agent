# ruff: noqa: F811  # imported pytest fixtures are injected by parameter name
"""Fact-consistency audit: rejected or inconsistent receipts cannot claim success."""

import os
import json
from copy import deepcopy

os.environ["BANK_CONVERSATION_DB"] = "memory"

import httpx
import pytest

pytest.importorskip("langgraph")
import agent
import main
from test_agent_gateway import gateway, _headers, _send  # noqa: F401
from bank_agent import runtime


def receipt(kind, payload, **state):
    return {"user_id": "user_zhang", **state, "execution_result": {
        "type": kind, "data": {"code": 200, "status": "SUCCESS", "data": payload},
    }}


@pytest.mark.parametrize("state", [
    {"user_id": "user_zhang", "execution_result": {"data": {"status": "SUCCESS"}}, "plan": {"tasks": [{"status": "SUCCEEDED"}]}},
    receipt("UNSUPPORTED_MERCHANT_PURCHASE", {"status": "SUCCESS"}),
    receipt("FRAUD_DISPUTE", {"status": "CARD_PROTECTED", "card": {"card_id": "CARD-001", "status": "FROZEN"}, "dispute_case": None, "transaction": None}),
    receipt("CARD_FREEZE", {"status": "FROZEN", "card": {"card_id": "CARD-001", "status": "ACTIVE"}}, target="CARD-001"),
    receipt("CARD_FREEZE", {"status": "FROZEN", "card": {"card_id": "CARD-002", "status": "FROZEN"}}, target="CARD-001"),
    receipt("CARD_LIMIT", {"status": "LIMIT_UPDATED", "card": {"card_id": "CARD-001", "status": "ACTIVE", "daily_limit": 9000}}, target="CARD-001", amount=1000),
    receipt("SUBSCRIPTION_CANCEL", {"status": "CANCELLED", "subscription": {"subscription_id": "SUB-APPLE", "merchant": "Apple", "status": "ACTIVE"}}, target="Apple"),
    receipt("SUBSCRIPTION_CANCEL", {"status": "CANCELLED", "subscription": {"subscription_id": "SUB-VIDEO", "merchant": "视频会员", "status": "CANCELLED"}}, target="Apple"),
    receipt("WEALTH_PURCHASE", {"status": "PURCHASED", "product": {"product_id": "HQ30060", "name": "Other product"}, "holding": {"holding_id": "H1", "product_id": "HQ30060", "principal": 1000}}, target="LX00118", amount=1000),
    receipt("WEALTH_REDEEM", {"status": "REDEEMED", "product": {"product_id": "HQ30060"}, "redeemed_amount": 1000}, target="LX00118", amount=1000),
    receipt("SCHEDULED_TRANSFER", {"status": "SCHEDULED", "task_id": "TSK1", "schedule_id": "SCH1", "scheduled_at": "2099-10-03T01:00:00+00:00", "target": "李四", "amount": 50}, target="李四", amount=50, scheduled_at="2099-10-04T09:00:00+08:00"),
    receipt("SPLIT_BILL", {"status": "SETTLED", "task_id": "TSK1", "split_id": "AA1", "participants": ["李四", "王美"], "amount": 300, "per_person": 100, "initiator_share": 100, "collected_amount": 500, "outstanding_amount": 0}, amount=300, participants=["李四", "王美"]),
])
def test_inconsistent_or_unsupported_receipt_does_not_claim_completion(state):
    result = agent.responder_node(deepcopy(state))
    assert "没有声称" in result["final_response"], result
    assert "execution_result" in result and result["execution_result"] is None
    assert "plan" in result and result["plan"] is None


def test_transfer_receipt_cannot_substitute_a_different_recipient():
    result = agent.responder_node({
        "user_id": "user_zhang", "intent": "TRANSFER", "target": "李四", "amount": 50,
        "execution_result": {"type": "TRANSFER_SUCCESS", "data": {
            "code": 200, "status": "SUCCESS", "transaction_id": "TX1", "receiver_name": "王美",
            "transferred_amount": 50, "sender_balance": 14950,
        }},
    })
    assert "没有声称" in result["final_response"], result


@pytest.mark.parametrize("execution", [
    {"type": "BALANCE", "data": {"code": 503, "total_balance": 100, "locked_amount": 0, "available_balance": 100}},
    {"type": "TRANSFER_SUCCESS", "data": {"code": 403, "status": "SUCCESS", "transaction_id": "TX1", "receiver_name": "李四", "transferred_amount": 10, "sender_balance": 90}},
    {"type": "CARD_QUERY", "data": {"code": 500, "data": [{"card_id": "CARD-001", "status": "ACTIVE", "type": "借记卡", "daily_limit": 5000}]}},
])
def test_error_code_cannot_be_overridden_by_success_looking_fields(execution):
    result = agent.responder_node({"user_id": "user_zhang", "amount": 10, "execution_result": execution})
    assert "没有声称" in result["final_response"], result


def test_gateway_validation_failure_does_not_leave_a_false_result_card(gateway, monkeypatch):
    import agent_api

    client, core = gateway
    monkeypatch.setattr(runtime, "llm", None)
    monkeypatch.setattr(agent_api, "RATE_LIMIT_STATE", {})
    core_http = runtime._http

    def inconsistent_balance(method, path, **kwargs):
        response = core_http(method, path, **kwargs)
        if path.endswith("/balance"):
            payload = response.json()
            payload["code"] = 503
            payload["untrusted_note"] = "SENSITIVE_RECEIPT_MARKER"
            return httpx.Response(200, json=payload)
        return response

    monkeypatch.setattr(runtime, "_http", inconsistent_balance)
    response = _send(client, "查余额", conversation_id="rejected-receipt-card")
    assert "没有声称" in response["reply"], response
    assert not response.get("result"), "Unverified receipt/plan must not remain visible in result cards"
    request_id = response["request_id"]
    assert request_id
    trace = core.get(f"/api/v1/security/audit/trace/{request_id}", headers=_headers())
    assert trace.status_code == 200, trace.text
    rejected = [event for event in trace.json()["events"] if event["action"] == "RESULT_REJECTED"]
    assert len(rejected) == 1
    event = rejected[0]
    assert event["request_id"] == request_id and event["user_id"] == "user_zhang"
    assert event["decision"] == "BLOCK" and event["outcome"] == "UNVERIFIED_RESULT"
    assert event["metadata"] == {"intent": "BALANCE_QUERY", "error_type": "CORE_ENVELOPE_REJECTED"}
    assert "SENSITIVE_RECEIPT_MARKER" not in json.dumps(event)
    assert "total_balance" not in json.dumps(event)


def test_gateway_aa_does_not_claim_collection_before_each_payer_confirms(gateway, monkeypatch):
    import agent_api

    client, _ = gateway
    monkeypatch.setattr(runtime, "llm", None)
    monkeypatch.setattr(agent_api, "RATE_LIMIT_STATE", {})
    main.ACCOUNTS["user_wife"]["balance"] = 0
    first = _send(client, "和李四、王美 AA 分摊 300 元", conversation_id="partial-aa-facts")
    assert first["status"] == "APPROVAL_REQUIRED", first
    response = client.post(
        f"/api/v1/agent/approvals/{first['approval']['approval_id']}",
        json={"decision": "approve"}, headers=_headers(),
    )
    assert response.status_code == 200, response.text
    result = response.json()
    data = result["result"]["execution_result"]["data"]["data"]
    assert data["status"] == "PENDING"
    assert data["collected_amount"] == 0 and data["outstanding_amount"] == 200
    assert "当前尚未收款" in result["reply"]
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000


@pytest.mark.parametrize("payee", ["李四", "13900139000", "老李"])
@pytest.mark.parametrize("wrong_receipt", [False, True])
def test_gateway_transfer_receipt_matches_core_resolved_payee(gateway, monkeypatch, payee, wrong_receipt):
    """Real approval/settlement with only the returned receipt adversarially changed."""
    import agent_api

    client, core = gateway
    monkeypatch.setattr(runtime, "llm", None)
    monkeypatch.setattr(agent_api, "RATE_LIMIT_STATE", {})
    if payee == "老李":
        added = core.post(
            "/api/v1/contacts/user_zhang/book",
            json={"name": "老李", "phone": "13900139000", "relationship": "同事"},
            headers=_headers(),
        )
        assert added.status_code == 200, added.text
    core_http = runtime._http

    def altered_receipt(method, path, **kwargs):
        response = core_http(method, path, **kwargs)
        if wrong_receipt and path == "transfers/execute" and response.status_code == 200:
            payload = response.json()
            payload["receiver_name"] = "王美"
            return httpx.Response(200, json=payload)
        return response

    monkeypatch.setattr(runtime, "_http", altered_receipt)
    first = _send(client, f"给{payee}转50元", conversation_id="receipt-payee-binding")
    assert first["status"] == "APPROVAL_REQUIRED", first
    approved = client.post(
        f"/api/v1/agent/approvals/{first['approval']['approval_id']}",
        json={"decision": "approve"}, headers=_headers(),
    )
    assert approved.status_code == 200, approved.text
    result = approved.json()
    # The actual core settlement is unchanged; reporting must match its payee.
    assert main.ACCOUNTS["user_li"]["balance"] == 2050
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14950
    if wrong_receipt:
        assert "没有声称" in result["reply"], result
        assert not result.get("result"), result
        trace = core.get(f"/api/v1/security/audit/trace/{result['request_id']}", headers=_headers())
        events = [event for event in trace.json()["events"] if event["action"] == "RESULT_REJECTED"]
        assert len(events) == 1
        assert events[0]["reason"] == "TRANSFER_RECIPIENT_MISMATCH"
    else:
        assert "转账结算成功" in result["reply"] and "收款人 李四" in result["reply"], result
        assert result["result"]["execution_result"]["data"]["receiver_name"] == "李四"


def test_stale_canonical_payee_cannot_validate_a_later_turn():
    result = agent.responder_node({
        "user_id": "user_zhang", "request_id": "new-request", "target": "李四", "amount": 50,
        "context": {"transfer_receipt_payee": {
            "request_id": "previous-request", "requested_target": "李四",
            "target_id": "user_wife", "target_name": "王美",
        }},
        "execution_result": {"type": "TRANSFER_SUCCESS", "data": {
            "code": 200, "status": "SUCCESS", "transaction_id": "TX1", "receiver_name": "王美",
            "transferred_amount": 50, "sender_balance": 14950,
        }},
    })
    assert "没有声称" in result["final_response"]
    assert result["execution_result"] is None


def test_real_dispute_receipt_confirms_only_existing_case(gateway):
    from test_fraud_dispute import authorize, h

    _, core = gateway
    token, proof = authorize(core)
    executed = core.post(
        "/api/v1/operations/execute",
        json={"user_id": "user_zhang", "operation": "FRAUD_PROTECT", "transaction_id": "TX1004",
              "auth_token": token, "mfa_proof": proof, "user_confirmed": True},
        headers={**h(token), "Idempotency-Key": "dispute-receipt-facts"},
    )
    assert executed.status_code == 200, executed.text
    state = {
        "user_id": "user_zhang", "target": "CARD-001", "transaction": {"tx_id": "TX1004"},
        "execution_result": {"type": "FRAUD_DISPUTE", "data": executed.json()},
    }
    assert "创建争议工单" in agent.responder_node(state)["final_response"]
    state["execution_result"]["data"]["data"]["dispute_case"]["transaction_id"] = "ANOTHER-TX"
    rejected = agent.responder_node(state)
    assert "没有声称" in rejected["final_response"]
    assert rejected["execution_result"] is None
