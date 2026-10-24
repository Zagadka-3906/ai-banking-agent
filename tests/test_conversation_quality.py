# ruff: noqa: F811
"""Question relevance, verified reference sets, constrained answer plans and approval side questions."""
from copy import deepcopy
from datetime import datetime
import json
from types import SimpleNamespace

import pytest

import agent
import conversation
import main
from bank_agent import runtime
from bank_agent.answering import compose_answer
from bank_agent.nodes.risk import risk_checker_node
from test_agent_gateway import _headers, _send, gateway  # noqa: F401
from test_inquiry_boundaries import financial_state, protect


def fixed_model(monkeypatch, intent, act, choose=None, calls=None, **extra):
    def invoke(messages):
        if calls is not None:
            calls.append(messages)
        if "银行只读咨询规划器" in str(messages[0].content):
            return SimpleNamespace(content='{"reads":[]}')
        if "独立银行回答核验器" in str(messages[0].content):
            return SimpleNamespace(content='{"in_scope":true,"grounded":true,"answers_question":true,"safe":true}')
        if "银行业务解答助手" in str(messages[0].content):
            payload = json.loads(messages[-1].content)
            ids = choose(payload) if choose else [payload["facts"][0]["id"]]
            answer = "\n".join(item["text"] for key in ids for item in payload["facts"] if item["id"] == key)
            return SimpleNamespace(content=json.dumps({"answer": answer, "evidence_ids": ids, "unanswered": []}, ensure_ascii=False))
        if "银行意图抽取器" in str(messages[-1].content):
            return SimpleNamespace(content=json.dumps({"intent": intent, "dialogue_act": act, "confidence": .99, **extra}, ensure_ascii=False))
        return SimpleNamespace(content='{"expression":null}')
    monkeypatch.setattr(runtime, "llm", SimpleNamespace(invoke=invoke))


def state(cid):
    thread = conversation.conversation_thread_id("user_zhang", cid)
    return conversation.get_graph().get_state(conversation.SERVICE._config(thread)).values


def approve(client, pending):
    response = client.post(f"/api/v1/agent/approvals/{pending['approval']['approval_id']}", json={"decision": "approve"}, headers=_headers())
    assert response.status_code == 200, response.text
    return response.json()


def test_historical_query_of_the_completed_transfer_does_not_start_another(gateway, monkeypatch):
    client, _ = gateway
    completed = approve(client, _send(client, "给李四转300元", conversation_id="receipt-reference"))
    tx_id = completed["result"]["execution_result"]["data"]["transaction_id"]
    before = financial_state()
    fixed_model(monkeypatch, "TRANSFER", "QUERY")
    reply = _send(client, "查一下这笔转账记录", conversation_id="receipt-reference")
    assert reply["approval"] is None and reply["intent"] == "BILL_QUERY"
    assert [row["tx_id"] for row in reply["result"]["execution_result"]["records"]] == [tx_id]
    assert tx_id in reply["reply"] and financial_state() == before


def test_a_request_to_repeat_an_explanation_does_not_repeat_the_last_write(gateway, monkeypatch):
    client, _ = gateway
    approve(client, _send(client, "给李四转300元", conversation_id="repeat-explanation"))
    before = financial_state()
    fixed_model(monkeypatch, "BANKING_HELP", "EXPLAIN")
    reply = _send(client, "再来一次，这回只是说明含义", conversation_id="repeat-explanation")
    assert reply["approval"] is None and financial_state() == before


def test_historical_five_referenced_transactions_does_not_return_the_whole_bill(gateway):
    client, _ = gateway
    now = datetime.now().strftime("%Y-%m-%d 12:00:00")
    main.TRANSACTIONS[:] = [{"tx_id": f"FACT-{index}", "account_id": "user_zhang", "amount": 100 + index,
                            "merchant": f"商户{index}", "category": "可疑交易" if index < 5 else "餐饮", "time": now,
                            "card_id": "CARD-001"} for index in range(7)]
    _send(client, "分析本月账单", conversation_id="five-reference")
    before = financial_state()
    reply = _send(client, "那5笔交易帮我查一下", conversation_id="five-reference")
    assert reply["intent"] == "BILL_QUERY" and reply["approval"] is None
    assert {row["tx_id"] for row in reply["result"]["execution_result"]["records"]} == {f"FACT-{index}" for index in range(5)}
    assert "哪一笔" not in reply["reply"] and financial_state() == before


