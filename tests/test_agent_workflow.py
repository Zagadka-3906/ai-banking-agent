"""Offline LangGraph smoke tests (Core is exercised in-process)."""

import json
from datetime import datetime

import pytest
import requests

pytest.importorskip("langgraph")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from langgraph.types import Command

import agent
import main


@pytest.fixture()
def wired(monkeypatch):
    main.reset_state()
    client = TestClient(main.app)

    def fake_http(method, path, *, state, json_body=None, headers=None):
        # Mirror the real ``agent._http`` contract: the request id from graph
        # state is propagated as X-Request-Id so the core records one linked
        # decision chain.  Replacing the transport must not silently drop it.
        merged = {"X-Request-Id": state.get("request_id") or "test-request"}
        merged.update(headers or {})
        return client.request(method, "/api/v1/" + path.lstrip("/"), json=json_body, headers=merged)

    monkeypatch.setattr(agent, "_http", fake_http)
    yield client
    main.reset_state()


def test_transfer_interrupt_confirm_and_settle(wired):
    config = {"configurable": {"thread_id": "test-agent-transfer"}}
    list(agent.app.stream({"user_id": "user_zhang", "user_input": "给李四转账 10 元"}, config=config, stream_mode="values"))
    snapshot = agent.app.get_state(config)
    assert snapshot.next
    pending = snapshot.tasks[0].interrupts[0].value
    assert pending["risk_level"] == "YELLOW"
    token = pending["auth_token"]
    confirm = wired.post("/api/v1/auth/confirm", json={}, headers={"X-User-Id": "user_zhang", "X-Auth-Token": token})
    assert confirm.status_code == 200
    list(agent.app.stream(Command(resume={"confirmed": True}), config=config, stream_mode="values"))
    result = agent.app.get_state(config).values
    assert result["execution_result"]["type"] == "TRANSFER_SUCCESS"
    assert "流水号" in result["final_response"]
    # The plan ledger must reflect what actually ran.
    plan_tasks = {task["task_id"]: task for task in result["plan"]["tasks"]}
    assert plan_tasks["validate_transfer"]["status"] == "SUCCEEDED"
    assert plan_tasks["execute_transfer"]["status"] == "SUCCEEDED"


def test_agent_run_links_core_decision_chain(wired):
    """One agent run must produce a core-side chain keyed by its request id."""

    config = {"configurable": {"thread_id": "test-agent-trace-link"}}
    list(agent.app.stream({"user_id": "user_zhang", "user_input": "给李四转账 10 元"}, config=config, stream_mode="values"))
    snapshot = agent.app.get_state(config)
    token = snapshot.values["auth_token"]
    request_id = snapshot.values["request_id"]
    assert request_id
    # The web channel resumes with the request id from the interrupt payload;
    # do the same here so confirmation shares the same chain.
    confirm = wired.post("/api/v1/auth/confirm", json={}, headers={"X-User-Id": "user_zhang", "X-Auth-Token": token, "X-Request-Id": request_id})
    assert confirm.status_code == 200
    list(agent.app.stream(Command(resume={"confirmed": True}), config=config, stream_mode="values"))

    trace = wired.get(f"/api/v1/security/audit/trace/{request_id}", headers={"X-User-Id": "user_zhang"})
    assert trace.status_code == 200, trace.text
    stages = set(trace.json()["stages"])
    assert {"RISK_PRECHECK", "USER_CONFIRMATION", "TRANSFER_EXECUTE", "AUTH_TOKEN_CONSUME"}.issubset(stages)
    assert trace.json()["terminal_outcome"] == "SUCCESS"


