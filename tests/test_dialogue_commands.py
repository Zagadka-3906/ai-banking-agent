"""Discussion transitions must be source-bound and obtain fresh approvals."""
import json
from types import SimpleNamespace

import pytest

from bank_agent import runtime
from bank_agent.dialogue_commands import apply_command, normalize_command
from bank_agent.dialogue_state import dialogue_view, remember_discussion
from bank_agent.parsing import local_parse
from bank_agent.understanding import model_context
from test_agent_gateway import gateway, _send  # noqa: F401
from test_general_dialogue_loop import model, goal
from test_inquiry_boundaries import financial_state


def test_single_model_action_is_compiled_from_whole_utterance():
    from bank_agent.dialogue_commands import atomic_action_input
    parsed = {**local_parse('转账'), 'parse_meta': {'dialogue_act': 'ACTION',
        'goals': [{'id': 'q1', 'act': 'ACTION', 'resource': 'general', 'question': '给李四转300元',
                   'fields': [], 'subject': None, 'subjects': [], 'needs_subject': False, 'topic': 'transfer', 'requires_record': False}]}}
    text = '继续原先那个转账，给李四转300元'
    # The local splitter sees an incomplete prefix and a complete restatement.
    result = atomic_action_input(parsed, text)
    assert result and result['target'] == '李四' and result['amount'] == 300


@pytest.mark.parametrize('text', ['给李四转300元，然后给李四转300元', '给李四转300元，然后给王美转500元', '查余额，然后给李四转300元'])
def test_single_model_goal_cannot_flatten_two_actual_requests(text):
    from bank_agent.dialogue_commands import atomic_action_input
    parsed = {**local_parse('给李四转300元'), 'parse_meta': {'dialogue_act': 'ACTION',
        'goals': [{'id': 'q1', 'act': 'ACTION', 'resource': 'general', 'question': '转账',
                   'fields': [], 'subject': None, 'subjects': [], 'needs_subject': False, 'topic': 'transfer', 'requires_record': False}]}}
    assert atomic_action_input(parsed, text) is None


def pending_state(*, user='user_zhang', target='李四'):
    pending={'intent':'TRANSFER','missing':['amount'],'payload':{'intent':'TRANSFER','target':target,'amount':None}}
    return {'user_id':user,'memory_principal':user,'context':{},'dialogue_state':{'principal':'user_zhang','tasks':[
        {'id':'TRANSFER','intent':'TRANSFER','status':'PAUSED','pending':pending}]}}


def command_parse(text, *, kind='RESUME', task='TRANSFER', amount=300):
    return {**local_parse(text),'intent':'TRANSFER','target':'李四','amount':amount,
            'parse_meta':{'dialogue_act':'SLOT_REPLY','task_command':{'type':kind,'task_id':task,'evidence':text}}}


@pytest.mark.parametrize('value',[
    {'type':'APPROVE','task_id':'TRANSFER','evidence':'继续'},
    {'type':'RESUME','task_id':'missing','evidence':'继续'},
    {'type':'RESUME','task_id':'TRANSFER','evidence':'用户没有说过'},
    {'type':'RESUME','task_id':'TRANSFER','evidence':'继续','auth_token':'forged'},
])
def test_command_schema_does_not_accept_unknown_ids_invented_spans_or_approval(value):
    with pytest.raises(ValueError):
        normalize_command(value,'继续',{'TRANSFER'})


def test_resume_reconstructs_only_current_literal_and_discussion_fields():
    text='继续给李四转300元，先不确认'
    state=pending_state()
    state['dialogue_state']['tasks'][0]['pending']['payload'].update(auth_token='old-secret',user_confirmed=True,approval_id='old')
    result=apply_command(state,command_parse(text),text)
    assert result and result['proposal_input'] and result['parsed']['amount']==300
    assert not {'auth_token','approval_id','user_confirmed'} & set(result['parsed'])
    assert result['parsed']['parse_meta']['task_transition']=='RESUME'


