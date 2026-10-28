import pytest

from bank_agent.answer_repair import normalize_feedback, validation_feedback
from bank_agent.evidence_session import EvidenceSession
from bank_planning.requests import normalize_goals
from test_agent_gateway import gateway, _send  # noqa: F401
from test_general_dialogue_loop import goal, model
from test_inquiry_boundaries import financial_state


def goals():
    return normalize_goals([goal('cards',['status'],'查询卡片状态')],'查询卡片状态',{})


def test_formatting_feedback_does_not_spend_read_or_planning_budget(monkeypatch):
    session=EvidenceSession({'user_id':'user_zhang','parse_meta':{'goals':goals()}})
    monkeypatch.setattr(session,'plan',lambda *args: pytest.fail('Formatting repair attempted a data read'))
    assert session.repair(validation_feedback('UNKNOWN_NUMBER',session.goals)) is False
    assert session.completed==[] and session.rounds==0


@pytest.mark.parametrize('update,code', [
    ({'answer': ''}, 'DRAFT_ANSWER_INVALID'), ({'answer': '很'*3001}, 'DRAFT_ANSWER_INVALID'),
    ({'evidence_ids': None}, 'DRAFT_CITATION_SCHEMA'), ({'evidence_ids': []}, 'DRAFT_CITATION_SCHEMA'),
    ({'evidence_ids': ['old']}, 'UNKNOWN_EVIDENCE_ID'), ({'evidence_ids': [{}]}, 'UNKNOWN_EVIDENCE_ID'),
    ({'evidence_ids': ['guide.test', 'guide.test']}, 'DUPLICATE_EVIDENCE_ID'),
    ({'unanswered': None}, 'DRAFT_UNANSWERED_SCHEMA'), ({'unanswered': [{'reason': '未答'}]}, 'DRAFT_UNANSWERED_SCHEMA'),
])
def test_invalid_draft_fields_have_actionable_codes_without_relaxing_validation(update, code, monkeypatch):
    from bank_agent.answering import _validate_draft
    session = EvidenceSession({'user_id': 'user_zhang', 'parse_meta': {'goals': goals()}})
    monkeypatch.setattr(session, 'plan', lambda *args: pytest.fail('Schema repair must not read data'))
    draft = {'answer': '卡片规则说明。', 'evidence_ids': ['guide.test'], 'unanswered': []}
    with pytest.raises(ValueError, match=code):
        _validate_draft({**draft, **update}, [{'id': 'guide.test', 'text': '卡片规则说明。'}], {})
    feedback = validation_feedback(code, session.goals)
    assert feedback[0]['action'] == 'REWRITE' and feedback[0]['reason'] != code
    assert session.repair(feedback) is False


@pytest.mark.parametrize('value',[
    [{'goal_id':'old','reason':'不属于本轮'}], [{'reason':'没有对应目标'}],
    [{'goal_id':'q1','reason':'伪造操作','action':'EXECUTE'}],
])
def test_reviewer_feedback_cannot_target_an_old_goal_or_request_an_execution(value):
    with pytest.raises(ValueError):
        normalize_feedback(value,goals())