def test_settle_retries_once_with_same_idempotency_key(wired, monkeypatch):
    """A transport failure retries once with the same key (no double settle)."""

    captured: list[dict[str, str]] = []
    real_http = agent._http
    calls = {"n": 0}

    def flaky(method, path, *, state, json_body=None, headers=None):
        if method == "POST" and "transfers/execute" in path:
            captured.append(dict(headers or {}))
            calls["n"] += 1
            if calls["n"] == 1:
                raise requests.ConnectionError("boom")
        return real_http(method, path, state=state, json_body=json_body, headers=headers)

    monkeypatch.setattr(agent, "_http", flaky)
    config = {"configurable": {"thread_id": "test-agent-idempotent-retry"}}
    list(agent.app.stream({"user_id": "user_zhang", "user_input": "给李四转账 10 元"}, config=config, stream_mode="values"))
    snapshot = agent.app.get_state(config)
    token = snapshot.values["auth_token"]
    assert wired.post("/api/v1/auth/confirm", json={}, headers={"X-User-Id": "user_zhang", "X-Auth-Token": token}).status_code == 200
    list(agent.app.stream(Command(resume={"confirmed": True}), config=config, stream_mode="values"))
    result = agent.app.get_state(config).values
    assert result["execution_result"]["type"] == "TRANSFER_SUCCESS"
    assert calls["n"] == 2
    assert captured[0]["Idempotency-Key"] and captured[0]["Idempotency-Key"] == captured[1]["Idempotency-Key"]
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000.0 - 10.0


def test_failed_lock_settle_triggers_compensation_verification(wired, monkeypatch):
    """A failed reserve_funds step marks the DAG FAILED and runs compensation."""

    real_http = agent._http

    def flaky(method, path, *, state, json_body=None, headers=None):
        if method == "POST" and "lock_fund" in path:
            raise requests.RequestException("network down")
        return real_http(method, path, state=state, json_body=json_body, headers=headers)

    monkeypatch.setattr(agent, "_http", flaky)
    config = {"configurable": {"thread_id": "test-agent-lock-compensation"}}
    list(agent.app.stream({"user_id": "user_zhang", "user_input": "我爱人生日是 2099-10-03，锁定 500 元并提醒订花"}, config=config, stream_mode="values"))
    snapshot = agent.app.get_state(config)
    token = snapshot.values["auth_token"]
    assert wired.post("/api/v1/auth/confirm", json={}, headers={"X-User-Id": "user_zhang", "X-Auth-Token": token}).status_code == 200
    list(agent.app.stream(Command(resume={"confirmed": True}), config=config, stream_mode="values"))
    result = agent.app.get_state(config).values
    plan_tasks = {task["task_id"]: task for task in result["plan"]["tasks"]}
    assert plan_tasks["reserve_funds"]["status"] == "FAILED"
    assert plan_tasks["reserve_funds"]["details"]["compensation_action"] == "UNLOCK_FUND"
    assert "补偿动作 UNLOCK_FUND" in result["final_response"]
    assert "无需反向解锁" in result["final_response"]  # balance check confirms no mutation
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000.0
    assert main.ACCOUNTS["user_zhang"]["locked_amount"] == 0.0


def test_red_resume_without_mfa_proof_is_blocked_by_agent_gate(wired):
    """The agent refuses to proceed when the red-zone resume lacks factors."""

    main.DAILY_TRANSFERS["user_zhang"] = 1000.0
    main.DAILY_TRANSFER_DATE["user_zhang"] = datetime.now().strftime("%Y-%m-%d")
    config = {"configurable": {"thread_id": "test-agent-red-gate"}}
    list(agent.app.stream({"user_id": "user_zhang", "user_input": "给李四转账 1 元"}, config=config, stream_mode="values"))
    snapshot = agent.app.get_state(config)
    assert snapshot.next
    pending = snapshot.tasks[0].interrupts[0].value
    assert pending["risk_level"] == "RED"
    assert pending["required_actions"]

    list(agent.app.stream(Command(resume={"confirmed": True}), config=config, stream_mode="values"))
    result = agent.app.get_state(config).values
    assert "多因子验证未完成" in result.get("final_response", "")
    assert not result.get("execution_result")
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000.0


