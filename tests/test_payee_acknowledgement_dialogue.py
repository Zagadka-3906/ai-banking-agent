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


def test_payee_emphasis_adverb_extracts_clean_name():
    from bank_agent.parsing import local_parse
    text = "我想预约一笔转账，收款人就是李四，但日期和金额还没定，麻烦先帮我办预约转账。"
    names = recipient_references(text)
    assert names == {"李四"}
    assert ambiguous_target(text) is False
    assert target_from_text(text) == "李四"
    parsed = local_parse(text)
    assert parsed.get("target") == "李四"
    assert parsed.get("intent") == "SCHEDULED_TRANSFER"


def test_possessive_phrase_binds_to_allowed_payee(monkeypatch):
    from bank_agent.parsing import local_parse
    from bank_agent.entities import bind_payee
    from bank_agent import runtime
    import unittest.mock as mock

    text = "请帮我恢复之前那笔给李四的300元转账，金额就是300元，收款人是李四。"
    parsed = local_parse(text)
    # At local_parse stage, lexical candidate contains '李四' and '李四的'
    assert (parsed.get("slots") or {}).get("target_ambiguous") is True

    # At bind_payee stage with Core allow-list containing '李四', ambiguity is safely resolved
    resp = mock.MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"data": [{"display_name": "李四", "contact_id": "user_li"}]}
    with mock.patch.object(runtime, "_http", return_value=resp):
        bound = bind_payee({"user_id": "user_zhang"}, parsed, text)
    assert bound.get("target") == "李四"
    assert bound.get("amount") == 300.0
    assert not (bound.get("slots") or {}).get("target_ambiguous")


def test_real_target_ambiguity_retained():
    text = "给李四或者王美转30元"
    assert ambiguous_target(text) is True


def test_missing_amount_insert_rule_resume_produces_approval_card(tmp_path, monkeypatch):
    from scripts.evaluate_dialogue_tasks import isolated_settings
    settings = isolated_settings(tmp_path)
    for k, v in settings.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("BANK_EVAL_ISOLATED_PROCESS", "1")

    from scripts.dialogue_eval.bank_environment import BankEnvironment

    task = types.SimpleNamespace(id="transfer_missing_amount_insert_rule_reject", setup="default")
    env = BankEnvironment(task)
    try:
        # Turn 1:
        resp1 = env.gateway.post(
            "/api/v1/agent/messages",
            json={"conversation_id": env.cid, "text": "我想给李四转一笔钱，不过金额还没定，先帮我看看怎么发起转账。", "message_id": "m1"},
            headers=env.headers,
        )
        assert resp1.status_code == 200
        assert resp1.json().get("approval") is None

        # Turn 2:
        resp2 = env.gateway.post(
            "/api/v1/agent/messages",
            json={"conversation_id": env.cid, "text": "这笔转账的到账时间是怎么规定的？", "message_id": "m2"},
            headers=env.headers,
        )
        assert resp2.status_code == 200
        assert resp2.json().get("approval") is None

        # Turn 3:
        resp3 = env.gateway.post(
            "/api/v1/agent/messages",
            json={"conversation_id": env.cid, "text": "请帮我恢复之前那笔给李四的300元转账，金额就是300元，收款人是李四。", "message_id": "m3"},
            headers=env.headers,
        )
        assert resp3.status_code == 200
        data3 = resp3.json()
        assert data3.get("approval") is not None
        summary = data3["approval"].get("summary", "")
        assert "李四" in summary and "300" in summary
    finally:
        env.close()


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
