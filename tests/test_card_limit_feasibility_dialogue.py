"""Regression test for card reference resolution and daily limit feasibility inquiry."""
import types
from bank_planning.requests import normalize_goals, _observed_reference


def test_observed_reference_distinguishes_general_from_card():
    # general resource should never claim an observed database reference
    assert _observed_reference("这张卡", "general", act="EXPLAIN") is False
    assert _observed_reference("这个", "general", act="EXPLAIN") is False

    # cards resource correctly recognizes card references
    assert _observed_reference("这张卡", "cards", act="QUERY") is True
    assert _observed_reference("这卡", "cards", act="QUERY") is True
    assert _observed_reference("该卡片", "cards", act="QUERY") is True


def test_normalize_goals_general_explain_not_unclear_with_card_reference():
    # When user asks general feasibility mentioning "这张卡", it must remain EXPLAIN and bound
    text = "那这张卡如果设成10000元的日限额，是可行的吗？"
    raw_goals = [{"act": "EXPLAIN", "resource": "general", "question": "这张卡设成10000元日限额是否可行", "subject": "这张卡"}]
    goals = normalize_goals(raw_goals, text, {})
    assert len(goals) == 1
    assert goals[0]["act"] == "EXPLAIN"
    assert goals[0]["needs_subject"] is False


def test_dialogue_turn_card_limit_feasibility(tmp_path, monkeypatch):
    from scripts.evaluate_dialogue_tasks import isolated_settings
    settings = isolated_settings(tmp_path)
    for k, v in settings.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("BANK_EVAL_ISOLATED_PROCESS", "1")

    from scripts.dialogue_eval.bank_environment import BankEnvironment

    task = types.SimpleNamespace(id="card_limit_proposal_cancel", setup="default")
    env = BankEnvironment(task)
    try:
        # Turn 1: Inquire card limit rules
        resp1 = env.gateway.post(
            "/api/v1/agent/messages",
            json={"conversation_id": env.cid, "text": "我的卡片额度限制是怎么规定的？能设置每日消费上限吗？", "message_id": "m1"},
            headers=env.headers,
        )
        assert resp1.status_code == 200
        data1 = resp1.json()
        reply1 = data1.get("reply", "")
        assert "CARD-001" in reply1 or "日额度" in reply1

        # Turn 2: Ask about feasibility of 10000 limit with "这张卡"
        resp2 = env.gateway.post(
            "/api/v1/agent/messages",
            json={
                "conversation_id": env.cid,
                "text": "那这张卡如果设成10000元的日限额，是可行的吗？",
                "message_id": "m2",
            },
            headers=env.headers,
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        reply2 = data2.get("reply", "")
        # Must not complain about missing object/clarification
        assert "还需明确本轮所指对象" not in reply2
        assert "尚未查询或办理该项" not in reply2
        # Must explain feasibility conditions
        assert "可行" in reply2 or "最高" in reply2 or "10,000" in reply2 or "10000" in reply2
        assert data2.get("approval") is None
    finally:
        env.close()
