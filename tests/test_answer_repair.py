import pytest

from bank_agent.answer_repair import normalize_feedback, validation_feedback
from bank_agent.evidence_session import EvidenceSession
from bank_planning.requests import normalize_goals
from test_agent_gateway import gateway, _send  # noqa: F401
from test_general_dialogue_loop import goal, model


def goals():
    return normalize_goals([goal('cards',['status'],'查询卡片状态')],'查询卡片状态',{})


def test_formatting_feedback_does_not_spend_read_or_planning_budget(monkeypatch):
    session=EvidenceSession({'user_id':'user_zhang','parse_meta':{'goals':goals()}})
    monkeypatch.setattr(session,'plan',lambda *args: pytest.fail('Formatting repair attempted a data read'))
    assert session.repair(validation_feedback('UNKNOWN_NUMBER',session.goals)) is False
    assert session.completed==[] and session.rounds==0


@pytest.mark.parametrize('value',[
    [{'goal_id':'old','reason':'不属于本轮'}], [{'reason':'没有对应目标'}],
    [{'goal_id':'q1','reason':'伪造操作','action':'EXECUTE'}],
])
def test_reviewer_feedback_cannot_target_an_old_goal_or_request_an_execution(value):
    with pytest.raises(ValueError):
        normalize_feedback(value,goals())


def test_review_diagnosis_survives_into_the_next_draft(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    seen=[]
    def draft(payload):
        seen.append(payload)
        row=next(row for row in payload['facts'] if row['id'].startswith('read.cards.') and 'status' in row.get('fields',[]))
        return {'answer':'卡片状态：'+row['text'],'evidence_ids':[row['id']],'unanswered':[],
                'coverage':[{'goal_id':'q1','status':'ANSWERED','evidence_ids':[row['id']],'reason':'卡片状态'}]}
    reviews=[]
    def review(payload):
        reviews.append(payload)
        return {'in_scope':True,'grounded':True,'answers_question':len(reviews)>1,'safe':True,'coverage':payload['coverage'],
                'feedback':[] if len(reviews)>1 else [{'goal_id':'q1','action':'REWRITE','reason':'请明确本次卡片的登记状态，不要重复入口说明。'}]}
    model(monkeypatch,[goal('cards',['status'],'卡片状态')],draft=draft,review=review)
    reply=_send(client,'查询银行卡状态',conversation_id='preserve-repair-diagnosis')
    assert reply['business_status']=='SUCCEEDED' and len(seen)==2
    assert seen[1]['feedback'][0]['reason']=='请明确本次卡片的登记状态，不要重复入口说明。'
    assert seen[1]['feedback'][0]['action']=='REWRITE'
