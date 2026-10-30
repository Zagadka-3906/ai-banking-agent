"""An observed read failure can explain uncertainty, never prove account state."""
import json
from types import SimpleNamespace

import pytest

from bank_agent import runtime
from bank_agent.evidence_session import EvidenceSession
from bank_agent.goal_answering import _validate_coverage
from bank_planning.requests import normalize_goals
from test_agent_gateway import _send, gateway  # noqa: F401
from test_general_dialogue_loop import goal, model
from test_inquiry_boundaries import financial_state


@pytest.mark.parametrize('resource', ['balance', 'transactions', 'cards', 'contacts', 'disputes',
                                     'wealth_products', 'wealth_holdings', 'risk_profile', 'tasks'])
def test_observed_failure_is_citable_but_never_satisfies_a_record_query(monkeypatch, resource):
    def fail(*args, **kwargs):
        raise RuntimeError('PRIVATE-CUSTOMER-AND-PROVIDER-ERROR')
    monkeypatch.setattr('bank_agent.consultation.read_evidence', fail)
    session = EvidenceSession({'user_id': 'user_zhang'})
    assert session.read({'tool': resource, 'fields': ['records']}) is False
    facts = session.facts()
    assert len(facts) == 1 and facts[0]['scope'] == 'read_outcome'
    assert '失败不代表没有' in facts[0]['text'] and not facts[0]['id'].startswith('read.')
    assert 'PRIVATE' not in json.dumps(facts) and 'RuntimeError' not in json.dumps(facts)
    coverage = [dict(goal_id='q1', status='ANSWERED', evidence_ids=[facts[0]['id']], reason='解释读取失败')]
    explanation = normalize_goals([goal(resource, [], '读取失败是否说明没有记录', act='EXPLAIN')], '', {})
    assert _validate_coverage(coverage, explanation, facts) == coverage
    fields = {'balance': ['available_balance'], 'risk_profile': ['risk_profile']}.get(resource, ['records'])
    query = normalize_goals([goal(resource, fields, '我的记录')], '', {})
    with pytest.raises(ValueError, match='MISSING_GOAL_RECORD'):
        _validate_coverage(coverage, query, facts)


def test_subject_binding_rejection_is_not_a_service_failure(monkeypatch):
    def unbound(*args, **kwargs):
        raise ValueError('UNBOUND_READ_SUBJECT')
    monkeypatch.setattr('bank_agent.consultation.read_evidence', unbound)
    session = EvidenceSession({'user_id': 'user_zhang'})
    assert session.read({'tool': 'tasks', 'fields': ['records']}) is False
    assert session.facts() == []


@pytest.mark.parametrize('tamper', ['text', 'source', 'fields', 'missing_failure'])
def test_responder_reconstructs_failure_evidence_and_rejects_tampered_claims(tamper):
    from bank_agent.nodes.responder import _respond
    state = {'user_id': 'user_zhang', 'intent': 'BANKING_HELP'}
    session = EvidenceSession(state)
    session.failures = [{'tool': 'tasks', 'reason': 'RuntimeError'}]
    result = session.result()
    assert '读取失败不代表' in _respond({**state, 'execution_result': result})['final_response']
    if tamper == 'missing_failure':
        result['data']['read_budget']['failures'] = []
    else:
        result['data']['documents'][0][tamper] = {'text': '当前没有任务。', 'source': 'core://tasks',
                                                  'fields': ['records']}[tamper]
        result['data']['facts'][0] = result['data']['documents'][0]['text']
    assert _respond({**state, 'execution_result': result})['business_status'] == 'FAILED'


