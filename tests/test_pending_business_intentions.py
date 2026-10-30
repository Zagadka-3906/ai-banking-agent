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


@pytest.mark.parametrize('text', [
    '我想给李四转账，金额还没定；这笔转账一般多久能到账？',
    '我想申购零星宝，金额没定；这款产品是否支持撤单？',
    '我想冻结这张银行卡，不过冻结有什么影响？',
    '我想申购零星宝；办理前先问一下，申购后支持撤单吗？',
    '我想给李四转账，在继续转账之前，我想先了解手续费是多少？',
    '我想调整卡片限额，确认前先核对一下日限额允许范围',
    '我想赎回零星宝，提交前先查询我的持仓',
])
def test_explicit_preliminary_inquiry_takes_precedence_over_background_action(text):
    assert execution_constraint(text) == 'PRIOR_INQUIRY'
    parsed = {'intent': 'WEALTH_PURCHASE', 'parse_meta': {'dialogue_act': 'ACTION'}}
    result = enforce_inquiry(parsed, text)
    assert result['intent'] in {'BANKING_HELP', 'BALANCE_QUERY', 'BILL_QUERY'}
    assert result['parse_meta']['dialogue_act'] == 'EXPLAIN'


@pytest.mark.parametrize('text', [
    '办理前先查一下余额，然后给李四转300元，先不确认',
    '我想给李四转300元，现在帮我办理；这笔转账一般多久到账？',
    '我想申购零星宝，请帮我提交申购；这款产品是否支持撤单？',
    '我想冻结这张卡，立即办理冻结；冻结有什么影响？',
])
def test_independent_action_after_a_preliminary_inquiry_is_not_silently_dropped(text):
    from intent_language import prior_inquiry_clause
    assert prior_inquiry_clause(text) is None


def test_consultation_after_failed_understanding_does_not_restart_missing_amount_action(gateway, monkeypatch):  # noqa: F811
    from bank_agent import runtime
    import workflow_runtime as workflow
    client, _ = gateway
    goals = [goal('general', [], '解释转账到账时效', act='EXPLAIN') | {'topic': 'transfer'}]
    calls, reads = [], []
    def draft(payload):
        policy = next(row for row in payload['facts'] if row['id'] == 'guide.transfer.timing')
        return {'answer': policy['text'], 'evidence_ids': [policy['id']], 'unanswered': [], 'coverage': [
            {'goal_id': 'q1', 'status': 'ANSWERED', 'evidence_ids': [policy['id']], 'reason': '说明演示到账边界'}]}
    model(monkeypatch, goals, draft=draft)
    underlying, original = runtime.llm, runtime._http
    def http(method, path, **kwargs):
        reads.append((method, path))
        return original(method, path, **kwargs)
    monkeypatch.setattr(runtime, '_http', http)
    def invoke(messages):
        system, prompt = str(messages[0].content), str(messages[-1].content)
        calls.append((system, workflow._understanding_tail_hedge.get()))
        if '补全goals协议' in system:
            return SimpleNamespace(content=json.dumps({'goals': goals}))
        if '你是银行意图抽取器' in prompt:
            text = json.loads(prompt.splitlines()[-1])
            if '先帮我办一下转账' in text:
                raise TimeoutError('test transport failure')
            return SimpleNamespace(content=json.dumps({'intent': 'TRANSFER', 'dialogue_act': 'ACTION',
                'scope': 'IN_SCOPE', 'target': '李四', 'goals': [goal('general', [], '转账', act='ACTION')]}))
        return underlying.invoke(messages)
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=invoke))
    before = financial_state()
    first = _send(client, '我想给李四转一笔钱，具体金额还没定，先帮我办一下转账。', conversation_id='failed-then-inquiry')
    assert first['business_status'] == 'FAILED' and first['approval'] is None
    second = _send(client, '我想给李四转账，金额还没定；这笔转账一般多久能到账？', conversation_id='failed-then-inquiry')
    assert second['business_status'] == 'SUCCEEDED' and second['approval'] is None
    assert '虚构账本' in second['reply'] and '请补充' not in second['reply']
    assert all(method == 'GET' for method, _ in reads) and financial_state() == before
    assert any('补全goals协议' in system and not hedge for system, hedge in calls)


def pending_quiz():
    return {'user_id': 'user_zhang', 'memory_principal': 'user_zhang',
            'context': {'risk_quiz': {'answers': {'1': 'B'}, 'origin_intent': 'WEALTH_PURCHASE',
                                     'origin_target': 'LX00118'}}}


