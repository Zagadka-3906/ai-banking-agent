"""Contact and subscription questions require the right records and a focused answer."""
from copy import deepcopy
from datetime import date
import json
from types import SimpleNamespace

import pytest

import main
from bank_agent import runtime
from bank_agent.answering import _validate_draft, compose_answer
from bank_agent.parsing import local_parse
from bank_agent.query_focus import charge_description, contact_documents, contact_question
from bank_agent.understanding import enforce_inquiry
from test_agent_gateway import _headers, _send, gateway  # noqa: F401
from test_inquiry_boundaries import financial_state


def seed_book():
    main.USER_CONTACTS['user_zhang'] = [{"contact_id": "CTT-TEST", "name": "陈测试", "phone": "13900000001",
                                        "relationship": "FRIEND", "birthday": None, "account_id": "user_li",
                                        "linked_existing": True, "created_at": "2026-10-17T12:00:00+08:00"}]


def confirm_query(client, text, cid="subscription-focus"):
    response = _send(client, text, conversation_id=cid)
    assert response['approval'] and response['approval']['risk_level'] == 'YELLOW', response
    assert '订阅' in response['approval']['summary']
    return client.post('/api/v1/agent/approvals/' + response['approval']['approval_id'],
                       json={'decision': 'approve'}, headers=_headers()).json()


@pytest.mark.parametrize('text', ['陈测试是常用收款人吗', '怎么确认陈测试是否已保存', '我保存了哪些联系人'])
def test_contact_questions_read_the_book_even_with_a_wrong_bill_classifier(gateway, monkeypatch, text):  # noqa: F811
    client, _ = gateway
    seed_book()
    before = financial_state()
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=lambda messages: SimpleNamespace(content=json.dumps({
        'intent': 'BILL_QUERY', 'target': '陈测试', 'dialogue_act': 'QUERY', 'scope': 'IN_SCOPE',
        'slots': {'help_topic': 'transfer'}, 'confidence': .99}))))
    response = _send(client, text, conversation_id='book-grounding')
    assert response['approval'] is None and '陈测试' in response['reply'] and '已保存' in response['reply']
    assert '侧边栏' in response['reply'] and '📋 共' not in response['reply']
    assert '13900000001' not in response['reply'] and '15 笔' not in response['reply']
    if text != '我保存了哪些联系人':
        assert response['reply'].count('陈测试') == 1
    documents = response['result']['execution_result']['data']['documents']
    assert any(item['id'].startswith('read.contacts.') for item in documents)
    assert financial_state() == before


def test_contact_entry_is_answered_without_unrelated_transfer_rules(gateway):  # noqa: F811
    client, _ = gateway
    response = _send(client, '怎么查看常用收款人列表', conversation_id='book-entry')
    assert '侧边栏 → 联系人' in response['reply']
    assert all(word not in response['reply'] for word in ('1000', '已结算转账', '验证码'))


def test_contact_book_read_failure_cannot_be_reviewed_as_a_success(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    seed_book()
    original = runtime._http

    def transport(method, path, **kwargs):
        if path.endswith('/book'):
            raise RuntimeError('unavailable')
        return original(method, path, **kwargs)

    monkeypatch.setattr(runtime, '_http', transport)
    response = _send(client, '陈测试是常用收款人吗', conversation_id='book-unavailable')
    assert response['business_status'] == 'FAILED' and response['approval'] is None
    assert '不能' in response['reply'] or '尚不能' in response['reply']


def test_wrong_record_family_is_rejected_before_the_semantic_reviewer(monkeypatch):
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=lambda messages: pytest.fail('wrong evidence reached model')))
    response = compose_answer({'user_id': 'user_zhang', 'intent': 'BILL_QUERY', 'user_input': '陈测试是常用收款人吗',
                               'execution_result': {'type': 'BILL_QUERY', 'data': {}, 'records': []}},
                              {'final_response': '他有一笔转账，无法确认是否保存。'})
    assert response['business_status'] == 'FAILED' and '不能根据流水' in response['final_response']


def test_a_failed_contact_receipt_is_not_resurrected_by_the_direct_answer():
    from bank_agent.nodes.responder import responder_node
    from banking_knowledge import KNOWLEDGE_VERSION

    forged = {'id': 'read.contacts.0', 'source': 'wrong://contacts', 'text': '陈测试已保存', 'required': True}
    response = responder_node({'user_id': 'user_zhang', 'intent': 'BANKING_HELP', 'user_input': '陈测试是否已保存',
                               'execution_result': {'type': 'BANKING_HELP', 'data': {'code': 200, 'version': 1,
                                'knowledge_version': KNOWLEDGE_VERSION, 'topic': 'contacts',
                                'facts': [forged['text']], 'documents': [forged]}}})
    assert response['business_status'] == 'FAILED' and '陈测试已保存' not in response['final_response']