def test_missing_reference_is_clarified_instead_of_substituting_the_entire_bill(gateway):
    client, _ = gateway
    before = financial_state()
    reply = _send(client, "那5笔交易帮我查一下", conversation_id="missing-reference")
    assert reply["business_status"] == "NEEDS_INPUT" and reply["approval"] is None
    assert "流水号" in reply["reply"] and not (reply["result"] or {}).get("execution_result")
    assert financial_state() == before


def test_a_literal_transaction_id_filters_actual_owned_records(gateway, monkeypatch):
    client, _ = gateway
    fixed_model(monkeypatch, "BILL_QUERY", "QUERY", slots={"transaction_id": "TX1004"})
    reply = _send(client, "查TX1004交易记录", conversation_id="literal-reference")
    assert [row["tx_id"] for row in reply["result"]["execution_result"]["records"]] == ["TX1004"]


def test_model_can_resolve_an_ordinal_to_an_already_verified_read_identifier(gateway, monkeypatch):
    client, _ = gateway
    _send(client, "查全部账单", conversation_id="ordinal-reference")
    records = state("ordinal-reference")["execution_result"]["records"]
    chosen = records[4]["tx_id"]
    before = financial_state()
    fixed_model(monkeypatch, "BILL_QUERY", "QUERY", slots={"transaction_id": chosen})
    reply = _send(client, "只看刚才列表的第5笔交易", conversation_id="ordinal-reference")
    assert [row["tx_id"] for row in reply["result"]["execution_result"]["records"]] == [chosen]
    assert financial_state() == before


def test_model_cannot_invent_a_read_identifier_not_in_the_current_users_evidence(gateway, monkeypatch):
    client, _ = gateway
    _send(client, "查全部账单", conversation_id="invented-read-reference")
    fixed_model(monkeypatch, "BILL_QUERY", "QUERY", slots={"transaction_id": "SOMEONE-ELSES-ID"})
    reply = _send(client, "只看刚才列表的第5笔交易", conversation_id="invented-read-reference")
    assert state("invented-read-reference")["slots"].get("transaction_id") is None
    assert "SOMEONE-ELSES-ID" not in reply["reply"]


def test_a_verified_read_identifier_does_not_supply_a_write_target(monkeypatch):
    from bank_agent.understanding import understanding_context
    fixed_model(monkeypatch, "FRAUD_DISPUTE", "ACTION", slots={"transaction_id": "TX1004"})
    with understanding_context({"user_id": "user_zhang", "memory_principal": "user_zhang",
                                "observations": {"BILL_QUERY": {"transaction_ids": ["TX1004"]}}}):
        parsed = agent.model_parse("这笔消费不是我的")
    assert parsed["slots"].get("transaction_id") is None


def test_historical_dispute_cancellation_question_explains_the_missing_capability(gateway, monkeypatch):
    client, core = gateway
    protect(core)
    before = financial_state()
    fixed_model(monkeypatch, "DISPUTE_QUERY", "QUERY", choose=lambda p: [row["id"] for row in p["facts"] if "没有提供撤回" in row["text"]])
    reply = _send(client, "能撤销这笔争议工单吗", conversation_id="cancel-dispute-question")
    assert reply["intent"] == "BANKING_HELP" and reply["approval"] is None
    assert "没有提供撤回" in reply["reply"] and "已冻结关联卡片并创建" not in reply["reply"]
    assert financial_state() == before


