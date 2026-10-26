"""Prepared follow-ups must work when clicked, including with an overpermissive model."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

import main
import security
from banking_knowledge import TOPICS, followup_catalog
from banking_rules import rule_documents
from bank_agent import runtime, suggestions
from test_agent_gateway import _headers, _send, gateway  # noqa: F401
from test_inquiry_boundaries import financial_state
from test_suggestion_quality import state


def test_prepared_coverage_references_real_rules_and_topics():
    known={row['id'] for row in rule_documents()}
    catalog=followup_catalog()
    assert len({row['id'] for row in catalog})==len(catalog)
    for row in catalog:
        assert set(row['from_topics'])<=TOPICS and row['coverage'] and row['excluded']
        assert set(row['rule_ids'])<=known
        assert all(2<=len(text)<=20 for text in row['questions'])


@pytest.mark.parametrize('intent,question',[(row['intent'],text) for row in followup_catalog() for text in row['questions']])
def test_every_registered_question_enters_its_prepared_chain_without_mutation(gateway,intent,question):  # noqa: F811
    client,_=gateway
    before=financial_state()
    reply=_send(client,question,conversation_id='prepared-click')
    if intent=='SUBSCRIPTION_QUERY':
        assert reply['approval'] and reply['approval']['risk_level']=='YELLOW'
        cancelled=client.post('/api/v1/agent/approvals/'+reply['approval']['approval_id'],headers=_headers('user_zhang'),json={'decision':'reject'})
        assert cancelled.status_code==200 and cancelled.json()['business_status']=='CANCELLED'
    else:
        assert reply['approval'] is None
        if intent == 'WEALTH_RECOMMEND' and reply['business_status']=='NEEDS_INPUT':
            assert reply['intent']=='WEALTH_RISK_ASSESSMENT'
            assert reply['result']['risk_quiz']
        else:
            assert reply['intent']==intent,reply
            assert reply['business_status']=='SUCCEEDED'
        assert '具体要办理什么' not in reply['reply']
    assert financial_state()[:4]==before[:4]


@pytest.mark.parametrize('text',[
    '争议工单多久能退款','真人客服处理到哪了','删除这张争议工单','商户扣款失败会重试几次',
    '视频会员什么时候恢复权益','帮我查蛋糕送到哪里了','预测理财下个月收益','查看我的实时任务进度',
    '如何设定订阅扣款重试次数','怎么修改会员自动续费日期',
])
def test_invented_questions_cannot_pass_even_with_real_citations_and_all_positive_votes(monkeypatch,text):
    current=state(user_input='查看订阅和争议工单',final_response='可查看登记记录。')
    facts=suggestions._facts(current)
    target=facts['prepared']['targets'][0]
    row={'index':0,'relevant':True,'not_answered':True,'supported':True,'safe_next':True,'answerable':True,
         'support_id':target['id'],'rule_ids':target['rule_ids']}
    monkeypatch.setattr(suggestions,'invoke_model',lambda *args:SimpleNamespace(content=json.dumps({'reviews':[row]})))
    assert suggestions._review([text],facts)==[]
    # The same words cannot bypass review by appearing as an explicit example.
    current['final_response']='可以说“'+text+'”。'
    current['suggestions']=[text]
    assert suggestions.instant_suggestions(current)==[]


def test_unprepared_model_prediction_is_dropped_before_review(monkeypatch):
    monkeypatch.setattr(runtime,'llm',object())
    calls=[]
    def invoke(*args):
        calls.append(args)
        return SimpleNamespace(content='["争议工单多久能退款"]')
    monkeypatch.setattr(suggestions,'invoke_model',invoke)
    assert suggestions.followup_suggestions(state())==([], 'none')
    assert len(calls)==1


@pytest.mark.parametrize('patch',[
    {'support_id':'made.up'}, {'rule_ids':[]}, {'rule_ids':['guide.subscription.failure']},
    {'rule_ids':['guide.dispute.query']*2}, {'answerable':False}, {'answerable':'true'},
])
def test_missing_wrong_or_unproven_support_is_rejected(monkeypatch,patch):
    facts=suggestions._facts(state())
    row={'index':0,'relevant':True,'not_answered':True,'supported':True,'safe_next':True,'answerable':True,
         'support_id':'disputes','rule_ids':['guide.dispute.query'],**patch}
    monkeypatch.setattr(suggestions,'invoke_model',lambda *args:SimpleNamespace(content=json.dumps({'reviews':[row]})))
    assert suggestions._review(['查看已有争议工单'],facts)==[]


def test_balance_context_cannot_offer_wealth_even_if_reviewer_says_yes(monkeypatch):
    facts=suggestions._facts(state(user_input='查一下余额',intent='BALANCE_QUERY'))
    assert 'products' not in {row['id'] for row in facts['prepared']['targets']}
    row={'index':0,'relevant':True,'not_answered':True,'supported':True,'safe_next':True,'answerable':True,
         'support_id':'products','rule_ids':['guide.wealth.read']}
    monkeypatch.setattr(suggestions,'invoke_model',lambda *args:SimpleNamespace(content=json.dumps({'reviews':[row]})))
    assert suggestions._review(['查看理财产品'],facts)==[]


def test_single_confirmed_merchant_binds_only_registered_field_questions(gateway):  # noqa: F811
    client,_=gateway
    reply=_send(client,'视频会员每期扣多少钱',conversation_id='bound-merchant')
    response=client.post('/api/v1/agent/approvals/'+reply['approval']['approval_id'],headers=_headers('user_zhang'),json={'decision':'approve'})
    assert response.status_code==200
    completed=response.json()
    current=state(user_input='视频会员每期扣多少钱',final_response=completed['reply'],intent='SUBSCRIPTION_QUERY',
                  execution_result=completed['result']['execution_result'])
    facts=suggestions._facts(current)
    questions=suggestions._prepared_questions(facts)
    assert '视频会员登记状态是什么' in questions and '视频会员原计划何时扣款' in questions
    assert '视频会员每期扣多少钱' not in questions
    assert all('Apple' not in text and '下次' not in text and '退款' not in text for text in questions)


def test_live_payee_choices_are_structured_exclude_self_and_click_to_confirmation(gateway):  # noqa: F811
    client,_=gateway
    reply=_send(client,'我要转账300元',conversation_id='prepared-payee')
    chips=reply['result']['suggestions']
    assert '李四' in chips and '张三' not in chips
    before=financial_state()
    reply=_send(client,'李四',conversation_id='prepared-payee')
    assert reply['approval'] and reply['approval']['risk_level']=='YELLOW'
    assert '李四' in reply['approval']['summary'] and '300' in reply['approval']['summary']
    assert financial_state()[:4]==before[:4] and financial_state()[4] != before[4]


def test_recommendation_ranking_does_not_call_core_or_change_authorization(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    _send(client,'争议工单多久有结果',conversation_id='prepared-readonly')
    before=deepcopy((main.ACCOUNTS,main.CARDS,main.TRANSACTIONS,main.DISPUTE_CASES,security.AUTH_TOKENS))
    from test_model_suggestions import _fake_model,_suggest
    _fake_model(monkeypatch,['查看已有争议工单'])
    def no_transport(*args,**kwargs):
        raise AssertionError('Recommendations cannot probe Core or precheck a future action')
    monkeypatch.setattr(runtime,'_http',no_transport)
    assert _suggest(client,'prepared-readonly')['suggestions']==['查看已有争议工单']
    assert before==(main.ACCOUNTS,main.CARDS,main.TRANSACTIONS,main.DISPUTE_CASES,security.AUTH_TOKENS)