@pytest.mark.parametrize('text', ['视频会员下次什么时候扣款', '那视频会员现在还是生效中吗', '视频会员每期扣多少钱'])
def test_understood_subscription_queries_survive_generic_inquiry_rules(text):
    result = enforce_inquiry({'intent': 'SUBSCRIPTION_QUERY', 'target': '视频会员',
                              'parse_meta': {'dialogue_act': 'QUERY', 'scope': 'IN_SCOPE'}}, text)
    assert result['intent'] == 'SUBSCRIPTION_QUERY' and result['target'] == '视频会员'
    assert not (local_parse(text).get('slots') or {}).get('target_ambiguous')


@pytest.mark.parametrize('text', ['给李四还是王美转100元', '李四还是王美'])
def test_real_alternative_payees_remain_ambiguous(text):
    assert (local_parse(text).get('slots') or {}).get('target_ambiguous')


@pytest.mark.parametrize('text,expected,forbidden', [
    ('视频会员每期扣多少钱', '¥25.00', ['Apple', '¥15.00', '下次扣款', '取消']),
    ('那视频会员现在还是生效中吗', '生效中', ['Apple', '收款人', '取消 Apple']),
    ('视频会员下次什么时候扣款', '已过期', ['Apple', '下次扣款 2026-10-25', '2026-11-25']),
])
def test_confirmed_subscription_answers_focus_on_the_named_object_and_field(gateway, monkeypatch, text, expected, forbidden):  # noqa: F811
    client, _ = gateway
    monkeypatch.setattr('bank_agent.query_focus.banking_today', lambda: date(2026, 10, 26))
    main.SUBSCRIPTIONS['user_zhang'][1]['next_charge'] = '2026-10-25'
    # The actual understanding model may identify the merchant while the local
    # parser only knows the inquiry act; financial facts still come from Core.
    original_model = runtime.llm
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=lambda messages: SimpleNamespace(content=json.dumps({
        'intent': 'SUBSCRIPTION_QUERY', 'target': '视频会员', 'dialogue_act': 'QUERY', 'scope': 'IN_SCOPE', 'confidence': .99}))))
    before = deepcopy((main.ACCOUNTS, main.SUBSCRIPTIONS, main.TRANSACTIONS))
    response = confirm_query(client, text)
    assert expected in response['reply'] and all(value not in response['reply'] for value in forbidden), response
    assert '管理页面' not in response['reply'] and '管理页' not in response['reply']
    rows = response['result']['execution_result']['data']['data']
    assert len(rows) == 1 and rows[0]['subscription_id'] == 'SUB-VIDEO'
    assert before == (main.ACCOUNTS, main.SUBSCRIPTIONS, main.TRANSACTIONS)
    monkeypatch.setattr(runtime, 'llm', original_model)


