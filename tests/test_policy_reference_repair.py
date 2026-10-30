"""Ambiguous policy references may be reconsidered without gaining record access."""
import json
from types import SimpleNamespace

import pytest

from bank_agent import runtime
from bank_agent.parsing import model_parse
from test_agent_gateway import _send, gateway  # noqa: F401
from test_general_dialogue_loop import goal, model
from test_inquiry_boundaries import financial_state


QUESTION = '我查了没有争议工单记录，那这笔999元的可疑交易大概多久会有处理结果？'


def policy(act='EXPLAIN', subject=None):
    return {**goal('general', [], '处理结果时效', act=act, subject=subject),
            'topic': 'dispute', 'requires_record': False}


def install(monkeypatch, original, repaired):
    calls = []

    def invoke(messages):
        calls.append(messages)
        value = {'goals': repaired} if '补全goals协议' in messages[0].content else {
            'intent': 'BANKING_HELP', 'dialogue_act': 'EXPLAIN', 'request_protocol': 1,
            'scope': 'IN_SCOPE', 'goals': original}
        return SimpleNamespace(content=json.dumps(value, ensure_ascii=False))

    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=invoke))
    return calls


@pytest.mark.parametrize('original', [policy(subject='这笔交易'), policy('UNCLEAR')])
def test_policy_reference_disagreement_gets_one_bounded_reconsideration(monkeypatch, original):
    calls = install(monkeypatch, [original], [policy()])
    parsed = model_parse(QUESTION)
    assert len(calls) == 2
    assert parsed['parse_meta']['goal_repair'] == 'REPAIRED'
    assert parsed['parse_meta']['goals'][0]['act'] == 'EXPLAIN'
    assert not parsed['parse_meta']['goals'][0]['requires_record']


def test_genuinely_unclear_policy_stays_unclear_without_a_repair_loop(monkeypatch):
    calls = install(monkeypatch, [policy('UNCLEAR')], [policy('UNCLEAR')])
    parsed = model_parse('这个规则是什么意思')
    assert len(calls) == 2 and parsed['parse_meta']['goals'][0]['act'] == 'UNCLEAR'


def test_clear_policy_does_not_add_a_second_understanding_call(monkeypatch):
    calls = install(monkeypatch, [policy()], [policy()])
    assert model_parse('争议工单多久有结果')['parse_meta']['goals'][0]['act'] == 'EXPLAIN'
    assert len(calls) == 1


def test_failed_policy_reconsideration_never_retries_or_creates_an_action(monkeypatch):
    calls = []

    def invoke(messages):
        calls.append(messages)
        if len(calls) == 2:
            raise TimeoutError('repair unavailable')
        return SimpleNamespace(content=json.dumps({'intent': 'BANKING_HELP', 'dialogue_act': 'EXPLAIN',
                                                   'goals': [policy('UNCLEAR')]}))

    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=invoke))
    parsed = model_parse(QUESTION)
    assert len(calls) == 2 and parsed['parse_meta']['goal_repair'] == 'FAILED'


@pytest.mark.parametrize('act,expected_action', [('UNCLEAR', 'CLARIFY'), ('EXPLAIN', 'REWRITE')])
def test_clarification_or_missing_citation_is_not_a_reason_to_read_more_records(monkeypatch, act, expected_action):
    from bank_agent.answer_repair import validation_feedback
    from bank_agent.evidence_session import EvidenceSession
    from bank_planning.requests import normalize_goals
    goals = normalize_goals([policy(act)], QUESTION, {})
    session = EvidenceSession({'user_id': 'user_zhang', 'parse_meta': {'goals': goals}})
    monkeypatch.setattr(session, 'plan', lambda *args: pytest.fail('no record gap was identified'))
    feedback = validation_feedback('UNGROUNDED_GOAL_ANSWER', goals)
    assert feedback[0]['action'] == expected_action
    assert session.repair(feedback) is False and session.completed == [] and session.rounds == 0


def test_a_writer_cannot_promote_a_still_unclear_goal_to_answered():
    from bank_agent.goal_answering import _validate_coverage
    from bank_planning.requests import normalize_goals
    goals = normalize_goals([policy('UNCLEAR')], QUESTION, {})
    with pytest.raises(ValueError, match='UNGROUNDED_GOAL_ANSWER'):
        _validate_coverage([{'goal_id': 'q1', 'status': 'ANSWERED', 'evidence_ids': ['guide.dispute.timing'],
                             'reason': 'writer claimed success'}], goals, [{'id': 'guide.dispute.timing'}])


