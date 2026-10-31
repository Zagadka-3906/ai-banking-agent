"""Consultation is not consent; identity evidence precedes grammatical repair."""
import json
from types import SimpleNamespace

import pytest

from bank_agent import runtime
from bank_agent.entities import bind_payee
from bank_agent.parsing import local_parse
from test_agent_gateway import gateway, _send  # noqa: F401
from test_inquiry_boundaries import wrong_model, financial_state


@pytest.mark.parametrize('text', [
    '我想把日限额改成10000，先了解这个数值行不行，暂不确认',
    '我想给李四转300元，先咨询是否可行',
    '我想申购零星宝，先了解可行性',
    '想把这张卡的日限额设成10000元，这个数在允许范围里吗？',
    '给李四转300元，这笔金额符合转账条件吗？',
])
def test_feasibility_does_not_create_a_proposal_even_with_wrong_action_model(gateway, monkeypatch, text):  # noqa: F811
    client, _ = gateway
    wrong_model(monkeypatch, 'CARD_LIMIT')
    before = financial_state()
    reply = _send(client, text, conversation_id='feasibility')
    assert reply['approval'] is None and reply['intent'] == 'BANKING_HELP'
    assert financial_state() == before


def test_explicit_limit_action_after_consultation_can_still_get_an_approval(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    monkeypatch.setattr(runtime, 'llm', None)
    _send(client, '先了解设置10000元日限额是否可行', conversation_id='ask-then-act')
    reply = _send(client, '现在请把CARD-001日限额设置为10000元', conversation_id='ask-then-act')
    assert reply['approval'] and '10,000' in reply['approval']['summary']


def test_contextual_limit_input_cannot_bypass_understanding_of_a_later_question(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    monkeypatch.setattr(runtime, 'llm', None)
    _send(client, '能取消卡片限额吗', conversation_id='limit-followup-question')
    calls = []
    wrong_model(monkeypatch, 'CARD_LIMIT', calls)
    before = financial_state()
    reply = _send(client, '那按你刚才说的规则，想把这张卡的日限额设成10000元，这个数在允许范围里吗？',
                  conversation_id='limit-followup-question')
    assert calls, 'Natural-language questions must not bypass the model through contextual slot filling'
    assert reply['approval'] is None and financial_state() == before


def responses(monkeypatch, book, payees):
    calls = []
    def http(method, path, **kwargs):
        assert method == 'GET' and kwargs['headers']['X-User-Id'] == 'user_zhang'
        calls.append(path)
        return SimpleNamespace(status_code=200, json=lambda: {'data': book if path.endswith('/book') else payees})
    monkeypatch.setattr(runtime, '_http', http)
    return calls


def test_suffix_is_bound_to_a_real_unique_core_payee(monkeypatch):
    calls = responses(monkeypatch, [], [{'display_name': '李四', 'contact_id': 'user_li'}])
    text = '按300元转给李四吧，请帮我恢复这个转账提案'
    parsed = local_parse(text)
    bound = bind_payee({'user_id': 'user_zhang'}, parsed, text)
    assert bound['target'] == '李四'
    assert bound['parse_meta']['target_binding']['entity_id'] == 'user_li'
    assert len(calls) == 2


@pytest.mark.parametrize('verb', ['办', '办理', '安排', '办一笔', '办理一笔', '安排一笔'])
def test_payee_business_verb_suffix_uses_current_unique_identity(monkeypatch, verb):
    responses(monkeypatch, [], [{'display_name': '李四', 'contact_id': 'user_li'}])
    text = '给李四' + verb + '预约转账，金额和日期没想好'
    parsed = {'intent': 'SCHEDULED_TRANSFER', 'target': '李四' + verb, 'parse_meta': {'dialogue_act': 'ACTION'}}
    result = bind_payee({'user_id': 'user_zhang'}, parsed, text)
    assert result['target'] == '李四' and result['parse_meta']['target_binding']['entity_id'] == 'user_li'


def test_actual_full_payee_label_wins_over_business_verb_suffix(monkeypatch):
    responses(monkeypatch, [{'name': '李四办', 'account_id': 'long-label'}], [{'display_name': '李四', 'contact_id': 'user_li'}])
    parsed = {'intent': 'SCHEDULED_TRANSFER', 'target': '李四办', 'parse_meta': {'dialogue_act': 'ACTION'}}
    assert bind_payee({'user_id': 'user_zhang'}, parsed, '给李四办预约转账') == parsed


def test_exact_real_label_wins_over_a_shorter_name_with_particle(monkeypatch):
    responses(monkeypatch, [{'name': '李四吧', 'account_id': 'actual-long-name'}], [{'display_name': '李四', 'contact_id': 'user_li'}])
    text = '给李四吧转300元'
    parsed = local_parse(text)
    assert bind_payee({'user_id': 'user_zhang'}, parsed, text)['target'] == '李四吧'


def test_two_core_identities_with_same_name_are_not_silently_selected(monkeypatch):
    responses(monkeypatch, [{'name': '李四', 'account_id': 'another-user'}], [{'display_name': '李四', 'contact_id': 'user_li'}])
    text = '给李四吧转300元'
    parsed = local_parse(text)
    assert bind_payee({'user_id': 'user_zhang'}, parsed, text)['target'] == '李四吧'


def test_suffix_never_supplies_a_person_missing_from_core(monkeypatch):
    responses(monkeypatch, [], [])
    text = '给赵星辰吧转300元'
    parsed = local_parse(text)
    assert bind_payee({'user_id': 'user_zhang'}, parsed, text)['target'] == '赵星辰吧'


@pytest.mark.parametrize('suffix', ['提案恢复出来', '转账提案恢复', '操作继续办理', '转账提案', '提案', '操作'])
def test_repeated_payee_and_discussion_object_bind_to_one_current_identity(monkeypatch, suffix):
    from intent_language import ambiguous_target
    responses(monkeypatch, [], [{'display_name': '李四', 'contact_id': 'user_li'}])
    text = '给李四转300元，把给李四的' + suffix
    assert ambiguous_target(text)
    parsed = {'intent': 'TRANSFER', 'target': None, 'amount': 300, 'slots': {'target_ambiguous': True},
              'parse_meta': {'dialogue_act': 'ACTION'}}
    result = bind_payee({'user_id': 'user_zhang'}, parsed, text)
    assert result['target'] == '李四' and not result['slots'].get('target_ambiguous')
    assert result['amount'] == 300 and result['parse_meta']['target_binding']['entity_id'] == 'user_li'


@pytest.mark.parametrize('book,payees,text', [
    ([], [], '给李四转300元，把给李四的提案恢复出来'),
    ([], [{'display_name': '李四', 'contact_id': 'user_li'}], '给李四转300元，把给赵星辰的提案恢复出来'),
    ([], [{'display_name': '李四', 'contact_id': 'user_li'}, {'display_name': '王美', 'contact_id': 'wife'}],
     '给李四转300元，把给王美的提案恢复出来'),
    ([{'name': '李四的提案恢复出来', 'account_id': 'literal-long-label'}],
     [{'display_name': '李四', 'contact_id': 'user_li'}], '给李四转300元，把给李四的提案恢复出来'),
    ([{'name': '李四', 'account_id': 'other-li'}], [{'display_name': '李四', 'contact_id': 'user_li'}],
     '给李四转300元，把给李四的提案恢复出来'),
    ([{'name': '李四的转账提案', 'account_id': 'literal-long-label'}],
     [{'display_name': '李四', 'contact_id': 'user_li'}], '给李四转300元，恢复之前那个300元转给李四的转账提案'),
    ([], [{'display_name': '李四', 'contact_id': 'user_li'}],
     '给李四转300元，恢复之前那个300元转给赵星辰的转账提案'),
    ([], [{'display_name': '李四', 'contact_id': 'user_li'}, {'display_name': '王美', 'contact_id': 'wife'}],
     '给李四转300元，恢复之前那个300元转给王美的转账提案'),
])
def test_discussion_grammar_never_discards_an_unknown_or_second_real_identity(monkeypatch, book, payees, text):
    responses(monkeypatch, book, payees)
    parsed = {'intent': 'TRANSFER', 'target': None, 'slots': {'target_ambiguous': True}, 'parse_meta': {'dialogue_act': 'ACTION'}}
    assert bind_payee({'user_id': 'user_zhang'}, parsed, text) == parsed


@pytest.mark.parametrize('text', [
    '我刚才取消了一笔给李四转300元的操作，现在想把这笔300元转给李四的提案恢复出来，继续办理。',
    '我这次想给李四转300元，请帮我恢复之前那个300元转给李四的转账提案。',
])
def test_resume_request_with_repeated_recipient_only_creates_a_confirmation(gateway, monkeypatch, text):  # noqa: F811
    client, _ = gateway
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=lambda _: SimpleNamespace(content=json.dumps(
        {'intent': 'TRANSFER', 'dialogue_act': 'ACTION', 'scope': 'IN_SCOPE', 'target': '李四'}))))
    before = financial_state()
    reply = _send(client, text, conversation_id='resume-recipient-object')
    assert reply['approval'] and '李四' in reply['approval']['summary'] and '300' in reply['approval']['summary']
    assert financial_state()[:4] == before[:4]


