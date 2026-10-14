"""The isolated emulator runner must hold actual replies without crossing users."""
import asyncio
from copy import deepcopy
import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from scripts.scenario_backend import (
    ScenarioResponseControls,
    ScenarioResponseMiddleware,
    install_receipt_controls,
    install_response_controls,
)


STAGE = b'event: stage\ndata: {"stage":"parse","label":"parse","phase":"started"}\n\n'
RESULT = b'event: result\ndata: {"reply":"ACCOUNT_A_ONLY"}\n\n'
STREAM_PATH = "/api/v1/agent/messages/stream"


def run_request(controls, *, principal="user_zhang", stream=True):
    sent = []

    async def real_app(scope, _receive, send):
        # Match only after the normal application has authenticated the caller.
        scope["state"] = {"principal": principal}
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream" if stream else b"application/json")]})
        for body in ([STAGE, RESULT] if stream else [b'{"tasks":[]}']):
            await send({"type": "http.response.body", "body": body, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b""}

    scope = {"type": "http", "method": "POST" if stream else "GET",
             "path": STREAM_PATH if stream else "/api/v1/tasks",
             "headers": [(b"x-user-id", principal.encode()), (b"x-session-token", b"DO_NOT_RECORD")]}
    return asyncio.create_task(ScenarioResponseMiddleware(real_app, controls)(scope, receive, send)), sent


async def wait_arrived(controls):
    for _ in range(100):
        if controls.holds["old"]["arrived"]:
            return
        await asyncio.sleep(0)
    pytest.fail("Response never reached the barrier")


@pytest.mark.parametrize("mode", ["success", "sse_401", "sse_500"])
def test_sse_sends_real_stage_then_holds_old_account_only(mode):
    async def exercise():
        controls = ScenarioResponseControls()
        controls.arm({"id": "old", "method": "POST", "path": STREAM_PATH,
                      "principal": "user_zhang", "mode": mode, "late_stage": "OLD_STAGE"})
        old_request, old_sent = run_request(controls)
        await wait_arrived(controls)
        assert not old_request.done()
        assert old_sent[0]["status"] == 200
        assert b"".join(part.get("body", b"") for part in old_sent) == STAGE
        assert controls.holds["old"]["frames_sent"] == 1
        new_request, new_sent = run_request(controls, principal="user_li")
        await new_request
        assert RESULT in b"".join(part.get("body", b"") for part in new_sent)
        assert not old_request.done()
        controls.release("old")
        await old_request
        old_body = b"".join(part.get("body", b"") for part in old_sent)
        assert b"OLD_STAGE" in old_body
        if mode == "success":
            assert RESULT in old_body
        else:
            assert RESULT not in old_body
            assert f'"http_status": {mode[-3:]}'.encode() in old_body
            assert b"event: error" in old_body
        assert controls.holds["old"]["completed"]
        assert controls.holds["old"]["late_stage_sent"]
        assert not controls.holds["old"]["timed_out"]
        assert "DO_NOT_RECORD" not in json.dumps(controls.requests)
        assert [record["principal"] for record in controls.requests] == ["user_zhang", "user_li"]
        # A hold applies once, even for another request from the same account.
        repeated, _ = run_request(controls)
        await asyncio.wait_for(repeated, timeout=1)

    asyncio.run(exercise())


@pytest.mark.parametrize("mode, expected", [("success", 200), ("http_401", 401), ("http_500", 500)])
def test_http_response_waits_until_released(mode, expected):
    async def exercise():
        controls = ScenarioResponseControls()
        controls.arm({"id": "old", "path": "/api/v1/tasks", "principal": "user_zhang", "mode": mode})
        request, sent = run_request(controls, stream=False)
        await wait_arrived(controls)
        assert not sent
        assert controls.holds["old"]["original_status"] == 200
        controls.release("old")
        await request
        assert sent[0]["status"] == expected
        body = json.loads(sent[1]["body"])
        if expected == 200:
            assert body == {"tasks": []}
        else:
            assert body["detail"]["http_status"] == expected
        assert controls.holds["old"]["completed"]

    asyncio.run(exercise())


def test_reset_releases_pending_requests():
    async def exercise():
        controls = ScenarioResponseControls()
        controls.arm({"id": "old", "path": "/api/v1/tasks", "principal": "user_zhang"})
        request, sent = run_request(controls, stream=False)
        await wait_arrived(controls)
        controls.reset()
        await asyncio.wait_for(request, timeout=1)
        assert sent[0]["status"] == 200
        assert not controls.holds and not controls.requests

    asyncio.run(exercise())


def test_sse_error_gate_reports_non_stream_response_instead_of_hanging():
    async def exercise():
        controls = ScenarioResponseControls()
        controls.arm({"id": "old", "path": "/api/v1/tasks", "principal": "user_zhang", "mode": "sse_401"})
        request, sent = run_request(controls, stream=False)
        await asyncio.wait_for(request, timeout=1)
        assert controls.holds["old"]["failure"] == "expected_event_stream_response"
        assert controls.holds["old"]["completed"]
        assert sent[0]["status"] == 200

    asyncio.run(exercise())


def test_control_routes_require_token_and_hide_runtime_events():
    app = FastAPI()

    def authorize(token):
        if token != "test-control-token":
            raise HTTPException(403, "Scenario control token required")

    controls = install_response_controls(app, authorize)
    client = TestClient(app)
    for method, path in [("POST", "hold"), ("POST", "release"), ("GET", "holds"), ("GET", "requests")]:
        assert client.request(method, f"/__scenario/{path}", json={}).status_code == 403
    assert not controls.holds
    headers = {"X-Scenario-Token": "test-control-token"}
    payload = {"id": "old", "path": "/api/v1/tasks", "principal": "user_zhang"}
    assert client.post("/__scenario/hold", json=payload, headers=headers).status_code == 200
    assert client.post("/__scenario/hold", json=payload, headers=headers).status_code == 400
    rows = client.get("/__scenario/holds", headers=headers).json()["holds"]
    assert len(rows) == 1 and "event" not in rows[0]
    assert client.post("/__scenario/release", json={"id": "missing"}, headers=headers).status_code == 404
    assert client.post("/__scenario/release", json={"id": "old"}, headers=headers).json()["released"] is True
    assert client.post("/__scenario/hold", json={**payload, "id": "bad", "path": "/__scenario/reset"}, headers=headers).status_code == 400


@pytest.fixture
def receipt_gateway(monkeypatch):
    from test_agent_gateway import gateway
    yield from gateway.__wrapped__(monkeypatch)


@pytest.fixture
def receipt_controls():
    app = FastAPI()

    def authorize(token):
        if token != "test-control-token":
            raise HTTPException(403, "Scenario control token required")

    install_receipt_controls(app, authorize)
    return TestClient(app)


def completed_transfer(gateway, conversation_id="retained-receipt"):
    from test_agent_gateway import _headers, _send
    client, _ = gateway
    pending = _send(client, "给李四转10元", conversation_id=conversation_id)
    response = client.post(f"/api/v1/agent/approvals/{pending['approval']['approval_id']}",
                           json={"decision": "approve"}, headers=_headers())
    assert response.status_code == 200, response.text
    envelope = response.json()
    assert envelope["business_status"] == "SUCCEEDED"
    operation_id = next(task["idempotency_key"] for task in envelope["result"]["plan"]["tasks"]
                        if task["action"] == "TRANSFER")
    return {"principal": "user_zhang", "conversation_id": conversation_id, "operation_id": operation_id}, envelope


def graph_snapshot(target):
    import conversation
    thread_id = conversation.conversation_thread_id(target["principal"], target["conversation_id"])
    return conversation.get_graph().get_state(conversation.SERVICE._config(thread_id))


def test_retained_receipt_reads_real_completed_operation_without_ledger_mutation(receipt_gateway, receipt_controls):
    import main
    from test_agent_gateway import _headers
    gateway, core = receipt_gateway
    target, envelope = completed_transfer(receipt_gateway)
    operation_id = target["operation_id"]
    transaction_id = envelope["result"]["execution_result"]["data"]["transaction_id"]
    core_receipt = core.get(f"/api/v1/operations/{operation_id}", params={"endpoint": "transfer_execute"}, headers=_headers()).json()
    assert core_receipt["status"] == "COMPLETED"
    assert core_receipt["receipt"]["transaction_id"] == transaction_id
    before = graph_snapshot(target)
    before_values = deepcopy(before.values)
    accounts, transactions = deepcopy(main.ACCOUNTS), deepcopy(main.TRANSACTIONS)
    assert not before_values.get("reconciliation")
    headers = {"X-Scenario-Token": "test-control-token"}
    for _ in range(2):
        retained = receipt_controls.post("/__scenario/retain-receipt", json=target, headers=headers)
        assert retained.status_code == 200, retained.text
        assert retained.json() == {"operation_id": operation_id}
    after = graph_snapshot(target)
    assert after.values == {**before_values, "reconciliation": {
        "operation_id": operation_id, "endpoint": "transfer_execute", "result_type": "TRANSFER_SUCCESS",
    }}
    assert after.next == before.next == ()
    resolved = gateway.get(f"/api/v1/agent/conversations/{target['conversation_id']}/operations/{operation_id}", headers=_headers())
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["business_status"] == "SUCCEEDED"
    assert resolved.json()["result"]["execution_result"]["data"]["transaction_id"] == transaction_id
    assert main.ACCOUNTS == accounts and main.TRANSACTIONS == transactions


@pytest.mark.parametrize("change", [
    {"principal": "user_li"}, {"conversation_id": "different-conversation"}, {"operation_id": "unowned-operation"},
])
def test_retained_receipt_rejects_unowned_operation_without_changing_graph(receipt_gateway, receipt_controls, change):
    target, _ = completed_transfer(receipt_gateway)
    original = deepcopy(graph_snapshot(target).values)
    response = receipt_controls.post("/__scenario/retain-receipt", json={**target, **change},
                                     headers={"X-Scenario-Token": "test-control-token"})
    assert response.status_code == 409
    assert graph_snapshot(target).values == original


@pytest.mark.parametrize("text", ["查一下余额", "给李四转10元"])
def test_retained_receipt_requires_completed_transfer(receipt_gateway, receipt_controls, text):
    from test_agent_gateway import _send
    target = {"principal": "user_zhang", "conversation_id": "not-completed-transfer", "operation_id": "not-a-receipt"}
    _send(receipt_gateway[0], text, conversation_id=target["conversation_id"])
    original = deepcopy(graph_snapshot(target).values)
    response = receipt_controls.post("/__scenario/retain-receipt", json=target,
                                     headers={"X-Scenario-Token": "test-control-token"})
    assert response.status_code == 409
    assert graph_snapshot(target).values == original


def test_retained_receipt_requires_control_token_before_accessing_graph(receipt_controls, monkeypatch):
    import conversation
    monkeypatch.setattr(conversation, "get_graph", lambda: pytest.fail("Unauthorized request accessed the graph"))
    target = {"principal": "user_zhang", "conversation_id": "owned-conversation", "operation_id": "owned-operation"}
    for headers in ({}, {"X-Scenario-Token": "wrong-token"}):
        assert receipt_controls.post("/__scenario/retain-receipt", json=target, headers=headers).status_code == 403
    for payload in ({}, {**target, "operation_id": " "}, {**target, "conversation_id": "invalid/conversation"}):
        assert receipt_controls.post("/__scenario/retain-receipt", json=payload,
                                     headers={"X-Scenario-Token": "test-control-token"}).status_code == 400
