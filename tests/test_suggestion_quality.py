"""Next-turn recommendations must help the current conversation, not invent transactions."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

import main
import security
from bank_agent import runtime
from bank_agent import suggestions
from test_agent_gateway import _send, gateway  # noqa: F401
from test_model_suggestions import _fake_model, _suggest


def state(**updates):
    return {"user_id": "user_zhang", "memory_principal": "user_zhang", "user_input": "帮我核对可疑交易",
            "final_response": "这里只能核对记录，不能认定盗刷。", "intent": "BILL_QUERY", "business_status": "SUCCEEDED",
            "parse_meta": {"dialogue_act": "QUERY"}, **updates}


@pytest.mark.parametrize("text", ["冻结关联卡片", "为这笔交易建争议", "给李四转300元", "申购LX00118 500元", "取消Apple订阅", "撤销这笔预约"])
def test_inquiries_do_not_offer_unsolicited_writes_even_if_a_model_approves(text):
    current = state(final_response="可核对300元，李四和产品LX00118，示例500元，Apple订阅。")
    assert not suggestions._allowed(text, suggestions._facts(current))


@pytest.mark.parametrize("text", ["5000元", "就5000元吧", "给李四转5000元", "五千元", "5000"])
def test_missing_amount_cannot_be_chosen_from_an_account_balance(text):
    current = state(user_input="给李四转点钱", final_response="可用余额5000元，请补充金额。", intent="TRANSFER",
                    parse_meta={"dialogue_act": "ACTION"}, context={"pending_clarification": {
                        "intent": "TRANSFER", "missing": ["amount"], "payload": {"target": "李四"}}})
    assert not suggestions._allowed(text, suggestions._facts(current))


@pytest.mark.parametrize("text", ["转给李四2500元", "转给李四0.25万元", "转给手机号13800138000"])
def test_number_substrings_and_different_units_cannot_invent_values(text):
    current = state(user_input="我要转账12500元", final_response="可以选李四，联系电话138001380001。", intent="TRANSFER",
                    parse_meta={"dialogue_act": "ACTION"}, context={"pending_clarification": {
                        "intent": "TRANSFER", "missing": ["target"], "payload": {"amount": 12500}}})
    assert not suggestions._allowed(text, suggestions._facts(current))


def test_previous_questions_are_not_recommended_again():
    current = state(dialogue_history=[{"user": "争议工单多久有结果？", "assistant": "没有预计时间。"}])
    assert not suggestions._allowed("争议工单多久有结果", suggestions._facts(current))


@pytest.mark.parametrize("text", ["可疑交易大概在10月25日", "我确认好金额再继续", "查CARD-999的状态", "查11月30日的可疑交易"])
def test_recommendations_cannot_assert_user_facts_or_invent_identifiers(text):
    assert not suggestions._allowed(text, suggestions._facts(state()))


def test_multi_turn_context_is_scoped_and_credentials_are_redacted():
    current = state(user_input="验证码888888，为什么要验证", dialogue_history=[
        {"user": "我的生日提醒呢", "assistant": "密码:Bank@2026，请勿提供。"}],
        dialogue_state={"principal": "user_zhang", "active_topic": "cross_scene", "tasks": []})
    facts = suggestions._facts(current)
    encoded = json.dumps(facts, ensure_ascii=False)
    assert "888888" not in encoded and "Bank@2026" not in encoded
    assert facts["active_topic"] == "cross_scene" and facts["recent_turns"]
    other = suggestions._facts({**current, "user_id": "user_li"})
    assert other["recent_turns"] == [] and other["active_topic"] == "general"


def test_verification_procedure_is_allowed_but_requesting_a_secret_is_not():
    facts = suggestions._facts(state())
    assert suggestions._allowed("为什么需要验证码", facts)
    assert not suggestions._allowed("提供验证码888888", facts)


def test_bare_payee_choices_must_be_explicitly_offered_for_the_current_slot():
    current = state(user_input="我要转账300元", final_response="请选择收款人，例如：李四。", intent="TRANSFER",
                    parse_meta={"dialogue_act": "ACTION"}, context={"pending_clarification": {
                        "intent": "TRANSFER", "missing": ["target"], "payload": {"amount": 300}}})
    assert suggestions._allowed("李四", suggestions._facts(current))
    assert not suggestions._allowed("张大千", suggestions._facts(current))


def test_independent_review_rejects_paraphrases_and_topic_jumps(monkeypatch):
    _fake_model(monkeypatch, [])
    candidates = ["这张卡怎么解冻", "查看我的理财产品", "查看本月账单"]
    rows = [{"index": 0, "relevant": True, "not_answered": False, "supported": True, "safe_next": True},
            {"index": 1, "relevant": False, "not_answered": True, "supported": True, "safe_next": True},
            {"index": 2, "relevant": True, "not_answered": True, "supported": True, "safe_next": True}]
    for row in rows:
        row.update(answerable=True, support_id='transactions', rule_ids=['guide.dispute.check_transaction'])
    monkeypatch.setattr(suggestions, "invoke_model", lambda *args: SimpleNamespace(content=json.dumps({"reviews": rows})))
    assert suggestions._review(candidates, suggestions._facts(state())) == [candidates[2]]


@pytest.mark.parametrize("index,flag", [(True, True), (10, True), (0, "true"), (0, 1), (0, False)])
def test_invalid_review_indices_and_non_boolean_votes_are_not_accepted(monkeypatch, index, flag):
    row = {"index": index, "relevant": flag, "not_answered": True, "supported": True, "safe_next": True,
           'answerable': True, 'support_id': 'transactions', 'rule_ids': ['guide.dispute.check_transaction']}
    monkeypatch.setattr(suggestions, "invoke_model", lambda *args: SimpleNamespace(content=json.dumps({"reviews": [row]})))
    assert suggestions._review(["查看本月账单"], suggestions._facts(state())) == []


def test_review_failure_does_not_release_unreviewed_model_chips(monkeypatch):
    monkeypatch.setattr(runtime, "llm", object())
    calls = []

    def invoke(model, messages):
        calls.append(messages)
        return SimpleNamespace(content='["查看本月账单"]' if len(calls) == 1 else '{"reviews":"invalid"}')

    monkeypatch.setattr(suggestions, "invoke_model", invoke)
    assert suggestions.followup_suggestions(state()) == ([], "none")
    assert len(calls) == 2


def test_explicit_examples_use_the_same_amount_policy():
    current = state(user_input="我想转账", final_response="请补充金额，例如：500元。", intent="TRANSFER",
                    context={"pending_clarification": {"intent": "TRANSFER", "missing": ["amount"], "payload": {}}})
    assert suggestions.instant_suggestions(current) == []


def test_suggestion_generation_cannot_create_authorization_or_business_changes(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    _send(client, "争议工单多久有结果", conversation_id="quality-query")
    before = deepcopy((main.ACCOUNTS, main.CARDS, main.TRANSACTIONS, main.DISPUTE_CASES, security.AUTH_TOKENS))
    _fake_model(monkeypatch, ["冻结关联卡片", "查看已有争议工单"])
    assert _suggest(client, "quality-query")["suggestions"] == ["查看已有争议工单"]
    assert before == (main.ACCOUNTS, main.CARDS, main.TRANSACTIONS, main.DISPUTE_CASES, security.AUTH_TOKENS)
