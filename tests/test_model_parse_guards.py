"""Model extraction may classify language, but cannot invent payment facts."""

import json
import os
from types import SimpleNamespace

import pytest
import requests

os.environ["BANK_CONVERSATION_DB"] = "memory"
pytest.importorskip("langgraph")

import agent
from planner import missing_slots, validate_intent_payload


@pytest.fixture
def model(monkeypatch):
    """Exercise the real merge with an intentionally unreliable offline LLM."""
    monkeypatch.setenv("BANK_MODEL_FAST_PATH", "0")
    def no_network(*args, **kwargs):
        raise AssertionError("Model-merge tests must never contact a service")

    monkeypatch.setattr(requests.sessions.Session, "request", no_network)

    def parse(text, **overrides):
        payload = {"intent": "TRANSFER", "confidence": 0.99, **overrides}
        calls = []

        def invoke(messages):
            calls.append(messages)
            return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))

        monkeypatch.setattr(agent, "llm", SimpleNamespace(invoke=invoke))
        parsed = validate_intent_payload(agent._model_parse(text))
        assert len(calls) == 1, "The model should still participate in extraction"
        return parsed

    return parse


def test_explicit_payment_facts_and_original_reason_outrank_model(model):
    text = "给李四转账 12.34 元,备注午餐"
    parsed = model(text, target="王美", amount=1234, extra_info="用户同意转1234元")
    assert (parsed["intent"], parsed["target"], parsed["amount"]) == ("TRANSFER", "李四", 12.34)
    assert parsed["extra_info"] == text


@pytest.mark.parametrize("text,guessed", [
    ("给李四转点钱", 100),
    ("给 13800138000 转账", 13800138000),
    ("给李四转大概 200 元", 200),
    ("给李四转 200 到 300 元", 200),
    ("给李四转账，备注发票200元", 200),
])
def test_missing_or_ambiguous_amount_still_requires_clarification(model, text, guessed):
    parsed = model(text, target="李四", amount=guessed)
    assert parsed["amount"] is None
    assert "amount" in missing_slots(parsed)
    if "13800138000" in text:
        assert parsed["target"] == "13800138000"


@pytest.mark.parametrize("text", ["转账 200 元", "再给他转 200 元", "转账 200 元，备注李四"])
def test_missing_recipient_is_not_filled_from_model_or_remark(model, text):
    parsed = model(text, target="李四", amount=200)
    assert parsed["target"] is None
    assert "target" in missing_slots(parsed)


@pytest.mark.parametrize("target", ["CARD-001", "7788"])
def test_card_tail_requires_core_lookup_not_a_model_card_id(model, target):
    parsed = model("把尾号 7788 的卡限额改为 3000 元", intent="CARD_LIMIT", target=target, amount=7788)
    assert parsed["target"] is None
    assert parsed["amount"] == 3000


@pytest.mark.parametrize("text", ["预约给李四转账 200 元", "下个月给李四转账 200 元"])
def test_unresolved_schedule_cannot_become_immediate_or_guessed(model, text):
    parsed = model(text, target="李四", amount=200, scheduled_at="2099-01-01T09:00:00+08:00")
    assert parsed["intent"] == "SCHEDULED_TRANSFER"
    assert parsed["scheduled_at"] is None
    assert "scheduled_at" in missing_slots(parsed)


def test_birthday_without_date_does_not_accept_model_date(model):
    parsed = model("爱人生日快到了，预留 1000 元", intent="CROSS_SCENE_LOCK", target="王美", amount=1000,
                   scheduled_at="2099-01-01T09:00:00+08:00", event_at="2099-01-01T09:00:00+08:00")
    assert parsed["target"] == "爱人"
    assert parsed["scheduled_at"] is None and parsed["event_at"] is None
    assert "scheduled_at" in missing_slots(parsed)


def test_explicit_relative_date_and_clock_outrank_model(model):
    text = "下周六下午三点半给李四转账 200 元"
    expected = agent._local_parse(text)["scheduled_at"]
    parsed = model(text, target="王美", amount=999, scheduled_at="2099-01-01T09:00:00+08:00")
    assert parsed["scheduled_at"] == expected
    assert "T15:30:00+08:00" in parsed["scheduled_at"]