def test_fact_question_is_not_overridden_by_a_generic_help_keyword(gateway, monkeypatch):
    client, _ = gateway
    fixed_model(monkeypatch, "CARD_QUERY", "QUERY")
    reply = _send(client, "我的卡是不是被冻结了", conversation_id="real-card-state")
    assert reply["intent"] == "CARD_QUERY" and reply["result"]["execution_result"]["data"]["data"][0]["status"] == "ACTIVE"
    assert "冻结、解冻、挂失" not in reply["reply"]


def test_question_focused_balance_answer_uses_only_selected_verified_field(gateway, monkeypatch):
    client, _ = gateway
    fixed_model(monkeypatch, "BALANCE_QUERY", "QUERY", choose=lambda p: ["balance.available_balance"])
    reply = _send(client, "只告诉我现在能用的钱", conversation_id="focused-balance")
    assert "可用余额" in reply["reply"] and "15,000.00" in reply["reply"]
    assert "总余额" not in reply["reply"] and "锁定金额" not in reply["reply"]
    assert reply["result"]["answer_presentation"] == {"version": 1, "source": "VERIFIED_FACTS", "result_type": "BALANCE", "mode": "GENERATED_REVIEWED"}


def test_effect_question_leads_with_the_effect_not_all_card_procedures(gateway, monkeypatch):
    client, _ = gateway
    fixed_model(monkeypatch, "BANKING_HELP", "EXPLAIN", choose=lambda p: [item["id"] for item in p["facts"] if "仍可接收" in item["text"]])
    reply = _send(client, "冻结卡片后还能收款吗", conversation_id="effect-focus")
    assert "仍可接收转账" in reply["reply"]
    assert "查询卡片状态" not in reply["reply"] and reply["approval"] is None


@pytest.mark.parametrize("act", ["QUERY", "EXPLAIN", "UNSUPPORTED", "UNCLEAR", "SOCIAL"])
def test_nonaction_dialogue_act_cannot_become_a_model_proposed_write(gateway, monkeypatch, act):
    client, _ = gateway
    fixed_model(monkeypatch, "TRANSFER", act, target="李四", amount=100)
    before = financial_state()
    reply = _send(client, "刚才那个是什么意思", conversation_id="nonaction-" + act)
    assert reply["approval"] is None and financial_state() == before
    result = risk_checker_node({"user_id": "user_zhang", "user_input": "刚才那个是什么意思", "intent": "TRANSFER", "parse_meta": {"dialogue_act": act}})
    assert result["auth_token"] is None


@pytest.mark.parametrize("kind,word", [("greeting", "你好"), ("thanks", "不客气"), ("goodbye", "有需要")])
def test_social_turns_do_not_repeat_a_business_menu_or_start_an_action(gateway, monkeypatch, kind, word):
    client, _ = gateway
    fixed_model(monkeypatch, "UNKNOWN", "SOCIAL", social_kind=kind)
    reply = _send(client, "你好" if kind == "greeting" else "谢谢" if kind == "thanks" else "再见", conversation_id="social-" + kind)
    assert word in reply["reply"] and reply["approval"] is None and len(reply["reply"]) < 60
    assert state("social-" + kind)["parse_meta"]["source"] == "model"


def test_clarification_and_unsupported_answers_are_remembered_for_the_next_question(gateway, monkeypatch):
    client, _ = gateway
    first = _send(client, "给李四转点钱", conversation_id="clarification-memory")
    assert "金额" in first["reply"]
    assert state("clarification-memory")["dialogue_history"][-1]["user"] == "给李四转点钱"
    calls = []
    fixed_model(monkeypatch, "BANKING_HELP", "EXPLAIN", calls=calls)
    _send(client, "为什么要问这个", conversation_id="clarification-memory")
    assert "给李四转点钱" in str(calls[0][-1].content)
    assert state("clarification-memory")["context"]["pending_clarification"]["intent"] == "TRANSFER"
    monkeypatch.setattr(runtime, "llm", None)
    continued = _send(client, "300元", conversation_id="clarification-memory")
    assert continued["approval"] is not None and state("clarification-memory")["target"] == "李四"


