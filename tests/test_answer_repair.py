import json
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


@pytest.mark.parametrize('claim', ['已冻结关联卡片', '已经为您转账', '成功退款', '已撤销操作'])
def test_unverified_operation_claims_stay_blocked_with_bounded_rewrite_details(claim, monkeypatch):
    from bank_agent.answering import DraftValidationError, _validate_draft
    facts = [{'id': 'guide.test', 'text': '这里只说明业务规则，没有执行操作。'}]
    with pytest.raises(DraftValidationError, match='UNVERIFIED_OPERATION_CLAIM') as caught:
        _validate_draft({'answer': claim, 'evidence_ids': ['guide.test'], 'unanswered': []}, facts, {})
    assert claim in caught.value.details['fragment'] and len(caught.value.details['fragment']) <= 160
    assert str(caught.value) == 'UNVERIFIED_OPERATION_CLAIM'
    feedback = validation_feedback(str(caught.value), goals(), caught.value.details)
    assert feedback[0]['action'] == 'REWRITE' and claim in feedback[0]['reason']
    session = EvidenceSession({'user_id': 'user_zhang', 'parse_meta': {'goals': goals()}})
    monkeypatch.setattr(session, 'plan', lambda *args: pytest.fail('Claim wording repair must not read account data'))
    assert session.repair(feedback) is False


def test_operation_claim_feedback_rewrites_freeze_effects_without_new_reads_or_writes(gateway, monkeypatch):  # noqa: F811
    from bank_agent import runtime
    import security
    client, _ = gateway
    seen, reads = [], []
    original = runtime._http
    def http(method, path, **kwargs):
        reads.append((method, path))
        return original(method, path, **kwargs)
    monkeypatch.setattr(runtime, '_http', http)
    def draft(payload):
        seen.append((payload, len(reads)))
        evidence = next(row for row in payload['facts'] if row['id'] == 'guide.card.receive')
        answer = '已冻结关联卡片' if len(seen) == 1 else evidence['text']
        return {'answer': answer, 'evidence_ids': [evidence['id']], 'unanswered': [], 'coverage': [
            {'goal_id': 'q1', 'status': 'ANSWERED', 'evidence_ids': [evidence['id']], 'reason': '说明冻结影响'}]}
    model(monkeypatch, [goal('general', [], '银行卡冻结后会有什么影响', act='EXPLAIN') | {'topic': 'card'}], draft=draft)
    before = financial_state()
    reply = _send(client, '我想先了解一下，银行卡冻结以后会有什么影响？', conversation_id='freeze-impact-claim-repair')
    assert reply['result']['answer_presentation']['mode'] == 'GENERATED_REVIEWED' and len(seen) == 2
    assert reply['business_status'] == 'SUCCEEDED' and reply['approval'] is None
    assert '限制该卡付款' in reply['reply'] and '账户仍可接收转账' in reply['reply'] and '不会自动解除' in reply['reply']
    assert '已冻结关联卡片' in seen[1][0]['feedback'][0]['reason']
    assert seen[0][1] == seen[1][1] and all(method == 'GET' for method, _ in reads)
    assert financial_state() == before
    events = [row for row in security.get_audit_logs(limit=1000) if row.get('request_id') == reply['request_id']]
    assert any(row.get('reason') == 'UNVERIFIED_OPERATION_CLAIM' for row in events)
    assert all('已冻结关联卡片' not in str(row) for row in events)


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


