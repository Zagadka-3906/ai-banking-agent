# ruff: noqa: F811  # imported pytest fixtures are injected by parameter name
"""Clarification text matches missing slots through the real channel gateway."""

import os

os.environ["BANK_CONVERSATION_DB"] = "memory"

import pytest
import requests

pytest.importorskip("langgraph")
pytest.importorskip("fastapi")

import conversation
import main
from test_agent_gateway import gateway, _send  # noqa: F401
from bank_agent import runtime


@pytest.fixture
def channel(gateway, monkeypatch):
    import agent_api

    monkeypatch.setattr(runtime, "llm", None)
    monkeypatch.setattr(agent_api, "RATE_LIMIT_STATE", {})

    def no_network(*args, **kwargs):
        raise AssertionError("Clarification tests must not contact external services")

    monkeypatch.setattr(requests.sessions.Session, "request", no_network)
    return gateway[0]


def _state(cid):
    thread = conversation.conversation_thread_id("user_zhang", cid)
    return conversation.get_graph().get_state({"configurable": {"thread_id": thread}}).values


@pytest.mark.parametrize("text,missing,wanted,unwanted", [
    ("我爱人生日是2099-10-03，提前两天提醒订花和蛋糕", {"amount"}, ("金额",), ("日期", "对象")),
    ("我爱人生日快到了，锁定1000元提醒订花", {"scheduled_at"}, ("生日日期",), ("金额", "对象")),
    ("过生日用，锁定1000元，2099-10-03前两天提醒订花", {"target"}, ("对象",), ("金额", "日期")),
    ("三个人吃饭一共300元，帮我AA", {"participants"}, ("参与人",), ("金额", "300")),
    ("和李四、王美AA分摊", {"amount"}, ("金额",), ("参与人", "李四", "王美")),
    ("转账300元", {"target"}, ("收款人",), ("金额", "日期")),
    ("给李四转账", {"amount"}, ("金额",), ("收款人", "日期")),
    ("预约给李四转账300元", {"scheduled_at"}, ("日期",), ("金额", "收款人")),
    ("比较理财 CJ01002", {"product_ids"}, ("再", "一个", "产品编号"), ("两个", "CJ01002")),
    ("把 CARD-001 的额度调整一下", {"amount"}, ("额度",), ("卡片编号", "日期")),
    ("取消订阅", {"target"}, ("哪个订阅",), ("金额", "日期")),
])
def test_gateway_asks_only_for_the_missing_fields(channel, text, missing, wanted, unwanted):
    cid = "precise-clarification"
    response = _send(channel, text, conversation_id=cid)
    state = _state(cid)
    assert response["approval"] is None, response
    assert state["clarification_required"] is True, response
    assert set(state["context"]["pending_clarification"]["missing"]) == missing
    assert all(word in response["reply"] for word in wanted), response["reply"]
    assert all(word not in response["reply"] for word in unwanted), response["reply"]
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000
    assert main.ACCOUNTS["user_zhang"]["locked_amount"] == 0


@pytest.mark.parametrize("first,answer,intent", [
    ("我爱人生日是2099-10-03，提前两天提醒订花和蛋糕", "1000元", "CROSS_SCENE_LOCK"),
    ("三个人吃饭一共300元，帮我AA", "李四和王美", "SPLIT_BILL"),
])
def test_gateway_slot_answer_preserves_the_values_already_given(channel, first, answer, intent):
    cid = "clarification-preserves-values"
    _send(channel, first, conversation_id=cid)
    before = _state(cid)
    response = _send(channel, answer, conversation_id=cid)
    after = _state(cid)
    assert response["status"] == "APPROVAL_REQUIRED", response
    assert after["intent"] == intent
    if intent == "SPLIT_BILL":
        assert before["amount"] == after["amount"] == 300
        assert after["participants"] == ["李四", "王美"]
    else:
        assert before["amount"] is None and after["amount"] == 1000
        assert before["target"] == after["target"] == "爱人"
        assert before["event_at"].startswith("2099-10-03")
        assert after["event_at"] == "2099-10-03T01:00:00+00:00"
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000
    assert main.ACCOUNTS["user_zhang"]["locked_amount"] == 0


@pytest.mark.parametrize("text,wanted", [
    ("帮我安排生日", ("对象", "金额", "生日日期")),
    ("帮我AA分摊", ("总金额", "参与人")),
])
def test_gateway_lists_all_missing_fields_when_none_were_supplied(channel, text, wanted):
    response = _send(channel, text, conversation_id="several-missing-fields")
    assert response["approval"] is None
    assert all(word in response["reply"] for word in wanted), response["reply"]


@pytest.mark.parametrize("answer,expected_ids", [
    ("AY90204", ["CJ01002", "AY90204"]),
    ("比较 AY90204 和 ZY18031", ["AY90204", "ZY18031"]),
])
def test_gateway_one_more_product_completes_comparison_but_full_request_replaces_it(channel, answer, expected_ids):
    cid = "complete-product-pair"
    first = _send(channel, "比较 CJ01002", conversation_id=cid)
    assert "再提供一个" in first["reply"]
    response = _send(channel, answer, conversation_id=cid)
    assert response["status"] == "COMPLETED" and response["approval"] is None, response
    assert _state(cid)["intent"] == "WEALTH_COMPARE"
    assert _state(cid)["product_ids"] == expected_ids
    execution = response["result"]["execution_result"]
    assert execution["type"] == "WEALTH_COMPARE"
    assert [product["product_id"] for product in execution["data"]["data"]] == expected_ids
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000
    assert main.WEALTH_HOLDINGS.get("user_zhang", []) == []


@pytest.mark.parametrize("answer", ["CJ01002", "CARD-001", "TXN-123456"])
def test_gateway_duplicate_or_unrelated_id_does_not_fill_the_second_product(channel, answer):
    cid = "product-pair-still-missing"
    _send(channel, "比较 CJ01002", conversation_id=cid)
    response = _send(channel, answer, conversation_id=cid)
    state = _state(cid)
    assert state["clarification_required"] is True, response
    assert state["product_ids"] == ["CJ01002"]
    assert response["approval"] is None
    assert not (response.get("result") or {}).get("execution_result")
    assert "再提供一个" in response["reply"]
