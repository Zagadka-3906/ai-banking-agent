"""Online-chain regressions: understanding stays live, authorization stays separate."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

import main
import security
from bank_agent import runtime
from bank_agent.understanding import enforce_inquiry
from test_agent_gateway import gateway as _gateway_fixture, _send
from test_conversation_quality import fixed_model, state
from test_inquiry_boundaries import financial_state


@pytest.fixture
def gateway(monkeypatch):
    yield from _gateway_fixture.__wrapped__(monkeypatch)


@pytest.mark.parametrize("text", [
    "我想给李四转点钱，先帮我看看怎么操作",
    "我想给李四转一点钱，先看看怎么办理",
    "给李四转点钱，先说明下流程",
])
def test_plain_request_then_procedure_keeps_missing_amount_and_survives_topic_detour(gateway, text):
    client, _ = gateway
    before = financial_state()
    first = _send(client, text, conversation_id="procedure-slot")
    assert first["approval"] is None and "金额" in first["reply"]
    assert state("procedure-slot")["context"]["pending_clarification"]["intent"] == "TRANSFER"
    answer = _send(client, "先别管转账金额，我想问生日联动那个功能怎么弄", conversation_id="procedure-slot")
    assert answer["intent"] == "BANKING_HELP" and "生日" in answer["reply"]
    assert financial_state() == before
    pending = _send(client, "300元", conversation_id="procedure-slot")
    assert pending["approval"] and state("procedure-slot")["amount"] == 300
    assert state("procedure-slot")["target"] == "李四"
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000


@pytest.mark.parametrize("text", [
    "生日联动先放一边，给李四那笔转账金额补上300元，暂不确认",
    "那先把给李四的转账金额补上，300元，暂不确认",
])
def test_referential_amount_supplement_cannot_replace_the_original_recipient(gateway, text):
    client, _ = gateway
    _send(client, "给李四转点钱", conversation_id="bound-payee")
    _send(client, "生日联动怎么做", conversation_id="bound-payee")
    reply = _send(client, text, conversation_id="bound-payee")
    assert reply["approval"] and state("bound-payee")["target"] == "李四"
    assert state("bound-payee")["amount"] == 300 and main.ACCOUNTS["user_zhang"]["balance"] == 15000


@pytest.mark.parametrize("text", ["给李四转300元，帮我看看怎么办理", "我想给李四转300元，先说明下流程"])
def test_procedure_qualifier_is_not_an_unnamed_second_operation(gateway, text):
    client, _ = gateway
    reply = _send(client, text, conversation_id="one-proposal")
    assert reply["approval"] and state("one-proposal")["target"] == "李四"
    assert state("one-proposal")["amount"] == 300 and main.ACCOUNTS["user_zhang"]["balance"] == 15000


def test_a_different_name_containing_the_previous_name_is_not_a_task_reference(gateway):
    client, _ = gateway
    _send(client, "给李四转点钱", conversation_id="partial-name")
    before = financial_state()
    reply = _send(client, "给王李四那笔转账金额补上300元", conversation_id="partial-name")
    assert reply["approval"] is None and financial_state() == before


@pytest.mark.parametrize("text", [
    "如何给李四转100元", "怎么给李四转100元", "如果余额够，就给李四转100元",
    "不要给李四转100元", "解释这句话：给李四转100元", "假设我给李四转100元，有什么影响",
    "转账多久能到账", "冻结卡片后还能收款吗", "解冻需要去柜台办理吗",
])
def test_non_action_language_is_understood_but_cannot_obtain_approval_with_a_wrong_model(gateway, monkeypatch, text):
    client, _ = gateway
    calls = []

    def wrong(messages):
        calls.append(messages)
        return SimpleNamespace(content=json.dumps({"intent": "TRANSFER", "dialogue_act": "ACTION", "target": "李四", "amount": 100, "confidence": .99}))

    monkeypatch.setattr(runtime, "llm", SimpleNamespace(invoke=wrong))
    before = financial_state()
    reply = _send(client, text, conversation_id="non-action-understood")
    assert calls, "A lexical rule must not bypass understanding"
    assert reply["approval"] is None and financial_state() == before
    assert reply["intent"] == "BANKING_HELP"
    assert "否定、引用或假设" not in reply["reply"]
    if text.startswith(("不要", "如果", "假设", "解释这句话")):
        events = [row for row in security.get_audit_logs(limit=1000) if row.get("request_id") == reply["request_id"]]
        assert any(row["action"] == "INTENT_WITHHELD" and row.get("decision") == "READ_ONLY" for row in events)


@pytest.mark.parametrize("text,intent", [("如果把卡片日额度设成10000", "CARD_LIMIT"), ("假设开通境外交易", "CARD_CHANNEL")])
def test_conditionals_limit_every_workflow_not_only_transfer_keywords(gateway, monkeypatch, text, intent):
    client, _ = gateway
    fixed_model(monkeypatch, intent, "ACTION", amount=10000, slots={"card_channel": "OVERSEAS", "channel_enabled": True})
    before = financial_state()
    reply = _send(client, text, conversation_id="conditional-workflow")
    assert reply["approval"] is None and financial_state() == before


@pytest.mark.parametrize("text", ["10000", "10000元", "不能取消限额的话，我就把这张卡的每日限额改成10000元吧，先不确认"])
def test_limit_value_uses_typed_context_even_if_model_would_repeat_old_explanation(gateway, monkeypatch, text):
    client, _ = gateway
    _send(client, "能取消卡片限额吗", conversation_id="typed-limit")
    assert state("typed-limit")["dialogue_state"]["next_input"] == {"intent": "CARD_LIMIT", "field": "amount"}
    fixed_model(monkeypatch, "BANKING_HELP", "EXPLAIN", slots={"help_topic": "limits"})
    cards = deepcopy(main.CARDS)
    reply = _send(client, text, conversation_id="typed-limit")
    assert reply["approval"] and reply["approval"]["risk_level"] == "RED"
    assert state("typed-limit")["amount"] == 10000
    assert main.CARDS == cards


@pytest.mark.parametrize("text", ["0", "-100", "10000或者20000", "CARD-10000", "改成10000需要怎么验证"])
def test_typed_value_does_not_turn_invalid_numbers_ids_or_questions_into_adjustments(gateway, text):
    client, _ = gateway
    _send(client, "能取消卡片限额吗", conversation_id="invalid-typed-limit")
    cards, tokens = deepcopy(main.CARDS), deepcopy(security.AUTH_TOKENS)
    reply = _send(client, text, conversation_id="invalid-typed-limit")
    assert reply["approval"] is None and main.CARDS == cards and security.AUTH_TOKENS == tokens


def test_new_topic_clears_typed_value_question_and_another_principal_cannot_inherit_it(gateway):
    client, _ = gateway
    _send(client, "能取消卡片限额吗", conversation_id="limit-expiry")
    reply = _send(client, "10000", conversation_id="limit-expiry", principal="user_li")
    assert reply["approval"] is None
    _send(client, "生日联动怎么做", conversation_id="limit-expiry")
    assert state("limit-expiry")["dialogue_state"].get("next_input") is None
    assert _send(client, "10000", conversation_id="limit-expiry")["approval"] is None


def test_typed_limit_preserves_the_users_literal_card_instead_of_selecting_primary(gateway):
    client, _ = gateway
    main.CARDS["user_zhang"].append({**main.CARDS["user_zhang"][0], "card_id": "CARD-EXTRA"})
    _send(client, "CARD-EXTRA能取消限额吗", conversation_id="typed-card-owner")
    assert state("typed-card-owner")["dialogue_state"]["next_input"]["target"] == "CARD-EXTRA"
    proposal = _send(client, "10000", conversation_id="typed-card-owner")
    assert proposal["approval"] and state("typed-card-owner")["target"] == "CARD-EXTRA"
    assert all(card["daily_limit"] == 5000 for card in main.CARDS["user_zhang"])


def test_uncertain_card_reference_cannot_be_replaced_with_primary_by_a_wrong_model(gateway, monkeypatch):
    client, _ = gateway
    _send(client, "尾号7788的卡能取消限额吗", conversation_id="typed-card-tail")
    assert state("typed-card-tail")["dialogue_state"].get("next_input") is None
    fixed_model(monkeypatch, "CARD_LIMIT", "SLOT_REPLY", amount=10000)
    cards, tokens = deepcopy(main.CARDS), deepcopy(security.AUTH_TOKENS)
    reply = _send(client, "10000", conversation_id="typed-card-tail")
    assert reply["approval"] is None and main.CARDS == cards and security.AUTH_TOKENS == tokens


@pytest.mark.parametrize("invalid_model", [False, True])
def test_focused_balance_is_preserved_when_generation_is_unavailable_or_invalid(gateway, monkeypatch, invalid_model):
    client, _ = gateway
    monkeypatch.setattr(runtime, "llm", SimpleNamespace(invoke=lambda _: SimpleNamespace(content="{}")) if invalid_model else None)
    reply = _send(client, "只告诉我现在可用多少钱", conversation_id="focused-safe-fallback")
    assert "15,000.00" in reply["reply"] and "可用" in reply["reply"]
    assert "总余额" not in reply["reply"] and "锁定" not in reply["reply"]
    assert reply["result"]["answer_presentation"]["mode"] == "CANONICAL"


def test_scope_refusal_explains_domain_without_pretending_a_novel_is_a_bank_query(gateway, monkeypatch):
    client, _ = gateway
    fixed_model(monkeypatch, "UNKNOWN", "UNSUPPORTED", scope="OUT_OF_SCOPE")
    before = financial_state()
    reply = _send(client, "帮我写一篇科幻小说", conversation_id="scope-refusal")
    assert "非银行" in reply["reply"] and "银行业务" in reply["reply"]
    assert "无法查询或办理" not in reply["reply"] and financial_state() == before


def test_rule_general_topic_does_not_erase_a_specific_model_understanding():
    parsed = {"intent": "BANKING_HELP", "slots": {"help_topic": "dispute"}, "parse_meta": {"dialogue_act": "EXPLAIN"}}
    assert enforce_inquiry(parsed, "这笔消费的原因是什么")["slots"]["help_topic"] == "dispute"


def test_question_focus_chooses_verification_evidence_over_an_incidental_card_noun(gateway, monkeypatch):
    client, _ = gateway
    fixed_model(monkeypatch, "BANKING_HELP", "EXPLAIN", slots={"help_topic": "security"})
    reply = _send(client, "我刚调过卡片额度，只转100元为什么也要强验证", conversation_id="question-focus")
    assert reply["approval"] is None
    data = reply["result"]["execution_result"]["data"]
    assert data["topic"] == "security"
    assert "App内解冻步骤" not in reply["reply"]


def test_a_conversational_intro_is_not_an_unnamed_second_business(gateway, monkeypatch):
    client, _ = gateway
    fixed_model(monkeypatch, "DISPUTE_QUERY", "QUERY")
    before = financial_state()
    reply = _send(client, "那我想问一下，争议工单的进度一般要怎么查", conversation_id="single-semantic-read")
    assert reply["approval"] is None and reply["intent"] == "DISPUTE_QUERY"
    assert "没有已登记" in reply["reply"] and "多项业务" not in reply["reply"]
    assert financial_state() == before


def test_transaction_verification_has_specific_knowledge_and_current_record_evidence(gateway):
    client, _ = gateway
    before = financial_state()
    reply = _send(client, "这笔境外未知商户的999元交易，我要怎么核对是不是我本人消费的", conversation_id="check-transaction")
    assert reply["approval"] is None and reply["intent"] == "BANKING_HELP"
    docs = reply["result"]["execution_result"]["data"]["documents"]
    assert any(row["id"] == "guide.dispute.check_transaction" for row in docs)
    assert any(row["id"].startswith("read.transactions.") and "TX1004" in row["text"] for row in docs)
    assert "订单" in reply["reply"] and financial_state() == before


@pytest.mark.parametrize("text,expected", [("金额补上,300元", 300), ("给李四转1,000元", 1000), ("给李四转100元,200元", None)])
def test_normalized_clause_commas_cannot_hide_an_amount_or_multiple_amounts(text, expected):
    from bank_agent.extraction import amount_from_text
    assert amount_from_text(text) == expected