@pytest.mark.parametrize('resume', [
    '我这次想给李四转300元，请帮我恢复之前那个300元转给李四的转账提案。',
    '我想给李四恢复那笔300元的转账，金额是300元，请帮我办理。',
    '我想给李四继续这笔300元的转账，金额是300元，请帮我办理。',
])
def test_missing_amount_then_rule_question_then_natural_resume_obtains_a_fresh_proposal(gateway, monkeypatch, resume):  # noqa: F811
    from test_general_dialogue_loop import goal, model
    client, _ = gateway
    cid = 'missing-amount-rule-resume-frame'
    action_model = SimpleNamespace(invoke=lambda _: SimpleNamespace(content=json.dumps(
        {'intent': 'TRANSFER', 'dialogue_act': 'ACTION', 'scope': 'IN_SCOPE', 'target': '李四'})))
    monkeypatch.setattr(runtime, 'llm', action_model)
    before = financial_state()
    first = _send(client, '我想给李四转一笔钱，金额我还没想好，先帮我看看怎么转吧。', conversation_id=cid)
    assert first['approval'] is None and '金额' in first['reply']
    model(monkeypatch, [goal('general', [], '转账到账时间规定', act='EXPLAIN')])
    side = _send(client, '金额我还没定，我想先问一下给李四转账的话，到账时间是怎么规定的？', conversation_id=cid)
    assert side['approval'] is None and financial_state() == before
    monkeypatch.setattr(runtime, 'llm', action_model)
    resumed = _send(client, resume, conversation_id=cid)
    assert resumed['approval'] and all(value in resumed['approval']['summary'] for value in ('李四', '300'))
    after = financial_state()
    assert after[:4] == before[:4] and len(after[4]) == len(before[4]) + 1


