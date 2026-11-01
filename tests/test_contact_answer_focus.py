"""Contact answers report the asked fact once, without unsolicited frequency rules."""
import pytest

from bank_agent.answer_presentation import question_facts, validate_answer_focus
from banking_knowledge import knowledge_documents
from test_agent_gateway import _send, gateway  # noqa: F401
from test_general_dialogue_loop import goal, model
from test_inquiry_boundaries import financial_state


@pytest.mark.parametrize('question', ['联系人从哪里打开？', '通讯录里有没有赵星辰？', '联系人支持按姓名搜索吗？'])
def test_frequency_policy_is_not_part_of_an_unrelated_contact_answer(question):
    facts = question_facts(knowledge_documents('contacts'), question)
    assert 'guide.contacts.common_flag' not in {row['id'] for row in facts}
    assert all('常用频率' not in row['text'] for row in facts)
    with pytest.raises(ValueError, match='UNREQUESTED_CONTACT_FREQUENCY'):
        validate_answer_focus('可以搜索。本产品没有单独的常用频率标记。', question)


def test_an_explicit_frequency_question_can_still_be_answered():
    facts = knowledge_documents('contacts')
    assert question_facts(facts, '经常转账和已保存是一个意思吗？') == facts
    validate_answer_focus('已保存和常用频率不同。', '经常转账和已保存是一个意思吗？')


def test_targeted_absence_is_not_duplicated_and_irrelevant_rules_rewrite_without_more_reads(gateway, monkeypatch):  # noqa: F811
    from bank_agent import runtime
    client, _ = gateway
    reads, drafts, reviews = [], [], []
    original = runtime._http
    def http(method, path, **kwargs):
        reads.append((method, path))
        return original(method, path, **kwargs)
    monkeypatch.setattr(runtime, '_http', http)
    def draft(payload):
        drafts.append(len(reads))
        assert all(row['id'] != 'guide.contacts.common_flag' for row in payload['facts'])
        row = next(row for row in payload['facts'] if row['id'].startswith('read.contacts.'))
        assert not row['required']
        answer = '联系人里没有赵星辰。' + ('本产品没有单独的常用频率标记。' if len(drafts) == 1 else '')
        return {'answer':answer,'evidence_ids':[row['id']],'unanswered':[], 'coverage':[
            {'goal_id':'q1','status':'ANSWERED','evidence_ids':[row['id']],'reason':'当前联系人簿未匹配'}]}
    def review(payload):
        reviews.append(payload['candidate'])
        return dict(in_scope=True,grounded=True,answers_question=True,safe=True,coverage=payload['coverage'],feedback=[])
    model(monkeypatch, [goal('contacts',['saved'],'赵星辰是否保存',subject='赵星辰')], draft=draft, review=review)
    before = financial_state()
    reply = _send(client, '联系人簿里有没有赵星辰？', conversation_id='contact-no-unsolicited-frequency')
    assert reply['reply'] == '联系人里没有赵星辰。' and reviews == [reply['reply']]
    assert len(drafts) == 2 and drafts[0] == drafts[1]
    assert reply['approval'] is None and financial_state() == before
