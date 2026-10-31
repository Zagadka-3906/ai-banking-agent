"""Regression tests for goals normalization and prior inquiry handling."""
import pytest
from bank_planning.requests import normalize_goals, read_only_goals


def test_normalize_goals_strips_slot_reply_background_when_inquiry_present():
    raw = [
        {"act": "SLOT_REPLY", "resource": "general", "question": "给李四的转账金额未定，先记着转账", "fields": [], "subject": "李四", "topic": "transfer"},
        {"act": "EXPLAIN", "resource": "general", "question": "转账一般多久能到账", "fields": [], "subject": None, "topic": "transfer"}
    ]
    text = "给李四的这笔转账我想先记着，金额还没定；另外想问一下，转账一般多久能到账？"
    normalized = normalize_goals(raw, text, {})
    assert len(normalized) == 1
    assert normalized[0]["act"] == "EXPLAIN"
    assert normalized[0]["question"] == "转账一般多久能到账"
    assert read_only_goals(normalized) is True


def test_normalize_goals_still_rejects_solo_invalid_act():
    raw = [
        {"act": "SLOT_REPLY", "resource": "general", "question": "先记着转账", "fields": [], "subject": "李四", "topic": "transfer"}
    ]
    text = "先记着转账"
    with pytest.raises(ValueError):
        normalize_goals(raw, text, {})


def test_dialogue_turn_handles_uncommitted_transfer_with_timing_inquiry(tmp_path, monkeypatch):
    from scripts.evaluate_dialogue_tasks import isolated_settings
    settings = isolated_settings(tmp_path)
    for k, v in settings.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("BANK_EVAL_ISOLATED_PROCESS", "1")

    import types
    from scripts.dialogue_eval.bank_environment import BankEnvironment

    task = types.SimpleNamespace(id="prior_inquiry_timing_test", setup="default")
    env = BankEnvironment(task)
    try:
        # Turn 1: user initiates transfer without amount
        resp1 = env.gateway.post(
            "/api/v1/agent/messages",
            json={"conversation_id": env.cid, "text": "我想给李四转一笔钱，金额还没定，先把这笔转账记下来。", "message_id": "m1"},
            headers=env.headers,
        )
        assert resp1.status_code == 200

        # Turn 2: user asks timing question while keeping background transfer uncommitted
        resp2 = env.gateway.post(
            "/api/v1/agent/messages",
            json={"conversation_id": env.cid, "text": "给李四的这笔转账我想先记着，金额还没定；另外想问一下，转账一般多久能到账？", "message_id": "m2"},
            headers=env.headers,
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        reply2 = data2.get("reply", "")
        assert "未形成有效的查询计划" not in reply2
        assert "时效" in reply2 or "到账" in reply2 or "虚构" in reply2 or "承诺" in reply2 or "多久" in reply2

        # Turn 3: resume transfer with amount and verify proposal card
        resp3 = env.gateway.post(
            "/api/v1/agent/messages",
            json={"conversation_id": env.cid, "text": "那继续之前的给李四那笔转账，金额定为300元，就按300元转。", "message_id": "m3"},
            headers=env.headers,
        )
        assert resp3.status_code == 200
        data3 = resp3.json()
        assert data3.get("approval") is not None
        assert "李四" in data3["approval"]["summary"]
        assert "300" in data3["approval"]["summary"]
    finally:
        env.close()