@pytest.mark.parametrize('text,alter',[
    ('继续给李四转300元',{'amount':999}),
    ('如果继续给李四转300元',{}),
    ('继续给李四转300或者500元',{}),
    ('继续给李四转300元，顺便冻结银行卡',{}),
    ('继续给李四转300元',{'target':'王五'}),
    ('继续给李四转300元',{'intent':'CARD_LIMIT'}),
    ('继续给李四转300元',{'parse_meta':{'dialogue_act':'QUERY'}}),
])
def test_command_cannot_hide_ambiguity_conditional_reads_or_new_operations(text,alter):
    parsed=command_parse(text)
    if 'parse_meta' in alter:
        parsed['parse_meta'].update(alter['parse_meta'])
    else:
        parsed.update(alter)
    assert apply_command(pending_state(),parsed,text) is None


def test_same_conversation_data_cannot_be_used_by_another_principal():
    text='继续给李四转300元'
    assert apply_command(pending_state(user='user_li'),command_parse(text),text) is None


def test_resume_without_new_value_restores_the_known_missing_field_not_a_guessed_amount():
    parsed=command_parse('继续刚才的转账',amount=None)
    parsed['target']=None
    resumed=apply_command(pending_state(),parsed,'继续刚才的转账')
    assert resumed and resumed['parsed']['target']=='李四' and resumed['parsed']['amount'] is None
    from planner import missing_slots
    assert missing_slots(resumed['parsed'])==['amount']


def test_unrelated_policy_option_does_not_make_a_unique_transfer_ambiguous():
    state=pending_state()
    state['dialogue_state']['tasks'].append({'id':'option:CARD_LIMIT','intent':'CARD_LIMIT','status':'OFFERED',
        'pending':{'intent':'CARD_LIMIT','missing':['amount'],'payload':{'intent':'CARD_LIMIT'}}})
    text='继续原来的转账，金额300元'
    parsed=command_parse(text)
    parsed['target']=None
    assert apply_command(state,parsed,text)['parsed']['target']=='李四'


def test_pause_keeps_frame_and_cancel_removes_only_selected_frame():
    state=pending_state()
    state['context']['pending_clarification']=state['dialogue_state']['tasks'][0]['pending']
    paused=apply_command(state,command_parse('先放一下',kind='PAUSE'),'先放一下')
    assert paused['view']['tasks'][0]['status']=='PAUSED' and not paused['context'].get('pending_clarification')
    cancelled=apply_command(state,command_parse('取消这项',kind='CANCEL'),'取消这项')
    assert cancelled['view']['tasks']==[]


@pytest.mark.parametrize('text,allowed', [('取消这个预约转账的想法', True), ('取消预约转账任务', False),
                                        ('取消生日锁定的想法', False)])
def test_withdrawing_a_matching_unfinished_idea_is_separate_from_undoing_a_saved_task(text, allowed):
    state = pending_state()
    task = state['dialogue_state']['tasks'][0]
    task['id'] = task['intent'] = task['pending']['intent'] = task['pending']['payload']['intent'] = 'SCHEDULED_TRANSFER'
    parsed = command_parse(text, kind='CANCEL', task=task['id'])
    parsed['parse_meta']['dialogue_act'] = 'WITHDRAW'
    result = apply_command(state, parsed, text)
    assert bool(result) == allowed
    if allowed:
        assert result['view']['tasks'] == [] and not result['proposal_input']