def test_projected_field_absence_is_rewritten_before_review_without_extra_reads(gateway, monkeypatch):  # noqa: F811
    import main
    from bank_agent import runtime
    client, _ = gateway
    main.CARDS['user_zhang'][0]['status'] = 'FROZEN'
    seen, reads = [], []
    original = runtime._http
    def http(method, path, **kwargs):
        reads.append((method, path))
        return original(method, path, **kwargs)
    monkeypatch.setattr(runtime, '_http', http)
    def draft(payload):
        seen.append((payload, len(reads)))
        row = next(row for row in payload['facts'] if row.get('entity_id') == 'CARD-001')
        policy = next(row for row in payload['facts'] if row['id'] == 'guide.card.receive')
        answer = ('本人卡片记录只包含渠道字段，没有显示冻结或挂失状态。' if len(seen) == 1
                  else row['text'] + policy['text'])
        ids = [row['id'], policy['id']]
        return {'answer': answer, 'evidence_ids': ids, 'unanswered': [], 'coverage': [
            {'goal_id': 'q1', 'status': 'ANSWERED', 'evidence_ids': ids, 'reason': '渠道与冻结区别'}]}
    calls = model(monkeypatch, [goal('cards', ['channels'], '当前渠道配置与冻结区别', subject='CARD-001')], draft=draft)
    before = financial_state()
    reply = _send(client, '查询CARD-001的网上支付和境外渠道，目前配置与冻结有何区别？', conversation_id='projection-context')
    assert len(seen) == 2 and reply['result']['answer_presentation']['mode'] == 'GENERATED_REVIEWED'
    assert 'FROZEN' in reply['reply'] and '限制该卡付款' in reply['reply']
    assert 'PROJECTED_FIELD_ABSENCE' not in reply['reply'] and reply['approval'] is None
    assert seen[0][1] == seen[1][1] and financial_state() == before
    feedback = seen[1][0]['feedback'][0]
    assert feedback['action'] == 'REWRITE' and '没有显示冻结或挂失状态' in feedback['reason']
    reviews = [payload for system, payload in calls if '独立银行回答核验器' in system]
    assert len(reviews) == 1 and reviews[0]['projection_contract'] == seen[1][0]['projection_contract']
    assert all(method == 'GET' for method, _ in reads)


@pytest.mark.parametrize('prefix', ['不代表', '不意味着', '不等于', '并非', '不是', '不能据此认定'])
@pytest.mark.parametrize('operation', ['已下单', '已扣款', '已转账', '已冻结', '已创建争议工单'])
def test_explicit_denial_of_completion_is_not_an_execution_claim(prefix, operation):
    from bank_agent.answering import _validate_draft
    facts = [{'id': 'guide.test', 'text': '这里只解释业务步骤，草稿尚未执行，也不证明资金或订单状态。'}]
    text = '生成提醒或草稿' + prefix + operation + '，执行仍须核对并由用户另行确认。'
    answer, _ = _validate_draft({'answer': text, 'evidence_ids': ['guide.test'], 'unanswered': []}, facts, {})
    assert answer == text


@pytest.mark.parametrize('text', [
    '提醒不代表已下单，但我已扣款。', '草稿不等于已转账；我已经为您冻结卡片。',
    '不是要取消，我已撤销订阅。', '不仅已下单，而且已扣款。',
    '不能说未执行，我已转账。', '草稿不代表已下单。已创建争议工单。',
    '不是没操作，已冻结。',
])
def test_a_completion_denial_cannot_mask_another_positive_claim(text):
    from bank_agent.answering import _validate_draft, DraftValidationError
    facts = [{'id': 'guide.test', 'text': '这里只解释步骤，没有执行操作。'}]
    with pytest.raises(DraftValidationError, match='UNVERIFIED_OPERATION_CLAIM'):
        _validate_draft({'answer': text, 'evidence_ids': ['guide.test'], 'unanswered': []}, facts, {})


@pytest.mark.parametrize('text', [
    '已创建的争议工单能否撤销：目前没有提供撤回、取消或删除争议工单的功能。',
    '取消订阅并不等同于成功退款，退款须向商户另行申请。',
    '取消订阅后不会自动成功退款。',
    '已扣款项不会自动退款，已扣款项不是成功退款。',
    '如果转账时提示写盘失败，系统会自动回滚，并不算成功转账。',
    '写盘失败意味着转账未成功，并未成功转账。',
    '付款超时后请先确认是否已扣款，不要重复提交。',
    '请先核对是否已转账。',
])
def test_valid_inquiry_and_negative_rules_not_blocked_as_operation_claims(text):
    from bank_agent.answering import _validate_draft
    facts = [{'id': 'guide.test', 'text': '这里只解释步骤，没有执行操作。'}]
    answer, _ = _validate_draft({'answer': text, 'evidence_ids': ['guide.test'], 'unanswered': []}, facts, {})
    assert answer == text