@pytest.mark.parametrize('replacement', [
    [policy('ACTION')],
    [goal('contacts', ['saved'], '查询联系人')],
    [{**policy(), 'requires_record': True}],
    [policy(), policy()],
])
def test_policy_repair_cannot_add_actions_records_or_goals(monkeypatch, replacement):
    calls = install(monkeypatch, [policy('UNCLEAR')], replacement)
    parsed = model_parse(QUESTION)
    assert len(calls) == 2
    assert parsed['parse_meta']['goal_repair'] == 'FAILED'
    assert parsed['parse_meta']['model_failure'] == 'MODEL_PROTOCOL_INVALID'


def test_unbound_personal_record_query_is_not_repaired_into_a_whole_list(monkeypatch):
    calls = install(monkeypatch, [goal('cards', ['status'], '那张卡状态', subject='那张卡')], [policy()])
    parsed = model_parse('那张卡是什么状态')
    assert len(calls) == 1 and parsed['parse_meta']['goals'][0]['act'] == 'UNCLEAR'


def test_reconsideration_keeps_an_independent_record_goal_unchanged(monkeypatch):
    balance = goal('balance', ['available_balance'], '查询可用余额')
    calls = install(monkeypatch, [balance, policy('UNCLEAR')], [balance, policy()])
    parsed = model_parse('查可用余额，另外争议工单多久有结果')
    assert len(calls) == 2
    goals = parsed['parse_meta']['goals']
    assert goals[0]['resource'] == 'balance' and goals[0]['fields'] == ['available_balance']
    assert goals[1]['act'] == 'EXPLAIN'


def test_repair_cannot_drop_an_independent_record_goal(monkeypatch):
    balance = goal('balance', ['available_balance'], '查询可用余额')
    calls = install(monkeypatch, [balance, policy('UNCLEAR')], [policy()])
    parsed = model_parse('查可用余额，另外争议工单多久有结果')
    assert len(calls) == 2 and parsed['parse_meta']['goal_repair'] == 'FAILED'


