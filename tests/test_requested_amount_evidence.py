"""User-proposed numbers are quotable inputs, never verified account values."""
import pytest

from bank_agent.answering import _anchored_facts, _validate_draft
from bank_agent.goal_answering import _bind_literal_values, _fallback, request_amount_facts
from bank_agent.evidence_session import EvidenceSession
from bank_planning.requests import normalize_goals
from test_general_dialogue_loop import goal


FACT = {'id': 'request.amount', 'source': 'user://current-request', 'fields': ['requested_amount'],
        'text': '你本轮询问的金额为¥10,000.00；只是询问参数。'}


@pytest.mark.parametrize('text', ['你询问的金额为10000元，需要按当前政策核对。', '拟设额度为10000元，还未办理。'])
def test_request_amount_can_be_quoted_with_an_explicit_input_label(text):
    _, anchors = _anchored_facts([FACT])
    plan = {'answer': text, 'evidence_ids': ['request.amount'], 'unanswered': []}
    rendered, _ = _validate_draft(_bind_literal_values(plan, [FACT], anchors), [FACT], anchors)
    assert '10,000.00' in rendered


@pytest.mark.parametrize('text', ['可用余额为10000元。', '当前日额度为10000元。',
                                '你询问的当前余额是10000元。', '批准的日限额为10000元。'])
def test_request_amount_cannot_become_an_account_fact(text):
    _, anchors = _anchored_facts([FACT])
    plan = {'answer': text, 'evidence_ids': ['request.amount'], 'unanswered': []}
    with pytest.raises(ValueError, match='REQUEST_VALUE_AS_ACCOUNT_FACT|UNANCHORED_MONEY'):
        _validate_draft(_bind_literal_values(plan, [FACT], anchors), [FACT], anchors)


def test_an_uncited_requested_value_cannot_supply_a_number():
    other = {'id': 'guide.test', 'text': '金额需核对当前规则。'}
    _, anchors = _anchored_facts([FACT, other])
    plan = {'answer': '你询问的金额为10000元。', 'evidence_ids': ['guide.test'], 'unanswered': []}
    with pytest.raises(ValueError, match='UNANCHORED_MONEY'):
        _validate_draft(_bind_literal_values(plan, [FACT, other], anchors), [FACT, other], anchors)


def test_multiple_self_reported_amounts_are_separate_quoted_inputs_not_verified_totals():
    facts = request_amount_facts('我算了消费合计47.90元，可余额显示15000元，这两个数怎么不相等？')
    assert len(facts) == 2 and len({row['id'] for row in facts}) == 2
    _, anchors = _anchored_facts(facts)
    plan = {'answer': '你提到的合计47.90元未经核验；期间支出和当前可用余额口径不同。',
            'evidence_ids': [facts[0]['id']], 'unanswered': []}
    assert '47.90' in _validate_draft(_bind_literal_values(plan, facts, anchors), facts, anchors)[0]
    with pytest.raises(ValueError, match='REQUEST_VALUE_AS_ACCOUNT_FACT'):
        _validate_draft({**plan, 'answer': '当前可用余额为{{value.0}}。'}, facts, anchors)
    with pytest.raises(ValueError, match='UNANCHORED_MONEY'):
        _validate_draft(_bind_literal_values({**plan, 'answer': '你提到的合计48元。'}, facts, anchors), facts, anchors)


def test_amount_mentions_are_bounded_and_identifiers_cannot_supply_them():
    assert request_amount_facts('TX1004和CARD-10000，查下这些流水编号') == []
    assert len(request_amount_facts('、'.join(str(i) + '元' for i in range(1, 10)))) == 5
    assert len(request_amount_facts('合计47.90元，还是47.90元')) == 1


