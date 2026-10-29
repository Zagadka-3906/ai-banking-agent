"""Natural action qualifiers and withdrawals preserve the discussion boundary."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from bank_agent import runtime
from bank_agent.dialogue_commands import apply_command, withdraw_known_task
from bank_agent.dialogue_state import dialogue_view
from bank_agent.understanding import enforce_inquiry
from intent_language import action_request_clause, execution_constraint, pending_withdrawal_clause
from test_agent_gateway import _send, gateway  # noqa: F401
from test_general_dialogue_loop import goal, model
from test_inquiry_boundaries import financial_state
from test_intent_language_boundaries import state as saved_state


@pytest.mark.parametrize('operation,intent', [
    ('申购零星宝', 'WEALTH_PURCHASE'), ('赎回零星宝', 'WEALTH_REDEEM'),
    ('给李四转账', 'TRANSFER'), ('冻结银行卡', 'CARD_FREEZE'),
])
def test_first_person_action_survives_a_later_procedural_qualifier(operation, intent):
    text = f'那我先{operation}吧，先按办理流程走，让我看看下一步怎么填。'
    parsed = {'intent': intent, 'parse_meta': {'source': 'model', 'dialogue_act': 'ACTION'}}
    assert action_request_clause(text)
    assert execution_constraint(text) is None
    assert enforce_inquiry(parsed, text)['intent'] == intent


@pytest.mark.parametrize('text', [
    '那我先怎么申购零星宝', '我先了解如何申购零星宝',
    '那我先申购零星宝可以吗', '如果我先申购零星宝，下一步怎么填',
    '我不想申购零星宝，下一步怎么填',
    '我想把日限额改成10000，先了解这个数值行不行，暂不确认',
])
def test_question_negation_and_feasibility_cannot_become_an_action(text):
    result = enforce_inquiry({'intent': 'WEALTH_PURCHASE', 'parse_meta': {'dialogue_act': 'ACTION'}}, text)
    assert result['intent'] == 'BANKING_HELP'


def pending_quiz():
    return {'user_id': 'user_zhang', 'memory_principal': 'user_zhang',
            'context': {'risk_quiz': {'answers': {'1': 'B'}, 'origin_intent': 'WEALTH_PURCHASE',
                                     'origin_target': 'LX00118'}}}


def withdrawal(text, *, command=False):
    meta = {'dialogue_act': 'WITHDRAW'}
    if command:
        meta['task_command'] = {'type': 'CANCEL', 'task_id': 'quiz:WEALTH_RISK_ASSESSMENT', 'evidence': text}
    return {'intent': 'UNKNOWN', 'parse_meta': meta}


@pytest.mark.parametrize('text', [
    '对，我要取消刚才那笔零星宝的待确认申购，麻烦帮我取消。',
    '请撤销零星宝未确认的申购', '我想放弃零星宝的申购意向',
    '既然零星宝申购后不能撤单，那我决定取消这次申购意向，先不申购了。',
    '那就先取消零星宝的申购意向', '那么我决定取消零星宝的待确认申购',
    '所以我决定取消零星宝的申购意向', '因此先取消这次零星宝申购意向',
    '那么，我决定取消零星宝的申购意向',
    '那我先不申购零星宝了，决定取消这次申购意向。',
    '决定取消零星宝的申购意向', '现在决定放弃零星宝的待确认申购',
])
def test_pending_purchase_withdrawal_resolves_its_existing_questionnaire(text):
    state = pending_quiz()
    before = deepcopy(state)
    assert pending_withdrawal_clause(text)
    parsed = enforce_inquiry(withdrawal(text), text)
    assert parsed['parse_meta']['dialogue_act'] == 'WITHDRAW'
    result = withdraw_known_task(state, parsed, text)
    assert result and result['view']['tasks'] == [] and not result['context'].get('risk_quiz')
    assert not result['proposal_input'] and result['origin_intent'] == 'WEALTH_PURCHASE'
    assert state == before


@pytest.mark.parametrize('text', [
    '取消零星宝的待确认申购会怎样', '零星宝未确认的申购怎么取消',
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


def test_no_existing_intention_or_two_matching_frames_cannot_be_guessed():
    text = '取消零星宝的待确认申购'
    assert withdraw_known_task({'user_id': 'user_zhang'}, withdrawal(text), text) is None
    state = pending_quiz()
    frame = dialogue_view(state)['tasks'][0]
    state['context'] = {}
    state['dialogue_state'] = {'principal': 'user_zhang', 'tasks': [frame, {**frame, 'id': 'second-quiz'}]}
    assert withdraw_known_task(state, withdrawal(text), text) is None
    state['user_id'] = 'user_li'
    assert withdraw_known_task(state, withdrawal(text), text) is None


@pytest.mark.parametrize('text', ['取消零星宝的待确认申购', '那我先不申购零星宝了，决定取消这次申购意向。'])
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


@pytest.mark.parametrize('cancellation', [
    '对，我要取消刚才那笔零星宝的待确认申购，麻烦帮我取消。',
    '既然零星宝申购后不能撤单，那我决定取消这次申购意向，先不申购了。',
    '那我先不申购零星宝了，决定取消这次申购意向。',
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
        elif '我要取消' in text or '决定取消' in text:
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