def test_purchase_prerequisite_remembers_which_intention_is_abandoned(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    monkeypatch.setattr(runtime, 'llm', None)
    quiz = _send(client, '申购零星宝', conversation_id='purchase-prerequisite')
    assert quiz['result']['risk_quiz']['active'] and '金额' in quiz['reply']
    before = financial_state()
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=lambda _: SimpleNamespace(content=json.dumps(
        {'intent': 'UNKNOWN', 'dialogue_act': 'WITHDRAW', 'scope': 'IN_SCOPE'}))))
    stopped = _send(client, '我先不申购了', conversation_id='purchase-prerequisite')
    assert stopped['business_status'] == 'CANCELLED' and '申购意向' in stopped['reply']
    assert not stopped['result']['risk_quiz']['active'] and financial_state() == before


def test_abandoning_an_ambiguous_transfer_does_not_choose_one_frame():
    from bank_agent.dialogue_commands import withdraw_known_task
    from test_dialogue_commands import pending_state
    state = pending_state()
    state['dialogue_state']['tasks'].append({'id': 'second', 'intent': 'TRANSFER', 'status': 'PAUSED',
        'pending': {'intent': 'TRANSFER', 'missing': ['amount'], 'payload': {'intent': 'TRANSFER', 'target': '王美'}}})
    parsed = {'intent': 'UNKNOWN', 'parse_meta': {'dialogue_act': 'WITHDRAW'}}
    assert withdraw_known_task(state, parsed, '我先不转账了') is None
    assert len(state['dialogue_state']['tasks']) == 2


@pytest.mark.parametrize('name', ['李四', '赵星辰', '司马青云'])
@pytest.mark.parametrize('resume', ['恢复那笔', '继续这笔', '接着那一笔', '恢复刚才那笔'])
def test_resume_reference_after_payee_binds_only_an_exact_core_name(monkeypatch, name, resume):
    responses(monkeypatch, [], [{'display_name': name, 'contact_id': 'recipient-current'}])
    text = f'我想给{name}{resume}300元的转账，金额是300元，请帮我办理。'
    parsed = {'intent': 'TRANSFER', 'target': name + resume, 'amount': 300, 'slots': {},
              'parse_meta': {'dialogue_act': 'ACTION'}}
    bound = bind_payee({'user_id': 'user_zhang'}, parsed, text)
    assert bound['target'] == name and bound['amount'] == 300
    assert bound['parse_meta']['target_binding']['entity_id'] == 'recipient-current'


@pytest.mark.parametrize('rows', [[], [{'display_name': '李', 'contact_id': 'short'}],
    [{'display_name': '李四', 'contact_id': 'first'}, {'display_name': '李四', 'contact_id': 'second'}],
    [{'display_name': '李四恢复那笔', 'contact_id': 'long'}, {'display_name': '李四', 'contact_id': 'short'}]])
def test_resume_reference_does_not_prefix_match_or_override_a_literal_real_label(monkeypatch, rows):
    responses(monkeypatch, [], rows)
    text = '我想给李四恢复那笔300元的转账，金额是300元，请帮我办理。'
    parsed = local_parse(text)
    assert bind_payee({'user_id': 'user_zhang'}, parsed, text)['target'] == '李四恢复那笔'


