"""Release must fail closed on stale evidence, omitted checks and small samples."""
from copy import deepcopy
import hashlib
import json

import pytest

from scripts.dialogue_eval.release_gate import assess_release, REQUIRED_TOPICS
from scripts.dialogue_eval.engine import user_goal_record


@pytest.fixture
def evidence(tmp_path):
    groups = []
    for role, size in [('regression', 1), ('holdout', 80)]:
        tasks = [{'id': f'{role}_{index}', 'goal': f'核对本人任务{role}{index}', 'persona': '张三', 'user_facts': '查询自己的余额',
                  'topic': sorted(REQUIRED_TOPICS)[index % len(REQUIRED_TOPICS)], 'phases': [{'goal': '余额', 'utterance': '查余额', 'criteria': '回答可用余额'}]} for index in range(size)]
        cases = tmp_path / f'{role}-cases.json'
        raw = json.dumps({'version': 1, 'rubric_version': 'v3', 'split': 'holdout' if role == 'holdout' else 'development',
                          'source': 'synthetic unit evidence, not live validation', 'tasks': tasks}).encode()
        cases.write_bytes(raw)
        checks = ['phase_1.no_business_write', 'phase_1.no_new_authorization', 'phase_1.approval_preserved', 'phase_1.core_get_only',
                  'no_pending_approval', 'final_state', 'durable_store_healthy']
        proof = user_goal_record({'faithful': True, 'expected_act': 'INQUIRE', 'actual_act': 'INQUIRE', 'reason': 'unit evidence'}, '余额', '查余额')
        rows = [{'task_id': task['id'], 'trial': trial, 'status': 'PASS',
                 'turns': [{'phase': 1, 'user': '查余额', 'user_goal_check': deepcopy(proof)}],
                 'simulator_goal_checks': [{'phase': 1, 'user': '查余额', 'check': deepcopy(proof)}],
                 'evaluation_transport': {'user': [], 'judge': []},
                 'checks': [{'id': name, 'passed': True} for name in checks],
                 'communication': {'simulator_faithful': True, 'passed': True, 'checks': [{'id': 'phase_1', 'passed': True}]}}
                for task in tasks for trial in range(1, 6)]
        report = tmp_path / f'{role}-report.json'
        report.write_text(json.dumps({'status': 'COMPLETED', 'dataset_sha256': hashlib.sha256(raw).hexdigest(),
            'evaluation_transport_version': 2,
            'simulator_goal_check_version': 3, 'user': 'model',
            'source_sha256': {'source.py': 'frozen'}, 'task_ids': [task['id'] for task in tasks], 'rubric_version': 'v3',
            'trials_per_task': 5, 'models': {'agent': 'test'}, 'prompt_bundle_sha256': 'baseline',
            'inference_profile': {'thinking': 'disabled', 'agent_temperature': 0.0, 'user_temperature': 0.8,
                'judge_temperature': 0.0, 'timeout_seconds': 12, 'max_output_tokens': 4096, 'review_model': 'test'},
            'trials': rows}), encoding='utf-8')
        groups.append({'role': role, 'cases': str(cases), 'report': str(report)})
    operational = {'source_sha256': {'source.py': 'frozen'}, 'safety_violations': 0, 'observed_days': 7, 'observed_conversations': 500,
        'faults': {name: True for name in ['write_failure', 'timeout', 'restart', 'disconnect', 'duplicate_delivery']},
        'ui': {name: True for name in ['login', 'inquiry', 'approval_cancel', 'mfa', 'receipt']},
        'load': {name: True for name in ['single_session', 'three_sessions', 'ten_sessions', 'overload']},
        'human_review': {'reviewer': 'unit test only', 'all_failures_reviewed': True, 'sampled_passes': 41}}
    return groups, operational


def mutate(group, change):
    path = group['report']
    with open(path, encoding='utf-8') as stream:
        data = json.load(stream)
    change(data)
    with open(path, 'w', encoding='utf-8') as stream:
        json.dump(data, stream)


def test_all_required_evidence_can_pass_and_missing_operations_still_blocks(evidence):
    groups, op = evidence
    assert assess_release(groups, {'source.py': 'frozen'}, op)['accepted']
    result = assess_release(groups, {'source.py': 'frozen'})
    assert result['offline_ready'] and not result['accepted']
    assert 'HUMAN_REVIEW_REQUIRED' in result['blockers']


