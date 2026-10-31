"""Evaluator schema failures stay failures, with bounded non-sensitive evidence."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from scripts.dialogue_eval.models import ConversationJudge, valid_judge_schema_diagnostics
from scripts.dialogue_eval.schema import load_dataset


def valid():
    return {'checks': [{'id': 'phase_1', 'passed': False, 'reason': '没有回答本轮问题'}],
            'simulator_faithful': True, 'naturalness': 3}


def judge_for(value):
    calls = []
    def invoke(messages):
        calls.append(messages)
        return SimpleNamespace(content=json.dumps(value, ensure_ascii=False))
    return ConversationJudge(SimpleNamespace(invoke=invoke), 'v3'), calls


def assess(judge):
    dataset, _ = load_dataset(Path(__file__).parent/'data/dialogue_tasks_v3.json')
    task = next(task for task in dataset.tasks if task.id == 'finite_limit')
    task = task.model_copy(update={'phases': task.phases[:1]})
    evidence = {'phase_observations': [{'id': 'phase_1', 'trusted_evidence': {'unit': True},
                                      'hard_checks': {'no_business_write': True}}]}
    return judge.assess(task, [{'role': 'user', 'text': '能不能取消卡片限额'},
                              {'role': 'assistant', 'text': '请告诉我要办理什么。'}], evidence)


@pytest.mark.parametrize('change,code', [
    (lambda row: row['checks'][0].update(passed='true'), 'bool_type'),
    (lambda row: row.update(naturalness='4'), 'int_type'),
    (lambda row: row.update(naturalness=6), 'less_than_equal'),
    (lambda row: row['checks'][0].update(reason='x'*501), 'string_too_long'),
    (lambda row: row['checks'][0].update(reason=''), 'string_too_short'),
    (lambda row: row.pop('simulator_faithful'), 'missing'),
])
def test_phase_schema_errors_record_field_codes_and_never_retry_or_rescore(change, code):
    raw = valid()
    change(raw)
    judge, calls = judge_for(raw)
    with pytest.raises(ValidationError):
        assess(judge)
    assert len(calls) == judge.requests == 1 and judge.transport_failures == []
    assert len(judge.schema_failures) == 1 and valid_judge_schema_diagnostics(judge.schema_failures)
    row = judge.schema_failures[0]
    assert row['stage'] == 'PHASE_JUDGEMENT' and row['phase'] == 1 and row['retried'] is False
    assert any(item['type'] == code for item in row['errors'])


def test_unknown_field_names_inputs_and_context_never_enter_schema_diagnostics():
    raw = valid()
    raw['SECRET-UNTRUSTED-KEY'] = 'sk-private-provider-content'
    raw['checks'][0]['reason'] = 'SENSITIVE-CUSTOMER-DATA'*100
    judge, calls = judge_for(raw)
    with pytest.raises(ValidationError):
        assess(judge)
    encoded = json.dumps(judge.schema_failures)
    assert len(calls) == 1 and valid_judge_schema_diagnostics(judge.schema_failures)
    assert all(value not in encoded for value in ('SECRET', 'sk-private', 'SENSITIVE', 'input', 'ctx'))
    assert '<other>' in encoded and len(encoded) < 1500


def test_user_goal_schema_error_has_its_own_stage_and_does_not_call_the_bank_or_retry():
    judge, calls = judge_for({'faithful': True, 'expected_act': 'ACTION', 'actual_act': 'UNSUPPORTED-ACT', 'reason': 'x'})
    with pytest.raises(ValidationError):
        judge.verify_user_goal({'known_facts': '本人', 'current_goal': '只查询', 'conversation': []}, '查余额')
    assert len(calls) == judge.requests == 1 and judge.transport_failures == []
    assert valid_judge_schema_diagnostics(judge.schema_failures)
    assert judge.schema_failures[0]['stage'] == 'USER_GOAL' and judge.schema_failures[0]['phase'] is None


def test_valid_negative_judgement_is_unchanged_and_never_retried():
    raw = valid()
    judge, calls = judge_for(raw)
    assert assess(judge) == raw
    assert len(calls) == 1 and judge.schema_failures == judge.transport_failures == []


@pytest.mark.parametrize('change', [
    lambda row: row.update(retried=True), lambda row: row.update(request=True), lambda row: row.update(stage=[]),
    lambda row: row.update(phase=True), lambda row: row.update(response_sha256='not-a-digest'),
    lambda row: row.update(errors=[]), lambda row: row['errors'][0].update(path=['PRIVATE-FIELD']),
    lambda row: row['errors'][0].update(type='raw provider text'), lambda row: row.update(raw_response='x'),
])
def test_schema_diagnostics_validator_rejects_unbounded_or_untrusted_fields(change):
    row = {'request': 1, 'stage': 'PHASE_JUDGEMENT', 'phase': 1, 'errors': [{'path': ['checks'], 'type': 'missing'}],
           'response_sha256': '0'*64, 'retried': False}
    assert valid_judge_schema_diagnostics([row])
    change(row)
    assert not valid_judge_schema_diagnostics([row])


def test_schema_diagnostics_enforce_bounds_and_request_order():
    row = {'request': 1, 'stage': 'USER_GOAL', 'phase': None, 'errors': [{'path': ['reason'], 'type': 'missing'}],
           'response_sha256': '0'*64, 'retried': False}
    assert not valid_judge_schema_diagnostics([row, deepcopy(row)])
    assert not valid_judge_schema_diagnostics([row]*17)
    assert not valid_judge_schema_diagnostics(None)


def test_extra_key_only_verdict_is_degraded_with_audit_not_retried():
    """20261101 保留集：checks[0] 的未知键降级剥离并审计，试验不再终止。"""
    raw = valid()
    raw['checks'][0].update(simulator_faithful=True, extra_note='判官自带的额外说明')
    judge, calls = judge_for(raw)
    verdict = assess(judge)
    assert verdict['checks'][0]['passed'] is False  # 保留原始判定值，只剥离未知键
    assert any(all(item['type'] == 'extra_forbidden' for item in row['errors'])
               for row in judge.schema_failures)
    assert len(calls) == judge.requests == 1  # 降级不是重试：判官只被调用一次
