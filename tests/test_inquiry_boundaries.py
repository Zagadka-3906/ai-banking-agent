"""Information requests stay read-only, including with a confidently wrong LLM."""
from copy import deepcopy
from datetime import datetime
import json
from types import SimpleNamespace
import uuid

import pytest

import main
from bank_agent import runtime
from bank_agent.nodes.risk import risk_checker_node
from bank_agent.understanding import model_context, remember_reply, understanding_context
from support import sign_face_attestation
from test_agent_gateway import _headers, _send, gateway  # noqa: F401


def wrong_model(monkeypatch, intent="FRAUD_DISPUTE", calls=None):
    def invoke(messages):
        if calls is not None:
            calls.append(messages)
        return SimpleNamespace(content=json.dumps({"intent": intent, "confidence": 0.99, "target": "CARD-001"}))
    monkeypatch.setattr(runtime, "llm", SimpleNamespace(invoke=invoke))


def financial_state():
    return deepcopy((main.ACCOUNTS, main.CARDS, main.TRANSACTIONS, main.DISPUTE_CASES, main.AUTH_TOKENS))


def protect(core):
    headers = _headers()
    body = {"user_id": "user_zhang", "operation": "FRAUD_PROTECT", "transaction_id": "TX1004"}
    pre = core.post("/api/v1/risk/precheck/operation", json=body, headers={**headers, "Idempotency-Key": uuid.uuid4().hex})
    assert pre.status_code == 200, pre.text
    token = pre.json()["auth_token"]
    authenticated = {**headers, "X-Auth-Token": token}
    assert core.post("/api/v1/auth/confirm", json={}, headers=authenticated).status_code == 200
    challenge = core.post("/api/v1/auth/mfa/face/challenge", json={"auth_token": token, "user_id": "user_zhang"}, headers=authenticated).json()
    signature = sign_face_attestation(challenge["challenge_id"], user_id="user_zhang", token_jti=challenge["auth_token_jti"])
    verified = core.post("/api/v1/auth/mfa/verify", json={"otp": "888888", "face_attestation": signature}, headers=authenticated)
    assert verified.status_code == 200, verified.text
    result = core.post("/api/v1/operations/execute", json={**body, "auth_token": token, "mfa_proof": verified.json()["mfa_proof"]},
                       headers={**headers, "Idempotency-Key": uuid.uuid4().hex})
    assert result.status_code == 200, result.text
    return result.json()["data"]