def test_failure_is_deduplicated_and_successful_retry_keeps_its_own_record_evidence(monkeypatch):
    attempts = []
    def read(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError('failed')
        return [dict(id='read.tasks.0', source='core://tasks', text='当前待办任务为空。',
                     fields=['records'], collection_size=0, complete=True)]
    monkeypatch.setattr('bank_agent.consultation.read_evidence', read)
    session = EvidenceSession({'user_id': 'user_zhang'})
    assert session.read({'tool': 'tasks', 'fields': ['records']}) is False
    assert session.read({'tool': 'tasks', 'fields': ['records']}) is True
    facts = session.facts()
    assert len([row for row in facts if row.get('scope') == 'read_outcome']) == 1
    current = [row for row in facts if row['id'].startswith('read.tasks.')]
    query = normalize_goals([goal('tasks', ['records'], '我的任务')], '', {})
    coverage = [dict(goal_id='q1', status='ANSWERED', evidence_ids=[current[0]['id']], reason='成功读取为空')]
    assert _validate_coverage(coverage, query, facts) == coverage
    rehydrated = EvidenceSession({'user_id': 'user_zhang'}, session.result())
    carried = rehydrated.result()['data']['documents']
    assert len(carried) == len({row['id'] for row in carried}) == len(facts)
    assert rehydrated.facts() == facts
    # A different turn does not inherit the old failure as a current attempt.
    fresh = EvidenceSession({'user_id': 'user_zhang', 'memory_principal': 'user_zhang',
                             'observations': {'BANKING_HELP': {'read_failures': session.failures}}})
    assert fresh.facts() == []


@pytest.mark.parametrize('resource', ['general', 'tasks'])
def test_failed_query_followup_can_explain_failure_without_claiming_absent_tasks(gateway, monkeypatch, resource):  # noqa: F811
    client, _ = gateway
    original = runtime._http
    def transport(method, path, **kwargs):
        if path == 'tasks':
            return SimpleNamespace(status_code=503, json=lambda: {'code': 503})
        return original(method, path, **kwargs)
    monkeypatch.setattr(runtime, '_http', transport)
    before = financial_state()
    model(monkeypatch, [goal('balance', ['available_balance'], '可用余额'), goal('tasks', ['status'], '待办状态')])
    first = _send(client, '同时核实可用余额和待办状态', conversation_id='failure-meaning')
    assert first['business_status'] == 'PARTIAL_SUCCESS'
    explanation = {**goal(resource, [], '读取失败是否说明没有待办任务', act='EXPLAIN'), 'topic': 'tasks'}
    def draft(payload):
        ids = [row['id'] for row in payload['facts'] if row.get('scope') == 'read_outcome' and row['resource'] == 'tasks']
        assert ids
        return dict(answer='读取失败不代表没有待办任务。此次未取得有效结果，实际状态仍不能确认。',
                    evidence_ids=ids, unanswered=[], coverage=[dict(goal_id='q1', status='ANSWERED',
                    evidence_ids=ids, reason='解释失败与空记录的区别，不判断实际任务状态')])
    model(monkeypatch, [explanation], draft=draft,
          planner=lambda payload: {'reads': [{'tool': 'tasks', 'fields': ['status']}]})
    reply = _send(client, '那刚才待办状态读取失败，是不是说明没有待办任务？', conversation_id='failure-meaning')
    assert reply['business_status'] == 'SUCCEEDED' and reply['approval'] is None
    assert '读取失败不代表没有待办任务' in reply['reply']
    assert reply['result']['answer_presentation']['mode'] == 'GENERATED_REVIEWED'
    assert reply['result']['answer_presentation']['coverage'][0]['status'] == 'ANSWERED'
    documents = reply['result']['execution_result']['data']['documents']
    assert len(documents) == len({row['id'] for row in documents})
    assert financial_state() == before


def test_query_with_approved_prose_cannot_promote_failure_to_account_fact(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    original = runtime._http
    def transport(method, path, **kwargs):
        if path == 'tasks':
            return SimpleNamespace(status_code=503, json=lambda: {'code': 503})
        return original(method, path, **kwargs)
    monkeypatch.setattr(runtime, '_http', transport)
    def fabricated(payload):
        ids = [row['id'] for row in payload['facts'] if row.get('scope') == 'read_outcome']
        return dict(answer='你当前没有待办任务。', evidence_ids=ids, unanswered=[],
                    coverage=[dict(goal_id='q1', status='ANSWERED', evidence_ids=ids, reason='没有任务')])
    calls = model(monkeypatch, [goal('tasks', ['records'], '当前待办任务')], draft=fabricated)
    before = financial_state()
    reply = _send(client, '查询我目前有哪些待办任务', conversation_id='failure-not-absence')
    assert reply['business_status'] != 'SUCCEEDED' and reply['approval'] is None
    assert '读取失败不代表没有记录' in reply['reply'] and '你当前没有待办任务' not in reply['reply']
    assert not any('独立银行回答核验器' in system for system, _ in calls)
    assert financial_state() == before
