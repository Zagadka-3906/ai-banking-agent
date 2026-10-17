# ruff: noqa: F811  # imported pytest fixtures are injected by parameter name
"""A model's isolated read classification must not erase a pending mutation.

No remote model or service is contacted: the model outputs are fixed, while the
real graph, clarification state and banking core run through the gateway fixture.
"""

import json
from types import SimpleNamespace

import pytest

import conversation
import main
from test_agent_gateway import gateway, _headers, _send  # noqa: F401
from bank_agent import runtime


def _state(conversation_id):
    thread = conversation.conversation_thread_id("user_zhang", conversation_id)
    return conversation.get_graph().get_state({"configurable": {"thread_id": thread}}).values


def _read_only_model(monkeypatch, intent, target):
    class IsolatedReadModel:
        def invoke(self, _messages):
            return SimpleNamespace(content=json.dumps({
                "intent": intent, "target": target, "amount": None,
                "confidence": 0.99, "date_scope": "ALL", "extra_info": "",
                "participants": [], "product_ids": [], "slots": {},
                "scheduled_at": None, "event_at": None,
            }, ensure_ascii=False))

    monkeypatch.setattr(runtime, "llm", IsolatedReadModel())
    # These tests are about the follow-up turn. With the model reading every
    # request, its misreading of the first one would be asked about instead
    # (tests/test_model_first_parsing.py); keep the first turn on the rules.
    monkeypatch.setenv("BANK_MODEL_FAST_PATH", "1")


def test_bare_card_id_model_query_must_complete_pending_lost_card_target(gateway, monkeypatch):
    client, _ = gateway
    cid = "model-context-card-loss"
    main.CARDS["user_zhang"].append({
        **main.CARDS["user_zhang"][0], "card_id": "CARD-002", "last4": "1111",
        "number_masked": "**** **** **** 1111",
    })
    _read_only_model(monkeypatch, "CARD_QUERY", "CARD-001")
    first = _send(client, "我的卡丢了", conversation_id=cid)
    assert first["approval"] is None, first
    pending = _state(cid)["context"]["pending_clarification"]
    assert pending["intent"] == "CARD_REPORT_LOST", first
    assert "target" in pending["missing"]

    second = _send(client, "CARD-001", conversation_id=cid)
    actual = {"status": second["status"], "intent": _state(cid)["intent"], "target": _state(cid)["target"], "reply": second["reply"]}
    assert actual["intent"] == "CARD_REPORT_LOST", actual
    assert second["status"] == "APPROVAL_REQUIRED", actual
    assert _state(cid)["target"] == "CARD-001"
    assert all(card["status"] == "ACTIVE" for card in main.CARDS["user_zhang"])


@pytest.mark.parametrize(("fragment", "merchant"), [("Apple那个", "Apple"), ("视频会员", "视频会员")])
def test_merchant_fragment_model_query_must_complete_pending_cancellation(gateway, monkeypatch, fragment, merchant):
    client, _ = gateway
    cid = "model-context-subscription-cancel"
    _read_only_model(monkeypatch, "SUBSCRIPTION_CANCEL", None)
    first = _send(client, "取消自动扣费", conversation_id=cid)
    assert first["approval"] is None, first
    pending = _state(cid)["context"]["pending_clarification"]
    assert pending["intent"] == "SUBSCRIPTION_CANCEL", first
    assert "target" in pending["missing"]

    # The model misreads only the bare answer as a fresh query.
    _read_only_model(monkeypatch, "SUBSCRIPTION_QUERY", merchant)

    second = _send(client, fragment, conversation_id=cid)
    actual = {"status": second["status"], "intent": _state(cid)["intent"], "target": _state(cid)["target"], "reply": second["reply"]}
    assert actual["intent"] == "SUBSCRIPTION_CANCEL", actual
    assert second["status"] == "APPROVAL_REQUIRED", actual
    assert _state(cid)["target"] == merchant
    assert all(item["status"] == "ACTIVE" for item in main.SUBSCRIPTIONS["user_zhang"])


def test_explicit_new_query_still_supersedes_pending_target(gateway, monkeypatch):
    client, _ = gateway
    cid = "model-context-new-query"
    _read_only_model(monkeypatch, "SUBSCRIPTION_CANCEL", None)
    first = _send(client, "取消自动扣费", conversation_id=cid)
    assert first["approval"] is None
    assert _state(cid)["context"]["pending_clarification"]["intent"] == "SUBSCRIPTION_CANCEL"

    _read_only_model(monkeypatch, "SUBSCRIPTION_QUERY", "Apple")

    second = _send(client, "查询Apple订阅", conversation_id=cid)
    # The new query supersedes the pending cancel; as a yellow read it asks to
    # confirm the query itself, never the abandoned cancellation.
    assert "查询Apple订阅" in second["approval"]["summary"], second
    assert _state(cid)["intent"] == "SUBSCRIPTION_QUERY", second
    assert all(item["status"] == "ACTIVE" for item in main.SUBSCRIPTIONS["user_zhang"])