@pytest.mark.parametrize(
    ("text", "expected_type"),
    [
        ("下周给李四转 500 元", "SCHEDULED_TRANSFER"),
        ("和李四、王美 AA 分摊 300 元", "SPLIT_BILL"),
    ],
)
def test_non_immediate_transfer_flows_use_server_nonce(wired, text, expected_type):
    config = {"configurable": {"thread_id": f"test-agent-{expected_type.lower()}"}}
    list(agent.app.stream({"user_id": "user_zhang", "user_input": text}, config=config, stream_mode="values"))
    snapshot = agent.app.get_state(config)
    assert snapshot.next
    pending = snapshot.tasks[0].interrupts[0].value
    assert pending["auth_token"]
    assert wired.post("/api/v1/auth/confirm", json={}, headers={"X-User-Id": "user_zhang", "X-Auth-Token": pending["auth_token"]}).status_code == 200
    list(agent.app.stream(Command(resume={"confirmed": True}), config=config, stream_mode="values"))
    result = agent.app.get_state(config).values
    assert result["execution_result"]["type"] == expected_type


def test_incomplete_transfer_is_clarification(wired):
    config = {"configurable": {"thread_id": "test-agent-clarify"}}
    list(agent.app.stream({"user_id": "user_zhang", "user_input": "帮我转账 10 元"}, config=config, stream_mode="values"))
    result = agent.app.get_state(config).values
    assert result["clarification_required"] is True
    assert result["risk_level"] == "UNKNOWN"
    assert "收款人" in result["final_response"]


def test_product_id_is_not_parsed_as_amount():
    parsed = agent._local_parse("申购 MMF001 1000 元")
    assert parsed["target"] == "MMF001"
    assert parsed["amount"] == 1000.0


def test_model_cannot_downgrade_explicit_scheduled_transfer(monkeypatch):
    """A model must not turn an explicit future transfer into an immediate one."""

    class FakeResponse:
        content = json.dumps(
            {
                "intent": "TRANSFER",
                "target": "李四",
                "amount": 500,
                "date_scope": "ALL",
                "extra_info": "下周给李四转 500 元",
                "confidence": 0.99,
                "participants": [],
                "product_ids": [],
                "slots": {},
                # Deliberately conflicting model output; local policy facts win.
                "scheduled_at": "2099-01-01T09:00:00+08:00",
                "event_at": None,
            }
        )

    class FakeLLM:
        def invoke(self, _messages):
            return FakeResponse()

    monkeypatch.setattr(agent, "llm", FakeLLM())
    parsed = agent._model_parse("下周给李四转 500 元")
    assert parsed["intent"] == "SCHEDULED_TRANSFER"
    assert parsed["scheduled_at"] != "2099-01-01T09:00:00+08:00"


def test_offline_parser_reads_card_limit_without_currency_unit():
    parsed = agent._local_parse("把我的卡额度调到 5000")
    assert parsed["intent"] == "CARD_LIMIT"
    assert parsed["amount"] == 5000.0


@pytest.mark.parametrize(
    ("text", "expected"),
    [("冻结我的卡", "CARD_FREEZE"), ("解冻我的卡", "CARD_UNFREEZE"), ("我的卡丢了", "CARD_REPORT_LOST"), ("解挂我的卡", "CARD_UNREPORT_LOST")],
)
def test_offline_parser_prioritizes_card_security_actions(text, expected):
    assert agent._local_parse(text)["intent"] == expected


def test_responder_does_not_claim_malformed_balance():
    result = agent.responder_node(
        {
            "user_id": "user_zhang",
            "request_id": "test-malformed-balance",
            "execution_result": {"type": "BALANCE", "data": {"total_balance": 999}},
        }
    )
    assert "没有声称" in result["final_response"]


@pytest.mark.parametrize(
    "execution_result",
    [
        {"type": "TRANSFER_SUCCESS", "data": {"status": "SUCCESS", "transaction_id": "T1", "receiver_name": "李四", "transferred_amount": "NaN", "sender_balance": 1}},
        {"type": "TRANSFER_SUCCESS", "data": {"status": "FAILURE", "transaction_id": "T1", "receiver_name": "李四", "transferred_amount": 1, "sender_balance": 1}},
        {"type": "LOCK_SUCCESS", "data": {"status": "LOCKED", "lock_id": "L1", "locked_amount": "not-a-number", "remaining_available": 1}},
        {"type": "CARD_APPLY", "data": {"status": "SUCCESS", "data": {"status": "ISSUED"}}},
    ],
)
def test_responder_never_claims_malformed_mutation_result(execution_result):
    result = agent.responder_node(
        {"user_id": "user_zhang", "amount": 1, "execution_result": execution_result}
    )
    assert "没有声称" in result["final_response"]


