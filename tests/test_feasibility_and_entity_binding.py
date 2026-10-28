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


@pytest.mark.parametrize('verb', ['办', '办理', '安排'])
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
