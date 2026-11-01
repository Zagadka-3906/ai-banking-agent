"""Live R009 regressions: one complete list, then a navigation-only follow-up."""
from copy import deepcopy

import pytest

import main
from bank_agent.answering import _validate_draft
from bank_agent.query_focus import contact_documents, contact_navigation_only, needs_contact_records
from test_agent_gateway import _send, gateway  # noqa: F401
from test_general_dialogue_loop import model, goal
from test_inquiry_boundaries import financial_state


def listing_facts():
    return contact_documents({'code':200,'data':[
        {'contact_id':'CTT1','name':'李四','phone':'13900139000'},
        {'contact_id':'CTT2','name':'王美（爱人）','phone':'13700137000'}]}, {'user_input':'查看我的联系人列表'})


def test_complete_natural_list_does_not_get_appended_again_and_other_fields_survive():
    facts = [*listing_facts(), {'id':'card.limit','text':'日额度为¥5,000.00。'}]
    answer = '已保存的联系人有2位：李四、王美（爱人）。日额度为{{value.0}}。'
    result, _ = _validate_draft({'answer':answer,'evidence_ids':[r['id'] for r in facts],'unanswered':[]},facts,
                               {'value.0':{'text':'¥5,000.00','evidence_id':'card.limit'}})
    assert result == '已保存的联系人有2位：李四、王美（爱人）。日额度为¥5,000.00。'


@pytest.mark.parametrize('answer',[
    '已保存的联系人有2位：李四。',
    '如果已保存的联系人有2位：李四、王美（爱人）。',
    '不是已保存的联系人有2位：李四、王美（爱人）。',
    '他已保存的联系人有2位：李四、王美（爱人）。',
    '已保存的联系人有2位：李四、李四。',
])
def test_partial_conditional_wrong_subject_or_repeated_names_keep_full_required_list(answer):
    facts=listing_facts()
    result,_=_validate_draft({'answer':answer,'evidence_ids':[r['id'] for r in facts],'unanswered':[]},facts,{})
    assert all(r['text'] in result for r in facts)


def test_equivalent_list_cannot_remove_truncation_notice():
    facts=listing_facts()
    facts += [{'id':'read.contacts.3','source':'core://contacts','required':True,
               'text':'这里只展示前45位，请在侧边栏 → 联系人查看完整列表。'}]
    result,_=_validate_draft({'answer':'已保存的联系人有2位：李四、王美（爱人）。',
                            'evidence_ids':[r['id'] for r in facts],'unanswered':[]},facts,{})
    assert facts[-1]['text'] in result and result.count('李四')==1


@pytest.mark.parametrize('question',[
    '联系人页面从哪里进去看？', '常用收款人列表从哪里打开？',
    '刚才这些联系人我在界面哪里找？', '我的联系人可以按名字搜索吗？在哪里操作？',
])
def test_navigation_does_not_require_personal_record_reads(question):
    assert contact_navigation_only(question) and not needs_contact_records(question)


@pytest.mark.parametrize('question',[
    '联系人页面在哪里打开，顺便查一下李四有没有保存？',
    '帮我查询联系人名单和银行卡日额度。',
    '联系人有多少人，在哪里看？', '联系人里有没有王美？',
    '联系人页面在哪里，另外给李四转账20元。',
    '联系人在哪里打开，顺便删除李四。',
])
def test_mixed_or_actual_record_queries_still_need_records(question):
    assert not contact_navigation_only(question) and needs_contact_records(question)


def test_navigation_followup_cannot_replay_contacts_or_cards_even_if_planner_requests_them(gateway,monkeypatch):  # noqa: F811
    from bank_agent import runtime
    client,_=gateway
    model(monkeypatch,[goal('contacts',['records'],'已保存联系人名单'),goal('cards',['daily_limit'],'卡片日额度')])
    first=_send(client,'帮我一起查一下已保存的联系人名单和银行卡的日额度。',conversation_id='r009-navigation')
    assert first['business_status']=='SUCCEEDED'
    reads=[]
    original=runtime._http
    def http(method,path,**kwargs):
        reads.append((method,path))
        return original(method,path,**kwargs)
    monkeypatch.setattr(runtime,'_http',http)
    def draft(payload):
        assert not any(row['id'].startswith('read.') for row in payload['facts'])
        row=next(row for row in payload['facts'] if row['id']=='guide.contacts.entry')
        return {'answer':'打开 App 侧边栏 → 联系人，即可查看和搜索联系人。','evidence_ids':[row['id']],
                'unanswered':[],'coverage':[{'goal_id':'q1','status':'ANSWERED','evidence_ids':[row['id']],'reason':'页面入口'}]}
    model(monkeypatch,[goal('contacts',['records'],'联系人页面从哪里进去看')],draft=draft,
          planner=lambda payload:{'reads':[{'tool':'contacts','fields':['records']},{'tool':'cards','fields':['daily_limit']}]})
    before=financial_state()
    contacts_before=deepcopy(main.USER_CONTACTS)
    reply=_send(client,'联系人页面从哪里进去看？',conversation_id='r009-navigation')
    assert reply['business_status']=='SUCCEEDED' and '侧边栏' in reply['reply']
    assert all('service-guide' in path for _,path in reads)
    assert all(word not in reply['reply'] for word in ['李四','王美','5000','5,000','2位'])
    assert financial_state()==before and main.USER_CONTACTS==contacts_before and reply['approval'] is None
