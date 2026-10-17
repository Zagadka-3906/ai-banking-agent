"""With a model configured it reads every request; rules ground the facts and catch disagreements."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import agent
import main
from bank_agent import runtime
from test_agent_gateway import _send, gateway  # noqa: F401


@pytest.fixture
def model(monkeypatch):
    """A fixed model answer; returns the list of calls so tests can see it was asked."""

    monkeypatch.delenv("BANK_MODEL_FAST_PATH", raising=False)
    calls: list = []

    def answer(intent, **extra):
        payload = {"intent": intent, "confidence": 0.9, **extra}
        monkeypatch.setattr(runtime, "llm", SimpleNamespace(
            invoke=lambda messages: calls.append(messages) or SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))))
        return calls

    return answer


def test_the_model_is_asked_even_for_a_request_the_rules_understand(model):
    calls = model("TRANSFER", target="李四", amount=100)
    parsed = agent.model_parse("给李四转100元")
    assert len(calls) == 1
    assert (parsed["intent"], parsed["target"], parsed["amount"]) == ("TRANSFER", "李四", 100)
    assert parsed["parse_meta"]["resolution"] == "model_confirmed"


def test_between_two_reads_the_model_decides(model):
    text = "查一下我的账单"
    assert agent.local_parse(text)["intent"] == "BILL_QUERY"
    model("BILL_ANALYSIS")
    assert agent.model_parse(text)["intent"] == "BILL_ANALYSIS"


def test_amounts_and_payees_still_come_from_what_was_said(model):
    model("TRANSFER", target="王美", amount=9999)
    parsed = agent.model_parse("给李四转100元")
    assert (parsed["target"], parsed["amount"]) == ("李四", 100)


def test_a_disagreement_about_changing_something_is_asked_not_guessed(gateway, model):  # noqa: F811
    client, _ = gateway
    model("CARD_QUERY")
    reply = _send(client, "我的卡丢了", conversation_id="conflict")
    assert reply["approval"] is None
    assert "挂失卡片" in reply["reply"] and "查看我的卡片" in reply["reply"] and "还没有发起任何操作" in reply["reply"]
    assert reply["result"]["suggestions"] == ["挂失卡片", "查看我的卡片"]
    assert all(card["status"] == "ACTIVE" for card in main.CARDS["user_zhang"])


def test_the_model_cannot_turn_a_read_into_a_payment(gateway, model):  # noqa: F811
    client, _ = gateway
    model("TRANSFER", target="李四", amount=500)
    reply = _send(client, "查一下余额", conversation_id="no-escalation")
    assert reply["approval"] is None and "转账" in reply["reply"]


def test_the_fast_path_can_still_be_switched_on(model, monkeypatch):
    calls = model("UNKNOWN")
    monkeypatch.setenv("BANK_MODEL_FAST_PATH", "1")
    assert agent.model_parse("给李四转100元")["intent"] == "TRANSFER"
    assert not calls