@pytest.mark.parametrize('operation,intent,target', [
    ('申购', 'WEALTH_PURCHASE', 'LX00118'), ('赎回', 'WEALTH_REDEEM', 'LX00118'),
    ('转账', 'TRANSFER', '李四'), ('预约转账', 'SCHEDULED_TRANSFER', '李四'),
    ('调额', 'CARD_LIMIT', 'CARD-001'),
])
@pytest.mark.parametrize('preamble', ['但想了想还是先', '经过权衡我决定先', '再斟酌一番后还是先', '不过考虑后我决定先'])
def test_explicit_stop_span_does_not_depend_on_enumerating_its_thought_preamble(operation, intent, target, preamble):
    text = f'{preamble}取消这次{operation}吧'
    pending = {'intent': intent, 'missing': ['amount'], 'payload': {'intent': intent, 'target': target}}
    state = {'user_id': 'user_zhang', 'memory_principal': 'user_zhang', 'context': {},
             'dialogue_state': {'principal': 'user_zhang', 'tasks': [
                 {'id': intent, 'intent': intent, 'status': 'PAUSED', 'pending': pending}]}}
    before = deepcopy(state)
    result = withdraw_known_task(state, withdrawal(text), text)
    assert result and result['view']['tasks'] == [] and not result['proposal_input']
    assert state == before


@pytest.mark.parametrize('prefix', [
    '解释为什么要', '说明', '我正在考虑', '我还没有决定', '我不打算',
    '不支持', '不能', '客服说', '朋友建议', '我昨天决定', '明天', '稍后我再',
])
def test_reported_uncertain_negated_or_deferred_stop_spans_are_not_current_commands(prefix):
    text = f'{prefix}先取消这次申购吧'
    state = pending_quiz()
    before = deepcopy(state)
    assert withdraw_known_task(state, withdrawal(text), text) is None and state == before


@pytest.mark.parametrize('text', ['张三还是李四', '给张三还是李四转账', '李四或者王五'])
def test_a_real_recipient_choice_is_not_erased_by_the_imperative_span_rule(text):
    from intent_language import ambiguous_target
    assert ambiguous_target(text)


@pytest.mark.parametrize('operation,intent,target', [
    ('申购零星宝', 'WEALTH_PURCHASE', 'LX00118'), ('赎回零星宝', 'WEALTH_REDEEM', 'LX00118'),
    ('给李四转账', 'TRANSFER', '李四'), ('预约一笔转账给李四', 'SCHEDULED_TRANSFER', '李四'),
    ('设置CARD-001日限额', 'CARD_LIMIT', 'CARD-001'),
])
@pytest.mark.parametrize('ending', ['不继续提交了', '我不再继续办理了', '这次不再处理下去了'])
def test_abandoning_workflow_binds_the_business_in_the_original_text(operation, intent, target, ending):
    text = f'我还是想{operation}，不过考虑后决定这次先取消，{ending}。'
    pending = {'intent': intent, 'missing': ['amount'], 'payload': {'intent': intent, 'target': target}}
    state = {'user_id': 'user_zhang', 'memory_principal': 'user_zhang', 'context': {},
             'dialogue_state': {'principal': 'user_zhang', 'tasks': [
                 {'id': intent, 'intent': intent, 'status': 'PAUSED', 'pending': pending}]}}
    before = deepcopy(state)
    assert withdrawal_statement(text, pending_discussion=True)
    assert execution_constraint(text) == 'NEGATED_ACTION'
    result = withdraw_known_task(state, withdrawal(text), text)
    assert result and result['view']['tasks'] == [] and not result['proposal_input']
    assert state == before


@pytest.mark.parametrize('operation,intent,target', [
    ('申购零星宝', 'WEALTH_PURCHASE', 'LX00118'), ('赎回零星宝', 'WEALTH_REDEEM', 'LX00118'),
    ('给李四转账', 'TRANSFER', '李四'), ('预约一笔转账给李四', 'SCHEDULED_TRANSFER', '李四'),
    ('设置CARD-001日限额', 'CARD_LIMIT', 'CARD-001'), ('冻结CARD-001', 'CARD_FREEZE', 'CARD-001'),
])
@pytest.mark.parametrize('ending', ['不再办理了', '不再继续提交了', '不继续处理了'])
def test_compound_cancel_and_abandonment_share_one_current_clause(operation, intent, target, ending):
    text = f'我原本想{operation}，但现在决定先取消{ending}。'
    pending = {'intent': intent, 'missing': ['amount'], 'payload': {'intent': intent, 'target': target}}
    state = {'user_id': 'user_zhang', 'memory_principal': 'user_zhang', 'context': {},
             'dialogue_state': {'principal': 'user_zhang', 'tasks': [
                 {'id': intent, 'intent': intent, 'status': 'PAUSED', 'pending': pending}]}}
    before = deepcopy(state)
    result = withdraw_known_task(state, withdrawal(text), text)
    assert result and result['view']['tasks'] == [] and not result['proposal_input']
    assert state == before


