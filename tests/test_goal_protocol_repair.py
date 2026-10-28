"""Repair a read protocol without silently switching to legacy or to an action."""
import json
from types import SimpleNamespace

import pytest

from bank_agent import runtime
from bank_agent.parsing import model_parse
from bank_agent.nodes.risk import risk_checker_node
from bank_planning.requests import RESOURCES, normalize_goals, request_goals
from bank_agent.evidence import ROW_FIELDS
from bank_agent.consultation import read_evidence
from test_agent_gateway import gateway, _send  # noqa: F401
from test_inquiry_boundaries import financial_state


INVALID = {'act': 'EXPLAIN', 'resource': 'general', 'fields': ['available_balance', 'amount'],
           'question': '消费合计为何不同于余额', 'topic': 'general'}
VALID = {**INVALID, 'fields': [], 'requires_record': False}


def responses(monkeypatch, repaired):
    calls = []
    def invoke(messages):
        calls.append(messages)
        result = ({'intent': 'BANKING_HELP', 'dialogue_act': 'EXPLAIN', 'request_protocol': 1, 'goals': [INVALID]}
                  if len(calls) == 1 else {'goals': [repaired]})
        return SimpleNamespace(content=json.dumps(result, ensure_ascii=False))
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=invoke))
    return calls


def test_wrong_resource_fields_are_repaired_once_without_losing_question(monkeypatch):
    calls = responses(monkeypatch, VALID)
    result = model_parse('本月消费合计47.90元为什么不等于现在可用余额15000元？')
    assert len(calls) == 2 and result['parse_meta']['goal_repair'] == 'REPAIRED'
    goals = request_goals(result)
    assert len(goals) == 1 and goals[0]['act'] == 'EXPLAIN' and goals[0]['fields'] == []
    assert result['intent'] == 'BANKING_HELP' and not result['parse_meta'].get('goal_error')


@pytest.mark.parametrize('repaired', [INVALID, {**VALID, 'act': 'ACTION'}, {**VALID, 'user_id': 'user_li'}])
def test_invalid_or_action_repair_cannot_change_a_read_into_a_proposal(monkeypatch, repaired):
    calls = responses(monkeypatch, repaired)
    result = model_parse('消费合计为什么不等于余额')
    assert len(calls) == 2
    assert result['parse_meta']['model_failure'] == 'MODEL_PROTOCOL_INVALID'
    assert result['parse_meta']['goal_repair'] == 'FAILED'


def test_bad_modern_plan_returns_an_explicit_failure_without_authorization(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    responses(monkeypatch, INVALID)
    before = financial_state()
    reply = _send(client, '消费合计为什么不等于余额', conversation_id='protocol-failure')
    assert reply['business_status'] == 'FAILED' and '未形成有效的查询计划' in reply['reply']
    assert reply['approval'] is None and financial_state() == before


def test_risk_node_independently_blocks_an_unreliable_model_parse(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No authorization call for an invalid understanding')
    monkeypatch.setattr(runtime, '_http', forbidden)
    result = risk_checker_node({'user_id': 'user_zhang', 'user_input': '给李四转300元', 'intent': 'TRANSFER',
                                'parse_meta': {'model_failure': 'MODEL_PROTOCOL_INVALID'}})
    assert result['business_status'] == 'FAILED' and result['auth_token'] is None


def test_projected_fields_are_all_available_to_the_read_protocol():
    for resource, fields in ROW_FIELDS.items():
        assert set(fields) <= set(RESOURCES[resource]['fields']), resource


def test_transaction_card_query_projects_only_the_owned_selected_receipt(gateway):  # noqa: F811
    import main

    main.TRANSACTIONS.extend([
        {'account_id': 'user_zhang', 'tx_id': 'TX-OWN-CARD', 'card_id': 'CARD-OWN', 'amount': 7},
        {'account_id': 'user_li', 'tx_id': 'TX-OTHER-CARD', 'card_id': 'CARD-OTHER', 'amount': 8},
    ])
    question = '查TX-OWN-CARD关联哪张卡'
    goals = normalize_goals([{'act': 'QUERY', 'resource': 'transactions', 'question': question,
                             'fields': ['card_id'], 'subject': 'TX-OWN-CARD'}], question, {})
    before = financial_state()
    documents = read_evidence({'user_id': 'user_zhang', 'user_input': question},
                              {'tool': 'transactions', 'subjects': goals[0]['subjects'], 'fields': goals[0]['fields']})
    encoded = json.dumps(documents)
    assert 'CARD-OWN' in encoded and 'CARD-OTHER' not in encoded and 'TX-OTHER-CARD' not in encoded
    assert any('card_id' in row['fields'] for row in documents)
    assert financial_state() == before
