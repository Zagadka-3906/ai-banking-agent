"""Interactive questionnaires obey the same discussion lifecycle as other tasks."""
import json
from types import SimpleNamespace
import pytest

from bank_agent import runtime
from bank_agent.dialogue_commands import apply_command
from bank_agent.dialogue_state import dialogue_view
from bank_agent.understanding import understanding_context, model_context
from test_agent_gateway import gateway, _send  # noqa: F401
from test_general_dialogue_loop import goal, model
from test_inquiry_boundaries import financial_state
from test_intent_language_boundaries import state as saved_state


def quiz_state():
    return {'user_id':'user_zhang','memory_principal':'user_zhang','context':{'risk_quiz':{'answers':{'1':'B','2':'C'}}}}


def test_questionnaire_frame_exposes_control_state_but_not_answers_to_understanding():
    state=quiz_state()
    frame=dialogue_view(state)['tasks'][0]
    assert frame['kind']=='questionnaire' and frame['quiz']['answers']=={'1':'B','2':'C'}
    with understanding_context(state):
        context=model_context()
        assert context['discussion']['tasks'][0]['kind']=='questionnaire'
        assert '"answers"' not in json.dumps(context)


def test_foreign_questionnaire_context_is_not_exposed_as_an_owned_frame():
    state={**quiz_state(),'user_id':'user_li'}
    assert dialogue_view(state)['tasks']==[]


def test_quiz_cancel_and_resume_never_accept_new_answers_from_model():
    state=quiz_state()
    text='继续刚才的测评'
    parsed={'intent':'WEALTH_RISK_ASSESSMENT','parse_meta':{'dialogue_act':'SLOT_REPLY',
            'task_command':{'type':'RESUME','task_id':'quiz:WEALTH_RISK_ASSESSMENT','evidence':text}},
            'answers':{'1':'E'},'user_confirmed':True}
    resumed=apply_command(state,parsed,text)
    assert resumed['context']['risk_quiz']['answers']=={'1':'B','2':'C'}
    assert 'answers' not in resumed['parsed'] and 'user_confirmed' not in resumed['parsed']


@pytest.mark.parametrize('question,act,goal_act', [
    ('我还没开始继续测评，只是想先问：申购支持撤单吗？', 'EXPLAIN', 'EXPLAIN'),
    ('我想申购零星宝，金额还没定；办理前先问一下，零星宝申购后支持撤单吗？', 'ACTION', 'EXPLAIN'),
    ('我想申购零星宝，金额还没定；办理前先问一下，零星宝申购后支持撤单吗？', 'SLOT_REPLY', 'EXPLAIN'),
    ('我想申购零星宝，金额还没定；办理前先问一下，零星宝申购后支持撤单吗？', 'ACTION', 'ACTION'),
])
def test_rule_question_pauses_quiz_and_explicit_resume_keeps_user_answers(gateway,monkeypatch,question,act,goal_act):  # noqa: F811
    client,_=gateway
    cid='quiz-query-resume'
    _send(client,'我想做风险测评',conversation_id=cid)
    _send(client,'1B 2C',conversation_id=cid)
    before=financial_state()
    model(monkeypatch,[
        {'act':'EXPLAIN','resource':'wealth_products','fields':[],'question':'申购是否支持撤单','topic':'wealth'}])
    underlying = runtime.llm
    repairs = []
    def invoke(messages):
        prompt = str(messages[-1].content)
        if '你是银行意图抽取器' in prompt:
            return SimpleNamespace(content=json.dumps({'intent': 'WEALTH_PURCHASE', 'dialogue_act': act,
                'scope': 'IN_SCOPE', 'target': 'LX00118',
                'goals': [{'act': goal_act, 'resource': 'general', 'fields': [],
                           'question': '申购是否支持撤单', 'topic': 'wealth'}]}))
        if '补全goals协议' in str(messages[0].content):
            repairs.append(True)
            return SimpleNamespace(content=json.dumps({'goals': [{'act': 'EXPLAIN', 'resource': 'general',
                'fields': [], 'question': '申购是否支持撤单', 'topic': 'wealth'}]}))
        return underlying.invoke(messages)
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=invoke))
    side=_send(client,question,conversation_id=cid)
    assert side['approval'] is None and '还差第' not in side['reply']
    assert side['intent'] == 'BANKING_HELP' and side['business_status'] == 'SUCCEEDED'
    assert all(row['act'] == 'EXPLAIN' for row in saved_state(cid)['parse_meta']['goals'])
    assert repairs == ([True] if goal_act == 'ACTION' else [])
    assert not (side['result'].get('risk_quiz') or {}).get('active')
    monkeypatch.setattr(runtime,'llm',SimpleNamespace(invoke=lambda messages:SimpleNamespace(content=json.dumps({
        'intent':'WEALTH_RISK_ASSESSMENT','dialogue_act':'SLOT_REPLY','scope':'IN_SCOPE',
        'task_command':{'type':'RESUME','task_id':'quiz:WEALTH_RISK_ASSESSMENT','evidence':'继续刚才的测评'}}))))
    resumed=_send(client,'继续刚才的测评',conversation_id=cid)
    assert '已记录 2/8 题' in resumed['reply'] and resumed['result']['risk_quiz']['active']
    assert resumed['approval'] is None and financial_state()==before