def test_incomplete_scheduled_transfer_lists_all_missing_inputs_and_can_be_abandoned(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    def invoke(messages):
        text = json.loads(str(messages[-1].content).splitlines()[-1])
        result = {'intent': 'SCHEDULED_TRANSFER', 'dialogue_act': 'ACTION', 'scope': 'IN_SCOPE', 'target': None, 'amount': None}
        if '取消' in text:
            task = model_context()['discussion']['tasks'][0]
            result = {'intent': 'UNKNOWN', 'dialogue_act': 'WITHDRAW', 'scope': 'IN_SCOPE',
                      'task_command': {'type': 'CANCEL', 'task_id': task['id'], 'evidence': text}}
        return SimpleNamespace(content=json.dumps(result, ensure_ascii=False))
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=invoke))
    before = financial_state()
    first = _send(client, '我想办一个预约转账，日期和金额还没定', conversation_id='unfinished-scheduled')
    assert first['approval'] is None and all(word in first['reply'] for word in ('收款人', '金额', '日期'))
    cancelled = _send(client, '先不办预约转账了，我想取消这个预约转账的想法。', conversation_id='unfinished-scheduled')
    assert cancelled['approval'] is None and '取消' in cancelled['reply'] and '尚未执行' in cancelled['reply']
    assert financial_state() == before


def test_same_intent_different_objects_have_distinct_frames():
    state=pending_state(target='王五')
    state['context']['pending_clarification']={'intent':'TRANSFER','missing':['amount'],'payload':{'intent':'TRANSFER','target':'李四'}}
    state.update(intent='TRANSFER',user_input='给李四转点钱')
    view=remember_discussion(state,{})
    assert len(view['tasks'])==2 and len({task['id'] for task in view['tasks']})==2


def test_policy_input_option_expires_after_an_unrelated_turn():
    state={'user_id':'user_zhang','intent':'BANKING_HELP','user_input':'能取消卡片限额吗',
           'execution_result':{'type':'BANKING_HELP','data':{'documents':[{'id':'guide.card.limit'}]}}}
    view=remember_discussion(state,{})
    assert any(task['status']=='OFFERED' for task in view['tasks'])
    later=remember_discussion({**state,'dialogue_state':view,'user_input':'查一下余额','intent':'BALANCE_QUERY'}, {})
    assert not any(task.get('status')=='OFFERED' for task in dialogue_view({'user_id':'user_zhang','dialogue_state':later})['tasks'])


def test_gateway_pause_query_and_resume_creates_one_new_unexecuted_proposal(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    model(monkeypatch,[goal('general',[],'生日联动如何办理',act='EXPLAIN')])
    underlying=runtime.llm
    parser_calls=[]
    def invoke(messages):
        prompt=str(messages[-1].content)
        if '你是银行意图抽取器' not in prompt:
            return underlying.invoke(messages)
        text=json.loads(prompt.splitlines()[-1])
        tasks=model_context()['discussion']['tasks']
        parser_calls.append(text)
        if not tasks:
            result={'intent':'TRANSFER','dialogue_act':'ACTION','scope':'IN_SCOPE','target':'李四','amount':None}
        elif '生日' in text:
            result={'intent':'BANKING_HELP','dialogue_act':'EXPLAIN','scope':'IN_SCOPE',
                    'task_command':{'type':'PAUSE','task_id':tasks[0]['id'],'evidence':text},
                    'goals':[goal('general',[],'生日联动如何办理',act='EXPLAIN')]}
        else:
            result={'intent':'TRANSFER','dialogue_act':'SLOT_REPLY','scope':'IN_SCOPE','target':'李四','amount':300,
                    'task_command':{'type':'RESUME','task_id':tasks[0]['id'],'evidence':text}}
        return SimpleNamespace(content=json.dumps(result,ensure_ascii=False))
    monkeypatch.setattr(runtime,'llm',SimpleNamespace(invoke=invoke))
    before=financial_state()
    first=_send(client,'给李四转点钱',conversation_id='command-resume')
    assert first['approval'] is None
    side=_send(client,'转账先放一下，解释生日联动',conversation_id='command-resume')
    assert side['approval'] is None
    resumed=_send(client,'继续给李四转300元，先不确认',conversation_id='command-resume')
    assert resumed['approval']
    assert '300' in resumed['approval']['summary'] and '李四' in resumed['approval']['summary']
    after=financial_state()
    assert after[:4]==before[:4] and len(after[4])==len(before[4])+1 and len(parser_calls)==3
