"""Human-review regressions: readable states without duplicate mandatory facts."""
import pytest

from bank_agent.answer_presentation import customer_language, required_fact_present
from bank_agent.answering import _validate_draft
from test_agent_gateway import _send, gateway  # noqa: F401
from test_general_dialogue_loop import goal, model
from test_inquiry_boundaries import financial_state


def test_known_workflow_codes_are_readable_without_claiming_a_payment_succeeded():
    text = '预约转账（SCHEDULED_TRANSFER）状态为PENDING；WAITING_USER；RUNNING；SUCCEEDED；FAILED；HUMAN_TAKEOVER。'
    result = customer_language(text, [])
    assert result == '预约转账状态为等待处理；等待你确认；正在处理；处理完成；处理失败；需要人工处理。'
    assert '已付款' not in result
    assert customer_language('FROZEN（冻结）、ACTIVE（正常）、LOST、CLOSED、CANCELLED。', []) == '冻结、正常、挂失、已关闭、已取消。'


def test_translation_preserves_unknown_states_identifiers_and_verified_merchant_names():
    facts = [{'subject': 'ACTIVE Coffee', 'entity_names': ['PENDING'], 'entity_id': 'TSK-PENDING-001'}]
    text = 'ACTIVE Coffee、PENDING、TSK-PENDING-001、UNKNOWN_STATE；状态WAITING_USER。'
    assert customer_language(text, facts) == 'ACTIVE Coffee、PENDING、TSK-PENDING-001、UNKNOWN_STATE；状态等待你确认。'


@pytest.mark.parametrize('answer', [
    '你本人的联系人簿目前共保存0位联系人，因此没有可列出的联系人姓名。',
    '本次读取显示，你的联系人簿当前共保存 0 位联系人。',
])
def test_equivalent_verified_contact_count_is_not_appended_again(answer):
    fact = {'id': 'read.contacts.0', 'source': 'core://contacts',
            'text': '本人的联系人簿共保存0位联系人。', 'required': True}
    result, _ = _validate_draft({'answer': answer, 'evidence_ids': [fact['id']], 'unanswered': []}, [fact], {})
    assert result == answer


@pytest.mark.parametrize('answer', [
    '你的联系人簿当前共保存 1 位联系人。',
    '他的联系人簿当前共保存 0 位联系人。',
    '假如你的联系人簿当前共保存 0 位联系人。',
    '你的联系人簿当前共保存 0 位联系人吗？',
    '你的联系人簿并非共保存 0 位联系人。',
    '你的联系人簿当前共保存 0 位联系人，但这只是猜测。',
])
def test_wrong_subject_count_or_nonassertion_cannot_replace_a_required_fact(answer):
    fact = {'source': 'core://contacts', 'text': '本人的联系人簿共保存0位联系人。'}
    assert not required_fact_present(answer, fact)


def test_equivalent_product_disclaimer_is_shown_once_but_other_limitations_remain():
    disclaimer = {'id': 'read.wealth_products.1', 'source': 'core://wealth_products',
                  'text': '以上为演示产品信息，不是未来收益预测或收益保证。', 'required': True}
    limit = {'id': 'read.wealth_products.2', 'source': 'core://wealth_products',
             'text': '当前证据仅展示部分记录，不能称为完整列表。', 'required': True}
    answer = '以上是本次产品货架中展示的公开产品信息，不是未来收益预测或收益保证。'
    result, _ = _validate_draft({'answer': answer, 'evidence_ids': [disclaimer['id']], 'unanswered': []}, [disclaimer, limit], {})
    assert result.count('不是未来收益预测或收益保证') == 1
    assert disclaimer['text'] in result
    assert limit['text'] in result
    assert not required_fact_present('以上产品提供收益保证。', disclaimer)


def test_normalizing_notices_preserves_required_money_and_does_not_drop_new_caveats():
    from bank_agent.answer_presentation import append_required_fact
    fact = {'source': 'core://wealth_products', 'text': '以上为演示产品信息，不是未来收益预测或收益保证。'}
    text = ('起购金额为¥1,000.00。以上为演示产品货架信息，不是未来收益预测或收益保证。\n'
            '以上为演示产品信息，不是未来收益预测或收益保证。')
    result = append_required_fact(text, fact)
    assert result == '起购金额为¥1,000.00。' + fact['text']
    other = '以上产品仍处于封闭期，不是未来收益预测或收益保证。'
    assert append_required_fact(other, fact) == other + '\n' + fact['text']


@pytest.mark.parametrize('notice', [
    '以上是本系统的订阅登记状态和原计划日期，不能证明真实会员权益、扣款结果或商户是否重试。',
    '以上为本系统订阅登记状态与原计划日期，不能证明真实会员权益、扣款结果或商户重试。',
])
def test_subscription_notice_is_canonicalized_once_without_losing_other_details(notice):
    from bank_agent.answer_presentation import append_required_fact
    fact = {'source': 'core://subscriptions',
            'text': '以上为本系统订阅登记状态与原计划日期，不能证明真实会员权益、扣款结果或商户重试。'}
    details = 'Apple订阅每期¥15.00，原计划2026-10-03，已过期，无法确认下次扣款时间。'
    result = append_required_fact(details + notice + '\n' + fact['text'], fact)
    assert result == details + fact['text']
    assert append_required_fact(result, fact) == result
    different = '以上订阅日期已过期，不能证明扣款失败；需向商户核实退款。'
    assert append_required_fact(different, fact) == different + '\n' + fact['text']


def test_customer_text_is_translated_before_review_and_evidence_keeps_original_codes(gateway, monkeypatch):  # noqa: F811
    from scheduler import TASK_STORE
    client, _ = gateway
    task = TASK_STORE.create('user_zhang', 'SCHEDULED_TRANSFER', {'amount': 40})
    reviewed = []
    def draft(payload):
        row = next(row for row in payload['facts'] if row.get('entity_id') == task.task_id)
        return {'answer': row['text'], 'evidence_ids': [row['id']], 'unanswered': [], 'coverage': [
            {'goal_id': 'q1', 'status': 'ANSWERED', 'evidence_ids': [row['id']], 'reason': '本人任务记录'}]}
    def review(payload):
        reviewed.append(payload)
        assert 'PENDING' not in payload['candidate'] and 'SCHEDULED_TRANSFER' not in payload['candidate']
        row = next(row for row in payload['evidence'] if row.get('entity_id') == task.task_id)
        assert 'PENDING' in row['text'] and 'SCHEDULED_TRANSFER' in row['text']
        return dict(in_scope=True, grounded=True, answers_question=True, safe=True,
                    coverage=payload['coverage'], feedback=[])
    model(monkeypatch, [goal('tasks', ['status'], '查询本人待办状态')], draft=draft, review=review)
    before = financial_state()
    reply = _send(client, '查一下我的待办状态', conversation_id='human-review-readable-states')
    assert reviewed and reply['result']['answer_presentation']['mode'] == 'GENERATED_REVIEWED'
    assert '等待处理' in reply['reply'] and '预约转账' in reply['reply']
    assert reply['approval'] is None and financial_state() == before