def test_review_diagnosis_survives_into_the_next_draft(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    seen=[]
    def draft(payload):
        seen.append(payload)
        row=next(row for row in payload['facts'] if row['id'].startswith('read.cards.') and 'status' in row.get('fields',[]))
        return {'answer':'卡片状态：'+row['text'],'evidence_ids':[row['id']],'unanswered':[],
                'coverage':[{'goal_id':'q1','status':'ANSWERED','evidence_ids':[row['id']],'reason':'卡片状态'}]}
    reviews=[]
    def review(payload):
        reviews.append(payload)
        return {'in_scope':True,'grounded':True,'answers_question':len(reviews)>1,'safe':True,'coverage':payload['coverage'],
                'feedback':[] if len(reviews)>1 else [{'goal_id':'q1','action':'REWRITE','reason':'请明确本次卡片的登记状态，不要重复入口说明。'}]}
    model(monkeypatch,[goal('cards',['status'],'卡片状态')],draft=draft,review=review)
    reply=_send(client,'查询银行卡状态',conversation_id='preserve-repair-diagnosis')
    assert reply['business_status']=='SUCCEEDED' and len(seen)==2
    assert seen[1]['feedback'][0]['reason']=='请明确本次卡片的登记状态，不要重复入口说明。'
    assert seen[1]['feedback'][0]['action']=='REWRITE'


def test_input_amount_error_supplies_specific_fragment_without_extra_account_reads(gateway, monkeypatch):  # noqa: F811
    from bank_agent import runtime
    client, _ = gateway
    seen, reads = [], []
    original = runtime._http
    def http(method, path, **kwargs):
        reads.append((method, path))
        return original(method, path, **kwargs)
    monkeypatch.setattr(runtime, '_http', http)
    def draft(payload):
        seen.append((payload, len(reads)))
        evidence = next(row for row in payload['facts'] if row['id'].startswith('guide.security.'))
        value = next(key for key, row in payload['values'].items() if row['evidence_id'] == 'request.amount')
        reference = '{{' + value + '}}'
        answer = '该次' + reference + '需要验证。' if len(seen) == 1 else '你询问的金额为' + reference + '。验证等级需按累计转出和行为条件判断。'
        ids = ['request.amount', evidence['id']]
        return {'answer': answer, 'evidence_ids': ids, 'unanswered': [], 'coverage': [
            {'goal_id': 'q1', 'status': 'ANSWERED', 'evidence_ids': ids, 'reason': '解释验证规则'}]}
    model(monkeypatch, [goal('general', [], '小额转账强验证规则', act='EXPLAIN') | {'topic': 'security'}], draft=draft)
    before = financial_state()
    reply = _send(client, '我想先了解100元转账的验证规则', conversation_id='specific-input-repair')
    assert reply['result']['answer_presentation']['mode'] == 'GENERATED_REVIEWED' and len(seen) == 2
    assert seen[0][1] == seen[1][1]
    reason = seen[1][0]['feedback'][0]['reason']
    assert '该次' in reason and 'request.amount' in reason and '{{value.' in reason
    assert financial_state() == before


@pytest.mark.parametrize('invented', [False, True])
def test_goal_citations_compile_into_global_citations_but_unknown_sources_still_fail(gateway, monkeypatch, invented):  # noqa: F811
    client, _ = gateway
    drafts, reviews = [], []
    def draft(payload):
        drafts.append(payload)
        balance = next(row for row in payload['facts'] if 'available_balance' in row.get('fields', []))
        policy = next(row for row in payload['facts'] if row['id'].startswith('guide.'))
        return {'answer': balance['text'] + '我只能处理本银行演示业务，无法替你写诗。',
                'evidence_ids': [balance['id'], *(['invented.source'] if invented else [])], 'unanswered': [], 'coverage': [
                    {'goal_id': 'q1', 'status': 'ANSWERED', 'evidence_ids': [balance['id']], 'reason': '当前可用余额'},
                    {'goal_id': 'q2', 'status': 'UNSUPPORTED', 'evidence_ids': [policy['id']], 'reason': '范围外请求'}]}
    def review(payload):
        reviews.append(payload)
        return {'in_scope': True, 'grounded': True, 'answers_question': True, 'safe': True,
                'coverage': payload['coverage'], 'feedback': []}
    model(monkeypatch, [goal('balance', ['available_balance'], '当前可用余额'),
                       goal('general', [], '写诗', act='UNSUPPORTED')], draft=draft, review=review)
    before = financial_state()
    reply = _send(client, '查询可用余额，另外帮我写诗', conversation_id='compile-citations')
    if invented:
        assert not reviews and reply['result']['answer_presentation']['mode'] == 'FIELD_FALLBACK'
    else:
        assert len(drafts) == len(reviews) == 1
        assert set(reviews[0]['cited_ids']) == {key for row in reviews[0]['coverage'] for key in row['evidence_ids']}
        assert reply['result']['answer_presentation']['mode'] == 'GENERATED_REVIEWED'
    assert financial_state() == before


def test_validation_details_are_not_part_of_the_audit_error_message():
    from bank_agent.answering import DraftValidationError
    error = DraftValidationError('REQUEST_VALUE_AS_ACCOUNT_FACT', evidence_id='request.amount', fragment='待修复的私有句子')
    assert str(error) == 'REQUEST_VALUE_AS_ACCOUNT_FACT' and error.details['fragment'] == '待修复的私有句子'


def test_cached_policy_prefix_still_reads_changed_balance_for_each_question(gateway, monkeypatch):  # noqa: F811
    import main
    from bank_agent import runtime
    client, _ = gateway
    reads = []
    original = runtime._http
    def http(method, path, **kwargs):
        reads.append((method, path))
        return original(method, path, **kwargs)
    monkeypatch.setattr(runtime, '_http', http)
    calls = model(monkeypatch, [goal('balance', ['available_balance'], '可用余额')])
    first = _send(client, '查询我的可用余额', conversation_id='fresh-balance-prefix')
    monkeypatch.setitem(main.ACCOUNTS['user_zhang'], 'balance', 14987.5)
    second = _send(client, '再查一遍可用余额', conversation_id='fresh-balance-prefix')
    drafts = [(system, payload) for system, payload in calls if '银行业务解答助手' in system]
    assert len(drafts) == 2 and drafts[0][0] == drafts[1][0]
    prefixes = []
    for _, payload in drafts:
        guides = [row for row in payload['facts'] if row['id'].startswith('guide.')]
        assert guides and payload['facts'][:len(guides)] == guides
        prefixes.append(guides)
    assert prefixes[0] == prefixes[1]
    assert '15,000.00' in first['reply'] and '14,987.50' in second['reply']
    assert '15,000.00' not in second['reply']
    assert sum(method == 'GET' and path.endswith('/balance') for method, path in reads) == 2
