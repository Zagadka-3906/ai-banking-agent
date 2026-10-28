"""Missing columns must not erase verified rows or become a complete success."""
import pytest

from bank_agent.evidence import project_records
from bank_agent.evidence_session import EvidenceSession
from bank_agent.goal_answering import _fallback, _record_gap_feedback, _validate_coverage
from bank_planning.requests import normalize_goals
from test_agent_gateway import gateway, _send  # noqa: F401
from test_general_dialogue_loop import model, goal
from test_inquiry_boundaries import financial_state


def partial_session(resource='transactions', *, act='QUERY'):
    fields = ['amount', 'card_id'] if resource == 'transactions' else ['status', 'run_at']
    goals = normalize_goals([goal(resource, fields, '查询本人记录', act=act) | {'requires_record': True}], '查询本人记录', {})
    session = EvidenceSession({'user_id': 'user_zhang', 'parse_meta': {'goals': goals}})
    rows = [{'tx_id': 'TX-PARTIAL', 'amount': 28, 'merchant': '咖啡店'}] if resource == 'transactions' else [
        {'owner_id': 'user_zhang', 'task_id': 'TASK-PARTIAL', 'task_type': 'ORDER_DRAFT', 'status': 'PENDING'}]
    docs = project_records(resource, {'code': 200, 'data': rows}, session.state, fields=fields)
    session.documents = {row['id']: row for row in docs}
    session.completed = [{'tool': resource, 'fields': fields}]
    return goals, session


@pytest.mark.parametrize('resource,known,missing', [('transactions', '28.00', '卡片编号'), ('tasks', 'PENDING', '计划执行时间')])
def test_partial_fallback_preserves_verified_rows_and_names_the_gap(resource, known, missing):
    goals, session = partial_session(resource)
    text, coverage = _fallback(goals, session)
    assert known in text and missing in text and '没有所问字段或匹配记录' not in text
    assert coverage[0]['status'] == 'NEEDS_DATA' and coverage[0]['evidence_ids']
    with pytest.raises(ValueError, match='MISSING_GOAL_RECORD'):
        _validate_coverage([{**coverage[0], 'status': 'ANSWERED'}], goals, session.facts())


def test_gap_feedback_is_specific_and_does_not_reread_an_already_requested_column():
    goals, session = partial_session()
    feedback = _record_gap_feedback(goals, session)
    assert len(feedback) == 1 and feedback[0]['goal_id'] == 'q1'
    assert feedback[0]['action'] == 'REWRITE' and '卡片编号' in feedback[0]['reason']
    session.completed = [{'tool': 'transactions', 'fields': ['amount']}]
    assert _record_gap_feedback(goals, session)[0]['action'] == 'READ'


def test_partial_personal_explanation_keeps_its_records_alongside_rules():
    from banking_knowledge import knowledge_documents
    goals, session = partial_session(act='EXPLAIN')
    goals[0]['question'] = '怎么核对这笔交易是否本人消费'
    session.documents.update({row['id']: row for row in knowledge_documents('dispute')})
    text, coverage = _fallback(goals, session)
    assert '28.00' in text and '卡片编号' in text and '订单' in text
    assert coverage[0]['status'] == 'NEEDS_DATA'


def test_gateway_retains_real_transaction_details_when_a_draft_overclaims_missing_card_fields(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    model(monkeypatch, [goal('transactions', ['amount', 'merchant', 'time', 'card_id'], '查询本月交易明细')])
    before = financial_state()
    response = _send(client, '查询本月交易明细', conversation_id='partial-records')
    assert response['approval'] is None and financial_state() == before
    assert '28.00' in response['reply'] and '咖啡店' in response['reply'] and '卡片编号' in response['reply']
    assert response['business_status'] == 'NEEDS_INPUT'
    assert response['result']['answer_presentation']['coverage'][0]['status'] == 'NEEDS_DATA'