def test_same_number_in_balance_and_request_keeps_the_parameter_label():
    balance = {'id': 'read.balance.0', 'source': 'core://balance', 'fields': ['available_balance'], 'text': '可用余额¥10,000.00。'}
    facts = [balance, FACT]
    _, anchors = _anchored_facts(facts)
    plan = {'answer': '你询问的金额为10000元，当前尚未办理。', 'evidence_ids': [balance['id'], FACT['id']], 'unanswered': []}
    rendered, _ = _validate_draft(_bind_literal_values(plan, facts, anchors), facts, anchors)
    assert '10,000.00' in rendered


def test_record_fallback_does_not_claim_to_complete_an_explanation():
    goals = normalize_goals([goal('cards', ['daily_limit'], '解释该额度是否可行', act='EXPLAIN') | {'requires_record': True}],
                            '解释该额度是否可行', {})
    session = EvidenceSession({'user_id': 'user_zhang', 'parse_meta': {'goals': goals}})
    session.documents['read.cards.0'] = {'id': 'read.cards.0', 'source': 'core://cards',
                                       'fields': ['daily_limit'], 'text': '日额度¥5,000.00。'}
    text, coverage = _fallback(goals, session)
    assert '5,000.00' in text and coverage[0]['status'] == 'NEEDS_DATA'


def test_verified_product_name_is_not_an_unproven_numeric_attribute():
    fact = {'id': 'read.wealth_products.0', 'text': 'HQ30060：起购金额¥1,000.00。',
            'subject': '恒泉稳利·60天持有', 'entity_names': ['恒泉稳利·60天持有', 'HQ30060']}
    _, anchors = _anchored_facts([fact])
    plan = {'answer': '恒泉稳利·60天持有（HQ30060）的起购金额为{{value.0}}。',
            'evidence_ids': [fact['id']], 'unanswered': []}
    assert '恒泉稳利·60天持有' in _validate_draft(plan, [fact], anchors)[0]
    with pytest.raises(ValueError, match='UNKNOWN_NUMBER'):
        _validate_draft({**plan, 'answer': plan['answer'] + '期限为60天。'}, [fact], anchors)
    with pytest.raises(ValueError, match='UNKNOWN_NUMBER'):
        _validate_draft({**plan, 'answer': plan['answer'].replace('60天持有', '90天持有')}, [fact], anchors)


def test_uncited_product_identity_cannot_prove_a_numeric_name():
    facts = [{'id': 'read.wealth_products.0', 'text': '已查到产品。'},
             {'id': 'read.wealth_products.1', 'text': '起购条件已查询。', 'subject': '恒泉稳利·60天持有'}]
    with pytest.raises(ValueError, match='UNKNOWN_NUMBER'):
        _validate_draft({'answer': '恒泉稳利·60天持有。', 'evidence_ids': [facts[0]['id']], 'unanswered': []}, facts, {})


def test_current_cited_card_tail_is_identity_not_an_unproven_number_or_money():
    from bank_agent.evidence import project_records
    data = {'code': 200, 'user_id': 'user_zhang', 'data': [{'card_id': 'CARD-001', 'type': '工资卡',
            'status': 'FROZEN', 'daily_limit': 5000, 'last4': '3082'}]}
    facts = project_records('cards', data, {'user_id': 'user_zhang'}, fields=['status'])
    card = next(row for row in facts if row.get('entity_id') == 'CARD-001')
    assert card['card_last4'] == '3082' and 'daily_limit' not in card['fields']
    plan = {'answer': '卡片尾号：3082，登记状态FROZEN。', 'evidence_ids': [card['id']], 'unanswered': []}
    assert '3082' in _validate_draft(plan, facts, {})[0]
    with pytest.raises(ValueError, match='UNKNOWN_NUMBER'):
        _validate_draft({**plan, 'answer': '卡片尾号3083。'}, facts, {})
    with pytest.raises(ValueError, match='UNKNOWN_NUMBER'):
        _validate_draft({**plan, 'evidence_ids': [facts[0]['id']]}, facts, {})
    with pytest.raises(ValueError, match='UNANCHORED_MONEY'):
        _validate_draft({**plan, 'answer': '当前日额度为3082元。'}, facts, {})