def test_bill_scope_and_category_are_not_rewritten(model):
    parsed = model("分析上个月交通花了多少", intent="BILL_ANALYSIS", date_scope="YEAR", slots={"category": "购物"})
    assert parsed["date_scope"] == "LAST_MONTH"
    assert parsed["slots"]["category"] == "交通"


def test_model_cannot_create_unrequested_subscription_reminders(model):
    parsed = model("查询 Apple 订阅", intent="SUBSCRIPTION_QUERY",
                   slots={"renewal_reminder": True, "days_before": 99})
    assert "renewal_reminder" not in parsed["slots"]
    explicit = model("Apple 续费提前 3 天提醒我", intent="SUBSCRIPTION_QUERY",
                     slots={"renewal_reminder": False, "days_before": 99})
    assert explicit["slots"]["renewal_reminder"] is True
    assert explicit["slots"]["days_before"] == 3


def test_model_cannot_change_risk_assessment_answers(model):
    parsed = model("风险评估：保守，新手，投资 2 年", intent="WEALTH_RISK_ASSESSMENT",
                   slots={"loss_tolerance": "HIGH", "experience": "PROFESSIONAL", "horizon_years": 20})
    assert parsed["slots"] == {"loss_tolerance": "LOW", "experience": "NONE", "horizon_years": 2}


def test_model_cannot_replace_aa_participants_or_add_unmentioned_products(model):
    split = model("和李四、王美 AA 300 元", intent="SPLIT_BILL", amount=300, participants=["张三", "王美"])
    assert split["participants"] == ["李四", "王美"]
    comparison = model("比较理财 CJ01002 和 AY90204", intent="WEALTH_COMPARE", product_ids=["CJ01002", "QK36509"])
    assert comparison["product_ids"] == ["CJ01002", "AY90204"]


def test_model_still_recognizes_new_phrasing_and_explicit_new_contact(model):
    text = "麻烦拨 200 元到赵敏户头"
    assert agent._local_parse(text)["intent"] == "UNKNOWN"
    assert agent._local_parse(text)["target"] is None
    parsed = model(text, target="赵敏", amount=200)
    assert (parsed["intent"], parsed["target"], parsed["amount"]) == ("TRANSFER", "赵敏", 200)
    assert missing_slots(parsed) == []


def test_model_still_recognizes_read_only_paraphrase(model):
    text = "请告诉我账户当前可用的资金数额"
    assert agent._local_parse(text)["intent"] == "UNKNOWN"
    parsed = model(text, intent="BALANCE_QUERY")
    assert parsed["intent"] == "BALANCE_QUERY"


@pytest.mark.parametrize("qualifier", ["预约", "明天"])
def test_model_recognized_transfer_paraphrase_still_respects_scheduling(model, qualifier):
    text = qualifier + "拨 200 元到赵敏户头"
    assert agent._local_parse(text)["intent"] == "UNKNOWN"
    parsed = model(text, target="赵敏", amount=200)
    assert parsed["intent"] == "SCHEDULED_TRANSFER"
    if qualifier == "预约":
        assert "scheduled_at" in missing_slots(parsed)
    else:
        assert parsed["scheduled_at"] == agent._local_parse(text)["scheduled_at"]


def test_model_can_compare_explicit_product_aliases_but_not_invent_another(model):
    text = "日日盈和季季丰各有什么优点"
    assert agent._local_parse(text)["intent"] == "UNKNOWN"
    parsed = model(text, intent="WEALTH_COMPARE", product_ids=["CJ01002", "AY90204", "QK36509"])
    assert parsed["intent"] == "WEALTH_COMPARE"
    assert parsed["product_ids"] == ["CJ01002", "AY90204"]
    assert missing_slots(parsed) == []


def test_model_may_extract_explicit_disputed_transaction_but_not_invent_it(model):
    parsed = model("这笔 TXN-123456 是盗刷", intent="FRAUD_DISPUTE", slots={"transaction_id": "TXN-123456"})
    assert parsed["slots"]["transaction_id"] == "TXN-123456"
    missing = model("这笔消费是盗刷", intent="FRAUD_DISPUTE", slots={"transaction_id": "TXN-123456"})
    assert "transaction_id" not in missing["slots"]


def test_unavailable_or_malformed_model_falls_back_to_local(monkeypatch):
    text = "给李四转账 10 元"
    monkeypatch.setattr(agent, "llm", SimpleNamespace(invoke=lambda _: SimpleNamespace(content="not json")))
    assert agent._model_parse(text) == agent._local_parse(text)
