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


def test_when_rules_and_model_disagree_the_model_decides(gateway, model):  # noqa: F811
    client, _ = gateway
    text = "我的卡好像找不到了"
    assert agent.local_parse(text)["intent"] == "CARD_QUERY"
    model("CARD_REPORT_LOST")
    parsed = agent.model_parse(text)
    assert parsed["intent"] == "CARD_REPORT_LOST" and parsed["parse_meta"]["resolution"] == "model_override"
    reply = _send(client, text, conversation_id="lost-card")
    # Reporting the loss is RED: nothing changes before the user verifies it.
    assert "挂失卡片" in reply["approval"]["summary"]
    assert all(card["status"] == "ACTIVE" for card in main.CARDS["user_zhang"])


def test_a_model_misreading_cannot_supply_a_payee_or_amount(gateway, model):  # noqa: F811
    client, _ = gateway
    model("TRANSFER", target="李四", amount=500)
    reply = _send(client, "查一下余额", conversation_id="no-invented-payment")
    # The explicit read cannot become a transfer, even if the model insists.
    assert reply["approval"] is None and reply["intent"] == "BALANCE_QUERY"
    assert reply["business_status"] == "SUCCEEDED"


def test_the_fast_path_can_still_be_switched_on(model, monkeypatch):
    calls = model("UNKNOWN")
    monkeypatch.setenv("BANK_MODEL_FAST_PATH", "1")
    assert agent.model_parse("给李四转100元")["intent"] == "TRANSFER"
    assert not calls
