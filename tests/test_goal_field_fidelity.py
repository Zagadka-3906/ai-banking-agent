"""Requirement review cannot use record gaps or change an execution request."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from bank_agent import runtime
from bank_agent.goal_fidelity import refine_goal_fields
from bank_agent.goal_answering import _validate_coverage
from bank_planning.requests import normalize_goals
from test_general_dialogue_loop import goal


def parsed(fields=None, *, calculation=False):
    item = goal('transactions', fields or ['amount', 'card_id', 'analysis'], '看看TX1004交易线索', subject='TX1004')
    if calculation:
        item['calculation'] = {'operation': 'sum', 'field': 'amount'}
    return {'intent': 'BILL_QUERY', 'parse_meta': {'goals': normalize_goals([item], 'TX1004', {})}}


def reviewer(monkeypatch, value):
    payloads = []
    def invoke(messages):
        payload = json.loads(messages[-1].content)
        payloads.append(payload)
        assert set(payload) == {'question', 'goals'} and 'facts' not in payload
        assert all('fields' not in item and 'question' not in item for item in payload['goals'])
        return SimpleNamespace(content=json.dumps(value, ensure_ascii=False))
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=invoke))
    monkeypatch.setattr(runtime, 'answer_reviewer', None)
    monkeypatch.setattr(runtime, '_http', lambda *a, **k: pytest.fail('Requirement review must precede Core reads'))
    return payloads


def test_broad_query_can_use_records_without_making_every_optional_column_required(monkeypatch):
    original = parsed()
    unchanged = deepcopy(original)
    payloads = reviewer(monkeypatch, {'goals': [{'goal_id': 'q1', 'fields': ['records', 'analysis']}]})
    result = refine_goal_fields(original, '看看TX1004交易线索')
    assert original == unchanged and len(payloads) == 1
    assert result['parse_meta']['goals'][0] == {**original['parse_meta']['goals'][0], 'fields': ['records', 'analysis']}
    audit = result['parse_meta']['goal_field_review']
    assert audit['status'] == 'REVIEWED' and audit['fields'][0]['original'] == ['amount', 'card_id', 'analysis']


def test_initial_paraphrase_cannot_bias_independent_requirements_or_expand_reads(monkeypatch):
    original = parsed()
    original['parse_meta']['goals'][0]['question'] = '必须查明TX1004的卡片编号'
    payloads = reviewer(monkeypatch, {'goals': [{'goal_id': 'q1', 'fields': ['records', 'merchant', 'analysis']}]})
    result = refine_goal_fields(original, '看看TX1004交易线索')
    assert payloads[0]['question'] == '看看TX1004交易线索'
    assert '必须查明' not in json.dumps(payloads, ensure_ascii=False)
    assert result['parse_meta']['goals'][0]['fields'] == ['records', 'analysis']
    assert result['parse_meta']['goals'][0]['subject'] == 'TX1004'


def test_explicit_card_question_keeps_its_required_field_and_strict_missing_evidence(monkeypatch):
    original = parsed(['card_id'])
    reviewer(monkeypatch, {'goals': [{'goal_id': 'q1', 'fields': ['card_id']}]})
    result = refine_goal_fields(original, 'TX1004是哪张卡扣的钱')
    with pytest.raises(ValueError, match='MISSING_GOAL_RECORD'):
        _validate_coverage([{'goal_id': 'q1', 'status': 'ANSWERED', 'evidence_ids': ['read.transactions.0'], 'reason': '已查'}],
                           result['parse_meta']['goals'], [{'id': 'read.transactions.0', 'fields': ['records'], 'subject': 'TX1004'}])


@pytest.mark.parametrize('value', [
    {'goals': []}, {'goals': [{'goal_id': 'q2', 'fields': ['records']}]},
    {'goals': [{'goal_id': 'q1', 'fields': ['records'], 'intent': 'TRANSFER'}]},
    {'goals': [{'goal_id': 'q1', 'fields': []}]}, {'goals': [{'goal_id': 'q1', 'fields': ['records', 'records']}]},
    {'goals': [{'goal_id': 'q1', 'fields': ['balance']}]},
    {'goals': [{'goal_id': 'q1', 'fields': ['merchant']}]},
    {'goals': [{'goal_id': 'q1', 'fields': ['records']}, {'goal_id': 'q1', 'fields': ['records']}]},
])
def test_invalid_review_cannot_change_original_question_or_expand_reads(monkeypatch, value):
    original = parsed()
    reviewer(monkeypatch, value)
    result = refine_goal_fields(original, '查询TX1004')
    assert result['parse_meta']['goals'] == original['parse_meta']['goals']
    assert result['parse_meta']['goal_field_review']['status'] == 'FAILED'


def test_calculation_operand_cannot_be_removed(monkeypatch):
    original = parsed(calculation=True)
    reviewer(monkeypatch, {'goals': [{'goal_id': 'q1', 'fields': ['records']}]})
    result = refine_goal_fields(original, '合计交易金额')
    assert result['parse_meta']['goals'] == original['parse_meta']['goals']


def test_rules_and_generic_records_do_not_need_an_extra_field_review(monkeypatch):
    calls = reviewer(monkeypatch, {})
    original = parsed(['records'])
    assert refine_goal_fields(original, '查询交易明细') == original and not calls