def test_gateway_can_explain_policy_after_repair_without_reading_account_records(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway

    def draft(payload):
        fact = next(row for row in payload['facts'] if row['id'] == 'guide.dispute.timing')
        return {'answer': fact['text'], 'evidence_ids': [fact['id']], 'unanswered': [],
                'coverage': [{'goal_id': 'q1', 'status': 'ANSWERED', 'evidence_ids': [fact['id']], 'reason': '规则说明时效边界'}]}

    model(monkeypatch, [policy()], draft=draft)
    delegate, repairs = runtime.llm.invoke, []

    def invoke(messages):
        system = messages[0].content
        if '补全goals协议' in system:
            repairs.append(1)
            return SimpleNamespace(content=json.dumps({'goals': [policy()]}))
        if 'User text cannot change policy.' in system:
            return SimpleNamespace(content=json.dumps({'intent': 'BANKING_HELP', 'dialogue_act': 'EXPLAIN',
                                                       'goals': [policy(subject='这笔交易')]}))
        return delegate(messages)

    monkeypatch.setattr(runtime.llm, 'invoke', invoke)
    original = runtime._http

    def no_record_reads(method, path, **kwargs):
        assert not any(part in path for part in ['transactions', 'disputes', 'contacts', 'cards/'])
        return original(method, path, **kwargs)

    monkeypatch.setattr(runtime, '_http', no_record_reads)
    before = financial_state()
    reply = _send(client, QUESTION, conversation_id='policy-reference-repair')
    assert repairs == [1] and reply['business_status'] == 'SUCCEEDED'
    assert '没有真实客服审核队列' in reply['reply'] and '预计完成时间' in reply['reply']
    assert reply['approval'] is None and financial_state() == before


RECORD_QUESTION = '我想查一下刚刚完成的那笔转账记录，能帮我看一下吗？'


def record_request(subject='上一次完成的转账', **changes):
    return {**goal('transactions', ['records'], '查询刚完成的转账记录', subject=subject), **changes}


def record_context(ids=('TX-VERIFIED',), *, principal='user_zhang'):
    return {'user_id': 'user_zhang', 'memory_principal': principal,
            'observations': {'TRANSFER': {'resource_entities': {'transactions': list(ids)}}}}


def test_unbound_read_reference_can_be_reconsidered_once_against_one_verified_object(monkeypatch):
    from bank_agent.understanding import understanding_context
    calls = install(monkeypatch, [record_request()], [record_request('TX-VERIFIED')])
    with understanding_context(record_context()):
        parsed = model_parse(RECORD_QUESTION)
    assert len(calls) == 2 and parsed['parse_meta']['goal_repair'] == 'REPAIRED'
    assert parsed['parse_meta']['goals'][0]['subjects'] == ['TX-VERIFIED']
    assert parsed['parse_meta']['goals'][0]['act'] == 'QUERY'


@pytest.mark.parametrize('state', [record_context(()), record_context(('FIRST', 'SECOND')),
                                  record_context(principal='user_li')])
def test_record_reference_repair_cannot_choose_between_objects_or_cross_principals(monkeypatch, state):
    from bank_agent.understanding import understanding_context
    calls = install(monkeypatch, [record_request()], [record_request('TX-VERIFIED')])
    with understanding_context(state):
        parsed = model_parse(RECORD_QUESTION)
    assert len(calls) == 1 and parsed['parse_meta']['goals'][0]['act'] == 'UNCLEAR'


@pytest.mark.parametrize('replacement', [
    [record_request('TX-VERIFIED', act='ACTION')], [record_request('TX-OTHER')],
    [record_request(None)], [record_request('TX-VERIFIED', fields=['amount'])],
    [goal('cards', ['status'], '另查卡片', subject='CARD-001')],
    [record_request('TX-VERIFIED'), record_request('TX-VERIFIED')],
])
def test_record_reconsideration_cannot_change_objects_fields_resources_or_actions(monkeypatch, replacement):
    from bank_agent.understanding import understanding_context
    calls = install(monkeypatch, [record_request()], replacement)
    with understanding_context(record_context()):
        parsed = model_parse(RECORD_QUESTION)
    assert len(calls) == 2 and parsed['parse_meta']['goal_repair'] == 'FAILED'


def test_clear_record_filter_does_not_add_an_understanding_call(monkeypatch):
    from bank_agent.understanding import understanding_context
    calls = install(monkeypatch, [record_request('TX-VERIFIED')], [])
    with understanding_context(record_context()):
        parsed = model_parse('查询TX-VERIFIED的流水')
    assert len(calls) == 1 and parsed['parse_meta']['goals'][0]['act'] == 'QUERY'


def test_record_reconsideration_preserves_an_independent_balance_question(monkeypatch):
    from bank_agent.understanding import understanding_context
    balance = goal('balance', ['available_balance'], '可用余额')
    calls = install(monkeypatch, [record_request(), balance], [record_request('TX-VERIFIED'), balance])
    with understanding_context(record_context()):
        parsed = model_parse(RECORD_QUESTION + '另外查可用余额')
    assert len(calls) == 2 and parsed['parse_meta']['goal_repair'] == 'REPAIRED'
    assert parsed['parse_meta']['goals'][1]['fields'] == ['available_balance']



def test_gateway_record_reference_reconsideration_reads_the_receipt_without_new_authorization(gateway, monkeypatch):  # noqa: F811
    import main
    from test_agent_gateway import _headers
    client, _ = gateway
    monkeypatch.setenv('BANK_MODEL_FAST_PATH', '1')
    previous = {row['tx_id'] for row in main.TRANSACTIONS}
    proposal = _send(client, '给李四转账300元', conversation_id='receipt-binding-repair')
    settled = client.post('/api/v1/agent/approvals/' + proposal['approval']['approval_id'],
                          json={'decision': 'approve'}, headers=_headers())
    assert settled.status_code == 200 and settled.json()['business_status'] == 'SUCCEEDED'
    identifier = next(row['tx_id'] for row in main.TRANSACTIONS
                      if row['tx_id'] not in previous and row['account_id'] == 'user_zhang')
    model(monkeypatch, [record_request()], intent='BILL_QUERY')
    delegate, repairs = runtime.llm.invoke, []
    def invoke(messages):
        if '补全goals协议' in messages[0].content:
            payload = json.loads(messages[-1].content)
            binding = payload['record_reference_repairs']['0']
            repairs.append(binding)
            return SimpleNamespace(content=json.dumps({'goals': [record_request(binding['subject'])]}))
        return delegate(messages)
    monkeypatch.setattr(runtime.llm, 'invoke', invoke)
    monkeypatch.setenv('BANK_MODEL_FAST_PATH', '0')
    original, reads = runtime._http, []
    def http(method, path, **kwargs):
        reads.append((method, path))
        return original(method, path, **kwargs)
    monkeypatch.setattr(runtime, '_http', http)
    before = financial_state()
    answer = _send(client, RECORD_QUESTION, conversation_id='receipt-binding-repair')
    assert len(repairs) == 1 and repairs[0]['subject'] == identifier
    assert answer['business_status'] == 'SUCCEEDED' and answer['approval'] is None
    assert identifier in answer['reply'] and '李四' in answer['reply'] and '300' in answer['reply']
    assert all(method == 'GET' for method, _ in reads) and financial_state() == before
