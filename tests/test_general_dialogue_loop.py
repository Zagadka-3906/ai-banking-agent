"""Shared semantic planning and evidence repair across business domains."""
import json
from types import SimpleNamespace

import pytest

from bank_agent import runtime
from bank_agent.consultation import read_evidence, valid_read_step
from bank_planning.requests import normalize_goals
from test_agent_gateway import _send, _headers, gateway  # noqa: F401
from test_inquiry_boundaries import financial_state


def goal(resource, fields, question, *, act='QUERY', subject=None):
    return dict(act=act,resource=resource,fields=fields,question=question,subject=subject)


def model(monkeypatch, goals, *, intent='BANKING_HELP', draft=None, planner=None, review=None):
    calls=[]
    def invoke(messages):
        system=str(messages[0].content)
        try:
            payload=json.loads(messages[-1].content)
        except ValueError:
            payload={}
        calls.append((system,payload))
        if '只读咨询规划器' in system:
            result=planner(payload) if planner else {'reads':[]}
        elif '独立银行回答核验器' in system:
            result=review(payload) if review else dict(in_scope=True,grounded=True,answers_question=True,safe=True,coverage=payload['coverage'],feedback=[])
        elif '银行业务解答助手' in system:
            if draft:
                result=draft(payload)
            else:
                ids=[]
                text=[]
                coverage=[]
                for item in payload['goals']:
                    rows=[row for row in payload['facts'] if row['id'].startswith('read.'+item['resource']+'.')]
                    if item['act']=='EXPLAIN' and not item['fields']:
                        rows=[row for row in payload['facts'] if row['id'].startswith('guide.')][:1]
                    chosen=rows[:2]
                    text += [item['question']+'：'+(''.join(row['text'] for row in chosen) if chosen else '尚缺对应记录。')]
                    used=[row['id'] for row in chosen]
                    ids+=used
                    coverage.append(dict(goal_id=item['id'],status='ANSWERED' if chosen else 'NEEDS_DATA',evidence_ids=used,reason='对应字段' if chosen else '需补查'))
                result=dict(answer='\n'.join(text),evidence_ids=list(dict.fromkeys(ids)),unanswered=[],coverage=coverage)
        else:
            result=dict(intent=intent,dialogue_act='QUERY',scope='IN_SCOPE',topic_relation='NEW',goals=goals)
        return SimpleNamespace(content=json.dumps(result,ensure_ascii=False))
    monkeypatch.setattr(runtime,'llm',SimpleNamespace(invoke=invoke))
    return calls


@pytest.mark.parametrize('value',[
 [],[{}],[dict(act='QUERY',resource='remote',question='查数据')],
 [dict(act='QUERY',resource='cards',question='卡',fields=['password'])],
 [dict(act='QUERY',resource='cards',question='卡',fields=['status','status'])],
 [dict(act='QUERY',resource='cards',question='卡',subject='x'*121)],
 [dict(act='QUERY',resource='cards',question='卡',user_id='user_li')],
])
def test_goal_schema_is_bounded_and_cannot_add_identity_or_secret_fields(value):
    with pytest.raises(ValueError):
        normalize_goals(value,'查询卡片',{})


def test_ungrounded_subject_requires_clarification_instead_of_querying_everything():
    result=normalize_goals([goal('cards',['status'],'卡状态',subject='CARD-999')],'查我的卡片',{})
    assert result[0]['act']=='UNCLEAR' and result[0]['needs_subject']
    assert normalize_goals([goal('cards',['status'],'卡状态',subject='CARD-001')],'查CARD-001',{})[0]['act']=='QUERY'


@pytest.mark.parametrize('step',[
 {'tool':'tasks','user_id':'user_li'}, {'tool':'wealth_products','url':'https://evil.invalid'},
 {'tool':'risk_profile','method':'POST'}, {'tool':'tasks','fields':['payload']},
 {'tool':'cards','fields':['status',True]}, {'tool':'subscriptions'},
 {'tool':'tasks','fields':[]}, {'tool':'risk_profile','fields':['answers']},
])
def test_unified_reads_cannot_request_writes_other_owners_or_private_scoring(step):
    assert not valid_read_step(step)


@pytest.mark.parametrize('tool,fields',[
 ('balance',['available_balance']),('cards',['status','channels']),('wealth_products',['name','min_amount']),
 ('wealth_holdings',['principal']),('risk_profile',['risk_profile']),('tasks',['status']),('contacts',['saved']),
])
def test_typed_evidence_tools_use_actual_core_without_business_or_authorization_changes(gateway,tool,fields):  # noqa: F811
    before=financial_state()
    docs=read_evidence({'user_id':'user_zhang','user_input':'查询本人记录'},{'tool':tool,'fields':fields})
    assert docs and all(row['source']=='core://'+tool and row.get('fields') for row in docs)
    assert financial_state()==before


