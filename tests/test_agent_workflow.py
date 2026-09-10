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
import security


def _approve_red(wired, token: str) -> str:
    """Confirm + complete the red-zone face ticket, returning the MFA proof."""

    headers = {"X-User-Id": "user_zhang", "X-Auth-Token": token}
    assert wired.post("/api/v1/auth/confirm", json={}, headers=headers).status_code == 200
    challenge = wired.post("/api/v1/auth/mfa/face/challenge", json={"auth_token": token, "user_id": "user_zhang"}, headers=headers)
    assert challenge.status_code == 200, challenge.text
    attestation = security.sign_face_attestation(challenge.json()["challenge_id"], user_id="user_zhang", token_jti=challenge.json()["auth_token_jti"])
    mfa = wired.post("/api/v1/auth/mfa/verify", json={"otp": "888888", "face_attestation": attestation}, headers=headers)
    assert mfa.status_code == 200, mfa.text
    return mfa.json()["mfa_proof"]


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


def test_model_cannot_drop_explicit_birthday_date(monkeypatch):
    """A simplified model answer must not erase the user's stated date.

    Reported from a live session: the user said “我爱人生日是下周六…”, the model
    returned the intent without the dates, and the agent asked for the date
    again even though it had already been given.
    """

    class FakeResponse:
        content = json.dumps(
            {
                "intent": "CROSS_SCENE_LOCK",
                "target": None,
                "amount": None,
                "date_scope": "ALL",
                "confidence": 0.9,
                "participants": [],
                "product_ids": [],
                "slots": {},
                "scheduled_at": None,
                "event_at": None,
            }
        )

    class FakeLLM:
        def invoke(self, _messages):
            return FakeResponse()

    monkeypatch.setattr(agent, "llm", FakeLLM())
    parsed = agent._model_parse("我爱人生日是下周六，帮我锁定 1000 元活期，生日前 2 天提醒我订花和蛋糕")
    assert parsed["intent"] == "CROSS_SCENE_LOCK"
    assert parsed["scheduled_at"], "the explicit date must survive the model answer"
    assert parsed["event_at"]
    assert parsed["amount"] == 1000.0
    from planner import needs_clarification

    assert needs_clarification(parsed) is False


def test_model_incomplete_result_falls_back_to_complete_local_parse(monkeypatch):
    class FakeResponse:
        content = json.dumps({"intent": "UNKNOWN", "target": None, "amount": None, "confidence": 0.99})

    class FakeLLM:
        def invoke(self, _messages):
            return FakeResponse()

    monkeypatch.setattr(agent, "llm", FakeLLM())
    parsed = agent._model_parse("给李四转账 10 元")
    assert parsed["intent"] == "TRANSFER"
    assert parsed["amount"] == 10.0


def test_bare_slot_answer_resumes_pending_clarification(wired):
    """Answering a clarification with only a date must resume the same task."""

    config = {"configurable": {"thread_id": "test-agent-pending-slot"}}
    list(agent.app.stream({"user_id": "user_zhang", "user_input": "我爱人生日快到了，帮我锁定 1000 元活期，生日前 2 天提醒我订花和蛋糕"}, config=config, stream_mode="values"))
    first = agent.app.get_state(config).values
    assert first["clarification_required"] is True
    assert first["intent"] == "CROSS_SCENE_LOCK"
    pending = (first.get("context") or {}).get("pending_clarification") or {}
    assert pending.get("missing") == ["scheduled_at"]

    list(agent.app.stream({"user_id": "user_zhang", "user_input": "下周六"}, config=config, stream_mode="values"))
    snapshot = agent.app.get_state(config)
    values = snapshot.values
    assert values["intent"] == "CROSS_SCENE_LOCK"
    assert values["event_at"], "the bare answer must fill the missing date"
    assert values["amount"] == 1000.0
    # The request now waits for the normal approval card, not another question.
    assert snapshot.next
    assert "锁定活期资金" in snapshot.tasks[0].interrupts[0].value["message"]


def test_new_complete_request_overrides_pending_clarification(wired):
    config = {"configurable": {"thread_id": "test-agent-pending-override"}}
    list(agent.app.stream({"user_id": "user_zhang", "user_input": "我爱人生日快到了，帮我锁定 1000 元活期，生日前 2 天提醒我订花和蛋糕"}, config=config, stream_mode="values"))
    assert agent.app.get_state(config).values["clarification_required"] is True

    list(agent.app.stream({"user_id": "user_zhang", "user_input": "给李四转账 10 元"}, config=config, stream_mode="values"))
    values = agent.app.get_state(config).values
    assert values["intent"] == "TRANSFER"
    assert values["target"] == "李四"


