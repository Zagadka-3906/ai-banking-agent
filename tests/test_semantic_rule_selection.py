"""Policy lookup can recover from a wrong topic using a closed rule directory."""
import pytest

from bank_agent import runtime
from bank_agent.consultation import valid_read_step, read_evidence
from banking_rules import rule_index, rule_documents
from test_agent_gateway import gateway, _send  # noqa: F401
from test_general_dialogue_loop import model, goal
from test_inquiry_boundaries import financial_state


def test_directory_is_complete_but_contains_only_public_identifiers_and_titles():
    index = rule_index()
    assert {row['id'] for row in index} == {row['id'] for row in rule_documents()}
    assert all(set(row) == {'id', 'topic', 'title'} for row in index)


@pytest.mark.parametrize('step', [
    {'tool': 'rules', 'rule_ids': ['guide.dispute.timing'], 'user_id': 'user_li'},
    {'tool': 'rules', 'rule_ids': ['unknown']}, {'tool': 'rules', 'rule_ids': [True]},
    {'tool': 'rules', 'rule_ids': ['guide.dispute.timing'] * 2},
    {'tool': 'rules', 'rule_ids': []}, {'tool': 'rules', 'rule_ids': ['guide.dispute.timing'], 'query': ''},
    {'tool': 'rules', 'rule_ids': ['guide.dispute.timing'], 'url': 'https://invalid'},
])
def test_selector_cannot_add_identity_or_arbitrary_transport(step):
    assert not valid_read_step(step)


def test_explicit_selection_is_verified_and_sent_as_ids_only(gateway, monkeypatch):  # noqa: F811
    original = runtime._http
    calls = []
    def track(method, path, **kwargs):
        calls.append((method, path))
        return original(method, path, **kwargs)
    monkeypatch.setattr(runtime, '_http', track)
    rows = read_evidence({'user_id': 'user_zhang'}, {'tool': 'rules', 'rule_ids': ['guide.dispute.timing']})
    assert len(rows) == 1 and rows[0]['id'] == 'guide.dispute.timing' and not rows[0]['context_only']
    assert calls == [('GET', 'service-guide?rule_ids=guide.dispute.timing')]


def test_planner_can_recover_missing_policy_without_same_words_or_right_topic(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    requested = []
    def planner(payload):
        assert any(row['id'] == 'guide.dispute.timing' for row in payload['rule_index'])
        requested.append(True)
        return {'reads': [{'tool': 'rules', 'rule_ids': ['guide.dispute.timing']}]}
    def draft(payload):
        selected = next(row for row in payload['facts'] if row['id'] == 'guide.dispute.timing')
        return {'answer': selected['text'], 'evidence_ids': [selected['id']], 'unanswered': [],
            'coverage': [{'goal_id': 'q1', 'status': 'ANSWERED', 'evidence_ids': [selected['id']], 'reason': '已读取时效规则'}]}
    model(monkeypatch, [goal('general', [], '这笔异常消费核查何时有处理结果', act='EXPLAIN')], planner=planner, draft=draft)
    before = financial_state()
    reply = _send(client, '这笔异常消费核查何时有处理结果', conversation_id='semantic-policy')
    assert requested and reply['business_status'] == 'SUCCEEDED'
    assert '没有真实客服审核队列' in reply['reply'] and reply['approval'] is None
    assert financial_state() == before
