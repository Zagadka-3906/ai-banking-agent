"""Natural action qualifiers and withdrawals preserve the discussion boundary."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from bank_agent import runtime
from bank_agent.dialogue_commands import apply_command, withdraw_known_task
from bank_agent.dialogue_state import dialogue_view
from bank_agent.extraction import target_from_text
from bank_agent.understanding import enforce_inquiry
from intent_language import action_request_clause, execution_constraint, recipient_references, withdrawal_statement


@pytest.mark.parametrize('ending', ['不再继续办理了', '不要再继续办理', '先不继续办了', '暂时不再办'])
def test_stacked_negative_adverbs_still_stop_the_current_unfinished_intention(ending):
    text = '我本来想恢复零星宝的申购，但现在决定取消，' + ending + '。'
    assert execution_constraint(text) == 'NEGATED_ACTION'
    assert withdrawal_statement(text, pending_discussion=True)
from test_agent_gateway import _send, gateway  # noqa: F401
from test_general_dialogue_loop import goal, model
from test_inquiry_boundaries import financial_state
from test_intent_language_boundaries import state as saved_state


@pytest.mark.parametrize('operation,intent', [
    ('申购零星宝', 'WEALTH_PURCHASE'), ('赎回零星宝', 'WEALTH_REDEEM'),
    ('给李四转账', 'TRANSFER'), ('冻结银行卡', 'CARD_FREEZE'),
    ('把这张卡的日限额改成10000', 'CARD_LIMIT'),
])
def test_first_person_action_survives_a_later_procedural_qualifier(operation, intent):
    for suffix in ('先按办理流程走，让我看看下一步怎么填。', '先别确认，帮我看看怎么办理。'):
        text = f'那我想{operation}，{suffix}'
        parsed = {'intent': intent, 'parse_meta': {'source': 'model', 'dialogue_act': 'ACTION'}}
        assert action_request_clause(text)
        assert execution_constraint(text) is None
        assert enforce_inquiry(parsed, text)['intent'] == intent


@pytest.mark.parametrize('text', [
    '那我先怎么申购零星宝', '我先了解如何申购零星宝',
    '那我先申购零星宝可以吗', '如果我先申购零星宝，下一步怎么填',
    '我不想申购零星宝，下一步怎么填',
    '我想把日限额改成10000，先了解这个数值行不行，暂不确认',
    '我还是想申购零星宝，但金额没定，现在决定先取消这次申购。',
])
def test_question_negation_and_feasibility_cannot_become_an_action(text):
    result = enforce_inquiry({'intent': 'WEALTH_PURCHASE', 'parse_meta': {'dialogue_act': 'ACTION'}}, text)
    assert result['intent'] == 'BANKING_HELP'


def pending_quiz():
    return {'user_id': 'user_zhang', 'memory_principal': 'user_zhang',
            'context': {'risk_quiz': {'answers': {'1': 'B'}, 'origin_intent': 'WEALTH_PURCHASE',
                                     'origin_target': 'LX00118'}}}


@pytest.mark.parametrize('text,target,names', [
    ('之前想申购零星宝的意向也一起取消', 'LX00118', set()),
    ('之前想申购的意向也一起取消', None, set()),
    ('这次理财方向偏保守', None, set()),
    ('投资倾向偏保守', None, set()),
    ('向李四转账', '李四', {'李四'}),
    ('我想向刘宗屏转账', '刘宗屏', {'刘宗屏'}),
    ('给意向转账', '意向', {'意向'}),
])
def test_recipient_preposition_does_not_consume_business_nouns_or_erase_literal_names(text, target, names):
    assert target_from_text(text) == target and recipient_references(text) == names


def withdrawal(text, *, command=False):
    meta = {'dialogue_act': 'WITHDRAW'}
    if command:
        meta['task_command'] = {'type': 'CANCEL', 'task_id': 'quiz:WEALTH_RISK_ASSESSMENT', 'evidence': text}
    return {'intent': 'UNKNOWN', 'parse_meta': meta}


@pytest.mark.parametrize('intent,target,text', [
    ('SCHEDULED_TRANSFER', '李四', '那我先不预约转账了，这笔预约转账帮我取消吧。'),
    ('SCHEDULED_TRANSFER', '李四', '这笔预约转账帮我取消吧。'),
    ('TRANSFER', '李四', '这笔转账帮我取消吧'),
    ('WEALTH_PURCHASE', 'LX00118', '这次零星宝申购帮我取消吧'),
    ('CARD_LIMIT', 'CARD-001', '本次调额请停止'),
])
def test_object_before_withdrawal_binds_only_one_current_unfinished_discussion(intent, target, text):
    from intent_language import pending_withdrawal_clause
    pending = {'intent': intent, 'missing': ['amount'], 'payload': {'intent': intent, 'target': target}}
    frame = {'id': intent, 'intent': intent, 'status': 'PAUSED', 'pending': pending}
    state = {'user_id': 'user_zhang', 'memory_principal': 'user_zhang', 'context': {},
             'dialogue_state': {'principal': 'user_zhang', 'tasks': [frame]}}
    before = deepcopy(state)
    assert pending_withdrawal_clause(text, current_discussion=True)
    assert withdrawal_statement(text, pending_discussion=True)
    transition = withdraw_known_task(state, withdrawal(text), text)
    assert transition and transition['view']['tasks'] == [] and transition['proposal_input'] is False
    assert state == before
    state['dialogue_state']['tasks'].append({**frame, 'id': 'another'})
    assert withdraw_known_task(state, withdrawal(text), text) is None


@pytest.mark.parametrize('text', ['这笔已完成转账帮我取消吧', '这笔预约转账不要取消', '这笔预约任务帮我取消吧',
                                '如果这笔预约转账取消会怎样', '这笔预约转账怎么取消', '说明“这笔预约转账帮我取消吧”'])
def test_object_first_syntax_never_supplies_an_undo_authority_or_converts_a_question(text):
    from intent_language import pending_withdrawal_clause
    assert pending_withdrawal_clause(text, current_discussion=True) is None
    assert withdrawal_statement(text, pending_discussion=True) is False


@pytest.mark.parametrize('text', ['先不预约转账了', '我不预约了', '暂时不预约转账'])
def test_negating_a_scheduled_transfer_constrains_execution(text):
    assert execution_constraint(text) == 'NEGATED_ACTION'


def test_original_scheduled_transfer_withdrawal_closes_the_discussion_without_creating_a_saved_task(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    model(monkeypatch, [goal('general', [], '准备预约转账但缺日期金额', act='ACTION')], intent='SCHEDULED_TRANSFER')
    _send(client, '我想给李四预约转账，不过日期和金额还没定，先帮我准备一下', conversation_id='scheduled-withdraw')
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=lambda messages: SimpleNamespace(content=json.dumps({
        'intent': 'UNKNOWN', 'dialogue_act': 'WITHDRAW', 'scope': 'IN_SCOPE'}))))
    before = financial_state()
    reply = _send(client, '那我先不预约转账了，这笔预约转账帮我取消吧。', conversation_id='scheduled-withdraw')
    assert reply['approval'] is None and reply['business_status'] == 'CANCELLED'
    assert '尚未执行' in reply['reply'] and financial_state() == before


def unspecified_scheduled_discussion():
    from bank_agent.dialogue_state import remember_discussion
    state = {'user_id': 'user_zhang', 'memory_principal': 'user_zhang', 'intent': 'BANKING_HELP',
             'user_input': '我还想预约一笔转账，但日期和金额都还没定，能先帮我办这个预约吗？'}
    return {**state, 'dialogue_state': remember_discussion(state, {'business_status': 'SUCCEEDED'})}


def test_consulted_intention_is_retained_even_before_the_recipient_date_and_amount_are_known():
    state = unspecified_scheduled_discussion()
    tasks = state['dialogue_state']['tasks']
    assert len(tasks) == 1 and tasks[0]['intent'] == 'SCHEDULED_TRANSFER'
    assert tasks[0]['kind'] == 'discussed_intention' and tasks[0]['pending']['payload']['target'] is None
    assert {'target', 'amount', 'scheduled_at'} <= set(tasks[0]['pending']['missing'])
    assert 'auth_token' not in tasks[0]['pending']['payload']


@pytest.mark.parametrize('text', ['那这笔预约转账我不打算办了，先取消这个意向吧。', '先取消这个意向吧。',
                                '这个意向帮我取消吧'])
def test_generic_intention_reference_can_stop_only_one_known_unfinished_discussion(text):
    state = unspecified_scheduled_discussion()
    before = deepcopy(state)
    assert withdrawal_statement(text, pending_discussion=True)
    result = withdraw_known_task(state, withdrawal(text), text)
    assert result and result['view']['tasks'] == [] and not result['proposal_input']
    assert state == before
    task = state['dialogue_state']['tasks'][0]
    state['dialogue_state']['tasks'].append({**task, 'id': 'another-scheduled'})
    assert withdraw_known_task(state, withdrawal(text), text) is None


@pytest.mark.parametrize('text', ['解释取消这个意向的含义', '取消这个意向的流程', '先取消这个意向可以吗',
                                '这个意向不要取消', '如果取消这个意向会怎样', '先取消这个已完成的意向'])
def test_generic_intention_reference_does_not_convert_explanations_or_completed_actions_to_commands(text):
    assert not withdrawal_statement(text, pending_discussion=True)


def test_business_in_another_clause_still_limits_which_intention_can_be_stopped():
    state = pending_quiz()
    text = '那这笔预约转账我不打算办了，先取消这个意向吧。'
    assert withdraw_known_task(state, withdrawal(text), text) is None
    scheduled = unspecified_scheduled_discussion()['dialogue_state']['tasks'][0]
    quiz = dialogue_view(state)['tasks'][0]
    state['context'] = {}
    state['dialogue_state'] = {'principal': 'user_zhang', 'tasks': [quiz, scheduled]}
    result = withdraw_known_task(state, withdrawal(text), text)
    assert result and [task['id'] for task in result['view']['tasks']] == [quiz['id']]
    ambiguous = '预约转账和零星宝申购我都不想办了，先取消这个意向吧。'
    assert withdraw_known_task(state, withdrawal(ambiguous), ambiguous) is None


def test_gateway_stops_the_consulted_scheduled_intention_before_any_required_input_was_provided(gateway, monkeypatch):  # noqa: F811
    import conversation
    client, _ = gateway
    cid = 'unspecified-scheduled-intention'
    model(monkeypatch, [goal('general', [], '日期金额未定的预约准备', act='EXPLAIN')])
    _send(client, '我还想预约一笔转账，但日期和金额都还没定，能先帮我办这个预约吗？', conversation_id=cid)
    thread = conversation.conversation_thread_id('user_zhang', cid)
    current = conversation.get_graph().get_state({'configurable': {'thread_id': thread}}).values
    assert any(task['pending']['payload'].get('target') is None for task in dialogue_view(current)['tasks'])
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=lambda messages: SimpleNamespace(content=json.dumps({
        'intent': 'UNKNOWN', 'dialogue_act': 'WITHDRAW', 'scope': 'IN_SCOPE'}))))
    before = financial_state()
    reply = _send(client, '那这笔预约转账我不打算办了，先取消这个意向吧。', conversation_id=cid)
    assert reply['approval'] is None and reply['business_status'] == 'CANCELLED' and '尚未执行' in reply['reply']
    assert financial_state() == before


@pytest.mark.parametrize('text', [
    '对，我要取消刚才那笔零星宝的待确认申购，麻烦帮我取消。',
    '请撤销零星宝未确认的申购', '我想放弃零星宝的申购意向',
    '既然零星宝申购后不能撤单，那我决定取消这次申购意向，先不申购了。',
    '那就先取消零星宝的申购意向', '那么我决定取消零星宝的待确认申购',
    '所以我决定取消零星宝的申购意向', '因此先取消这次零星宝申购意向',
    '那么，我决定取消零星宝的申购意向',
    '那我先不申购零星宝了，决定取消这次申购意向。',
    '决定取消零星宝的申购意向', '现在决定放弃零星宝的待确认申购',
    '我还是想申购零星宝，但金额没定，现在决定先取消这次申购。',
    '我想申购零星宝，但现在决定取消本次申购',
    '我想申购零星宝，不过还是先取消这笔申购',
    '零星宝我还是想申购，但这次决定先取消了，麻烦帮我取消这次申购。',
    '零星宝我还是想申购，但这次决定先撤销了，请撤销本次申购。',
    '我还是想申购零星宝，不过现在决定取消了，帮我取消吧。',
    '我前面想申购零星宝，现在决定取消了，先不办了。',
    '那就算了，帮我取消吧。',
    '那我不申购零星宝了，之前想申购的意向也一起取消，先不办这个了。',
    '之前想申购零星宝的意向也一起取消',
    '零星宝的待确认申购请先撤销', '零星宝的申购意向就先放弃吧',
])
def test_pending_purchase_withdrawal_resolves_its_existing_questionnaire(text):
    state = pending_quiz()
    before = deepcopy(state)
    assert withdrawal_statement(text, pending_discussion=True)
    parsed = enforce_inquiry(withdrawal(text), text)
    assert parsed['parse_meta']['dialogue_act'] == 'WITHDRAW'
    result = withdraw_known_task(state, parsed, text)
    assert result and result['view']['tasks'] == [] and not result['context'].get('risk_quiz')
    assert not result['proposal_input'] and result['origin_intent'] == 'WEALTH_PURCHASE'
    assert state == before


@pytest.mark.parametrize('text', [
    '取消零星宝的待确认申购会怎样', '零星宝未确认的申购怎么取消',
    '这次零星宝申购已成交，请取消这次申购。',
    '这次零星宝申购已扣款，请取消这次申购。',
    '我已经取消了零星宝的待确认申购，麻烦帮我取消这次申购。',
    '我曾经撤销了零星宝的申购，请撤销本次申购。',
    '能取消零星宝未确认的申购吗', '取消零星宝的待确认申购？',
    '如果取消零星宝的待确认申购', '我已经取消了零星宝的待确认申购',
    '取消这个申购意向会怎样', '如果取消这个申购意向', '我已经取消这个申购意向',
    '解释这句话“取消零星宝待确认申购”',
    '取消零星宝的申购，另一个转账还待确认',
    '取消恒泉稳利的待确认申购', '取消零星宝或恒泉稳利的待确认申购',
    '取消Apple订阅', '撤销已完成的零星宝申购',
    '那我决定取消这次申购意向会怎样', '那么取消零星宝的申购意向可以吗',
    '如果支持，那就取消零星宝的申购意向',
    '取消这次零星宝申购意向什么意思', '取消零星宝的待确认申购么',
    '决定取消零星宝的申购意向会怎样', '如果决定取消零星宝的申购意向',
    '那我先不申购恒泉稳利了，决定取消这次申购意向。',
    '先不申购零星宝和恒泉稳利了，决定取消这次申购意向。',
    '我想申购恒泉稳利，但金额没定，现在决定先取消这次申购。',
    '现在决定取消这次已完成的零星宝申购',
    '现在决定取消这次零星宝申购会怎样',
    '如果金额没定，现在决定取消这次申购',
    '我不取消这次零星宝申购', '现在不用取消这次零星宝申购',
    '我不想取消这次零星宝申购', '不要帮我取消这次零星宝申购',
    '解释这句话“现在决定取消这次申购”',
    '告诉我零星宝申购意向怎么取消', '解释零星宝申购意向取消',
    '零星宝申购意向不要取消', '零星宝申购意向不用再取消',
    '零星宝申购意向已经取消了',
    '零星宝申购已成交，帮我取消吧。',
    '如果零星宝申购失败，帮我取消吧。',
    '我只是转述“帮我取消吧”',
    '零星宝和恒泉稳利都想申购，现在帮我取消吧。',
    '我想申购恒泉稳利，不过决定取消了，帮我取消吧。',
])
@pytest.mark.parametrize('act', ['WITHDRAW', 'QUERY'])
def test_a_wrong_withdraw_label_or_command_cannot_cancel_an_unrelated_discussion(text, act):
    state = pending_quiz()
    before = deepcopy(state)
    assert withdraw_known_task(state, withdrawal(text), text) is None
    parsed = withdrawal(text, command=True)
    parsed['parse_meta']['dialogue_act'] = act
    assert apply_command(state, parsed, text) is None
    assert state == before


@pytest.mark.parametrize('text', ['取消零星宝的待确认申购', '我还是想申购零星宝，但金额没定，现在决定先取消这次申购。'])
def test_no_existing_intention_or_two_matching_frames_cannot_be_guessed(text):
    assert withdraw_known_task({'user_id': 'user_zhang'}, withdrawal(text), text) is None
    state = pending_quiz()
    frame = dialogue_view(state)['tasks'][0]
    state['context'] = {}
    state['dialogue_state'] = {'principal': 'user_zhang', 'tasks': [frame, {**frame, 'id': 'second-quiz'}]}
    assert withdraw_known_task(state, withdrawal(text), text) is None
    state['user_id'] = 'user_li'
    assert withdraw_known_task(state, withdrawal(text), text) is None


@pytest.mark.parametrize('text', ['取消零星宝的待确认申购', '那我先不申购零星宝了，决定取消这次申购意向。',
                                 '我还是想申购零星宝，但金额没定，现在决定先取消这次申购。'])
def test_withdrawal_also_cancels_only_the_matching_missing_amount_discussion(text):
    state = pending_quiz()
    state['context'] = {}
    frames = []
    for target in ('LX00118', 'HQ30060'):
        frames.append({'id': target, 'intent': 'WEALTH_PURCHASE', 'status': 'PAUSED',
                       'pending': {'intent': 'WEALTH_PURCHASE', 'missing': ['amount'],
                                   'payload': {'intent': 'WEALTH_PURCHASE', 'target': target, 'amount': None}}})
    state['dialogue_state'] = {'principal': 'user_zhang', 'tasks': frames}
    result = withdraw_known_task(state, withdrawal(text), text)
    assert result and [task['id'] for task in result['view']['tasks']] == ['HQ30060']
    assert not result['proposal_input'] and state['dialogue_state']['tasks'] == frames


@pytest.mark.parametrize('intent,target,text', [
    ('TRANSFER', '李四', '我还想给李四转账，但金额没定，现在决定先取消这笔转账。'),
    ('CARD_LIMIT', 'CARD-001', '我还想调额，但数值没定，现在决定取消本次调额。'),
    ('WEALTH_REDEEM', 'LX00118', '我想赎回零星宝，但份额没定，最后决定先取消这次赎回。'),
    ('TRANSFER', '李四', '我还想给李四转账，但决定先取消了，请取消这笔转账。'),
    ('CARD_LIMIT', 'CARD-001', '我还想调额，但决定先取消了，请取消本次调额。'),
    ('WEALTH_REDEEM', 'LX00118', '我想赎回零星宝，但决定先撤销了，请撤销这次赎回。'),
    ('TRANSFER', '李四', '我还想给李四转账，不过现在决定取消了，帮我取消吧。'),
    ('CARD_LIMIT', 'CARD-001', '我还想设置CARD-001日限额，不过现在决定取消了，帮我取消吧。'),
    ('WEALTH_REDEEM', 'LX00118', '我还想赎回零星宝，不过现在决定撤销了，请撤销吧。'),
])
def test_current_operation_reference_binds_only_its_existing_business_discussion(intent, target, text):
    pending = {'intent': intent, 'missing': ['amount'], 'payload': {'intent': intent, 'target': target, 'amount': None}}
    state = {'user_id': 'user_zhang', 'memory_principal': 'user_zhang', 'context': {},
             'dialogue_state': {'principal': 'user_zhang', 'tasks': [
                 {'id': intent, 'intent': intent, 'status': 'PAUSED', 'pending': pending}]}}
    before = deepcopy(state)
    result = withdraw_known_task(state, withdrawal(text), text)
    assert result and result['view']['tasks'] == [] and not result['proposal_input']
    assert state == before


@pytest.mark.parametrize('cancellation', [
    '我前面想申购零星宝，现在决定取消了，先不办了。',
    '我还是想申购零星宝，不过现在决定取消了，帮我取消吧。',
    '零星宝我还是想申购，但这次决定先取消了，麻烦帮我取消这次申购。',
    '对，我要取消刚才那笔零星宝的待确认申购，麻烦帮我取消。',
    '既然零星宝申购后不能撤单，那我决定取消这次申购意向，先不申购了。',
    '那我先不申购零星宝了，决定取消这次申购意向。',
    '我还是想申购零星宝，但金额没定，现在决定先取消这次申购。',
    '那我不申购零星宝了，之前想申购的意向也一起取消，先不办这个了。',
])
def test_purchase_then_cancellation_question_then_withdrawal_is_read_only(gateway, monkeypatch, cancellation):  # noqa: F811
    client, _ = gateway
    cid = 'purchase-qualifier-withdrawal'
    model(monkeypatch, [goal('wealth_products', [], '未确认申购是否支持撤单', act='EXPLAIN')])
    underlying = runtime.llm

    def invoke(messages):
        prompt = str(messages[-1].content)
        if '你是银行意图抽取器' not in prompt:
            return underlying.invoke(messages)
        text = json.loads(prompt.splitlines()[-1])
        if text.startswith('那我先申购'):
            result = {'intent': 'WEALTH_PURCHASE', 'dialogue_act': 'ACTION', 'scope': 'IN_SCOPE',
                      'target': 'LX00118', 'amount': None}
        elif '取消' in text:
            result = {'intent': 'UNKNOWN', 'dialogue_act': 'WITHDRAW', 'scope': 'IN_SCOPE'}
        else:
            return underlying.invoke(messages)
        return SimpleNamespace(content=json.dumps(result, ensure_ascii=False))

    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=invoke))
    before = financial_state()
    started = _send(client, '那我先申购零星宝吧。金额我还没定，先按申购流程走，让我看看下一步怎么填。', conversation_id=cid)
    assert started['approval'] is None and started['result']['risk_quiz']['active']
    assert '风险测评' in started['reply']
    side = _send(client, '申购零星宝后如果还没确认，能撤单吗？', conversation_id=cid)
    assert side['approval'] is None
    assert any(task.get('quiz', {}).get('origin_target') == 'LX00118'
               for task in dialogue_view(saved_state(cid))['tasks'])
    cancelled = _send(client, cancellation, conversation_id=cid)
    assert cancelled['business_status'] == 'CANCELLED' and cancelled['approval'] is None
    assert '已停止本轮申购意向' in cancelled['reply']
    assert dialogue_view(saved_state(cid))['tasks'] == []
    assert financial_state() == before


@pytest.mark.parametrize('command', [False, True])
def test_out_of_scope_labels_cannot_cancel_existing_bank_discussions(command):
    state = pending_quiz()
    before = deepcopy(state)
    text = '取消零星宝的待确认申购'
    parsed = withdrawal(text, command=command)
    parsed['parse_meta']['scope'] = 'OUT_OF_SCOPE'
    assert withdraw_known_task(state, parsed, text) is None
    if command:
        assert apply_command(state, parsed, text) is None
    assert state == before


def test_literal_stop_without_an_existing_task_acknowledges_only_intention(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=lambda _: SimpleNamespace(content=json.dumps({
        'intent': 'UNKNOWN', 'dialogue_act': 'WITHDRAW', 'scope': 'IN_SCOPE'}))))
    before = financial_state()
    response = _send(client, '我还是想申购零星宝，不过现在决定取消了，帮我取消吧。', conversation_id='no-task-stop')
    assert response['approval'] is None and '本轮不继续办理' in response['reply']
    assert '请明确' not in response['reply'] and '成功' not in response['reply']
    assert dialogue_view(saved_state('no-task-stop'))['tasks'] == []
    assert financial_state() == before
