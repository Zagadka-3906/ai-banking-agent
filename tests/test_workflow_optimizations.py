"""Flow latency, partial results, per-operation approval and reconciliation."""
import json
import threading
import time
from types import SimpleNamespace

import pytest
import requests

import agent
import main
from test_agent_gateway import gateway as _gateway_fixture, _headers, _send, _parse_sse
from support import sign_face_attestation
from workflow_contract import reply_contract


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setenv("BANK_AGENT_RATE_LIMIT_PER_MINUTE", "0")
    yield from _gateway_fixture.__wrapped__(monkeypatch)


def approve(client, envelope, decision="approve", stream=False):
    path = f"/api/v1/agent/approvals/{envelope['approval']['approval_id']}" + ("/stream" if stream else "")
    response = client.post(path, json={"decision": decision}, headers=_headers())
    assert response.status_code == 200, response.text
    return _parse_sse(response.text) if stream else response.json()


def test_explicit_request_skips_model_but_ambiguous_request_uses_it(monkeypatch):
    calls = []
    monkeypatch.setenv("BANK_MODEL_FAST_PATH", "1")
    monkeypatch.setattr(agent, "llm", SimpleNamespace(invoke=lambda messages: calls.append(messages) or SimpleNamespace(content='{"intent":"UNKNOWN"}')))
    assert agent._model_parse("给李四转100元")["intent"] == "TRANSFER"
    assert not calls
    assert agent._model_parse("帮我看看情况")["intent"] == "UNKNOWN"
    assert len(calls) == 1


def test_model_timeout_falls_back_without_inventing_missing_slots(monkeypatch):
    release = threading.Event()
    monkeypatch.setenv("BANK_MODEL_TIMEOUT_SECONDS", "0.1")
    monkeypatch.setenv("BANK_MODEL_FAST_PATH", "0")
    def slow(_):
        release.wait(3)
        return SimpleNamespace(content='{"intent":"TRANSFER","amount":999}')
    monkeypatch.setattr(agent, "llm", SimpleNamespace(invoke=slow))
    started = time.monotonic()
    try:
        result = agent._model_parse("给李四转账")
        assert time.monotonic() - started < 1
        assert result["amount"] is None
    finally:
        release.set()


def test_independent_reads_overlap_and_propagate_session(gateway, monkeypatch):
    client, _ = gateway
    barrier = threading.Barrier(2)
    original = agent._http
    tokens = []
    def synchronized(method, path, **kwargs):
        if path.endswith(("/balance", "/transactions")):
            tokens.append(agent._CHANNEL_SESSION.get())
            barrier.wait(timeout=3)
        return original(method, path, **kwargs)
    monkeypatch.setattr(agent, "_http", synchronized)
    result = _send(client, "查一下余额，同时查看本月账单", conversation_id="parallel")
    assert result["business_status"] == "SUCCEEDED"
    assert len(result["response"]["sections"]) == 2
    assert len(tokens) == 2 and all(tokens)
    tasks = result["result"]["plan"]["tasks"]
    assert any(task["task_id"].startswith("1_") and not task["depends_on"] for task in tasks)


@pytest.mark.parametrize("malformed", [False, True])
def test_partial_reads_preserve_only_verified_sections(gateway, monkeypatch, malformed):
    client, _ = gateway
    original = agent._http
    def failed(method, path, **kwargs):
        if path.endswith("/balance"):
            if malformed:
                return SimpleNamespace(status_code=200, json=lambda: {"user_id": "user_li", "total_balance": 123456, "available_balance": 123456, "locked_amount": 0})
            raise requests.Timeout("PRIVATE_BANK_DETAIL")
        return original(method, path, **kwargs)
    monkeypatch.setattr(agent, "_http", failed)
    result = _send(client, "查一下余额，同时查看本月账单", conversation_id="partial")
    assert result["business_status"] == "PARTIAL_SUCCESS"
    assert [section["status"] for section in result["response"]["sections"]] == ["FAILED", "SUCCEEDED"]
    assert "123456" not in json.dumps(result) and "PRIVATE_BANK_DETAIL" not in json.dumps(result)
    assert any(task["status"] == "FAILED" for task in result["result"]["plan"]["tasks"])