def test_offline_parser_supports_annual_bill_report():
    parsed = agent._local_parse("请生成今年年度账单报告")
    assert parsed["intent"] == "BILL_ANALYSIS"
    assert parsed["date_scope"] == "YEAR"


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("帮我做风险评估，我是新手，低风险", "WEALTH_RISK_ASSESSMENT"),
        ("比较 MMF001 和 BOND002", "WEALTH_COMPARE"),
        ("修改我的登录密码", "PASSWORD_CHANGE"),
    ],
)
def test_offline_parser_covers_security_and_wealth_extensions(text, intent):
    parsed = agent._local_parse(text)
    assert parsed["intent"] == intent


def test_responder_rejects_malformed_read_collections():
    result = agent.responder_node(
        {
            "user_id": "user_zhang",
            "execution_result": {
                "type": "CARD_QUERY",
                "data": {"code": 200, "data": [{"card_id": "CARD-001", "status": "ACTIVE", "daily_limit": "NaN"}]},
            },
        }
    )
    assert "没有声称" in result["final_response"]


def test_birthday_without_date_is_clarification(wired):
    config = {"configurable": {"thread_id": "test-agent-birthday-clarify"}}
    list(agent.app.stream({"user_id": "user_zhang", "user_input": "我爱人生日，锁定 1000 元并提醒订花"}, config=config, stream_mode="values"))
    result = agent.app.get_state(config).values
    assert result["clarification_required"] is True
    assert result.get("auth_token") is None
    assert "生日日期" in result["final_response"]


def test_birthday_reminder_has_verified_run_at(wired):
    config = {"configurable": {"thread_id": "test-agent-birthday-run-at"}}
    list(agent.app.stream({"user_id": "user_zhang", "user_input": "我爱人生日是 2099-10-03，锁定 1000 元并提醒订花和蛋糕"}, config=config, stream_mode="values"))
    snapshot = agent.app.get_state(config)
    assert snapshot.next
    token = snapshot.values["auth_token"]
    assert wired.post("/api/v1/auth/confirm", json={}, headers={"X-User-Id": "user_zhang", "X-Auth-Token": token}).status_code == 200
    list(agent.app.stream(Command(resume={"confirmed": True}), config=config, stream_mode="values"))
    tasks = wired.get("/api/v1/tasks", headers={"X-User-Id": "user_zhang"}).json()["data"]
    reminder = next(task for task in tasks if task["task_type"] == "REMINDER")
    assert reminder["run_at"] == reminder["payload"]["reminder_at"]
    assert reminder["run_at"]
    drafts = [task for task in tasks if task["task_type"] == "ORDER_DRAFT"]
    assert {task["payload"]["item"] for task in drafts} == {"鲜花", "蛋糕"}
    assert all(task["status"] == "PENDING" for task in drafts)
    # The plan ledger: consumed steps succeeded; the reminder/orders step stays
    # PENDING because the core task center owns it until it runs due.
    result = agent.app.get_state(config).values
    plan_tasks = {task["task_id"]: task for task in result["plan"]["tasks"]}
    assert plan_tasks["validate_event"]["status"] == "SUCCEEDED"
    assert plan_tasks["reserve_funds"]["status"] == "SUCCEEDED"
    assert plan_tasks["schedule_orders"]["status"] == "PENDING"
    assert "任务中心" in plan_tasks["schedule_orders"]["details"]["note"]


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("给李四转 2000 元", "TRANSFER"),
        ("下周给李四转 500 元", "SCHEDULED_TRANSFER"),
        ("和李四、王美 AA 分摊 300 元", "SPLIT_BILL"),
    ],
)
def test_offline_parser_covers_transfer_variants(text, intent):
    assert agent._local_parse(text)["intent"] == intent