def test_gateway_explains_birthday_dependencies_without_treating_not_completed_as_completed(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    def draft(payload):
        rows = [row for row in payload['facts'] if row['id'] in
                {'guide.cross_scene.capability', 'guide.cross_scene.fulfillment'}]
        ids = [row['id'] for row in rows]
        answer = ''.join(row['text'] for row in rows) + '生成提醒和模拟订单草稿不代表已下单，也不等于已扣款。'
        return {'answer': answer, 'evidence_ids': ids, 'unanswered': [], 'coverage': [
            {'goal_id': 'q1', 'status': 'ANSWERED', 'evidence_ids': ids, 'reason': '说明预留预算与提醒订单依赖及执行限制'}]}
    calls = model(monkeypatch, [goal('general', [], '资金锁定生日提醒订单草稿如何联动', act='EXPLAIN') |
                              {'topic': 'cross_scene'}], draft=draft)
    before = financial_state()
    answer = _send(client, '资金锁定、生日提醒和鲜花订单草稿具体怎么联动？', conversation_id='birthday-denied-completion')
    assert answer['business_status'] == 'SUCCEEDED' and answer['approval'] is None
    assert answer['result']['answer_presentation']['mode'] == 'GENERATED_REVIEWED'
    assert '预留明确预算' in answer['reply'] and '到期仍需用户再次确认' in answer['reply']
    assert sum('银行业务解答助手' in system for system, _ in calls) == 1
    assert financial_state() == before


@pytest.mark.parametrize('code,phrase', [
    ('UNKNOWN_NUMBER', '不要自行算出该数值'),
    ('UNANCHORED_MONEY', '不自行汇总'),
])
def test_aggregate_repair_feedback_names_the_no_calculation_path(code, phrase):
    goals=normalize_goals([goal('transactions',['records'],'消费合计与可用余额')],'消费合计与可用余额',{})
    feedback=validation_feedback(code,goals)
    assert feedback[0]['action']=='REWRITE' and phrase in feedback[0]['reason']
    assert 'calculation' in feedback[0]['reason'] and '逐笔记录' in feedback[0]['reason']


def test_self_computed_aggregate_converges_by_citing_the_synthesized_calculation(gateway, monkeypatch):  # noqa: F811
    client,_=gateway
    seen=[]
    def draft(payload):
        seen.append(payload)
        calc=next((row for row in payload['facts'] if row.get('source','').startswith('calculation://')),None)
        if calc is None:
            rows=[row for row in payload['facts'] if row['id'].startswith('read.transactions.')]
            # Mirror the 20261031 server failure: the draft self-computes the
            # month total although no calculation evidence exists this round.
            return {'answer':'本月消费合计为67.8，与可用余额口径不同。','evidence_ids':[row['id'] for row in rows],
                    'unanswered':[],'coverage':[{'goal_id':'q1','status':'ANSWERED','evidence_ids':[row['id'] for row in rows],'reason':'已给出合计'}]}
        return {'answer':'本月'+calc['text']+'该合计与可用余额是不同口径，可用余额是当前时点可动用的资金。',
                'evidence_ids':[calc['id']],'unanswered':[],
                'coverage':[{'goal_id':'q1','status':'ANSWERED','evidence_ids':[calc['id']],'reason':'引用服务端合计并说明口径'}]}
    model(monkeypatch,[goal('transactions',['records','amount'],'本月消费合计为什么不等于当前可用余额')],draft=draft)
    before=financial_state()
    reply=_send(client,'消费合计为什么不等于当前可用余额？',conversation_id='aggregate-synthesis-cite')
    assert reply['business_status']=='SUCCEEDED' and len(seen)==2
    assert reply['result']['answer_presentation']['mode']=='GENERATED_REVIEWED'
    assert '合计为¥' in reply['reply'] and '¥1,466.70' in reply['reply']
    feedback=seen[1]['feedback'][0]
    assert feedback['action']=='REWRITE' and '不自行写出数值' in feedback['reason']
    assert next(row for row in seen[1]['facts'] if row.get('source','').startswith('calculation://'))['input_evidence_ids']
    assert financial_state()==before


def test_rejected_draft_capture_is_opt_in_and_stays_out_of_the_audit(gateway, monkeypatch, tmp_path):  # noqa: F811
    import security
    client, _ = gateway
    sink = tmp_path/'rejected-drafts.jsonl'
    monkeypatch.setenv('BANK_ANSWER_REJECTION_CAPTURE', str(sink))
    def draft(payload):
        evidence = next(row for row in payload['facts'] if row['id'] == 'guide.card.receive')
        return {'answer': '已冻结关联卡片', 'evidence_ids': [evidence['id']], 'unanswered': [], 'coverage': [
            {'goal_id': 'q1', 'status': 'ANSWERED', 'evidence_ids': [evidence['id']], 'reason': '说明冻结影响'}]}
    model(monkeypatch, [goal('general', [], '银行卡冻结后会有什么影响', act='EXPLAIN') | {'topic': 'card'}], draft=draft)
    reply = _send(client, '我想先了解一下，银行卡冻结以后会有什么影响？', conversation_id='rejection-capture-file')
    assert reply['result']['answer_presentation']['mode'] == 'FIELD_FALLBACK'
    rows = [json.loads(line) for line in sink.read_text(encoding='utf8').splitlines()]
    assert any(row['code'] == 'UNVERIFIED_OPERATION_CLAIM' and '已冻结关联卡片' in row['answer'] for row in rows)
    events = [row for row in security.get_audit_logs(limit=1000) if row.get('request_id') == reply['request_id']]
    assert all('已冻结关联卡片' not in str(row) for row in events)


def test_rejection_capture_defaults_to_off(gateway, monkeypatch, tmp_path):  # noqa: F811
    client, _ = gateway
    monkeypatch.delenv('BANK_ANSWER_REJECTION_CAPTURE', raising=False)
    def draft(payload):
        evidence = next(row for row in payload['facts'] if row['id'] == 'guide.card.receive')
        return {'answer': '已冻结关联卡片', 'evidence_ids': [evidence['id']], 'unanswered': [], 'coverage': [
            {'goal_id': 'q1', 'status': 'ANSWERED', 'evidence_ids': [evidence['id']], 'reason': '说明冻结影响'}]}
    model(monkeypatch, [goal('general', [], '银行卡冻结后会有什么影响', act='EXPLAIN') | {'topic': 'card'}], draft=draft)
    reply = _send(client, '我想先了解一下，银行卡冻结以后会有什么影响？', conversation_id='rejection-capture-off')
    assert reply['result']['answer_presentation']['mode'] == 'FIELD_FALLBACK'
    assert not list(tmp_path.iterdir())


def test_parse_rejection_capture_records_invalid_goals(gateway, monkeypatch, tmp_path):  # noqa: F811
    import json as json_module
    client, _ = gateway
    sink = tmp_path/'parse-rejections.jsonl'
    monkeypatch.setenv('BANK_ANSWER_REJECTION_CAPTURE', str(sink))
    # A structurally invalid goals payload fails normalization and the repair
    # round; the turn must be refused and the invalid shape captured on file.
    model(monkeypatch, [{'bad': True}], draft=lambda payload: pytest.fail('No answer stage may run after a protocol failure'))
    reply = _send(client, '这两只产品的起购金额分别是多少，哪个门槛更低？', conversation_id='parse-capture-invalid')
    assert reply['business_status'] == 'FAILED'
    rows = [json_module.loads(line) for line in sink.read_text(encoding='utf8').splitlines()]
    assert any(row['code'] == 'INVALID_REQUEST_GOALS' and row.get('detail') for row in rows)


def test_negated_completion_predicates_with_extended_denials_pass_validation():
    """The captured 20261031 drafts all said “不能视为已解冻” and were rejected."""
    from bank_agent.answering import _validate_draft
    facts = [{'id': 'guide.test', 'text': '这里只说明业务规则，没有执行操作。解冻须另行提出并完成确认和强验证。'}]
    answer = ('冻结不会自动解除：需要另行提出解冻，并完成确认和强验证；'
              '未收到核心成功回执前，不能视为已解冻。以上是演示产品的规则说明，不代表你名下某张卡当前的实际状态。')
    text, ids = _validate_draft({'answer': answer, 'evidence_ids': ['guide.test'], 'unanswered': []}, facts, {})
    assert '不能视为已解冻' in text and ids == ['guide.test']
    with pytest.raises(ValueError, match='UNVERIFIED_OPERATION_CLAIM'):
        _validate_draft({'answer': '本卡片已解冻。', 'evidence_ids': ['guide.test'], 'unanswered': []}, facts, {})


def test_captured_freeze_impact_draft_now_passes_in_one_attempt(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    captured = ('本演示没有“冻结整个账户”的功能，冻结针对的是单张卡片；你说的“账户冻结”可按卡片冻结来理解。\n\n'
                '1. 对正常使用的影响：被冻结的卡片会被限制付款，该卡不能用于支付类交易。\n'
                '2. 对资金进出的影响：冻结只限制这张卡付款，账户本身仍可接收转账，进账不受影响。\n'
                '3. 冻结不会自动解除：需要另行提出解冻，并完成确认和强验证；未收到核心成功回执前，不能视为已解冻。\n'
                '4. 冻结与挂失是不同状态：挂失卡须先解除挂失，单纯解冻不能解除挂失。\n\n'
                '以上是演示产品的规则说明，不代表你名下某张卡当前的实际状态；实际状态需以本人卡片查询结果为准。')
    calls = []
    def draft(payload):
        calls.append(payload)
        evidence = next(row for row in payload['facts'] if row['id'] == 'guide.card.receive')
        return {'answer': captured, 'evidence_ids': [evidence['id']], 'unanswered': [], 'coverage': [
            {'goal_id': 'q1', 'status': 'ANSWERED', 'evidence_ids': [evidence['id']], 'reason': '说明冻结影响'}]}
    model(monkeypatch, [goal('general', [], '账户冻结对使用和资金的影响', act='EXPLAIN') | {'topic': 'card'}], draft=draft)
    reply = _send(client, '我想确认一下，如果账户被冻结，会对我正常使用和资金进出产生哪些影响？',
                  conversation_id='captured-freeze-impact-pass')
    assert reply['result']['answer_presentation']['mode'] == 'GENERATED_REVIEWED' and len(calls) == 1
    assert '不能视为已解冻' in reply['reply']


def test_negated_continuation_of_the_business_is_a_withdrawal_statement():
    from intent_language import withdrawal_statement
    assert withdrawal_statement('我原本想申购零星宝，现在决定先取消，不再继续这笔申购了。', pending_discussion=True)
    assert withdrawal_statement('不再继续这笔申购了', pending_discussion=True)
    assert withdrawal_statement('先不转了，取消吧。', pending_discussion=True)
    assert not withdrawal_statement('不要取消这笔转账。', pending_discussion=True)
    assert not withdrawal_statement('申购零星宝需要什么条件？', pending_discussion=True)
    assert not withdrawal_statement('我之前已经取消了这笔转账。', pending_discussion=True)


def test_third_draft_converges_after_step_numbers_and_literal_money(gateway, monkeypatch):  # noqa: F811
    """Replay of the 20261031 batch: draft 1 used numbered steps, draft 2 a literal yuan."""
    client, _ = gateway
    seen = []
    def draft(payload):
        seen.append(payload)
        row = next(row for row in payload['facts'] if row['id'].startswith('read.balance.'))
        if len(seen) == 1:
            answer = '办理前需要两件事：' + chr(10) + '1. 确定金额；' + chr(10) + '2. 完成核对。'
        elif len(seen) == 2:
            answer = '办理前需要确认金额与核对；当前可用余额为15,000元。'
        else:
            answer = '当前可用余额为' + row['text']
        return {'answer': answer, 'evidence_ids': [row['id']], 'unanswered': []}
    model(monkeypatch, [goal('balance', ['available_balance'], '当前可用余额')], intent='BALANCE', draft=draft)
    _send(client, '我现在账户里还有多少钱？', conversation_id='third-draft-converges')
    # The documented budget is three drafts on this path as well; the first two
    # rejections above must not end the loop before the third try.
    assert len(seen) == 3


def test_status_gloss_anchors_the_chinese_form_of_a_cited_enum(monkeypatch):
    """The 20261031 breadth drafts said “FROZEN（已冻结）” and were rejected."""
    from bank_agent.answering import _validate_draft
    facts = [{'id': 'read.cards.1', 'text': 'CARD-001 交易渠道：网上支付=开启；登记状态：FROZEN'}]
    answer = '该卡登记状态为 FROZEN（已冻结）。渠道配置是交易场景开关，冻结是卡片登记状态。'
    text, ids = _validate_draft({'answer': answer, 'evidence_ids': ['read.cards.1'], 'unanswered': []}, facts, {})
    assert '已冻结' in text
    # The same state claim is anchored whenever the cited row carries the enum.
    text, ids = _validate_draft({'answer': '您的卡片已冻结。', 'evidence_ids': ['read.cards.1'], 'unanswered': []}, facts, {})
    assert '已冻结' in text
    other = [{'id': 'guide.test', 'text': '这里只说明业务规则，没有执行操作。'}]
    with pytest.raises(ValueError, match='UNVERIFIED_OPERATION_CLAIM'):
        _validate_draft({'answer': '该卡已冻结。', 'evidence_ids': ['guide.test'], 'unanswered': []}, other, {})


import pytest
from intent_language import withdrawal_statement
@pytest.mark.parametrize('text,expected', [
    ('零星宝的申购我不办了，先取消吧。之前只是问能不能撤单，现在决定不继续了。', True),
    ('我原本想申购零星宝，现在决定先取消，不再继续这笔申购了。', True),
    ('先不转了，取消吧。', True),
    ('这笔预约转账帮我取消吧。', True),
    ('取消这个申购意向会怎样', False),
    ('那我决定取消这次零星宝申购会怎样', False),
    ('转账能取消吗？', False),
    ('怎么取消转账', False),
    ('不要取消这笔转账。', False),
    ('我之前已经取消了这笔转账。', False),
    ('零星宝的申购我明天决定先取消不再办理了', False),
    ('客服说现在决定先取消不再办理了', False),
    ('申购零星宝需要什么条件？', False),
    ('零星宝和恒泉稳利都想申购，现在帮我取消吧。', True),
    ('如果零星宝申购失败，帮我取消吧。', False),
])
def test_withdrawal_statement_judged_per_clause(text, expected):
    assert withdrawal_statement(text, pending_discussion=True) is expected


def test_persistence_rules_rescue_reclassifies_out_of_scope_questions():
    """The 3a0c0eb holdout failure: 写盘失败规则问句被误判非银行请求。"""
    from bank_agent.understanding import _persistence_rules_rescue
    rescued = _persistence_rules_rescue('如果写入磁盘时失败了，这算不算交易成功？')
    assert rescued is not None
    assert rescued['intent'] == 'BANKING_HELP'
    assert rescued['parse_meta']['dialogue_act'] == 'EXPLAIN'
    assert rescued['parse_meta']['scope'] == 'IN_SCOPE'
    assert rescued['parse_meta']['goals'][0]['act'] == 'EXPLAIN'
    # 非持久化与普通业务问句不触发救援（保持原判定路径）。
    assert _persistence_rules_rescue('帮我转账给李四') is None
    assert _persistence_rules_rescue('查询我的账户余额') is None


def test_step_self_reference_converted_before_number_check():
    """The 557db6e batch: “建议先按第 1 步核对” tripped UNKNOWN_NUMBER."""
    from bank_agent.answering import _validate_draft
    facts = [{'id': 'guide.test', 'text': '在对话页输入“我的卡片”并发送，核对目标卡片及其当前状态。输入“解冻”加该卡片编号并发送，核对红色审批卡。'}]
    answer = ('App 内解冻可按以下步骤操作：\n1. 在对话页输入“我的卡片”并发送，核对目标卡片及其当前状态。\n'
              '2. 输入“解冻”加该卡片编号并发送，核对红色审批卡。\n建议先按第 1 步核对。')
    text, ids = _validate_draft({'answer': answer, 'evidence_ids': ['guide.test'], 'unanswered': []}, facts, {})
    assert '第一步' in text and '第 1 步' not in text
    with pytest.raises(ValueError, match='UNKNOWN_NUMBER'):
        bad = answer.replace('第 1 步', '第 9 步')
        _validate_draft({'answer': bad, 'evidence_ids': ['guide.test'], 'unanswered': []}, facts, {})


def test_inline_list_numbers_not_flagged_as_unknown_number():
    """Inline procedure list numbers after punctuation (e.g. 1. ...; 2. ...) must not trip UNKNOWN_NUMBER."""
    from bank_agent.answering import _validate_draft
    facts = [{'id': 'guide.security.factors', 'text': '强验证的步骤是核对本次审批卡，用户确认，填写一次性验证码，再由登记设备完成签名；以本次审批卡列出的实际要求为准。'}]
    answer = '强验证的步骤是：1. 核对本次审批卡；2. 由你确认；3. 填写一次性验证码；4. 由登记设备完成签名。具体以本次审批卡列出的实际要求为准。'
    text, ids = _validate_draft({'answer': answer, 'evidence_ids': ['guide.security.factors'], 'unanswered': []}, facts, {})
    assert '1.' in text
    assert ids == ['guide.security.factors']


def test_card_freeze_question_routes_to_card_topic():
    """Card freeze receiving transfer question routes to card, not transfer."""
    from bank_agent.understanding import help_topic
    assert help_topic('换个问题，银行卡冻结之后还能接收转账吗？我只想了解这条规则。') == 'card'
    assert help_topic('卡片冻结之后还能接收转账吗？') == 'card'
    assert help_topic('银行卡解冻后怎么转账') == 'card'


def test_subscription_confirmation_inquiry_does_not_trigger_approval():
    """Asking whether confirmation is required before checking subscriptions is an explanation, not a query."""
    from intent_language import nonexecuting_action, execution_constraint
    from bank_agent.understanding import enforce_inquiry
    text = '我想查看自己的订阅记录，需要先确认什么吗？'
    assert nonexecuting_action(text) is True
    assert execution_constraint(text) == 'PROCEDURE_QUESTION'
    parsed = {'intent': 'SUBSCRIPTION_QUERY', 'parse_meta': {
        'dialogue_act': 'QUERY',
        'goals': [{'id': 'q1', 'act': 'QUERY', 'resource': 'subscriptions', 'fields': ['records'], 'requires_record': True}]
    }}
    res = enforce_inquiry(parsed, text)
    assert res['intent'] == 'BANKING_HELP'
    assert res['slots']['help_topic'] == 'subscription'
    assert res['parse_meta']['dialogue_act'] == 'EXPLAIN'


def test_transfer_with_proposal_confirmation_display_creates_proposal():
    """Explicit transfer with tail '先给我看一下确认要求吧' creates proposal, not banking help."""
    from bank_agent.parsing import local_parse
    parsed = local_parse('那我想给李四转300元，先给我看一下确认要求吧。')
    assert parsed['intent'] == 'TRANSFER'
    assert parsed['amount'] == 300.0
    assert parsed['target'] == '李四'


def test_validate_coverage_unknown_id_raises_detailed_error():
    """Unknown evidence id in coverage raises DraftValidationError with actionable code."""
    from bank_agent.goal_answering import _validate_coverage
    from bank_agent.answering import DraftValidationError
    with pytest.raises(DraftValidationError) as exc:
        _validate_coverage([{'goal_id': 'g1', 'status': 'ANSWERED', 'evidence_ids': ['unknown_id'], 'reason': 'test'}],
                           [{'id': 'g1', 'act': 'EXPLAIN'}], [])
    assert str(exc.value) == 'GOAL_COVERAGE_FIELDS'
    assert exc.value.details.get('evidence_id') == 'unknown_id'