@pytest.mark.parametrize('text', [
    '我原本想申购零星宝，但现在不想取消不再办理了',
    '我已经申购零星宝，但现在决定先取消不再办理了',
    '我原本想申购零星宝，如果取消不再办理了会怎样？',
    '我原本想申购零星宝，请解释“决定取消不再办理了”',
    '我原本想申购零星宝，客服说现在决定先取消不再办理了',
    '我原本想申购零星宝，明天决定先取消不再办理了',
    '我原本想申购零星宝，但现在决定先取消暂时不提交',
    '我原本想申购零星宝，但现在决定取消恒泉稳利不再办理了',
])
def test_compound_stop_does_not_hide_history_quotes_deferral_or_a_different_product(text):
    state = pending_quiz()
    before = deepcopy(state)
    assert withdraw_known_task(state, withdrawal(text), text) is None and state == before


def test_compound_stop_keeps_the_recipient_named_in_the_stopping_clause():
    text = '我原本想给李四转账，但现在决定取消给王五的转账不再办理了。'
    pending = {'intent': 'TRANSFER', 'missing': ['amount'], 'payload': {'intent': 'TRANSFER', 'target': '李四'}}
    state = {'user_id': 'user_zhang', 'memory_principal': 'user_zhang', 'context': {},
             'dialogue_state': {'principal': 'user_zhang', 'tasks': [
                 {'id': 'original', 'intent': 'TRANSFER', 'status': 'PAUSED', 'pending': pending}]}}
    before = deepcopy(state)
    assert withdraw_known_task(state, withdrawal(text), text) is None and state == before


@pytest.mark.parametrize('text', [
    '先别提交', '先不提交了', '暂不提交', '暂时不继续提交了', '不确认',
    '我想申购恒泉稳利，不继续提交了', '我想申购零星宝和恒泉稳利，不继续提交了',
    '我已经申购零星宝，不继续提交了', '这笔申购已成交，不继续提交了',
    '如果申购失败，不继续提交了', '不继续提交了可以吗？', '解释“不继续提交了”',
    '昨天不继续提交了', '不要取消零星宝的申购，不继续提交了',
])
def test_workflow_abandonment_does_not_erase_holds_history_scope_or_identity(text):
    state = pending_quiz()
    before = deepcopy(state)
    assert withdraw_known_task(state, withdrawal(text), text) is None and state == before


@pytest.mark.parametrize('operation', ['申购', '赎回', '转账', '预约转账', '冻结', '解冻', '挂失', '调额'])
@pytest.mark.parametrize('auxiliary', ['办', '办理', '做'])
def test_negated_auxiliary_and_current_operation_reference_forbid_execution(operation, auxiliary):
    text = f'先不{auxiliary}这笔{operation}了'
    assert execution_constraint(text) == 'NEGATED_ACTION'
    assert withdrawal_statement(text, pending_discussion=True)


@pytest.mark.parametrize('text', [
    '如果先不办理这笔申购了会怎样', '先不办理这笔申购了可以吗？',
    '解释“先不办理这笔申购了”', '我已经申购零星宝，现在不办理这笔申购了',
    '我想申购恒泉稳利，现在不办理这笔申购了',
    '我想申购零星宝和恒泉稳利，现在不办理这笔申购了',
])
def test_negated_auxiliary_never_stops_a_different_historical_or_hypothetical_discussion(text):
    state = pending_quiz()
    before = deepcopy(state)
    parsed = {'intent': 'UNKNOWN', 'parse_meta': {'dialogue_act': 'WITHDRAW'}}
    assert withdraw_known_task(state, parsed, text) is None and state == before


@pytest.mark.parametrize('ending', ['不过现在决定先取消了', '不过我决定先取消了', '现在决定取消了吧', '最后决定先取消掉了'])
def test_sentence_final_le_with_a_current_decision_stops_the_matching_unfinished_purchase(ending):
    text = '我还是想申购零星宝，' + ending + '。'
    state = pending_quiz()
    before = deepcopy(state)
    assert withdrawal_statement(text, pending_discussion=True)
    result = withdraw_known_task(state, {'intent': 'UNKNOWN', 'parse_meta': {'dialogue_act': 'WITHDRAW'}}, text)
    assert result and result['view']['tasks'] == [] and not result['context'].get('risk_quiz')
    assert not result['proposal_input'] and result['origin_intent'] == 'WEALTH_PURCHASE'
    assert state == before