@pytest.mark.parametrize("act,text,word", [("SOCIAL", "谢谢", "不客气"), ("UNSUPPORTED", "帮我申请信用卡", "没有接入")])
def test_non_task_reply_keeps_its_meaning_and_the_unfinished_slots(gateway, monkeypatch, act, text, word):
    client, _ = gateway
    _send(client, "给李四转点钱", conversation_id="non-task-detour")
    fixed_model(monkeypatch, "UNKNOWN", act, social_kind="thanks")
    reply = _send(client, text, conversation_id="non-task-detour")
    assert word in reply["reply"] and reply["approval"] is None
    assert state("non-task-detour")["context"]["pending_clarification"]["intent"] == "TRANSFER"
    monkeypatch.setattr(runtime, "llm", None)
    resumed = _send(client, "300元", conversation_id="non-task-detour")
    assert resumed["approval"] is not None and state("non-task-detour")["target"] == "李四"


def test_query_during_approval_answers_and_preserves_the_exact_operation(gateway):
    client, _ = gateway
    pending = _send(client, "给李四转300元", conversation_id="side-inquiry")
    original = deepcopy(state("side-inquiry"))
    before = financial_state()
    reply = _send(client, "查余额", conversation_id="side-inquiry")
    assert "15,000.00" in reply["reply"] and reply["approval"]["approval_id"] == pending["approval"]["approval_id"]
    assert reply["response"]["sections"][0]["type"] == "BALANCE"
    assert reply["result"]["side_inquiry"] is True
    assert state("side-inquiry") == original and financial_state() == before
    completed = approve(client, pending)
    assert completed["business_status"] == "SUCCEEDED" and main.ACCOUNTS["user_zhang"]["balance"] == 14700


def test_read_failure_during_approval_leaves_it_available(gateway, monkeypatch):
    client, _ = gateway
    pending = _send(client, "给李四转300元", conversation_id="side-error")
    before = financial_state()
    original_http = runtime._http
    def broken(method, path, **kwargs):
        if path.endswith("/balance"):
            raise RuntimeError("simulated read failure")
        return original_http(method, path, **kwargs)
    monkeypatch.setattr(runtime, "_http", broken)
    reply = _send(client, "查余额", conversation_id="side-error")
    assert "没有取得可核验" in reply["reply"]
    assert reply["approval"]["approval_id"] == pending["approval"]["approval_id"] and financial_state() == before


@pytest.mark.parametrize("resolution", ["approve", "reject", "expire"])
def test_retried_side_query_never_turns_into_the_original_writes_receipt(gateway, resolution):
    client, _ = gateway
    cid = "side-replay-" + resolution
    pending = _send(client, "给李四转300元", conversation_id=cid)
    query = _send(client, "查余额", conversation_id=cid, message_id="read-replay")
    if resolution == "expire":
        conversation.SERVICE._approvals[pending["approval"]["approval_id"]].expires_at = 0
    else:
        resolved = client.post(f"/api/v1/agent/approvals/{pending['approval']['approval_id']}", json={"decision": resolution}, headers=_headers())
        assert resolved.status_code == 200
    before = financial_state()
    replay = _send(client, "查余额", conversation_id=cid, message_id="read-replay")
    assert replay["approval"] is None and replay["business_status"] == "SUCCEEDED"
    assert replay["response"]["sections"][0]["type"] == "BALANCE"
    assert replay["reply"] in query["reply"] and "转账结算成功" not in replay["reply"]
    assert financial_state() == before


def test_a_new_write_during_approval_does_not_issue_another_nonce(gateway):
    client, _ = gateway
    pending = _send(client, "给李四转300元", conversation_id="side-no-write")
    before = financial_state()
    reply = _send(client, "给王美转50元", conversation_id="side-no-write")
    assert reply["approval"]["approval_id"] == pending["approval"]["approval_id"] and financial_state() == before