def test_bare_amount_answer_completes_card_limit_request(wired):
    """Reported live: answering “额度调整” with “3000” asked the same question again."""

    config = {"configurable": {"thread_id": "test-agent-card-limit-slot"}}
    list(agent.app.stream({"user_id": "user_zhang", "user_input": "额度调整"}, config=config, stream_mode="values"))
    first = agent.app.get_state(config).values
    assert first["intent"] == "CARD_LIMIT"
    assert first["clarification_required"] is True

    list(agent.app.stream({"user_id": "user_zhang", "user_input": "3000"}, config=config, stream_mode="values"))
    snapshot = agent.app.get_state(config)
    assert snapshot.values["intent"] == "CARD_LIMIT"
    assert snapshot.values["amount"] == 3000.0
    assert snapshot.next, "the bare amount must complete the request"

    message = snapshot.tasks[0].interrupts[0].value["message"]
    assert "日限额" in message and "3,000.00" in message
    assert "CARD_LIMIT" not in message, "internal intent codes must not reach the user"

    proof = _approve_red(wired, snapshot.values["auth_token"])
    list(agent.app.stream(Command(resume={"confirmed": True, "mfa_proof": proof}), config=config, stream_mode="values"))
    final = agent.app.get_state(config).values["final_response"]
    assert "日限额已调整" in final and "CARD_LIMIT" not in final
    assert main.CARDS["user_zhang"]["daily_limit"] == 3000.0


@pytest.mark.parametrize("answer", ["3000 元", "三千", "¥3,000"])
def test_bare_amount_variants_fill_the_pending_amount_slot(wired, answer):
    config = {"configurable": {"thread_id": f"test-agent-limit-variant-{hash(answer)}"}}
    list(agent.app.stream({"user_id": "user_zhang", "user_input": "额度调整"}, config=config, stream_mode="values"))
    assert agent.app.get_state(config).values["clarification_required"] is True

    list(agent.app.stream({"user_id": "user_zhang", "user_input": answer}, config=config, stream_mode="values"))
    values = agent.app.get_state(config).values
    assert values["intent"] == "CARD_LIMIT"
    assert values["amount"] == 3000.0


def test_numeric_answer_does_not_fill_a_date_slot(wired):
    """A number must never be mistaken for the date that was actually asked for."""

    config = {"configurable": {"thread_id": "test-agent-date-slot-guard"}}
    list(agent.app.stream({"user_id": "user_zhang", "user_input": "我爱人生日快到了，帮我锁定 1000 元活期，生日前 2 天提醒我订花和蛋糕"}, config=config, stream_mode="values"))
    assert agent.app.get_state(config).values["clarification_required"] is True

    list(agent.app.stream({"user_id": "user_zhang", "user_input": "3000"}, config=config, stream_mode="values"))
    snapshot = agent.app.get_state(config)
    assert snapshot.values["clarification_required"] is True
    assert "生日日期" in snapshot.values["final_response"]
    assert not snapshot.next


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
        # Listing products is a fact query; recommending narrows to the profile.
        ("查看理财产品", "WEALTH_QUERY"),
        ("有哪些理财产品", "WEALTH_QUERY"),
        ("推荐稳健理财产品", "WEALTH_RECOMMEND"),
        ("理财产品收益率", "WEALTH_RECOMMEND"),
    ],
)
def test_wealth_listing_and_recommendation_are_distinguished(text, intent):
    assert agent._local_parse(text)["intent"] == intent


def test_read_collections_are_rendered_readably(wired):
    """Wealth/subscription/card answers must read like a bank assistant's reply.

    Reported from a live session: asking for a product recommendation returned
    the raw core JSON blob.
    """

    cases = [
        ("推荐稳健理财产品", "WEALTH_RECOMMEND", ("MMF001", "稳健现金管理", "风险 R1", "2.10%", "不构成投资建议")),
        ("查看理财产品", "WEALTH_QUERY", ("MMF001", "BOND002", "INDEX003", "在售理财产品")),
        ("比较 MMF001 和 BOND002", "WEALTH_COMPARE", ("产品对比", "MMF001", "BOND002")),
        ("我有哪些订阅", "SUBSCRIPTION_QUERY", ("Apple 订阅", "视频会员", "下次扣款")),
        ("我的卡片", "CARD_QUERY", ("CARD-001", "日限额", "状态 ACTIVE")),
    ]
    for index, (text, intent, expected) in enumerate(cases):
        config = {"configurable": {"thread_id": f"test-render-{index}"}}
        list(agent.app.stream({"user_id": "user_zhang", "user_input": text}, config=config, stream_mode="values"))
        values = agent.app.get_state(config).values
        assert values["intent"] == intent, text
        response = values["final_response"]
        assert '"code"' not in response, f"{text} leaked the raw payload"
        assert not response.strip().startswith("{"), f"{text} returned raw JSON"
        for token in expected:
            assert token in response, f"{text} missing {token} in:\n{response}"


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