@pytest.mark.parametrize('text', [
    '我取消了', '取消了', '零星宝申购取消了', '我昨天决定取消了',
    '我已经决定取消了', '我曾经决定取消了', '我已经申购了零星宝，现在决定先取消了',
    '零星宝申购已成交，现在决定先取消了', '如果想申购零星宝，现在决定先取消了',
    '我还是想申购零星宝，不过现在决定先取消了可以吗？',
    '我还是想申购零星宝，不过现在决定不要取消了',
    '解释“我想申购零星宝，不过现在决定先取消了”',
    '我想申购恒泉稳利，不过现在决定先取消了',
    '我想申购零星宝和恒泉稳利，不过现在决定先取消了',
])
def test_past_reports_and_wrong_subjects_do_not_cancel_a_quiz_via_sentence_final_le(text):
    state = pending_quiz()
    before = deepcopy(state)
    result = withdraw_known_task(state, {'intent': 'UNKNOWN', 'parse_meta': {'dialogue_act': 'WITHDRAW'}}, text)
    assert result is None and state == before


@pytest.mark.parametrize('failure', ['capacity', 'timeout', 'connection'])
def test_model_unavailability_does_not_prevent_a_verified_local_discussion_stop(gateway, monkeypatch, failure):  # noqa: F811
    from bank_agent.nodes.intent import intent_parser_node
    def unavailable(*args, **kwargs):
        if failure == 'capacity':
            raise TimeoutError('MODEL_CAPACITY_EXHAUSTED')
        if failure == 'timeout':
            raise TimeoutError('timed out')
        raise ConnectionError('PRIVATE-PROVIDER-CONTENT')
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=unavailable))
    state = {**pending_quiz(), 'user_input': '我还是想申购零星宝，不过现在决定先取消了。'}
    original, before = deepcopy(state), financial_state()
    result = intent_parser_node(state)
    assert result['business_status'] == 'CANCELLED' and result['risk_quiz'] == {'active': False}
    assert '停止本轮申购意向' in result['final_response'] and not result['context'].get('risk_quiz')
    assert not result.get('approval') and not result['dialogue_state']['tasks']
    assert state == original and financial_state() == before


@pytest.mark.parametrize('text,patch', [
    ('我想申购恒泉稳利，不过现在决定先取消了', {}),
    ('查询余额，然后取消零星宝申购意向', {}),
    ('如果取消零星宝申购意向会怎样', {}),
    ('我已经申购零星宝，现在决定取消了', {}),
    ('零星宝申购意向不要取消', {}),
    ('取消零星宝申购意向', {'memory_principal': 'user_li'}),
    ('取消零星宝申购意向', {'pending_operation': {'operation': 'WEALTH_PURCHASE'}}),
    ('取消零星宝申购意向', {'approval': {'id': 'PENDING-APPROVAL'}}),
    ('给李四转300元', {}),
])
def test_unavailable_model_does_not_make_other_requests_or_approvals_locally_executable(gateway, monkeypatch, text, patch):  # noqa: F811
    from bank_agent.nodes.intent import intent_parser_node
    def unavailable(*args, **kwargs):
        raise TimeoutError('MODEL_CAPACITY_EXHAUSTED')
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=unavailable))
    state = {**pending_quiz(), 'user_input': text, **patch}
    original, before = deepcopy(state), financial_state()
    result = intent_parser_node(state)
    assert result['business_status'] == 'FAILED' and '原任务保留' in result['final_response']
    assert not result.get('approval') and result['context'] == state['context']
    assert state == original and financial_state() == before


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
    '我还是想申购零星宝，但想了想还是先取消这次申购吧。',
    '我还是想申购零星宝，不过考虑后决定这次先取消，不继续提交了。',
    '我之前想申购零星宝，现在决定取消，先不办这笔申购了。',
    '我之前想申购零星宝，现在决定取消，暂时不办理这次申购了。',
    '我前面想申购零星宝，现在决定取消了，先不办了。',
    '我还是想申购零星宝，不过现在决定取消了，帮我取消吧。',
    '零星宝我还是想申购，但这次决定先取消了，麻烦帮我取消这次申购。',
    '对，我要取消刚才那笔零星宝的待确认申购，麻烦帮我取消。',
    '既然零星宝申购后不能撤单，那我决定取消这次申购意向，先不申购了。',
    '那我先不申购零星宝了，决定取消这次申购意向。',
    '我还是想申购零星宝，但金额没定，现在决定先取消这次申购。',
    '那我不申购零星宝了，之前想申购的意向也一起取消，先不办这个了。',
    '我原本想申购零星宝，但现在决定先取消不再办理了。',
    '我原本想申购零星宝，经过权衡决定取消不再继续提交了。',
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