def test_subscription_list_marks_old_dates_without_rolling_or_cancelling(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    monkeypatch.setattr('bank_agent.query_focus.banking_today', lambda: date(2026, 10, 26))
    for row, value in zip(main.SUBSCRIPTIONS['user_zhang'], ('2026-10-03', '2026-10-25')):
        row['next_charge'] = value
    before = deepcopy(main.SUBSCRIPTIONS)
    response = confirm_query(client, '我目前订阅了哪些会员')
    assert response['reply'].count('已过期') == 2
    assert '取消 Apple' not in response['reply'] and '下次扣款 2026-10' not in response['reply']
    assert main.SUBSCRIPTIONS == before


def test_subscription_failure_rule_does_not_push_cancellation(gateway):  # noqa: F811
    client, _ = gateway
    response = _send(client, '视频会员扣款失败会怎样', conversation_id='failure-policy')
    assert response['approval'] is None and '失败回调' in response['reply'] and '自动重试' in response['reply']
    assert '是否取消' not in response['reply']


def test_contact_read_projection_is_owner_scoped_and_does_not_assume_a_common_flag():
    assert not contact_question('查询给联系人转账的流水')
    documents = contact_documents({'code': 200, 'data': []}, {'user_input': '陈测试是常用收款人吗'})
    assert '未找到' in documents[0]['text']
    assert '频繁' not in documents[0]['text']


@pytest.mark.parametrize('value,expected', [('invalid', '无效'), ('2026-10-25', '已过期'), ('2026-10-27', '登记的下次扣款')])
def test_dates_are_described_as_records_not_settlement_proofs(value, expected):
    assert expected in charge_description({'next_charge': value, 'status': 'ACTIVE'}, date(2026, 10, 26))


def test_a_supported_but_expired_date_cannot_be_relabelled_as_the_next_charge():
    facts = [{'id': 'subscription.date', 'text': '原记录计划扣款2026-10-25（已过期；尚未更新）。'}]
    with pytest.raises(ValueError, match='STALE_SCHEDULE_CLAIM'):
        _validate_draft({'answer': '下次扣款日期是2026-10-25。', 'evidence_ids': ['subscription.date'], 'unanswered': []}, facts, {})


def test_the_same_conversation_id_cannot_read_another_principals_saved_contacts(gateway):  # noqa: F811
    client, _ = gateway
    seed_book()
    response = _send(client, '陈测试是常用收款人吗', conversation_id='book-owner', principal='user_li')
    assert '未找到' in response['reply'] and '陈测试已保存' not in response['reply']


def test_original_ten_turn_sequence_keeps_data_domains_targets_and_permissions_separate(gateway, monkeypatch):  # noqa: F811
    client, core = gateway
    created = core.post('/api/v1/contacts/user_zhang/book', headers=_headers(),
                        json={'name': '陈测试', 'phone': '13900000001', 'relationship': 'FRIEND'})
    assert created.status_code == 200
    receiver = created.json()['data']['account_id']
    monkeypatch.setattr('bank_agent.query_focus.banking_today', lambda: date(2026, 10, 26))
    for row, value in zip(main.SUBSCRIPTIONS['user_zhang'], ('2026-10-03', '2026-10-25')):
        row['next_charge'] = value
    before_transfer = main.ACCOUNTS['user_zhang']['balance']
    cases = [('查一下余额', 'BALANCE_QUERY', None), ('给陈测试转1000', 'TRANSFER', '陈测试'),
             ('陈测试是常用收款人吗', 'BILL_QUERY', '陈测试'), ('怎么查看常用收款人列表', 'BANKING_HELP', None),
             ('怎么确认陈测试是否已保存', 'BILL_QUERY', '陈测试'), ('我目前订阅了哪些会员', 'SUBSCRIPTION_QUERY', None),
             ('视频会员下次什么时候扣款', 'SUBSCRIPTION_QUERY', '视频会员'), ('视频会员每期扣多少钱', 'SUBSCRIPTION_QUERY', '视频会员'),
             ('视频会员扣款失败会怎样', 'BANKING_HELP', '视频会员'), ('那视频会员现在还是生效中吗', 'SUBSCRIPTION_QUERY', '视频会员')]
    answers = []
    for index, (text, intent, target) in enumerate(cases):
        payload = {'intent': intent, 'target': target, 'confidence': .99, 'scope': 'IN_SCOPE',
                   'dialogue_act': 'ACTION' if index == 1 else 'QUERY', 'topic_relation': 'CONTINUE'}
        monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=lambda messages, value=payload: SimpleNamespace(content=json.dumps(value))))
        before = deepcopy((main.ACCOUNTS, main.CARDS, main.SUBSCRIPTIONS, main.TRANSACTIONS))
        reply = _send(client, text, conversation_id='ten-turn-domain-regression')
        if index in (1, 5, 6, 7, 9):
            assert reply['approval'] and reply['approval']['risk_level'] == 'YELLOW'
            reply = client.post('/api/v1/agent/approvals/' + reply['approval']['approval_id'],
                                headers=_headers(), json={'decision': 'approve'}).json()
        else:
            assert reply['approval'] is None
        assert reply['business_status'] == 'SUCCEEDED', reply
        answers.append(reply['reply'])
        if index != 1:
            assert before == (main.ACCOUNTS, main.CARDS, main.SUBSCRIPTIONS, main.TRANSACTIONS)
    assert main.ACCOUNTS['user_zhang']['balance'] == before_transfer - 1000
    assert main.ACCOUNTS[receiver]['balance'] == 1000
    assert '已保存' in answers[2] and '侧边栏 → 联系人' in answers[3] and '已保存' in answers[4]
    assert '已过期' in answers[6] and 'Apple' not in answers[6]
    assert '¥25.00' in answers[7] and 'Apple' not in answers[7]
    assert '生效中' in answers[9] and '收款人' not in answers[9]
    assert all('取消 Apple' not in value and '订阅管理页' not in value for value in answers)
