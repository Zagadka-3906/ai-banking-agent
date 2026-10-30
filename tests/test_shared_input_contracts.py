"""Resource and period boundaries survive legacy slots, follow-ups and repair."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta

import pytest

from bank_agent.parsing import local_parse, _local_parse_rules
from bank_agent.dialogue_state import resume_task
from bank_agent.entities import bind_payee
from bank_agent.evidence_session import EvidenceSession
from bank_agent.understanding import enforce_inquiry
from bank_planning.requests import normalize_goals
from intent_language import requested_date_range
from test_agent_gateway import gateway, _send  # noqa: F401
from test_dialogue_commands import pending_state
from test_general_dialogue_loop import model, goal
from test_inquiry_boundaries import financial_state
from test_feasibility_and_entity_binding import responses


@pytest.mark.parametrize('resource,legacy,selector', [('transactions', 'transaction_ids', 'TX1000'),
                                                    ('disputes', 'case_ids', 'CASE-001')])
def test_follow_up_uses_verified_modern_record_ids_without_falling_back_to_other_resources(resource, legacy, selector):
    from bank_agent.understanding import referenced_case, referenced_transactions
    resolver = referenced_case if resource == 'disputes' else lambda state: referenced_transactions(state)[0]
    expected = selector if resource == 'disputes' else [selector]
    state = {'user_id': 'user_zhang', 'memory_principal': 'user_zhang', 'user_input': '刚才这笔的记录',
             'observations': {'BANKING_HELP': {'resource_entities': {resource: [selector], 'wealth_products': ['LX00118']}}}}
    assert resolver(state) == expected
    state['observations']['BANKING_HELP'][legacy] = [selector, 'UNSELECTED-999']
    assert resolver(state) == expected
    state['memory_principal'] = 'user_li'
    assert resolver(state) in (None, [])


def test_modern_transaction_reference_does_not_choose_one_of_multiple_verified_records():
    from bank_agent.understanding import referenced_transactions
    state = {'user_id': 'user_zhang', 'memory_principal': 'user_zhang', 'user_input': '这笔交易有疑问',
             'observations': {'BANKING_HELP': {'resource_entities': {'transactions': ['TX1000', 'TX-CLOUD-0']}}}}
    assert referenced_transactions(state) == ([], '')
    state['slots'] = {'read_reference': 'listed_transactions'}
    state['user_input'] = '请基于刚才交易列表整体分析线索'
    assert referenced_transactions(state) == (['TX1000', 'TX-CLOUD-0'], 'TRANSACTION_REFERENCE')


def test_empty_modern_selection_does_not_revive_an_older_record_or_unselected_anomaly():
    from bank_agent.understanding import referenced_case, referenced_transactions
    state = {'user_id': 'user_zhang', 'memory_principal': 'user_zhang', 'user_input': '刚才这笔可疑交易',
             'observations': {'old': {'transaction_ids': ['OLD-TX'], 'case_ids': ['OLD-CASE']},
                'new': {'resource_entities': {'transactions': [], 'disputes': []},
                        'transaction_ids': ['OLD-TX'], 'suspicious_transaction_ids': ['OLD-TX']}}}
    assert referenced_transactions(state) == ([], '') and referenced_case(state) is None
    state['observations']['new']['resource_entities']['transactions'] = ['TX1000']
    state['user_input'] = '那5笔可疑交易'
    assert referenced_transactions(state) == ([], '')


def test_legacy_follow_up_refreshes_only_the_record_verified_by_a_modern_query(gateway, monkeypatch):  # noqa: F811
    from test_conversation_quality import fixed_model
    client, _ = gateway
    model(monkeypatch, [goal('transactions', ['records'], '查询TX1000', subject='TX1000')], intent='BILL_QUERY')
    first = _send(client, '查询TX1000的交易明细', conversation_id='modern-to-legacy-reference')
    assert first['business_status'] == 'SUCCEEDED'
    import conversation
    thread = conversation.conversation_thread_id('user_zhang', 'modern-to-legacy-reference')
    observed = conversation.get_graph().get_state({'configurable': {'thread_id': thread}}).values['observations']
    assert any(row.get('resource_entities', {}).get('transactions') == ['TX1000'] for row in observed.values()), observed
    before = financial_state()
    fixed_model(monkeypatch, 'BILL_QUERY', 'QUERY')
    reply = _send(client, '刚才这笔交易再帮我核对一下', conversation_id='modern-to-legacy-reference')
    assert reply['approval'] is None and reply['business_status'] == 'SUCCEEDED', reply['reply']
    records = reply['result']['execution_result']['records']
    assert [row['tx_id'] for row in records] == ['TX1000']
    assert financial_state() == before


@pytest.mark.parametrize('resource,identifiers', [('transactions', ['TX1000', 'TX-CLOUD-0']), ('cards', ['CARD-001', 'CARD-002']),
                                                ('wealth_products', ['LX00118', 'HQ30060']), ('tasks', ['TASK-OWN-1'])])
def test_resource_clarification_keeps_verified_candidates_separate_from_private_or_unrelated_records(resource, identifiers):
    from bank_agent.understanding import contextual_clarification
    goals = normalize_goals([goal(resource, ['records'], '对象不明确', act='UNCLEAR')], '哪项记录', {})
    parsed = {'parse_meta': {'scope': 'IN_SCOPE', 'goals': goals}}
    state = {'user_id': 'user_zhang', 'memory_principal': 'user_zhang', 'observations': {
        'old': {'resource_entities': {resource: ['STALE-PRIVATE']}},
        'new': {'resource_entities': {resource: identifiers, 'contacts': ['UNRELATED-CONTACT']}, 'transaction_ids': ['UNSELECTED-999']}}}
    answer = contextual_clarification(state, parsed)
    assert answer and all(identifier in answer for identifier in identifiers)
    assert all(value not in answer for value in ('STALE-PRIVATE', 'UNRELATED-CONTACT', 'UNSELECTED-999'))
    state['memory_principal'] = 'user_li'
    answer = contextual_clarification(state, parsed)
    assert answer and all(identifier not in answer for identifier in identifiers)
    parsed['parse_meta']['scope'] = 'OUT_OF_SCOPE'
    assert contextual_clarification(state, parsed) is None


def test_current_empty_selection_cannot_reintroduce_older_candidates_into_clarification():
    from bank_agent.understanding import contextual_clarification
    parsed = {'parse_meta': {'goals': normalize_goals([goal('transactions', ['records'], '对象不明确', act='UNCLEAR')], '哪笔', {})}}
    state = {'user_id': 'user_zhang', 'memory_principal': 'user_zhang', 'observations': {
        'old': {'resource_entities': {'transactions': ['STALE-1']}}, 'new': {'resource_entities': {'transactions': []}}}}
    assert 'STALE-1' not in contextual_clarification(state, parsed)


def test_unclear_follow_up_keeps_the_resource_and_shows_actual_candidates_without_business_writes(gateway, monkeypatch):  # noqa: F811
    from bank_agent import runtime
    from types import SimpleNamespace
    import json
    client, _ = gateway
    model(monkeypatch, [goal('transactions', ['records'], '最近交易')], intent='BILL_QUERY')
    first = _send(client, '查询最近交易列表', conversation_id='resource-clarification')
    assert first['business_status'] == 'SUCCEEDED'
    def invoke(messages):
        return SimpleNamespace(content=json.dumps({'intent': 'UNKNOWN', 'dialogue_act': 'UNCLEAR', 'scope': 'IN_SCOPE',
            'goals': [goal('transactions', ['records'], '确认最近交易列表中所指对象', act='UNCLEAR')]}))
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=invoke))
    before = financial_state()
    answer = _send(client, '最近交易中有一笔想核对，但还没说明哪笔', conversation_id='resource-clarification')
    assert answer['approval'] is None and 'TX1000' in answer['reply'] and 'TX-CLOUD-0' in answer['reply']
    assert '想查询还是办理' not in answer['reply'] and financial_state() == before


@pytest.mark.parametrize('text', ['那就按10000元给我设置日限额', '帮我把日限额设置为10000元', '请给我冻结卡片'])
def test_card_input_never_uses_the_payee_grammar_as_its_card_identifier(text):
    assert _local_parse_rules(text)['target'] is None
    assert local_parse(text)['target'] is None
    assert local_parse(text+'，指定CARD-001')['target'] == 'CARD-001'


def test_action_after_limit_consultation_uses_the_owned_card_instead_of_a_verb_phrase(gateway):  # noqa: F811
    client, _ = gateway
    _send(client, '卡片日额度规则是什么', conversation_id='typed-card')
    result = _send(client, '那就按10000元给我设置日限额，并把待确认的内容展示出来', conversation_id='typed-card')
    assert result['approval'] and 'CARD-001' in result['approval']['summary']
    assert '10,000' in result['approval']['summary']


@pytest.mark.parametrize('text', ['查本人账单哪些交易可疑，最近总担心误扣款', '解释本月消费与余额的区别，我最近有点担心'])
def test_recent_background_does_not_invent_a_query_period(text):
    assert requested_date_range(text) is None
    assert local_parse(text)['date_scope'] == ('MONTH' if '本月' in text else 'ALL')


def test_recent_query_has_a_bounded_period_and_explicit_calendar_scope_wins():
    now = datetime(2026, 10, 28, tzinfo=timezone(timedelta(hours=8)))
    assert requested_date_range('查最近几笔交易', now=now) == {'start': '2026-09-29', 'end': '2026-10-28'}
    assert requested_date_range('查本月消费类别，最近总感觉余额不对', now=now) is None


def test_one_explicitly_resumed_task_keeps_its_known_payee_without_repetition():
    state = pending_state()
    parsed = {'intent': 'TRANSFER', 'parse_meta': {'dialogue_act': 'SLOT_REPLY'}}
    result = resume_task(state, parsed, '那回到刚才的转账，金额是300元，先不确认')
    assert result and result['_verified_slot_resume'] == 300 and result['payload']['target'] == '李四'
    state['dialogue_state']['tasks'].append(deepcopy(state['dialogue_state']['tasks'][0]) | {'id': 'second'})
    assert resume_task(state, parsed, '那回到刚才的转账，金额是300元，先不确认') is None


@pytest.mark.parametrize('prefix', ['先不管生日联动', '暂时不谈生日联动', '我先不聊生日安排'])
def test_fill_clause_selects_the_current_business_after_a_parked_topic(prefix):
    state = pending_state()
    before = deepcopy(state)
    parsed = {'intent': 'TRANSFER', 'parse_meta': {'dialogue_act': 'SLOT_REPLY'}}
    text = prefix + '，原转账金额补上300元，暂不确认。'
    restored = resume_task(state, parsed, text)
    assert restored and restored['_verified_slot_resume'] == 300 and restored['payload']['target'] == '李四'
    assert state == before
    state['dialogue_state']['tasks'].append(deepcopy(state['dialogue_state']['tasks'][0]) | {'id': 'other'})
    assert resume_task(state, parsed, text) is None


@pytest.mark.parametrize('text', [
    '帮我办理生日联动，原转账金额补上300元，暂不确认',
    '申购零星宝，原转账金额补上300元，暂不确认',
    '先不管生日联动，原转账金额补上300元，另外给王美转500元',
    '先不管生日联动，原转账金额补上300元可以吗？',
    '如果不管生日联动，原转账金额补上300元',
    '先不管生日联动，原转账金额补上300元，但不要转账',
    '解释这句话“原转账金额补上300元”',
])
def test_fill_clause_cannot_discard_another_operation_or_a_whole_message_constraint(text):
    assert resume_task(pending_state(), {'intent': 'TRANSFER', 'parse_meta': {'dialogue_act': 'SLOT_REPLY'}}, text) is None


def test_parked_birthday_topic_does_not_erase_original_transfer_payee_at_the_gateway(gateway, monkeypatch):  # noqa: F811
    import json
    from bank_agent import runtime
    client, _ = gateway
    cid = 'parked-topic-slot-fill'
    first = _send(client, '我想给李四转一点钱，金额还没定，先帮我发起转账。', conversation_id=cid)
    assert first['intent'] == 'TRANSFER' and '金额' in first['reply'], (first['intent'], first['reply'])
    model(monkeypatch, [goal('general', [], '生日联动规则', act='EXPLAIN')])
    _send(client, '转账金额我先不补，想先问一下生日联动怎么弄？', conversation_id=cid)
    from types import SimpleNamespace
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=lambda messages: SimpleNamespace(content=json.dumps({
        'intent': 'TRANSFER', 'dialogue_act': 'SLOT_REPLY', 'scope': 'IN_SCOPE',
        'goals': [goal('general', [], '补充原转账金额', act='ACTION')]}))))
    before = financial_state()
    reply = _send(client, '先不管生日联动，原转账金额补上300元，暂不确认。', conversation_id=cid)
    assert reply['approval'], (reply['intent'], reply['reply'])
    assert '李四' in reply['approval']['summary'] and '300' in reply['approval']['summary']
    assert financial_state()[:4] == before[:4]


def test_successful_side_question_cannot_consume_the_paused_task_named_by_its_pause_command():
    from bank_agent.dialogue_state import remember_discussion
    state = {**pending_state(), 'intent': 'BANKING_HELP', 'user_input': '转账先放着，生日联动怎么做',
             'parse_meta': {'task_command': {'type': 'PAUSE', 'task_id': 'TRANSFER', 'evidence': '转账先放着'}}}
    result = remember_discussion(state, {'business_status': 'SUCCEEDED'})
    assert len(result['tasks']) == 1 and result['tasks'][0]['pending']['payload']['target'] == '李四'


@pytest.mark.parametrize('act', ['QUERY', 'EXPLAIN', 'SLOT_REPLY'])
def test_literal_resume_amount_is_verified_independently_of_the_model_act(act):
    result = resume_task(pending_state(), {'intent': 'TRANSFER', 'parse_meta': {'dialogue_act': act}},
                         '回到刚才的转账，金额就按原来的300元。')
    assert result and result['_verified_slot_resume'] == 300
    assert resume_task(pending_state(), {'intent': 'TRANSFER', 'parse_meta': {'dialogue_act': act}},
                       '回到刚才的转账，300元的手续费是多少') is None


@pytest.mark.parametrize('text', ['好，那转账金额补上300元，先别确认。',
                                '转账金额补300元，先别确认，等确认卡出来我再看。',
                                '转账金额补三百元，先不确认。'])
def test_explicitly_filling_the_transfer_amount_can_resume_one_paused_discussion(text):
    parsed = {'intent': 'TRANSFER', 'parse_meta': {'dialogue_act': 'SLOT_REPLY', 'resume_task_id': 'TRANSFER'}}
    state = pending_state()
    result = resume_task(state, parsed, text)
    assert result and result['_verified_slot_resume'] == 300 and result['payload']['target'] == '李四'
    state['dialogue_state']['tasks'].append(deepcopy(state['dialogue_state']['tasks'][0]) | {'id': 'another'})
    assert resume_task(state, parsed, text) is None


@pytest.mark.parametrize('act', ['ACTION', 'SLOT_REPLY'])
@pytest.mark.parametrize('text', ['转账金额是300元，先帮我看看，暂不确认。', '转账金额就按300元，先展示待确认信息。',
                                '转账金额是300元，先给我看确认信息，我暂不确认。', '转账金额是300元，先给我把确认卡生成出来，我暂不确认。'])
def test_typed_amount_reply_can_fill_one_known_task_without_any_restore_keyword(act, text):
    parsed = {'intent': 'TRANSFER', 'parse_meta': {'dialogue_act': act}}
    state = pending_state()
    restored = resume_task(state, parsed, text)
    assert restored and restored['_verified_slot_resume'] == 300 and restored['payload']['target'] == '李四'
    state['dialogue_state']['tasks'].append(deepcopy(state['dialogue_state']['tasks'][0]) | {'id': 'other-transfer'})
    assert resume_task(state, parsed, text) is None


@pytest.mark.parametrize('text', ['转账金额是300元的手续费是多少', '转账金额是300元，先不办理',
                                '如果转账金额是300元会怎样', '给王五转300元', '300元', '转账金额是300或500元'])
def test_typed_amount_reply_still_requires_literal_business_and_unambiguous_matching_request(text):
    assert resume_task(pending_state(), {'intent': 'TRANSFER', 'parse_meta': {'dialogue_act': 'SLOT_REPLY'}}, text) is None


@pytest.mark.parametrize('act', ['QUERY', 'EXPLAIN', 'UNCLEAR', 'UNSUPPORTED'])
def test_typed_slot_cannot_override_a_read_only_or_unclear_goal(act):
    text = '转账金额是300元，先帮我看看，暂不确认。'
    goals = normalize_goals([goal('general', [], '转账咨询', act=act)], text, {})
    parsed = {'intent': 'TRANSFER', 'parse_meta': {'dialogue_act': 'ACTION', 'scope': 'IN_SCOPE', 'goals': goals}}
    assert resume_task(pending_state(), parsed, text) is None
    parsed['parse_meta'].update(scope='OUT_OF_SCOPE', goals=[])
    assert resume_task(pending_state(), parsed, text) is None


@pytest.mark.parametrize('act', ['QUERY', 'EXPLAIN', 'UNCLEAR', 'UNSUPPORTED'])
def test_discussion_action_reference_never_binds_personal_query_or_unclear_goals(act):
    context = {'action_reference_subjects': ['李四']}
    result = normalize_goals([goal('general', [], '承接对象', act=act, subject='李四')], '金额是300元', context)[0]
    assert result['act'] == 'UNCLEAR' and result['needs_subject']
    action = normalize_goals([goal('general', [], '补资料', act='ACTION', subject='李四')], '金额是300元', context)[0]
    assert action['act'] == 'ACTION' and action['subject'] == '李四'


@pytest.mark.parametrize('selected', ['TRANSFER', None, 'FAKE-TASK'])
def test_model_fill_goal_preserves_selected_discussion_target_without_requiring_repetition(gateway, monkeypatch, selected):  # noqa: F811
    from bank_agent import runtime
    from types import SimpleNamespace
    import json
    client, _ = gateway
    cid = 'selected-discussion-goal'
    _send(client, '给李四转一点钱', conversation_id=cid)
    model(monkeypatch, [goal('general', [], '生日联动规则', act='EXPLAIN')])
    _send(client, '先问一下生日联动怎么弄，转账金额稍后说', conversation_id=cid)
    def invoke(messages):
        value = {'intent': 'TRANSFER', 'dialogue_act': 'SLOT_REPLY', 'scope': 'IN_SCOPE',
                 'goals': [goal('general', [], '给李四转300元的待确认提案', act='ACTION', subject='李四')]}
        if selected:
            value['task_command'] = {'type': 'FILL', 'task_id': selected, 'evidence': '转账金额是300元'}
        return SimpleNamespace(content=json.dumps(value))
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=invoke))
    before = financial_state()
    reply = _send(client, '转账金额是300元，先帮我看看，暂不确认。', conversation_id=cid)
    assert reply['approval'] and '李四' in reply['approval']['summary'] and '300.00' in reply['approval']['summary']
    assert financial_state()[:4] == before[:4]


@pytest.mark.parametrize('text', ['转账补贴300元', '转账补偿300元', '如果转账金额补300元会怎样',
                                '转账金额补300元的手续费是多少', '转账金额补300元或500元', '转账金额补300元，先不办理'])
def test_amount_filling_does_not_resume_a_noun_consultation_ambiguity_or_withdrawal(text):
    assert resume_task(pending_state(), {'intent': 'TRANSFER'}, text) is None


@pytest.mark.parametrize('text', ['转账金额补300元，先不办理', '金额补300元，我暂时不办', '本次先不要办理'])
def test_explicit_standalone_do_not_proceed_clause_restricts_every_execution_path(text):
    from intent_language import execution_constraint
    assert execution_constraint(text) == 'NEGATED_ACTION'


@pytest.mark.parametrize('text', ['转账金额补300元，先别确认', '给李四转300元，等确认卡出来我再看', '不是不办理，给李四转300元'])
def test_waiting_for_approval_or_negating_a_refusal_is_not_withdrawing_the_request(text):
    from intent_language import execution_constraint
    assert execution_constraint(text) is None


@pytest.mark.parametrize('act', ['ACTION', 'SLOT_REPLY', 'EXPLAIN', 'UNCLEAR'])
def test_literal_stop_closes_only_the_unique_matching_discussion_even_when_model_act_differs(act):
    from bank_agent.dialogue_commands import withdraw_known_task
    parsed = {'intent': 'TRANSFER', 'parse_meta': {'dialogue_act': act, 'scope': 'IN_SCOPE'}}
    transition = withdraw_known_task(pending_state(), parsed, '转账金额补300元，先不办理。')
    assert transition and transition['type'] == 'CANCEL' and transition['view']['tasks'] == []
    assert transition['parsed']['parse_meta']['dialogue_act'] == 'WITHDRAW' and transition['proposal_input'] is False
    parsed['parse_meta']['scope'] = 'OUT_OF_SCOPE'
    assert withdraw_known_task(pending_state(), parsed, '转账金额补300元，先不办理。') is None


@pytest.mark.parametrize('text', ['转账金额补300元，先不办理，解释手续费', '先不办理，查询我的余额',
                                '给王五转账先不办理', '如果转账先不办理会怎样'])
def test_literal_stop_does_not_consume_a_separate_question_or_a_different_object(text):
    from bank_agent.dialogue_commands import withdraw_known_task
    parsed = {'intent': 'TRANSFER', 'parse_meta': {'dialogue_act': 'EXPLAIN', 'scope': 'IN_SCOPE'}}
    assert withdraw_known_task(pending_state(), parsed, text) is None


@pytest.mark.parametrize('act', ['ACTION', 'SLOT_REPLY', 'EXPLAIN'])
def test_gateway_acknowledges_stopping_a_known_task_without_turning_it_into_unrelated_help(gateway, monkeypatch, act):  # noqa: F811
    from bank_agent import runtime
    from types import SimpleNamespace
    import json
    client, _ = gateway
    cid = 'literal-stop-acknowledgement'
    _send(client, '给李四转一点钱', conversation_id=cid)
    model(monkeypatch, [goal('general', [], '生日联动规则', act='EXPLAIN')])
    _send(client, '先问一下生日联动怎么弄，转账金额稍后说', conversation_id=cid)
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=lambda messages: SimpleNamespace(content=json.dumps({
        'intent': 'TRANSFER', 'dialogue_act': act, 'scope': 'IN_SCOPE',
        'goals': [goal('general', [], '补金额但暂不办理', act='ACTION')]}))))
    before = financial_state()
    reply = _send(client, '转账金额补300元，先不办理。', conversation_id=cid)
    assert reply['approval'] is None and reply['business_status'] == 'CANCELLED'
    assert '尚未执行' in reply['reply'] and '当前待补信息任务' in reply['reply']
    assert financial_state() == before


def test_consulted_incomplete_intention_can_be_explicitly_resumed_without_turning_the_question_into_a_proposal(gateway, monkeypatch):  # noqa: F811
    from bank_agent import runtime
    client, _ = gateway
    model(monkeypatch, [goal('general', [], '如何给李四转账', act='EXPLAIN')])
    before = financial_state()
    first = _send(client, '我想给李四转一点钱，先了解怎么转', conversation_id='consulted-intention')
    assert first['approval'] is None and financial_state() == before
    monkeypatch.setattr(runtime, 'llm', None)
    _send(client, '先问一下生日联动怎么弄', conversation_id='consulted-intention')
    final = _send(client, '那回到刚才的转账，金额300元，帮我继续转账，先不确认', conversation_id='consulted-intention')
    assert final['approval'] and '李四' in final['approval']['summary'] and '300' in final['approval']['summary']
    assert financial_state()[:4] == before[:4]


def test_discussed_intention_does_not_make_a_bare_amount_an_action(gateway, monkeypatch):  # noqa: F811
    from bank_agent import runtime
    client, _ = gateway
    model(monkeypatch, [goal('general', [], '如何给李四转账', act='EXPLAIN')])
    _send(client, '我想给李四转一点钱，先了解怎么转', conversation_id='consulted-bare-number')
    monkeypatch.setattr(runtime, 'llm', None)
    before = financial_state()
    reply = _send(client, '300元', conversation_id='consulted-bare-number')
    assert reply['approval'] is None and financial_state() == before


def test_payee_pronoun_is_bound_to_one_verified_contact_and_rechecked_against_core(monkeypatch):
    responses(monkeypatch, [{'name': '李四', 'contact_id': 'CT-LI', 'account_id': 'user_li'}],
              [{'display_name': '李四', 'contact_id': 'user_li'}])
    state = {'user_id': 'user_zhang', 'memory_principal': 'user_zhang',
             'observations': {'BANKING_HELP': {'resource_entities': {'contacts': ['CT-LI']}}}}
    text = '我想给他办一笔预约转账'
    parsed = local_parse(text)
    assert bind_payee(state, parsed, text)['target'] == '李四'
    state['memory_principal'] = 'user_li'
    assert bind_payee(state, parsed, text)['target'] is None
    state['memory_principal'] = 'user_zhang'
    state['observations']['BANKING_HELP']['resource_entities']['contacts'].append('CT-OTHER')
    assert bind_payee(state, parsed, text)['target'] is None


def test_real_contact_label_wins_even_when_it_looks_like_a_pronoun_and_verb(monkeypatch):
    responses(monkeypatch, [{'name': '他办一笔', 'contact_id': 'CT-REAL', 'account_id': 'real-user'}], [])
    text = '给他办一笔预约转账'
    assert bind_payee({'user_id': 'user_zhang'}, local_parse(text), text)['target'] == '他办一笔'


def test_unsaved_discussed_name_requires_fresh_allowed_payee_evidence(monkeypatch):
    responses(monkeypatch, [], [{'display_name': '李四', 'contact_id': 'user_li'}])
    state = {'user_id': 'user_zhang', 'memory_principal': 'user_zhang', 'observations': {
        'BANKING_HELP': {'resource_entities': {'contacts': []}, 'requested_subjects': {'contacts': ['李四']}}}}
    text = '我想给他办一笔预约转账'
    assert bind_payee(state, local_parse(text), text)['target'] == '李四'
    responses(monkeypatch, [], [])
    assert bind_payee(state, local_parse(text), text)['target'] is None


def test_a_discussion_noun_after_a_name_is_repaired_only_with_current_core_evidence(monkeypatch):
    text = '给李四的事，金额补300元，先不确认'
    parsed = {'intent': 'TRANSFER', 'target': '李四的事', 'amount': 300}
    responses(monkeypatch, [], [{'display_name': '李四', 'contact_id': 'user_li'}])
    assert bind_payee({'user_id': 'user_zhang'}, parsed, text)['target'] == '李四'
    responses(monkeypatch, [{'name': '李四的事', 'account_id': 'actual-full-name'}], [{'display_name': '李四', 'contact_id': 'user_li'}])
    assert bind_payee({'user_id': 'user_zhang'}, parsed, text)['target'] == '李四的事'


@pytest.mark.parametrize('text', ['我看到云端存储代扣19.90元，查这笔交易线索',
    '我看到有一笔云端存储19.90的订阅代扣，我不太记得开过这个，能帮我查一下这笔可疑交易的线索吗？'])
def test_wrong_subscription_classifier_cannot_send_a_transaction_goal_to_precheck(gateway, monkeypatch, text):  # noqa: F811
    from bank_agent import runtime
    client, _ = gateway
    model(monkeypatch, [goal('transactions', ['records', 'merchant', 'amount'], '云端存储这笔扣款的线索', subject='云端存储')],
          intent='SUBSCRIPTION_QUERY')
    before = financial_state()
    calls = []
    underlying = runtime._http
    def track(method, path, **kwargs):
        calls.append((method, path))
        return underlying(method, path, **kwargs)
    monkeypatch.setattr(runtime, '_http', track)
    result = _send(client, text, conversation_id='read-contract')
    assert result['approval'] is None and result['intent'] == 'BANKING_HELP'
    assert financial_state() == before and '不接受金额' not in result['reply']
    assert '起止日期' not in result['reply']
    assert any(method == 'GET' and '/transactions' in path for method, path in calls)
    assert all(method == 'GET' for method, _ in calls)


def test_legacy_transaction_route_is_not_overridden_by_subscription_merchant_keyword():
    parsed = {'intent': 'SUBSCRIPTION_QUERY', 'amount': 19.9, 'slots': {}, 'parse_meta': {'dialogue_act': 'QUERY'}}
    assert enforce_inquiry(parsed, '查询这笔订阅代扣交易的线索')['intent'] == 'BILL_QUERY'
    assert enforce_inquiry(parsed, '视频会员每期扣多少钱')['intent'] == 'SUBSCRIPTION_QUERY'
    assert enforce_inquiry(parsed, '视频会员每期扣25元吗')['amount'] is None


def test_self_labels_do_not_make_owned_singleton_queries_ambiguous():
    goals = normalize_goals([goal('balance', ['available_balance'], '当前可用余额', subject='当前账户')],
                            '我只想知道可用的钱是多少', {})
    assert goals[0]['act'] == 'QUERY' and not goals[0]['subjects'] and not goals[0]['needs_subject']
    other = normalize_goals([goal('balance', ['available_balance'], '他人余额', subject='不存在的姓名')],
                            '查一下余额', {})
    assert other[0]['act'] == 'UNCLEAR' and other[0]['needs_subject']


def test_product_comparison_goals_do_not_reenter_the_legacy_product_id_clarification(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    model(monkeypatch, [goal('wealth_products', ['min_amount'], '比较起购金额') | {'subjects': ['零星宝', '恒泉稳利']}],
          intent='WEALTH_COMPARE')
    result = _send(client, '对比零星宝和恒泉稳利起购金额', conversation_id='typed-product-goals')
    assert result['intent'] == 'BANKING_HELP' and '提供产品编号' not in result['reply']
    assert '1,000.00' in result['reply']


def test_generic_shelf_description_is_not_an_unbound_product_name(gateway):  # noqa: F811
    goals = normalize_goals([goal('wealth_products', [], '货架产品起购条件', act='EXPLAIN', subject='在售理财产品')],
                            '货架产品起购条件是什么', {})
    assert goals[0]['act'] == 'EXPLAIN' and not goals[0]['subjects']
    session = EvidenceSession({'user_id': 'user_zhang', 'user_input': '货架产品起购条件是什么', 'parse_meta': {'goals': goals}})
    session.bootstrap()
    assert any(row['source'] == 'core://wealth_products' and 'min_amount' in row.get('fields', []) for row in session.facts())


def test_explicit_incomplete_action_is_not_lost_in_background_rule_words(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    model(monkeypatch, [goal('general', [], '申购规则', act='EXPLAIN')])
    reply = _send(client, '我记不太清零星宝的申购规则，不过我想先申购零星宝，金额我还没定。', conversation_id='incomplete-action')
    assert reply['approval'] is None and '金额' in reply['reply']
    assert reply['result']['risk_quiz']['active']


@pytest.mark.parametrize('text', ['我想先了解申购零星宝是否可行', '如果我想先申购零星宝，会怎样',
                                '请解释怎么申购零星宝', '我想先申购零星宝，但只想了解可行性'])
def test_consultation_and_hypothesis_still_do_not_start_purchase_or_quiz(gateway, monkeypatch, text):  # noqa: F811
    client, _ = gateway
    model(monkeypatch, [goal('general', [], '申购规则', act='EXPLAIN')])
    before = financial_state()
    reply = _send(client, text, conversation_id='keep-consultation')
    assert reply['approval'] is None and financial_state() == before
    assert not (reply['result'].get('risk_quiz') or {}).get('active')


def test_refreshed_explanation_and_repair_keep_one_period(gateway):  # noqa: F811
    goals = normalize_goals([goal('transactions', ['amount'], '解释消费金额', act='EXPLAIN') | {'requires_record': True}],
                            '解释消费金额', {})
    state = {'user_id': 'user_zhang', 'memory_principal': 'user_zhang', 'date_scope': 'ALL',
             'user_input': '解释消费金额', 'parse_meta': {'goals': goals, 'topic_relation': 'CONTINUE'},
             'observations': {'BANKING_HELP': {'read_requests': [{'tool': 'transactions', 'fields': ['amount']}],
                 'read_contexts': {'transactions': {'date_scope': 'MONTH', 'date_range': None}}}}}
    session = EvidenceSession(state)
    session.bootstrap()
    session.read({'tool': 'transactions', 'fields': ['amount', 'card_id']})
    assert session.contexts['transactions']['date_scope'] == 'MONTH'
    assert sum('entity_id' in row for row in session.facts()) == 2
    session.read({'tool': 'transactions', 'fields': ['amount']}, date_context={'date_scope': 'ALL', 'date_range': None})
    assert session.contexts['date_scope'] if 'date_scope' in session.contexts else session.contexts['transactions']['date_scope'] == 'ALL'
    assert sum('entity_id' in row for row in session.facts()) == 8


def test_recent_transaction_period_and_deictic_two_count_survive_unclear_clarification(gateway, monkeypatch):  # noqa: F811
    from bank_agent.understanding import referenced_transactions
    now = datetime(2026, 10, 30, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    for phrase in ('你能帮我先整体看看最近这些交易里有没有什么可疑的地方吗？', '这些最近交易里有一笔我有点疑问'):
        assert requested_date_range(phrase, now=now) == {'start': '2026-10-01', 'end': '2026-10-30'}
    state = {'user_id': 'user_zhang', 'memory_principal': 'user_zhang',
             'observations': {'BILL_QUERY': {'resource_entities': {'transactions': ['TX1000', 'TX-CLOUD-0']}}}}
    state['user_input'] = '这些最近交易里有一笔我有点疑问，但一时说不上是哪一笔'
    assert referenced_transactions(state) == ([], '')
    state['user_input'] = '这两笔交易我一时说不上具体哪一笔有问题，整体帮我看看'
    assert referenced_transactions(state) == (['TX1000', 'TX-CLOUD-0'], 'TRANSACTION_REFERENCE')

    client, _ = gateway
    cid = 'recent-period-across-unclear'
    model(monkeypatch, [goal('transactions', ['records'], '查询最近交易明细') | {'requires_record': True}], intent='BILL_QUERY')
    turn1 = _send(client, '好的，那麻烦帮我查一下最近的交易明细。', conversation_id=cid)
    assert turn1['business_status'] == 'SUCCEEDED'
    assert [row['tx_id'] for row in turn1['result']['execution_result']['records']] == ['TX1000', 'TX-CLOUD-0']

    from bank_agent import runtime
    from types import SimpleNamespace
    import json
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=lambda messages: SimpleNamespace(content=json.dumps(
        {'intent': 'UNKNOWN', 'dialogue_act': 'UNCLEAR', 'scope': 'IN_SCOPE',
         'goals': [goal('transactions', ['records'], '确认哪笔交易', act='UNCLEAR')]}
    ))))
    turn2 = _send(client, '这些最近交易里有一笔我有点疑问，但一时说不上是哪一笔，你能先帮我一起确认一下我指的是哪笔吗？', conversation_id=cid)
    assert turn2['business_status'] == 'NEEDS_INPUT' and 'TX1000' in turn2['reply'] and 'TX-CLOUD-0' in turn2['reply']

    planned = []
    def plan_once(payload):
        if not planned:
            planned.append(True)
            return {'reads': [{'tool': 'transactions', 'fields': ['records', 'merchant', 'amount', 'category', 'analysis']}]}
        return {'reads': []}

    def cite_all(payload):
        rows = [row for row in payload['facts'] if row['id'].startswith('read.transactions.')]
        ids = [row['id'] for row in rows]
        return {'answer': '\n'.join(row['text'] for row in rows), 'evidence_ids': ids, 'unanswered': [],
                'coverage': [{'goal_id': payload['goals'][0]['id'], 'status': 'ANSWERED', 'evidence_ids': ids, 'reason': 'ok'}]}

    model(monkeypatch, [goal('transactions', ['records', 'analysis'], '整体分析这两笔交易可疑线索', act='EXPLAIN') | {'requires_record': True}],
          intent='BILL_ANALYSIS', planner=plan_once, draft=cite_all)
    turn3 = _send(client, '我还是不确定是哪一笔，麻烦你就根据刚才查到的这两笔交易整体帮我看看，有没有哪笔可疑或者异常线索？', conversation_id=cid)
    assert turn3['business_status'] == 'SUCCEEDED'
    records = turn3['result']['execution_result']['records']
    assert [row['tx_id'] for row in records] == ['TX1000', 'TX-CLOUD-0']
    assert 'TX1004' not in turn3['reply'] and '全部时间' not in turn3['reply']