def test_compound_two_writes_get_separate_single_use_approvals(gateway):
    client, _ = gateway
    text = "查一下余额，然后给李四转10元，然后给李四转20元"
    first = _send(client, text, conversation_id="three-steps", message_id="original")
    assert first["business_status"] == "WAITING_APPROVAL"
    assert len(first["response"]["sections"]) == 1
    assert first["response"]["workflow_plan"]["current_step"] == 2
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000
    second = approve(client, first)
    assert second["request_id"] == first["request_id"]
    assert second["business_status"] == "WAITING_APPROVAL"
    assert second["approval"]["approval_id"] != first["approval"]["approval_id"]
    assert second["response"]["workflow_plan"]["current_step"] == 3
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14990
    replay = _send(client, text, conversation_id="three-steps", message_id="original")
    assert replay["approval"]["approval_id"] == second["approval"]["approval_id"]
    assert replay["response"]["workflow_plan"]["current_step"] == 3
    done = approve(client, second)
    assert done["business_status"] == "SUCCEEDED"
    assert len(done["response"]["sections"]) == 3
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14970
    assert _send(client, text, conversation_id="three-steps", message_id="original") == done
    repeat = client.post(f"/api/v1/agent/approvals/{first['approval']['approval_id']}", json={"decision": "approve"}, headers=_headers())
    assert repeat.status_code == 409
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14970
    assert "auth_token" not in json.dumps(done) and "mfa_proof" not in json.dumps(done)


@pytest.mark.parametrize("cancel_text", [None, "取消这次操作"])
def test_compound_cancellation_keeps_completed_receipts_and_stops_future_steps(gateway, cancel_text):
    client, _ = gateway
    first = _send(client, "查一下余额，然后给李四转10元，然后给李四转20元", conversation_id="cancel-flow")
    second = approve(client, first)
    cancelled = _send(client, cancel_text, conversation_id="cancel-flow") if cancel_text else approve(client, second, decision="reject")
    assert cancelled["business_status"] == "CANCELLED"
    assert len(cancelled["response"]["sections"]) == 2
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14990
    fresh = _send(client, "查一下余额", conversation_id="cancel-flow")
    assert fresh["business_status"] == "SUCCEEDED"
    assert fresh["response"]["workflow_plan"] is None


def test_compound_slot_answer_and_revision_do_not_repeat_earlier_query(gateway):
    client, _ = gateway
    incomplete = _send(client, "查一下余额，然后给李四转账，然后给李四转20元", conversation_id="clarify-flow")
    assert incomplete["business_status"] == "NEEDS_INPUT"
    assert len(incomplete["response"]["sections"]) == 1
    pending = _send(client, "10元", conversation_id="clarify-flow")
    assert pending["approval"]
    revised = _send(client, "改成15元", conversation_id="clarify-flow")
    assert revised["approval"]["approval_id"] != pending["approval"]["approval_id"]
    assert len(revised["response"]["sections"]) == 1
    second = approve(client, revised)
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14985
    assert second["approval"]


def test_approval_stream_emits_real_execution_and_validation_progress(gateway):
    client, _ = gateway
    pending = _send(client, "给李四转10元", conversation_id="stream-approval")
    frames = approve(client, pending, stream=True)
    assert frames[-1][0] == "result" and frames[-1][1]["business_status"] == "SUCCEEDED"
    started = [data["stage"] for event, data in frames if event == "stage" and data["phase"] == "started"]
    assert started == ["human_review", "executor", "responder"]
    assert all(data["elapsed_ms"] >= 0 for event, data in frames if event == "stage")


@pytest.mark.parametrize("compound", [False, True])
def test_lost_receipt_reconciles_without_resubmitting_and_is_owner_scoped(gateway, monkeypatch, compound):
    client, core = gateway
    text = "查一下余额，然后给李四转10元，然后给李四转20元" if compound else "给李四转10元"
    pending = _send(client, text, conversation_id="lost-receipt", message_id="original")
    original = agent._http
    settlements = []
    def lost(method, path, **kwargs):
        response = original(method, path, **kwargs)
        if path == "transfers/execute":
            settlements.append(kwargs["headers"]["Idempotency-Key"])
            raise requests.ConnectionError("PRIVATE_RECEIPT_ERROR")
        return response
    monkeypatch.setattr(agent, "_http", lost)
    unknown = approve(client, pending)
    assert unknown["business_status"] == "PENDING_RECONCILIATION"
    assert _send(client, text, conversation_id="lost-receipt", message_id="original") == unknown
    assert len(settlements) == 2 and settlements[0] == settlements[1]
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14990
    operation = unknown["response"]["operation_id"]
    foreign = core.get(f"/api/v1/operations/{operation}", params={"endpoint": "transfer_execute"}, headers=_headers("user_li"))
    assert foreign.json()["status"] == "UNKNOWN" and foreign.json()["receipt"] is None
    route = f"/api/v1/agent/conversations/lost-receipt/operations/{operation}"
    assert client.get(route, headers=_headers("user_li")).status_code == 404
    blocked = _send(client, "给李四转999元", conversation_id="lost-receipt")
    assert blocked["business_status"] == "PENDING_RECONCILIATION"
    resolved = client.get(route, headers=_headers())
    assert resolved.status_code == 200, resolved.text
    result = resolved.json()
    assert result["business_status"] == ("WAITING_APPROVAL" if compound else "SUCCEEDED")
    replay = _send(client, text, conversation_id="lost-receipt", message_id="original")
    assert replay["business_status"] == result["business_status"]
    if compound:
        assert replay["approval"]["approval_id"] == result["approval"]["approval_id"]
    else:
        assert replay == result
    assert len(settlements) == 2
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14990


