"""Regression tests for payee acknowledgement isolation and scheduled transfer clarification."""
import types
from intent_language import ambiguous_target, recipient_references
from bank_agent.extraction import target_from_text


def test_payee_acknowledgement_does_not_corrupt_subsequent_payee_designation():
    text = "给李四的知道了。那安排一笔预约转账，收款人给李四，日期和金额都还没定；先帮我登记这笔预约转账，缺的日期和金额我后续再补齐。"
    names = recipient_references(text)
    assert names == {"李四"}
    assert ambiguous_target(text) is False
    assert target_from_text(text) == "李四"


def test_payee_acknowledgement_with_different_subsequent_payee():
    text = "给李四的知道了。那安排一笔预约转账，收款人给王五，日期和金额都还没定。"
    names = recipient_references(text)
    assert names == {"王五"}
    assert ambiguous_target(text) is False
    assert target_from_text(text) == "王五"


def test_dialogue_turn_scheduled_transfer_clarifies_missing_date_and_amount_not_payee(tmp_path, monkeypatch):
    from scripts.evaluate_dialogue_tasks import isolated_settings
    settings = isolated_settings(tmp_path)
    for k, v in settings.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("BANK_EVAL_ISOLATED_PROCESS", "1")

    from scripts.dialogue_eval.bank_environment import BankEnvironment

    task = types.SimpleNamespace(id="contacts_check_and_scheduled_transfer_cancel", setup="default")
    env = BankEnvironment(task)
    try:
        # Turn 1: check contacts
        resp1 = env.gateway.post(
            "/api/v1/agent/messages",
            json={"conversation_id": env.cid, "text": "帮我确认一下联系人里有没有李四？怎么确认？", "message_id": "m1"},
            headers=env.headers,
        )
        assert resp1.status_code == 200

        # Turn 2: acknowledge contacts and initiate scheduled transfer with missing date/amount
        resp2 = env.gateway.post(
            "/api/v1/agent/messages",
            json={
                "conversation_id": env.cid,
                "text": "给李四的知道了。那安排一笔预约转账，收款人给李四，日期和金额都还没定；先帮我登记这笔预约转账，缺的日期和金额我后续再补齐。",
                "message_id": "m2",
            },
            headers=env.headers,
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        reply2 = data2.get("reply", "")
        # Must not complain about payee ambiguity
        assert "只选一位" not in reply2
        assert "收款人或业务对象" not in reply2
        # Must clarify missing amount and/or date
        assert "金额" in reply2
        assert "日期" in reply2 or "时间" in reply2

        # Turn 3: cancel scheduled transfer
        resp3 = env.gateway.post(
            "/api/v1/agent/messages",
            json={"conversation_id": env.cid, "text": "取消预约转账，不预约转账了。", "message_id": "m3"},
            headers=env.headers,
        )
        assert resp3.status_code == 200
        data3 = resp3.json()
        assert data3.get("approval") is None
    finally:
        env.close()