def test_user_reported_three_turns_never_freeze_or_create_a_case(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    now = datetime.now().strftime("%Y-%m-%d 12:00:00")
    main.TRANSACTIONS[:] = [{"tx_id": f"SUSP-{index}", "account_id": "user_zhang", "merchant": f"待核对商户{index}",
                            "category": "可疑交易", "amount": 100 + index, "time": now, "card_id": "CARD-001"} for index in range(6)]
    main.TRANSACTIONS.append({"tx_id": "PRIVATE", "account_id": "user_li", "merchant": "其他人的交易",
                             "category": "可疑交易", "amount": 999, "time": now, "card_id": "PRIVATE-CARD"})
    wrong_model(monkeypatch)
    _send(client, "分析本月账单", conversation_id="reported-loop")
    before = financial_state()
    query = _send(client, "那6笔可疑交易帮我查下", conversation_id="reported-loop")
    assert query["approval"] is None and query["business_status"] == "SUCCEEDED"
    assert query["intent"] == "BILL_QUERY"
    assert len(query["result"]["execution_result"]["records"]) == 6
    assert "其他人的交易" not in query["reply"]
    for question in ("争议工单进度怎么查", "争议工单多少有结果"):
        reply = _send(client, question, conversation_id="reported-loop")
        assert reply["intent"] == "DISPUTE_QUERY" and reply["approval"] is None
        assert "没有已登记" in reply["reply"]
        assert "预计完成时间" in reply["reply"]
    assert financial_state() == before


def test_existing_case_progress_and_timing_query_do_not_repeat_protection(gateway, monkeypatch):  # noqa: F811
    client, core = gateway
    case = protect(core)["dispute_case"]
    before = financial_state()
    wrong_model(monkeypatch)
    for text in ("争议工单进度怎么查", "这张争议工单多少有结果", "查看工单" + case["case_id"]):
        reply = _send(client, text, conversation_id="existing-case")
        assert reply["intent"] == "DISPUTE_QUERY" and reply["approval"] is None
        assert case["case_id"] in reply["reply"] and "待人工审核" in reply["reply"]
        assert "预计完成时间" in reply["reply"]
    assert financial_state() == before


@pytest.mark.parametrize("question", ["怎么冻结银行卡", "转账多久能到账", "取消订阅会马上停用吗", "申请挂失需要什么手续", "冻结卡片后还能收款吗", "怎么提交争议工单",
                                      "请问冻结CARD-001可以吗", "帮我了解怎么冻结CARD-001可以吗", "帮我冻结CARD-001后还能收款可以吗"])
def test_business_procedure_questions_do_not_become_writes(gateway, monkeypatch, question):  # noqa: F811
    client, _ = gateway
    wrong_model(monkeypatch, "CARD_FREEZE")
    before = financial_state()
    reply = _send(client, question, conversation_id="procedure")
    assert reply["intent"] == "BANKING_HELP", reply
    assert reply["approval"] is None and reply["business_status"] == "SUCCEEDED"
    assert financial_state() == before


@pytest.mark.parametrize("question", ["帮我查下是不是被盗刷", "我的卡是不是被盗刷了", "我的卡被盗刷了吗", "这是盗刷吗", "查看这些可疑交易"])
def test_checking_a_suspicion_is_not_a_denial_or_a_freeze_request(gateway, monkeypatch, question):  # noqa: F811
    client, _ = gateway
    wrong_model(monkeypatch, "CARD_FREEZE")
    before = financial_state()
    reply = _send(client, question, conversation_id="suspicion")
    assert reply["intent"] == "BILL_QUERY" and reply["approval"] is None
    assert "不能据此确认盗刷" in reply["reply"]
    assert financial_state() == before


@pytest.mark.parametrize("text,intent", [("昨晚这笔境外消费不是我的", "FRAUD_DISPUTE"), ("帮我冻结CARD-001可以吗", "CARD_FREEZE"),
                                        ("请冻结CARD-001好吗", "CARD_FREEZE"), ("替我冻结CARD-001可以吗", "CARD_FREEZE")])
def test_explicit_protection_still_requires_a_real_red_approval(gateway, monkeypatch, text, intent):  # noqa: F811
    client, _ = gateway
    wrong_model(monkeypatch, intent)
    reply = _send(client, text, conversation_id="actual-protection")
    assert reply["status"] == "APPROVAL_REQUIRED" and reply["approval"]["mfa_required"] is True
    assert main.CARDS["user_zhang"][0]["status"] == "ACTIVE" and not main.DISPUTE_CASES


def test_model_sees_actual_previous_case_for_an_elliptical_question(gateway, monkeypatch):  # noqa: F811
    client, core = gateway
    case_id = protect(core)["dispute_case"]["case_id"]
    monkeypatch.setattr(runtime, "llm", None)
    _send(client, "查争议工单", conversation_id="contextual-case")
    calls = []
    def understand(messages):
        calls.append(messages)
        return SimpleNamespace(content='{"intent":"DISPUTE_QUERY","confidence":0.99}')
    monkeypatch.setattr(runtime, "llm", SimpleNamespace(invoke=understand))
    reply = _send(client, "那什么时候有结果", conversation_id="contextual-case")
    prompt = "\n".join(str(message.content) for message in calls[0])
    assert case_id in prompt and "recent_turns" in prompt and "recent_observations" in prompt
    assert case_id in reply["reply"] and reply["approval"] is None
    assert "预计完成时间" in reply["reply"]


def test_core_disputes_are_owner_scoped_and_require_a_session_in_strict_mode(gateway, monkeypatch):  # noqa: F811
    _, core = gateway
    case_id = protect(core)["dispute_case"]["case_id"]
    monkeypatch.setenv("BANK_REQUIRE_SESSION", "1")
    assert core.get("/api/v1/disputes").status_code == 401
    assert core.get("/api/v1/service-guide").status_code == 401
    assert core.get("/api/v1/disputes", headers=_headers("user_li")).json()["data"] == []
    other = core.get("/api/v1/disputes", params={"case_id": case_id}, headers=_headers("user_li"))
    assert other.status_code == 404 and case_id not in other.text


def test_risk_boundary_independently_refuses_an_inquiry_disguised_as_a_write(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("An inquiry must not obtain an authorization nonce")
    monkeypatch.setattr(runtime, "_http", forbidden)
    for text in ("那6笔可疑交易帮我查下", "争议工单进度怎么查", "争议工单多少有结果", "怎么冻结银行卡"):
        result = risk_checker_node({"intent": "FRAUD_DISPUTE", "user_id": "user_zhang", "user_input": text})
        assert result["auth_token"] is None and result["business_status"] == "NEEDS_INPUT"


@pytest.mark.parametrize('text', [
    '我不冻结了，放弃冻结卡片，不用再往下办了。',
    '放弃这次给李四转账', '我不申购了', '暂缓修改限额',
    '停止办理取消Apple订阅', '我不转账了', '不用再往下办了',
])
def test_withdrawal_cannot_create_approval_even_when_model_requests_a_freeze(gateway, monkeypatch, text):  # noqa: F811
    client, _ = gateway
    wrong_model(monkeypatch, 'CARD_FREEZE')
    before = financial_state()
    calls = []
    original = runtime._http
    def track(method, path, **kwargs):
        calls.append((method, path))
        return original(method, path, **kwargs)
    monkeypatch.setattr(runtime, '_http', track)
    reply = _send(client, text, conversation_id='withdrawal')
    assert reply['approval'] is None
    assert financial_state() == before
    assert all(method == 'GET' for method, _ in calls), calls


@pytest.mark.parametrize('text', [
    '我不冻结了，放弃冻结卡片，不用再往下办了。',
    '放弃给李四转账', '我不申购了', '暂停调额', '不办了',
    '我还是想申购零星宝，但金额没定，现在决定先取消这次申购。',
    '我还想给李四转账，但金额没定，现在决定先取消这笔转账。',
    '我还想调额，但数值没定，现在决定取消本次调额。',
])
def test_risk_boundary_rechecks_withdrawal_without_obtaining_a_nonce(monkeypatch, text):
    def forbidden(*args, **kwargs):
        raise AssertionError('A withdrawn operation must not obtain an authorization nonce')
    monkeypatch.setattr(runtime, '_http', forbidden)
    result = risk_checker_node({'intent': 'CARD_FREEZE', 'user_id': 'user_zhang', 'user_input': text})
    assert result['auth_token'] is None and result['business_status'] == 'NEEDS_INPUT'


@pytest.mark.parametrize('text', ['先不确认，给李四转300元', '取消Apple订阅', '帮我冻结CARD-001'])
def test_withdrawal_boundary_does_not_turn_proposals_or_service_cancellation_into_refusal(text):
    from intent_language import execution_constraint
    assert execution_constraint(text) is None


def test_dialogue_memory_is_bounded_credential_free_and_not_shared_between_users():
    state = {"user_id": "alice", "user_input": "密码：secret"}
    for index in range(10):
        state.update(remember_reply(state, {"final_response": f"回复{index}"}))
    assert len(state["dialogue_history"]) == 6
    with understanding_context(state):
        context = json.dumps(model_context(), ensure_ascii=False)
        assert "secret" not in context
    with understanding_context({**state, "user_id": "bob"}):
        assert model_context()["recent_turns"] == [] and model_context()["recent_observations"] == []
    state["observations"] = {"DISPUTE_QUERY": {"case_ids": ["ALICE-PRIVATE"]}}
    switched = {**state, "user_id": "bob"}
    switched.update(remember_reply(switched, {"final_response": "查询失败", "business_status": "FAILED"}))
    with understanding_context(switched):
        assert model_context()["recent_observations"] == []


def test_repeated_explicit_protection_links_the_existing_case(gateway):  # noqa: F811
    _, core = gateway
    first, second = protect(core), protect(core)
    assert first["case_created"] is True and second["case_created"] is False
    assert first["dispute_case"]["case_id"] == second["dispute_case"]["case_id"]
    assert len(main.DISPUTE_CASES) == 1