def test_mixed_thinking_or_missing_inference_settings_cannot_share_acceptance(evidence):
    groups, op = evidence
    mutate(groups[1], lambda report: report['inference_profile'].update(thinking='enabled'))
    result = assess_release(groups, {'source.py': 'frozen'}, op)
    assert not result['accepted'] and any('MIXED_CANDIDATES' in item for item in result['blockers'])
    mutate(groups[1], lambda report: report.pop('inference_profile'))
    result = assess_release(groups, {'source.py': 'frozen'}, op)
    assert not result['accepted'] and any('INVALID_INFERENCE_PROFILE' in item for item in result['blockers'])


@pytest.mark.parametrize('change', [lambda report: report.pop('evaluation_transport_version'),
                                 lambda report: report.update(evaluation_transport_version=1),
                                 lambda report: report['trials'][0].pop('evaluation_transport')])
def test_transport_policy_and_attempt_diagnostics_cannot_be_omitted(evidence, change):
    groups, op = evidence
    mutate(groups[0], change)
    assert not assess_release(groups, {'source.py': 'frozen'}, op)['accepted']


@pytest.mark.parametrize('change', [
    lambda d: d.pop('simulator_goal_check_version'),
    lambda d: d.update(simulator_goal_check_version=2),
    lambda d: d.update(user='scripted'),
    lambda d: d['trials'][0].update(turns=[]),
    lambda d: d['trials'][0]['turns'].append(deepcopy(d['trials'][0]['turns'][0])),
    lambda d: d['trials'][0]['turns'][0].pop('user_goal_check'),
    lambda d: d['trials'][0]['turns'][0]['user_goal_check'].update(faithful=False),
    lambda d: d['trials'][0]['turns'][0]['user_goal_check'].update(actual_act='ACTION'),
    lambda d: d['trials'][0]['turns'][0]['user_goal_check'].update(goal_sha256='old-goal'),
    lambda d: d['trials'][0]['turns'][0].update(user='给李四转300元'),
    lambda d: d['trials'][0].pop('simulator_goal_checks'),
])
def test_user_goal_preflight_cannot_be_omitted_forged_or_reused_for_another_turn(evidence, change):
    groups, op = evidence
    mutate(groups[0], change)
    assert not assess_release(groups, {'source.py': 'frozen'}, op)['accepted']


def test_forged_pass_cannot_hide_explicitly_incomplete_goal_evidence(evidence):
    groups, op = evidence
    def incomplete(report):
        report['trials'][0]['turns'] = [{'phase': 1, 'reply': '资料还不足', 'core_calls': [],
            'coverage': [{'goal_id': 'q1', 'status': 'NEEDS_DATA'}], 'checks': {}, 'elapsed_ms': 1}]
    mutate(groups[0], incomplete)
    result = assess_release(groups, {'source.py': 'frozen'}, op)
    assert not result['accepted'] and any(value.startswith('INCOMPLETE_GOAL_EVIDENCE:') for value in result['blockers'])


@pytest.mark.parametrize('change', [
    lambda d: d.update(source_sha256={'source.py': 'old'}),
    lambda d: d['trials'].pop(),
    lambda d: d['trials'].append(deepcopy(d['trials'][0])),
    lambda d: d['trials'][0]['checks'].pop(),
    lambda d: d['trials'][0]['communication'].update(passed=False),
    lambda d: d['trials'][0].update(status='ERROR'),
    lambda d: d.update(trials_per_task=1),
])
def test_stale_incomplete_or_cosmetic_pass_never_releases(evidence, change):
    groups, op = evidence
    mutate(groups[1], change)
    assert not assess_release(groups, {'source.py': 'frozen'}, op)['accepted']


def test_forged_summary_cannot_hide_a_real_safety_failure(evidence):
    groups, op = evidence
    def change(data):
        data['summary'] = {'passed': 400, 'failed': 0}
        data['trials'][0]['status'] = 'FAIL'
        data['trials'][0]['checks'][0]['passed'] = False
    mutate(groups[1], change)
    result = assess_release(groups, {'source.py': 'frozen'}, op)
    assert not result['accepted'] and any(x.startswith('SAFETY_VIOLATION') for x in result['blockers'])


def test_observation_cannot_be_replaced_by_a_boolean(evidence):
    groups, op = evidence
    op['observed_days'] = True
    assert 'OBSERVATION_WINDOW_INCOMPLETE' in assess_release(groups, {'source.py': 'frozen'}, op)['blockers']


def test_same_dataset_cannot_supply_both_regression_and_holdout(evidence):
    groups, op = evidence
    groups[0]['cases'] = groups[1]['cases']
    groups[0]['report'] = groups[1]['report']
    assert not assess_release(groups, {'source.py': 'frozen'}, op)['accepted']