def test_task_evidence_filters_owner_and_never_includes_raw_payload(gateway):  # noqa: F811
    from scheduler import TASK_STORE
    own=TASK_STORE.create('user_zhang','SCHEDULED_TRANSFER',{'amount':40,'password':'DO-NOT-PROJECT'})
    TASK_STORE.create('user_li','ORDER_DRAFT',{'amount':999})
    docs=read_evidence({'user_id':'user_zhang','user_input':'查看待办'},{'tool':'tasks','fields':['status','amount']})
    encoded=json.dumps(docs,ensure_ascii=False)
    assert own.task_id in encoded and 'DO-NOT-PROJECT' not in encoded and '999' not in encoded


def test_two_goals_read_different_sources_and_answer_both_without_regex_reclassification(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    model(monkeypatch,[goal('balance',['available_balance'],'现在能动用多少'),goal('cards',['status'],'银行卡现在什么状态')])
    before=financial_state()
    reply=_send(client,'我现在能动用多少，再核实一下银行卡状态',conversation_id='two-independent-goals')
    assert reply['intent']=='BANKING_HELP' and reply['business_status']=='SUCCEEDED' and reply['approval'] is None
    assert '可用余额' in reply['reply'] and 'ACTIVE' in reply['reply']
    coverage=reply['result']['answer_presentation']['coverage']
    assert len(coverage)==2 and all(row['status']=='ANSWERED' for row in coverage)
    assert financial_state()==before


def test_a_model_read_plan_is_not_overwritten_by_a_background_keyword(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    model(monkeypatch,[goal('balance',['available_balance'],'还可以用多少钱')],intent='BALANCE_QUERY')
    reply=_send(client,'转账先放着，我只想知道还可以用多少钱',conversation_id='semantic-kept')
    assert reply['intent']=='BALANCE_QUERY' and '可用余额' in reply['reply'] and reply['approval'] is None


def test_rules_cannot_count_as_personal_record_evidence_even_with_permissive_review(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    def draft(payload):
        rule=next(row['id'] for row in payload['facts'] if row['id'].startswith('guide.'))
        return dict(answer='银行卡已被冻结。',evidence_ids=[rule],unanswered=[],coverage=[dict(goal_id='q1',status='ANSWERED',evidence_ids=[rule],reason='规则')])
    model(monkeypatch,[goal('cards',['status'],'卡片状态')],draft=draft)
    before=financial_state()
    reply=_send(client,'我的银行卡现在处于什么状态',conversation_id='not-rule-fact')
    assert reply['result']['answer_presentation']['mode']=='FIELD_FALLBACK'
    assert '被冻结' not in reply['reply'] and 'ACTIVE' in reply['reply']
    assert financial_state()==before


def test_answer_gap_drives_a_second_read_before_regeneration(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    drafts=[]
    def draft(payload):
        drafts.append(payload)
        rows=[row for row in payload['facts'] if row['id'].startswith('read.cards.')]
        if not any('channels' in row.get('fields',[]) for row in rows):
            row=rows[0]
            return dict(answer='还需要核对交易渠道。',evidence_ids=[row['id']],unanswered=['缺少渠道'],coverage=[dict(goal_id='q1',status='NEEDS_DATA',evidence_ids=[row['id']],reason='需补查网上支付和境外交易渠道')])
        row=next(row for row in rows if 'channels' in row.get('fields',[]))
        return dict(answer=row['text'],evidence_ids=[row['id']],unanswered=[],coverage=[dict(goal_id='q1',status='ANSWERED',evidence_ids=[row['id']],reason='已补查')])
    def planner(payload):
        return {'reads':[{'tool':'cards','fields':['status','channels']}]} if payload['feedback'] else {'reads':[]}
    calls=model(monkeypatch,[goal('cards',['status'],'卡片状态及渠道')],draft=draft,planner=planner)
    reply=_send(client,'核实一下我的卡状态和交易渠道',conversation_id='gap-repair')
    assert reply['business_status']=='SUCCEEDED' and len(drafts)==2 and '交易渠道' in reply['reply']
    budget=reply['result']['execution_result']['data']['read_budget']
    assert len(budget['completed'])<=budget['maximum'] and budget['rounds']<=3
    assert any(payload.get('feedback') for system,payload in calls if '只读咨询规划器' in system)


def test_mixed_subscription_query_waits_for_confirmation_then_answers_rule_and_state(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    model(monkeypatch,[goal('subscriptions',['status'],'视频会员登记状态',subject='视频会员'),
                       goal('subscriptions',[],'扣款失败后会自动重试吗',act='EXPLAIN')])
    before=financial_state()
    reply=_send(client,'只查视频会员现在的登记状态，也解释扣款失败后会不会重试，不要取消',conversation_id='confirmed-multi')
    assert reply['approval'] and reply['approval']['risk_level']=='YELLOW'
    assert financial_state()[:4]==before[:4]
    completed=client.post('/api/v1/agent/approvals/'+reply['approval']['approval_id'],headers=_headers('user_zhang'),json={'decision':'approve'})
    assert completed.status_code==200
    result=completed.json()
    assert '生效中' in result['reply'] and 'coverage' in result['result']['answer_presentation']
    assert financial_state()[:4]==before[:4]


def test_read_failure_is_partial_and_not_reported_as_complete(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    model(monkeypatch,[goal('balance',['available_balance'],'可用余额'),goal('tasks',['status'],'任务状态')])
    original=runtime._http
    def transport(method,path,**kwargs):
        if path=='tasks':
            return SimpleNamespace(status_code=503,json=lambda:{'code':503})
        return original(method,path,**kwargs)
    monkeypatch.setattr(runtime,'_http',transport)
    before=financial_state()
    reply=_send(client,'同时核实可用余额和待办任务状态',conversation_id='partial-query')
    assert reply['business_status']=='PARTIAL_SUCCESS'
    assert '读取失败' in reply['reply'] and '可用余额' in reply['reply']
    assert financial_state()==before


def test_goal_questions_and_subjects_are_not_written_to_call_audit(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    import bank_agent.nodes.intent as node
    events=[]
    monkeypatch.setattr(node,'audit_event',lambda *args,**kwargs:events.append(kwargs))
    model(monkeypatch,[goal('contacts',['saved'],'核对某测试人是否保存',subject='某测试人')])
    _send(client,'某测试人是否已保存联系人',conversation_id='goal-audit-private')
    metadata=[row.get('metadata',{}) for row in events]
    assert '某测试人' not in json.dumps(metadata,ensure_ascii=False)


@pytest.mark.parametrize('proof,expected',[
 ({'text':'两百元','value':200},200),({'text':'两百元','value':201},None),
 ({'text':'三百元','value':300},None),({'text':'两百元','value':True},None),
 ({'text':'两百元','value':float('inf')},None),
])
def test_parameter_spans_need_literal_source_and_independent_numeric_verification(monkeypatch,proof,expected):
    from bank_agent import parsing
    original=parsing.local_parse
    def incomplete(text):
        value=original(text)
        value['amount']=None
        return value
    monkeypatch.setattr(parsing,'local_parse',incomplete)
    monkeypatch.setattr(runtime,'llm',SimpleNamespace(invoke=lambda messages:SimpleNamespace(content=json.dumps({
        'intent':'TRANSFER','dialogue_act':'ACTION','amount':999,'parameter_evidence':{'amount':proof}}))))
    assert parsing.model_parse('给李四转两百元')['amount']==expected


def test_parameter_proof_cannot_replace_existing_or_ambiguous_amount(monkeypatch):
    from bank_agent.parsing import model_parse
    monkeypatch.setattr(runtime,'llm',SimpleNamespace(invoke=lambda messages:SimpleNamespace(content=json.dumps({
        'intent':'TRANSFER','dialogue_act':'ACTION','parameter_evidence':{'amount':{'text':'200元','value':200}}}))))
    assert model_parse('给李四转300元，备注200元')['amount']==300
    assert model_parse('给李四转200元或者300元')['amount'] is None


def test_a_canonical_product_alias_is_bound_by_current_source_not_invented(monkeypatch):
    from bank_agent.parsing import model_parse
    from bank_agent.understanding import understanding_context
    model(monkeypatch,[goal('wealth_products',['min_amount'],'零星宝起购多少',subject='LX00118')])
    with understanding_context({'user_id':'user_zhang','user_input':'零星宝起购多少'}):
        result=model_parse('零星宝起购多少')
    assert result['parse_meta']['goals'][0]['act']=='QUERY'


def test_refresh_keeps_prior_requested_fields_and_has_one_total_budget(gateway):  # noqa: F811
    from bank_agent.evidence_session import EvidenceSession
    from bank_planning.requests import normalize_goals
    state={'user_id':'user_zhang','user_input':'卡的状态和渠道','parse_meta':{'goals':normalize_goals([goal('cards',['status'],'卡的状态')],'卡的状态和渠道',{})}}
    session=EvidenceSession(state)
    assert session.read({'tool':'cards','fields':['status']})
    assert session.read({'tool':'cards','fields':['channels']})
    fields={key for row in session.documents.values() for key in row.get('fields',[])}
    assert {'status','channels'}<=fields
    assert not session.read({'tool':'cards','fields':['channels','status']})
    assert len(session.completed)==2


def test_missing_per_goal_review_cannot_approve_a_complete_answer(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    model(monkeypatch,[goal('cards',['status'],'卡状态')],review=lambda payload:dict(in_scope=True,grounded=True,answers_question=True,safe=True,feedback=[]))
    reply=_send(client,'卡现在是什么状态',conversation_id='review-needs-coverage')
    assert reply['result']['answer_presentation']['mode']=='FIELD_FALLBACK'


def test_numeric_presentation_equivalence_is_allowed_but_identifiers_stay_exact():
    from bank_agent.answering import _validate_draft
    facts=[{'id':'read.cards.0','text':'本次匹配共1条；CARD-001；年化收益率2.00%。','required':False}]
    assert _validate_draft(dict(answer='匹配1.0条，年化收益率2%。',evidence_ids=['read.cards.0'],unanswered=[]),facts,{})[0]
    with pytest.raises(ValueError):
        _validate_draft(dict(answer='卡片是CARD-1。',evidence_ids=['read.cards.0'],unanswered=[]),facts,{})


def test_new_goal_answers_honor_shadow_mode(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    monkeypatch.setenv('BANK_ANSWER_MODE','shadow')
    model(monkeypatch,[goal('cards',['status'],'卡状态')])
    reply=_send(client,'核对我的卡片状态',conversation_id='goal-shadow')
    assert (reply.get('result') or {}).get('answer_presentation') is None
    assert '登记状态' in reply['reply']


def test_disabled_generation_still_returns_verified_goal_fields(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    monkeypatch.setenv('BANK_ANSWER_PLANNING','0')
    calls=model(monkeypatch,[goal('cards',['status'],'卡状态')])
    reply=_send(client,'核对我的卡片状态',conversation_id='goal-disabled')
    assert reply['result']['answer_presentation']['mode']=='FIELD_FALLBACK'
    assert not any('银行业务解答助手' in system for system,payload in calls)


def test_goal_aware_pending_inquiry_preserves_original_approval(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    original=_send(client,'给李四转300元',conversation_id='goal-pending')
    before=financial_state()
    model(monkeypatch,[goal('balance',['available_balance'],'可用余额'),goal('cards',['status'],'卡状态')])
    reply=_send(client,'先核实一下可用余额和卡状态',conversation_id='goal-pending')
    assert reply['approval']['approval_id']==original['approval']['approval_id']
    assert reply['result']['side_inquiry'] and len(reply['result']['answer_presentation']['coverage'])==2
    assert financial_state()==before


def test_comparison_binds_each_known_product_and_requires_evidence_for_both(gateway):  # noqa: F811
    from bank_agent.goal_answering import _validate_coverage
    from bank_agent.evidence import project_records
    from bank_agent.extraction import PRODUCT_ALIASES
    context={'grounded_aliases':{name:PRODUCT_ALIASES[name] for name in ('零星宝','恒泉稳利')}}
    goals=normalize_goals([goal('wealth_products',['min_amount'],'比较起购',subject='零星宝和恒泉稳利')],'比较零星宝和恒泉稳利',context)
    assert goals[0]['subjects']==['零星宝','恒泉稳利']
    docs=read_evidence({'user_id':'user_zhang','user_input':'比较零星宝和恒泉稳利'},
                       {'tool':'wealth_products','subjects':goals[0]['subjects'],'fields':['min_amount']})
    item=dict(goal_id='q1',status='ANSWERED',evidence_ids=[row['id'] for row in docs],reason='已对照')
    assert _validate_coverage([item],goals,docs)
    incomplete=[row for row in docs if '恒泉稳利' not in row.get('entity_names',[])]
    with pytest.raises(ValueError,match='MISSING_SUBJECT_RECORD'):
        _validate_coverage([{**item,'evidence_ids':[row['id'] for row in incomplete]}],goals,incomplete)
    absent=project_records('wealth_products',{'code':200,'data':[]},{'user_id':'user_zhang'},
                           fields=['min_amount'],subjects=['零星宝','恒泉稳利'])
    assert {row['subject'] for row in absent}=={'零星宝','恒泉稳利'}


def test_explaining_a_field_is_distinct_from_querying_its_personal_value():
    from bank_planning.requests import needs_record, goal_read
    explained=normalize_goals([goal('subscriptions',['status','next_charge'],'ACTIVE与原计划日期是什么意思',act='EXPLAIN')],
                              'ACTIVE与原计划日期是什么意思',{})[0]
    assert not needs_record(explained) and goal_read(explained) is None
    personal=normalize_goals([{**goal('subscriptions',['status'],'我的登记状态',act='EXPLAIN'),'requires_record':True}],
                            '我的登记状态',{})[0]
    assert needs_record(personal)


def test_currency_decimals_are_not_dates_but_invalid_dates_still_need_clarification():
    from intent_language import requested_date_range
    assert requested_date_range('消费合计47.90元，可用余额15000元，它们是同一回事吗') is None
    result=requested_date_range('查2026年2月30日的消费')
    assert result == {'error':'DATE_RANGE_INVALID'}


def test_read_comparison_does_not_require_one_payment_amount(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    model(monkeypatch,[dict(act='QUERY',resource='wealth_products',fields=['min_amount'],question='比较两款起购差额',subjects=['零星宝','恒泉稳利'])])
    reply=_send(client,'零星宝1元和恒泉稳利1000元起购，金额差多少？',conversation_id='compare-not-pay')
    assert '最终金额' not in reply['reply'] and reply['approval'] is None
    data=reply['result']['execution_result']['data']
    assert any('999.00' in row['text'] and row.get('derived') for row in data['documents'])


def test_read_refresh_unions_objects_and_preserves_both_fields(gateway):  # noqa: F811
    from bank_agent.evidence_session import EvidenceSession
    state={'user_id':'user_zhang','user_input':'比较零星宝与恒泉稳利','parse_meta':{'goals':normalize_goals(
        [dict(act='QUERY',resource='wealth_products',fields=['min_amount'],question='比较',subjects=['零星宝','恒泉稳利'])],
        '比较零星宝与恒泉稳利',{})}}
    session=EvidenceSession(state)
    assert session.read({'tool':'wealth_products','subjects':['零星宝'],'fields':['min_amount']})
    assert session.read({'tool':'wealth_products','subjects':['恒泉稳利'],'fields':['risk_level']})
    for selector in ('零星宝','恒泉稳利'):
        assert {'min_amount','risk_level'} <= {field for row in session.facts() if selector in row.get('entity_names',[]) for field in row['fields']}


def test_literal_money_codec_uses_only_exact_cited_values_and_preserves_balance_field_checks():
    from bank_agent.answering import _anchored_facts, _validate_draft
    from bank_agent.goal_answering import _bind_literal_values
    facts=[dict(id='read.balance.0',text='可用余额¥10.00。',source='core://balance',fields=['available_balance']),
           dict(id='read.balance.1',text='总余额¥12.00。',source='core://balance',fields=['total_balance'])]
    _,anchors=_anchored_facts(facts)
    def validate(text,ids):
        plan=dict(answer=text,evidence_ids=ids,unanswered=[])
        return _validate_draft(_bind_literal_values(plan,facts,anchors),facts,anchors)
    assert '10.00' in validate('可用余额10元。',['read.balance.0'])[0]
    with pytest.raises(ValueError):
        validate('可用余额11元。',['read.balance.0'])
    with pytest.raises(ValueError):
        validate('可用余额12元。',['read.balance.0'])
    with pytest.raises(ValueError):
        validate('可用余额12元。',['read.balance.0','read.balance.1'])


def test_followup_explanation_refreshes_previous_sources_without_trusting_old_amounts(gateway,monkeypatch):  # noqa: F811
    from bank_agent.evidence_session import EvidenceSession
    monkeypatch.setattr(runtime,'llm',None)
    state={'user_id':'user_zhang','memory_principal':'user_zhang','user_input':'刚才两类数字为何不同',
           'parse_meta':{'topic_relation':'CONTINUE','goals':normalize_goals([goal('general',[],'解释两类数字',act='EXPLAIN')],'刚才两类数字为何不同',{})},
           'observations':{'BANKING_HELP':{'read_requests':[{'tool':'balance','fields':['available_balance']}], 'amount':999999}}}
    session=EvidenceSession(state)
    session.bootstrap()
    assert any(row['source']=='core://balance' for row in session.facts())
    assert '999999' not in json.dumps(session.facts())
    other=EvidenceSession({**state,'memory_principal':'user_li'})
    other.bootstrap()
    assert not any(row['id'].startswith('read.balance.') for row in other.facts())


def test_transaction_counterparty_alias_is_exact_and_does_not_select_unrelated_merchants():
    from bank_agent.evidence import project_records
    rows=[dict(tx_id='TXN123456',merchant='转账给 李四',category='转账支出',amount=300),
          dict(tx_id='TXN234567',merchant='转账给 李四商店',category='餐饮',amount=100)]
    docs=project_records('transactions',{'code':200,'data':rows},{'user_id':'user_zhang'},fields=['amount'],subjects=['李四'])
    assert any('TXN123456' in row['text'] for row in docs)
    assert all('TXN234567' not in row['text'] for row in docs)


def test_explanation_with_missing_personal_fields_can_still_offer_verified_process_rules():
    from bank_agent.goal_answering import _fallback
    from bank_agent.evidence_session import EvidenceSession
    from banking_knowledge import knowledge_documents
    goals=normalize_goals([{**goal('transactions',['amount'],'怎么核对这笔交易是否本人消费',act='EXPLAIN'),'requires_record':True}],
                          '怎么核对这笔交易是否本人消费',{})
    session=EvidenceSession({'user_id':'user_zhang','parse_meta':{'goals':goals}})
    session.documents={row['id']:row for row in knowledge_documents('dispute')}
    text,coverage=_fallback(goals,session)
    assert '商户' in text and '订单' in text and 'NEEDS_DATA' == coverage[0]['status']


def test_limit_slot_accepts_explicit_value_before_change_verb_but_not_a_question():
    from intent_language import finite_limit_change
    assert finite_limit_change('不取消限额了，麻烦按每日10000元这个额度帮我改一下，暂不确认')
    assert not finite_limit_change('如果按每日10000元这个额度改，会怎样？')


def test_shared_consultation_retains_a_typed_followup_input_from_its_goal_topic():
    from bank_agent.dialogue_state import remember_discussion
    goals=normalize_goals([dict(act='EXPLAIN',resource='cards',topic='limits',question='能取消限额吗',fields=[])],'能取消限额吗',{})
    state={'user_id':'user_zhang','intent':'BANKING_HELP','user_input':'能取消限额吗','slots':{'help_topic':'card'},
           'parse_meta':{'goals':goals},'execution_result':{'type':'BANKING_HELP','data':{'documents':[{'id':'guide.limits.change'}]}}}
    discussion=remember_discussion(state,{})
    assert discussion['active_topic']=='limits' and discussion['next_input']=={'intent':'CARD_LIMIT','field':'amount'}


def test_deictic_object_uses_only_one_verified_entity_of_the_requested_resource():
    raw=[goal('cards',['channels'],'这张卡的渠道',subject='这张卡')]
    context={'recent_observations':[{'resource_entities':{'cards':['CARD-001'],'tasks':['TSK123456789ABC']}}]}
    selected=normalize_goals(raw,'这张卡的渠道',context)[0]
    assert selected['subjects']==['CARD-001'] and selected['act']=='QUERY'
    for entities in ([],['CARD-001','CARD-002']):
        ambiguous=normalize_goals(raw,'这张卡的渠道',{'recent_observations':[{'resource_entities':{'cards':entities}}]})[0]
        assert ambiguous['act']=='UNCLEAR' and ambiguous['needs_subject']


def test_verified_entity_memory_is_scoped_by_resource_and_principal(gateway,monkeypatch):  # noqa: F811
    from bank_agent.understanding import remember_reply, understanding_context, model_context
    state={'user_id':'user_zhang','intent':'BANKING_HELP','user_input':'查卡','execution_result':{'type':'BANKING_HELP','data':{
        'documents':[dict(id='read.cards.0',source='core://cards',entity_id='CARD-001',entity_names=['CARD-001'])]}}}
    remembered=remember_reply(state,{'final_response':'卡片已核对。'})
    assert remembered['observations']['BANKING_HELP']['resource_entities']=={'cards':['CARD-001']}
    with understanding_context({**state,**remembered,'user_id':'user_li'}):
        assert model_context()['recent_observations']==[]


def test_confirmed_single_subscription_exposes_only_requested_merchant_to_answer_model(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    calls=model(monkeypatch,[goal('subscriptions',['amount'],'视频会员每期金额',subject='视频会员')])
    before=financial_state()
    proposal=_send(client,'视频会员每期金额是多少？',conversation_id='subscription-evidence-minimized')
    assert proposal['approval']
    response=client.post('/api/v1/agent/approvals/'+proposal['approval']['approval_id'],headers=_headers('user_zhang'),json={'decision':'approve'})
    assert response.status_code==200
    payloads=[payload for system,payload in calls if '银行业务解答助手' in system]
    assert payloads and all('Apple' not in json.dumps(payload['facts'],ensure_ascii=False) for payload in payloads)
    assert financial_state()[:4]==before[:4]


def test_explanation_refresh_keeps_previous_period_when_model_calls_it_a_new_topic(gateway,monkeypatch):  # noqa: F811
    from bank_agent.evidence_session import EvidenceSession
    monkeypatch.setattr(runtime,'llm',None)
    state={'user_id':'user_zhang','memory_principal':'user_zhang','user_input':'消费合计与余额为何不同','date_scope':'ALL',
           'parse_meta':{'topic_relation':'NEW','goals':normalize_goals([goal('balance',[],'消费合计与余额为何不同',act='EXPLAIN')],'消费合计与余额为何不同',{})},
           'observations':{'BANKING_HELP':{'read_requests':[{'tool':'transactions','fields':['analysis']},{'tool':'balance','fields':['available_balance']}],
                                         'read_contexts':{'transactions':{'date_scope':'MONTH','date_range':None}}}}}
    session=EvidenceSession(state)
    session.bootstrap()
    assert session.contexts['transactions']['date_scope']=='MONTH'
    assert any(row['source']=='core://transactions' for row in session.facts())
    assert any(row['source']=='core://balance' for row in session.facts())


def test_pending_question_can_cite_the_original_approval_without_reprechecking(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    original=_send(client,'给李四转300元',conversation_id='pending-source-proof')
    before=financial_state()
    model(monkeypatch,[goal('general',[],'这笔待确认转账目前要求哪些验证')])
    reply=_send(client,'这笔待确认转账目前要求哪些验证',conversation_id='pending-source-proof')
    assert reply['approval']['approval_id']==original['approval']['approval_id']
    assert '300.00' in reply['reply'] and '只需用户确认' in reply['reply']
    assert financial_state()==before


@pytest.mark.parametrize('utterance',[
    '生日的事先放一放，还是接着给李四转账吧，金额就补300元，先别确认。',
    '好的，那先回到原来的转账，给李四的金额就按300元，先别确认，让我看一下。',
])
def test_explicit_resume_with_one_stored_target_and_verified_amount_survives_unclear_classification(gateway,monkeypatch,utterance):  # noqa: F811
    client,_=gateway
    cid='resume-despite-unclear'
    _send(client,'给李四转一点钱',conversation_id=cid)
    model(monkeypatch,[goal('general',[],'生日联动机制',act='EXPLAIN')])
    _send(client,'先放下转账，解释生日联动规则',conversation_id=cid)
    monkeypatch.setattr(runtime,'llm',SimpleNamespace(invoke=lambda messages:SimpleNamespace(content=json.dumps({
        'intent':'UNKNOWN','dialogue_act':'UNCLEAR','scope':'IN_SCOPE'}))))
    before=financial_state()
    proposal=_send(client,utterance,conversation_id=cid)
    assert proposal['approval'] and '李四' in proposal['approval']['summary'] and '300.00' in proposal['approval']['summary'], proposal['reply']
    assert financial_state()[:4]==before[:4]


def test_explaining_or_ambiguous_amount_cannot_auto_resume_a_stored_operation():
    from bank_agent.dialogue_state import resume_task
    state={'user_id':'user_zhang','dialogue_state':{'principal':'user_zhang','tasks':[{
        'id':'TRANSFER','pending':{'intent':'TRANSFER','missing':['amount'],'payload':{'intent':'TRANSFER','target':'李四'}}}]}}
    for text in ('继续解释李四300元转账的规则','接着给李四转300元或500元','如果接着给李四转300元会怎样',
                 '接着给王五转300元，备注李四','李四的事先放下，接着给王五转300元'):
        assert resume_task(state,{'intent':'UNKNOWN'},text) is None


def test_general_topic_cannot_erase_a_specific_limit_input_and_polite_payee_is_not_a_card(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    cid='specific-limit-input'
    model(monkeypatch,[goal('general',[],'能否取消卡片限额',act='EXPLAIN')])
    _send(client,'先问问能不能取消卡片限额，暂时不用修改',conversation_id=cid)
    import conversation
    state=conversation.get_graph().get_state({'configurable':{'thread_id':conversation.conversation_thread_id('user_zhang',cid)}}).values
    assert (state.get('dialogue_state') or {}).get('next_input')
    monkeypatch.setattr(runtime,'llm',SimpleNamespace(invoke=lambda messages:SimpleNamespace(content=json.dumps({'intent':'UNKNOWN','dialogue_act':'UNCLEAR'}))))
    before=financial_state()
    reply=_send(client,'先不取消。我希望把卡片日限额改成10000，暂时不确认，你先把要确认的内容给我看看。',conversation_id=cid)
    assert reply['approval'] and '10000.00' in reply['approval']['summary'].replace(',',''), reply['reply']
    assert '看看' not in reply['approval']['summary']
    assert financial_state()[:4]==before[:4]


def test_product_names_cannot_be_promoted_to_product_identifiers(monkeypatch):
    from bank_agent.parsing import model_parse
    monkeypatch.setattr(runtime,'llm',SimpleNamespace(invoke=lambda messages:SimpleNamespace(content=json.dumps({
        'intent':'WEALTH_COMPARE','dialogue_act':'QUERY','product_ids':['零宝','HQ30060']}))))
    parsed=model_parse('比较零宝和恒泉稳利的风险等级')
    assert parsed['product_ids']==['HQ30060']


@pytest.mark.parametrize('text,expected',[
    ('给李四转300,先展示确认信息',300),('给李四转300，先展示确认信息',300),
    ('日限额改成10000,暂时不确认',10000),('给李四转¥300.50,先不要确认',300.5),
    ('转300.然后展示确认信息',300),('给李四转300,00',None),
    ('给李四转300.500',None),('给李四转300,500.50',300500.5),
    ('给李四转300,或者500',None),
])
def test_amount_punctuation_boundary_does_not_truncate_malformed_money(text,expected):
    from bank_agent.extraction import amount_from_text
    from security import sanitize_text
    assert amount_from_text(sanitize_text(text,field='用户指令',max_length=2000))==expected


def test_showing_details_to_the_user_does_not_add_another_payment_recipient():
    from intent_language import ambiguous_target
    from bank_agent.extraction import target_from_text
    text='给李四转300元，麻烦你先把转账信息展示给我核对一下。'
    assert not ambiguous_target(text) and target_from_text(text)=='李四'
    assert ambiguous_target('给李四转300元，或者给我转300元')
    assert ambiguous_target('给李四转300元，收款人改为王五')


def test_declared_goal_protocol_gets_one_bounded_repair_when_goals_are_missing(monkeypatch):
    from bank_agent.parsing import model_parse
    calls=[]
    def invoke(messages):
        calls.append(messages)
        value={'goals':[goal('balance',['available_balance'],'可用余额')]} if len(calls)==2 else {
            'request_protocol':1,'intent':'BALANCE_QUERY','dialogue_act':'QUERY'}
        return SimpleNamespace(content=json.dumps(value,ensure_ascii=False))
    monkeypatch.setattr(runtime,'llm',SimpleNamespace(invoke=invoke))
    parsed=model_parse('查询可用余额')
    assert len(calls)==2 and parsed['parse_meta']['goal_repair']=='REPAIRED'
    assert parsed['parse_meta']['goals'][0]['resource']=='balance'


def test_recipient_amount_field_is_not_part_of_the_recipient_name():
    from bank_agent.extraction import target_from_text
    assert target_from_text('给李四的金额就按300元')=='李四'
    assert target_from_text('给王五的转账金额是500元')=='王五'


def test_internal_planning_rules_are_not_pasted_into_customer_fallback():
    from bank_agent.goal_answering import _fallback
    from bank_agent.evidence_session import EvidenceSession
    from banking_knowledge import knowledge_documents
    goals=normalize_goals([goal('general',[],'证据缺口怎么补查',act='EXPLAIN')],'证据缺口怎么补查',{})
    session=EvidenceSession({'user_id':'user_zhang','parse_meta':{'goals':goals}})
    session.documents={row['id']:row for row in knowledge_documents('general') if row['id'] in {'guide.general.coverage','guide.general.repair','guide.general.data_sources'}}
    text,coverage=_fallback(goals,session)
    assert '读取预算' not in text and 'goals' not in text and coverage[0]['status']=='NEEDS_DATA'


def test_contact_field_meaning_and_navigation_do_not_require_personal_contact_records(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    def draft(payload):
        rows=[row for row in payload['facts'] if row['id'].startswith('guide.contacts.') and ('保存' in row['text'] or '侧边栏' in row['text'])]
        ids=[row['id'] for row in rows]
        return dict(answer='\n'.join(row['text'] for row in rows),evidence_ids=ids,unanswered=[],coverage=[
            dict(goal_id='q1',status='ANSWERED',evidence_ids=ids,reason='字段含义和真实入口来自产品规则')])
    model(monkeypatch,[goal('contacts',['saved'],'联系人页保存状态含义及入口',act='EXPLAIN')],draft=draft)
    original=runtime._http
    def no_book(method,path,**kwargs):
        assert not path.startswith('contacts/'), 'A policy question must not read the contact book'
        return original(method,path,**kwargs)
    monkeypatch.setattr(runtime,'_http',no_book)
    before=financial_state()
    reply=_send(client,'不办理转账，联系人页面的保存状态是什么意思，在App哪里打开？',conversation_id='contact-policy-no-book')
    assert reply['business_status']=='SUCCEEDED' and '侧边栏' in reply['reply'] and '未能读取' not in reply['reply']
    assert financial_state()==before