def test_cancelling_questionnaire_with_natural_language_does_not_repeat_questions(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    cid='quiz-natural-cancel'
    _send(client,'我想做风险测评',conversation_id=cid)
    before=financial_state()
    text='我暂时不做风险测评了，也先不申购了'
    monkeypatch.setattr(runtime,'llm',SimpleNamespace(invoke=lambda messages:SimpleNamespace(content=json.dumps({
        'intent':'UNKNOWN','dialogue_act':'UNSUPPORTED','scope':'IN_SCOPE',
        'task_command':{'type':'CANCEL','task_id':'quiz:WEALTH_RISK_ASSESSMENT','evidence':text}}))))
    cancelled=_send(client,text,conversation_id=cid)
    assert cancelled['business_status']=='CANCELLED' and '已取消未完成的风险测评' in cancelled['reply']
    assert not cancelled['result']['risk_quiz']['active'] and financial_state()==before


@pytest.mark.parametrize('failure', ['action', 'malformed', 'timeout'])
def test_preliminary_question_goal_repair_is_bounded_and_never_submits_quiz_answers(gateway,monkeypatch,failure):  # noqa: F811
    client, _ = gateway
    cid = 'prior-question-protocol-failure'
    _send(client, '我想做风险测评', conversation_id=cid)
    _send(client, '1B 2C', conversation_id=cid)
    before = financial_state()
    repairs = []
    def invoke(messages):
        value = {'intent': 'WEALTH_PURCHASE', 'dialogue_act': 'ACTION', 'scope': 'IN_SCOPE',
                 'goals': [{'act': 'ACTION', 'resource': 'general', 'fields': [], 'question': '申购', 'topic': 'wealth'}]}
        if '补全goals协议' in str(messages[0].content):
            repairs.append(True)
            if failure == 'timeout':
                raise TimeoutError('timed out')
            value = {'goals': value['goals']} if failure == 'action' else {'goals': 'invalid'}
        return SimpleNamespace(content=json.dumps(value))
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=invoke))
    side = _send(client, '我想申购零星宝；办理前先问一下，申购支持撤单吗？', conversation_id=cid)
    assert repairs == [True] and side['business_status'] == 'FAILED' and side['approval'] is None
    assert saved_state(cid)['context']['risk_quiz']['answers'] == {'1': 'B', '2': 'C'}
    assert financial_state() == before


def test_current_principal_name_is_bound_from_core_not_asserted_identity(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    model(monkeypatch,[goal('balance',['available_balance'],'张三目前可用余额',subject='张三')])
    own=_send(client,'你好，我是张三，查一下我的可用余额',conversation_id='own-principal-name')
    assert own['business_status']=='SUCCEEDED' and '15,000.00' in own['reply']
    model(monkeypatch,[goal('balance',['available_balance'],'李四可用余额',subject='李四')])
    other=_send(client,'查一下李四的可用余额',conversation_id='other-name-not-owner')
    assert other['business_status']!='SUCCEEDED' and '15,000.00' not in other['reply']