@pytest.mark.parametrize('text', ['如果给李四恢复那笔300元的转账会怎样',
    '怎么给李四恢复那笔300元的转账', '不要给李四恢复那笔300元的转账',
    '给李四或者王美恢复那笔300元的转账'])
def test_resume_payee_grammar_never_overrides_inquiry_negation_or_recipient_choice(monkeypatch, text):
    monkeypatch.setattr(runtime, '_http', lambda *args, **kwargs: pytest.fail('binding must not read or select a payee'))
    parsed = local_parse(text)
    assert bind_payee({'user_id': 'user_zhang'}, parsed, text) == parsed


@pytest.mark.parametrize('text', ['我想恢复给李四的那笔300元转账，继续办理。', '把给李四的那笔300元转账恢复出来吧。'])
def test_stacked_possessive_measure_suffix_still_binds_the_exact_name(monkeypatch, text):
    """The 20261031 formal batch failed to bind “李四的那笔” with the verb up front."""
    responses(monkeypatch, [], [{'display_name': '李四', 'contact_id': 'recipient-current'},
                                {'display_name': '王美', 'contact_id': 'spouse'}])
    parsed = {'intent': 'TRANSFER', 'target': '李四的那笔', 'amount': 300, 'slots': {},
              'parse_meta': {'dialogue_act': 'ACTION'}}
    bound = bind_payee({'user_id': 'user_zhang'}, parsed, text)
    assert bound['target'] == '李四' and bound['amount'] == 300
    assert bound['parse_meta']['target_binding']['entity_id'] == 'recipient-current'


def test_literal_label_containing_stacked_suffixes_wins_over_stripping(monkeypatch):
    responses(monkeypatch, [], [{'display_name': '李四的那笔', 'contact_id': 'literal'},
                                {'display_name': '李四', 'contact_id': 'short'}])
    text = '我想恢复给李四的那笔300元转账，继续办理。'
    parsed = {'intent': 'TRANSFER', 'target': '李四的那笔', 'amount': 300, 'slots': {},
              'parse_meta': {'dialogue_act': 'ACTION'}}
    assert bind_payee({'user_id': 'user_zhang'}, parsed, text)['target'] == '李四的那笔'


def test_drifted_resume_message_with_stacked_suffix_creates_the_proposal(gateway, monkeypatch):  # noqa: F811
    """End-to-end replay of the 20261031 formal-batch phase 3 failure."""
    client, _ = gateway
    cid = 'drifted-resume-stacked-suffix'
    action_model = SimpleNamespace(invoke=lambda _: SimpleNamespace(content=json.dumps(
        {'intent': 'TRANSFER', 'dialogue_act': 'ACTION', 'scope': 'IN_SCOPE', 'target': '李四的那笔'})))
    monkeypatch.setattr(runtime, 'llm', action_model)
    before = financial_state()
    first = _send(client, '我想给李四转一笔钱，金额还没定，先帮我办这笔转账。', conversation_id=cid)
    assert first['approval'] is None and '金额' in first['reply']
    resumed = _send(client, '我想恢复给李四的那笔300元转账，继续办理。', conversation_id=cid)
    assert resumed['approval'] and all(value in resumed['approval']['summary'] for value in ('李四', '300'))
    assert financial_state()[:4] == before[:4]


def test_category_word_prefix_on_the_payee_is_discourse_glue(monkeypatch):
    """The 20261031 breadth failed to bind “联系人李四”."""
    responses(monkeypatch, [], [{'display_name': '李四', 'contact_id': 'recipient-current'},
                                {'display_name': '王美', 'contact_id': 'spouse'}])
    text = '我想预约一笔转账给联系人李四，不过日期和金额还没定，先帮我办这个预约转账吧。'
    parsed = {'intent': 'SCHEDULED_TRANSFER', 'target': '联系人李四', 'amount': None, 'slots': {},
              'parse_meta': {'dialogue_act': 'ACTION'}}
    bound = bind_payee({'user_id': 'user_zhang'}, parsed, text)
    # The stripped clean name flows to the normal downstream identity check.
    assert bound['target'] == '李四'


def test_category_prefix_with_an_unknown_name_still_refuses_to_guess(monkeypatch):
    responses(monkeypatch, [], [{'display_name': '王美', 'contact_id': 'spouse'}])
    parsed = {'intent': 'SCHEDULED_TRANSFER', 'target': '联系人王五', 'amount': None, 'slots': {},
              'parse_meta': {'dialogue_act': 'ACTION'}}
    bound = bind_payee({'user_id': 'user_zhang'}, parsed, '我想预约一笔转账给联系人王五。')
    assert bound['target'] == '王五' and bound['parse_meta'].get('target_binding') is None