def test_protocol_removes_credentials_recursively():
    state = {"execution_result": {"type": "BALANCE", "data": {"session_token": "secret", "nested": [{"otp": "123456", "available_balance": 10}]}}}
    result = reply_contract(state, "核验结果", "SUCCEEDED")
    assert "secret" not in json.dumps(result) and "123456" not in json.dumps(result)
    assert "available_balance" in json.dumps(result)


def test_failed_compound_step_stops_future_writes_and_new_turn_starts_fresh(gateway):
    client, _ = gateway
    failed = _send(client, "查一下余额，然后给不存在的账户转10元，然后给李四转20元", conversation_id="failed-flow")
    assert failed["business_status"] == "BLOCKED" and failed["approval"] is None
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000
    fresh = _send(client, "给李四转30元", conversation_id="failed-flow")
    assert fresh["response"]["workflow_plan"] is None
    assert approve(client, fresh)["business_status"] == "SUCCEEDED"
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14970


def test_compound_wealth_quiz_resumes_original_step_without_bypassing_approval(gateway):
    client, _ = gateway
    quiz = _send(client, "查一下余额，然后申购CJ01002 500元，然后给李四转20元", conversation_id="quiz-flow")
    assert quiz["business_status"] == "NEEDS_INPUT"
    assert quiz["result"]["risk_quiz"]
    answered = _send(client, "1A 2A 3B 4A 5B 6B 7A 8A", conversation_id="quiz-flow")
    assert answered["business_status"] == "WAITING_APPROVAL"
    assert answered["response"]["workflow_plan"]["current_step"] == 2
    assert len(answered["response"]["sections"]) == 1
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000


def test_compound_remark_does_not_authorize_a_second_transfer(gateway):
    client, _ = gateway
    pending = _send(client, "给李四转10元，备注：然后给王美转20元", conversation_id="remark-data")
    assert pending["response"]["workflow_plan"] is None
    assert approve(client, pending)["business_status"] == "SUCCEEDED"
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14990
    assert main.ACCOUNTS["user_wife"]["balance"] == 5000


def test_eight_step_flow_fits_graph_budget_and_nine_steps_are_not_partially_executed(gateway):
    client, _ = gateway
    pending = _send(client, "然后".join(["查一下余额"] * 7 + ["给李四转10元"]), conversation_id="eight-steps")
    assert pending["approval"]
    assert pending["response"]["workflow_plan"]["current_step"] == 8
    too_many = _send(client, "然后".join(["查一下余额"] * 9), conversation_id="nine-steps")
    assert too_many["business_status"] == "NEEDS_INPUT"
    assert not too_many["response"]["sections"]
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000


def test_second_red_step_requires_a_fresh_device_challenge(gateway):
    client, _ = gateway
    first = _send(client, "给李四转2000元，然后给李四转50元", conversation_id="fresh-mfa")
    challenge = client.post(f"/api/v1/agent/approvals/{first['approval']['approval_id']}/face-challenge", json={}, headers=_headers()).json()
    attestation = sign_face_attestation(challenge["challenge_id"], user_id="user_zhang", token_jti=challenge["auth_token_jti"])
    response = client.post(f"/api/v1/agent/approvals/{first['approval']['approval_id']}",
        json={"decision": "approve", "otp": "888888", "face_attestation": attestation}, headers=_headers())
    assert response.status_code == 200, response.text
    second = response.json()
    assert second["approval"]["mfa_required"] is True
    replay = client.post(f"/api/v1/agent/approvals/{second['approval']['approval_id']}",
        json={"decision": "approve", "otp": "888888", "face_attestation": attestation}, headers=_headers())
    assert replay.status_code >= 400
    assert main.ACCOUNTS["user_zhang"]["balance"] == 13000
