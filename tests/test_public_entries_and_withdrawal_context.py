"""Read-only entry contracts and present decisions with unrelated past context."""
import json
from types import SimpleNamespace

import pytest

from bank_agent import runtime
from bank_agent.parsing import model_parse
from intent_language import withdrawal_statement
from test_agent_gateway import _send, gateway  # noqa: F401
from test_general_dialogue_loop import goal, model
from test_inquiry_boundaries import financial_state


@pytest.mark.parametrize('act,intent', [('UNCLEAR', 'UNKNOWN'), ('ACTION', 'WEALTH_PURCHASE'), ('SOCIAL', 'UNKNOWN')])
@pytest.mark.parametrize('text', ['理财', '投资理财', '理财。'])
def test_exact_catalog_entry_cannot_become_clarification_or_purchase(monkeypatch, act, intent, text):
    calls = []
    def invoke(messages):
        calls.append(messages)
        return SimpleNamespace(content=json.dumps({'intent': intent, 'dialogue_act': act,
            'target': 'LX00118', 'amount': 999, 'goals': [goal('general', [], '错误推测', act='UNCLEAR')]}))
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=invoke))
    parsed = model_parse(text)
    assert len(calls) == 1 and parsed['intent'] == 'WEALTH_QUERY'
    assert parsed['amount'] is None and parsed['target'] is None
    assert parsed['parse_meta']['dialogue_act'] == 'QUERY'
    assert parsed['parse_meta']['goals'][0]['resource'] == 'wealth_products'
    assert parsed['parse_meta']['goals'][0]['requires_record'] is True
    assert not parsed.get('scheduled_at') and not parsed['parse_meta'].get('task_command')


def test_free_form_wealth_explanation_keeps_model_semantics(monkeypatch):
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=lambda messages: SimpleNamespace(content=json.dumps({
        'intent': 'BANKING_HELP', 'dialogue_act': 'EXPLAIN', 'goals': [goal('general', [], '理财的风险是什么', act='EXPLAIN')]}))))
    parsed = model_parse('理财的风险是什么')
    assert parsed['intent'] == 'BANKING_HELP' and parsed['parse_meta']['goals'][0]['act'] == 'EXPLAIN'


def test_catalog_entry_reaches_the_real_shelf_without_questionnaire_or_authorization(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    model(monkeypatch, [goal('general', [], '请明确理财诉求', act='UNCLEAR')], intent='UNKNOWN')
    before = financial_state()
    reply = _send(client, '理财', conversation_id='public-entry-contract')
    assert reply['intent'] == 'WEALTH_QUERY' and reply['approval'] is None
    assert reply['business_status'] == 'SUCCEEDED'
    shelf = reply['result']['execution_result']['data']['data']
    assert {'LX00118', 'HQ30060', 'SG20270', 'YH50365', 'XH70730'} == {row['product_id'] for row in shelf}
    assert financial_state() == before


@pytest.mark.parametrize('text', [
    '我已经了解清楚了，决定不冻结卡片了。', '我已经想清楚了，先不转账了。',
    '事情已经说清楚了，放弃这次申购意向。',
])
def test_present_withdrawal_is_not_blocked_by_a_separate_non_business_past_clause(text):
    assert withdrawal_statement(text, pending_discussion=True)


@pytest.mark.parametrize('text', [
    '我已经冻结卡片了，现在不冻结了', '我曾经取消过转账，取消这个意向',
    '这笔已经完成，取消这次转账意向', '我已经转账成功，先不转账了',
    '我已经了解清楚了，如果不冻结会怎样？', '我已经了解清楚了，不要取消这个意向',
    '我已经了解清楚了，“决定不冻结卡片了”是什么意思？',
])
def test_history_questions_quotes_and_negated_cancellation_stay_protected(text):
    assert not withdrawal_statement(text, pending_discussion=True)


def test_model_withdrawal_after_consultation_acknowledges_no_action_instead_of_reasking(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    def invoke(messages):
        return SimpleNamespace(content=json.dumps({'intent': 'UNKNOWN', 'dialogue_act': 'WITHDRAW',
                                                   'scope': 'IN_SCOPE', 'topic_relation': 'CONTINUE'}))
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=invoke))
    before = financial_state()
    reply = _send(client, '我已经了解清楚了，决定不冻结卡片了。', conversation_id='present-withdrawal')
    assert reply['approval'] is None and '请明确' not in reply['reply']
    assert '未' in reply['reply'] or '没有' in reply['reply']
    assert financial_state() == before
