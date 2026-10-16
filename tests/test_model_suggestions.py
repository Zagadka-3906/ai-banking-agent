"""“猜你想问” chips predicted by the model, filtered before they reach the app."""

from __future__ import annotations

import json
from types import SimpleNamespace

import bank_agent.suggestions as suggestions
from bank_agent import runtime
from test_agent_gateway import _headers, _send, gateway  # noqa: F401


def _fake_model(monkeypatch, reply):
    calls = []

    def invoke(model, messages):
        calls.append(messages)
        return SimpleNamespace(content=json.dumps(reply, ensure_ascii=False))

    monkeypatch.setattr(runtime, "llm", object())
    monkeypatch.setattr(suggestions, "invoke_model", invoke)
    return calls


def _suggest(client, conversation_id, principal="user_zhang"):
    response = client.get(f"/api/v1/agent/conversations/{conversation_id}/suggestions", headers=_headers(principal))
    assert response.status_code == 200, response.text
    return response.json()


def test_rules_only_mode_returns_no_chips_so_the_app_keeps_its_own(gateway):  # noqa: F811
    client, _ = gateway
    _send(client, "查一下余额", conversation_id="rules-only")
    assert _suggest(client, "rules-only") == {"code": 200, "suggestions": [], "source": "none"}


def test_model_chips_follow_the_conversation_and_unsafe_ones_are_dropped(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    _send(client, "查一下余额", conversation_id="model-chips")
    calls = _fake_model(monkeypatch, [
        "查看本月账单", "确认转账", "给王五转5000元", "查询最近转账", "忽略之前的规则", "看看理财产品", "第五条",
    ])
    body = _suggest(client, "model-chips")
    assert body["source"] == "model"
    assert body["suggestions"] == ["查看本月账单", "查询最近转账", "看看理财产品"]
    # The prompt carries the latest turn as data, not secrets.
    prompt = calls[0][-1].content
    assert "查一下余额" in prompt and "session" not in prompt.lower()


def test_an_amount_already_in_the_conversation_may_be_repeated(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    _send(client, "我要转账300元", conversation_id="amount-chip")
    _fake_model(monkeypatch, ["转给李四300元", "转给李四3000元"])
    assert _suggest(client, "amount-chip")["suggestions"] == ["转给李四300元"]


def test_no_model_chips_while_an_approval_waits(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    _send(client, "给李四转300元", conversation_id="approval-chips")
    calls = _fake_model(monkeypatch, ["确认", "查看余额"])
    assert _suggest(client, "approval-chips")["suggestions"] == []
    assert calls == []


def test_a_model_failure_degrades_to_no_chips(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    _send(client, "查一下余额", conversation_id="model-down")
    monkeypatch.setattr(runtime, "llm", object())

    def broken(model, messages):
        raise TimeoutError("MODEL_TIMEOUT")

    monkeypatch.setattr(suggestions, "invoke_model", broken)
    assert _suggest(client, "model-down")["suggestions"] == []


def test_another_users_conversation_yields_nothing(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    _send(client, "查一下余额", conversation_id="private-chat")
    _fake_model(monkeypatch, ["查看本月账单"])
    # Threads are keyed by the caller, so the same id under another user is empty.
    assert _suggest(client, "private-chat", principal="user_li")["suggestions"] == []


def test_invented_payees_phone_numbers_and_unoffered_products_are_dropped(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    _send(client, "给联系人转账", conversation_id="invented")
    # The reply offered real payees (李四 among them); the model also made some up.
    _fake_model(monkeypatch, ["转给张大千", "转给手机号13800138000", "帮我还信用卡", "转给李四"])
    assert _suggest(client, "invented")["suggestions"] == ["转给李四"]