def test_verification_question_uses_this_approval_and_remembers_the_side_discussion(gateway, monkeypatch):
    client, _ = gateway
    pending = _send(client, "给李四转300元", conversation_id="verification-question")
    before = financial_state()
    calls = []
    fixed_model(monkeypatch, "BANKING_HELP", "EXPLAIN", slots={"help_topic": "security"}, calls=calls,
                choose=lambda p: ["pending.verification"])
    reply = _send(client, "这次还需要验证码吗", conversation_id="verification-question")
    assert "无需验证码" in reply["reply"] and reply["approval"]["approval_id"] == pending["approval"]["approval_id"]
    _send(client, "那设备签名呢", conversation_id="verification-question")
    assert any("这次还需要验证码吗" in str(call[-1].content) for call in calls)
    assert financial_state() == before


def test_unsupported_question_during_approval_is_answered_without_changing_it(gateway, monkeypatch):
    client, _ = gateway
    pending = _send(client, "给李四转300元", conversation_id="unsupported-side")
    before = financial_state()
    fixed_model(monkeypatch, "CARD_APPLY", "UNSUPPORTED")
    reply = _send(client, "帮我申请信用卡", conversation_id="unsupported-side")
    assert "能力目前没有接入" in reply["reply"] and reply["approval"]["approval_id"] == pending["approval"]["approval_id"]
    assert financial_state() == before


def test_transport_read_only_guard_is_scoped_and_blocks_writes(monkeypatch):
    calls = []
    monkeypatch.setattr(runtime.requests, "request", lambda *args, **kwargs: calls.append(args) or SimpleNamespace(status_code=200))
    with runtime.read_only_calls():
        runtime._http("GET", "accounts/user_zhang/balance", state={})
        with pytest.raises(RuntimeError):
            runtime._http("POST", "risk/precheck/transfer", state={})
    runtime._http("POST", "allowed-outside-side-inquiry", state={})
    assert [item[0] for item in calls] == ["GET", "POST"]


@pytest.mark.parametrize("plan", [
    {"fact_ids": ["invented"], "layout": "direct"},
    {"fact_ids": ["balance.available_balance"], "layout": "direct", "reply": "已冻结卡片"},
    {"fact_ids": ["balance.available_balance", "balance.available_balance"], "layout": "direct"},
    {"fact_ids": [], "layout": "direct"},
    {"fact_ids": ["balance.available_balance"], "layout": "arbitrary"},
])
def test_bad_answer_plan_cannot_forge_text_or_facts(monkeypatch, plan):
    monkeypatch.setattr(runtime, "llm", SimpleNamespace(invoke=lambda _: SimpleNamespace(content=json.dumps(plan))))
    current = {"user_input": "可用余额", "execution_result": {"type": "BALANCE", "data": {"total_balance": 100, "available_balance": 80, "locked_amount": 20}}}
    canonical = {"final_response": "核验的余额"}
    assert compose_answer(current, canonical) == canonical


def test_composer_cannot_omit_a_required_mock_sla_limitation(monkeypatch):
    fixed_model(monkeypatch, "DISPUTE_QUERY", "QUERY", choose=lambda p: ["fact.0"])
    current = {"user_input": "多久处理", "execution_result": {"type": "DISPUTE_QUERY"}}
    reply = compose_answer(current, {"final_response": "查到1张工单\n当前没有真实客服审核队列，未提供预计完成时间。"})
    assert "未提供预计完成时间" in reply["final_response"]
    current = {"user_input": "推荐产品", "execution_result": {"type": "WEALTH_RECOMMEND"}}
    reply = compose_answer(current, {"final_response": "匹配到产品\n> 推荐仅基于演示资料，不构成投资建议。"})
    assert "不构成投资建议" in reply["final_response"]


def test_invalid_core_result_never_reaches_the_answer_planner(monkeypatch):
    def forbidden(_):
        raise AssertionError("Unverified results cannot reach the answer planner")
    monkeypatch.setattr(runtime, "llm", SimpleNamespace(invoke=forbidden))
    reply = agent.responder_node({"user_id": "user_zhang", "execution_result": {"type": "BALANCE", "data": {"total_balance": -1}}})
    assert reply["business_status"] == "FAILED"