@pytest.mark.parametrize(
    ("text", "intent", "target", "scope"),
    [
        ("我的卡好像被人盗刷了", "CARD_FREEZE", None, "ALL"),
        ("钱都花哪儿去了", "BILL_ANALYSIS", None, "MONTH"),
        ("这个月怎么花了这么多钱", "BILL_ANALYSIS", None, "MONTH"),
        ("帮我看看有没有乱扣费", "SUBSCRIPTION_QUERY", None, "ALL"),
        ("我不想再被扣会员费了", "SUBSCRIPTION_CANCEL", None, "ALL"),
        ("取不出钱来了", "CARD_QUERY", None, "ALL"),
        ("这个钱放着也是放着", "WEALTH_RECOMMEND", None, "ALL"),
        ("转点钱给老婆", "TRANSFER", "老婆", "ALL"),
        ("帮我买点理财", "WEALTH_RECOMMEND", None, "ALL"),
    ],
)
def test_implicit_phrasings_map_to_allowlisted_intents(text, intent, target, scope):
    """Implicit/vague phrasings resolve deterministically, never to UNKNOWN."""

    parsed = agent._local_parse(text)
    assert parsed["intent"] == intent, text
    assert parsed["target"] == target
    assert parsed["date_scope"] == scope


def test_ellipsis_repeat_reuses_request_with_fresh_authorization(wired):
    config = {"configurable": {"thread_id": "test-agent-ellipsis-repeat"}}
    list(agent.app.stream({"user_id": "user_zhang", "user_input": "给李四转账 100 元"}, config=config, stream_mode="values"))
    first = agent.app.get_state(config)
    assert first.values["intent"] == "TRANSFER"
    first_token = first.tasks[0].interrupts[0].value["auth_token"]

    list(agent.app.stream({"user_id": "user_zhang", "user_input": "再转一次"}, config=config, stream_mode="values"))
    second = agent.app.get_state(config)
    assert second.values["intent"] == "TRANSFER"
    assert second.values["target"] == "李四"
    assert second.values["amount"] == 100.0
    assert second.next, "a repeat still needs approval"
    # The repeat is re-authorized: it must not reuse the consumed nonce.
    assert second.tasks[0].interrupts[0].value["auth_token"] != first_token


def test_ellipsis_pronoun_resolves_last_subscription_context(wired):
    config = {"configurable": {"thread_id": "test-agent-ellipsis-pronoun"}}
    list(agent.app.stream({"user_id": "user_zhang", "user_input": "我有哪些订阅"}, config=config, stream_mode="values"))
    assert agent.app.get_state(config).values["intent"] == "SUBSCRIPTION_QUERY"

    list(agent.app.stream({"user_id": "user_zhang", "user_input": "把它取消掉"}, config=config, stream_mode="values"))
    values = agent.app.get_state(config).values
    assert values["intent"] == "SUBSCRIPTION_CANCEL"
    # Which subscription is genuinely ambiguous, so it must be asked for.
    assert values["clarification_required"] is True
    assert "订阅" in values["final_response"]


def test_unresolvable_backreference_asks_for_details(wired):
    config = {"configurable": {"thread_id": "test-agent-ellipsis-unresolvable"}}
    list(agent.app.stream({"user_id": "user_zhang", "user_input": "还是上次那个"}, config=config, stream_mode="values"))
    values = agent.app.get_state(config).values
    assert values["intent"] == "UNKNOWN"
    assert "没有找到可以沿用" in values["final_response"]


def test_compound_read_request_becomes_ordered_dag(wired):
    """“查一下余额，再分析上个月账单” is two ordered sub-tasks, not one."""

    config = {"configurable": {"thread_id": "test-agent-multi-read"}}
    list(agent.app.stream({"user_id": "user_zhang", "user_input": "查一下余额，再分析上个月账单"}, config=config, stream_mode="values"))
    values = agent.app.get_state(config).values
    assert [item["intent"] for item in values["sub_requests"]] == ["BALANCE_QUERY", "BILL_ANALYSIS"]
    assert values["risk_level"] == "GREEN"

    tasks = values["plan"]["tasks"]
    assert [task["task_id"] for task in tasks] == ["0_fetch_balance", "1_fetch_transactions", "1_compute_facts"]
    assert tasks[1]["depends_on"] == ["0_fetch_balance"]
    assert tasks[2]["depends_on"] == ["1_fetch_transactions"]
    assert all(task["status"] == "SUCCEEDED" for task in tasks)
    assert tasks[1]["details"]["sub_intent"] == "BILL_ANALYSIS"

    response = values["final_response"]
    assert "总余额" in response, "first sub-answer missing"
    assert "笔交易" in response, "second sub-answer missing"
    assert '"code"' not in response


def test_compound_request_with_a_write_is_not_decomposed(wired):
    """A mutating part must not be silently dropped or auto-split."""

    config = {"configurable": {"thread_id": "test-agent-multi-mixed"}}
    list(agent.app.stream({"user_id": "user_zhang", "user_input": "查一下余额，再给李四转 100 元"}, config=config, stream_mode="values"))
    snapshot = agent.app.get_state(config)
    assert not snapshot.values.get("sub_requests")
    assert snapshot.values["clarification_required"] is True
    assert "分开逐条办理" in snapshot.values["final_response"]
    assert not snapshot.next, "nothing may be executed or left pending"
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000.0


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
