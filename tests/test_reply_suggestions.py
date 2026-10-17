"""Wording a reply offers ("可以说“…”", "例如：…") becomes tap-to-send chips."""

from __future__ import annotations

import pytest

from bank_agent.suggestions import reply_examples
from test_agent_gateway import _headers, _send, gateway  # noqa: F401


@pytest.mark.parametrize(("reply", "chips"), [
    ("📊 您当前没有理财持仓。想看看在售产品可以说“有哪些理财产品”。", ["有哪些理财产品"]),
    ("请至少提供两个理财产品编号（例如：比较 LX00118 和 HQ30060）。", ["比较 LX00118 和 HQ30060"]),
    ("要转给哪位收款人？请说出联系人姓名或手机号，例如：张三、李四。", ["张三", "李四"]),
    ("请补充理财产品编号（例如 LX00118），我不会猜测产品。", ["LX00118"]),
    ("现在可以直接说出需求（例如“查看理财产品”或“申购 LX00118 500 元”）。", ["查看理财产品", "申购 LX00118 500 元"]),
    ("请说明要取消哪个订阅（例如：取消 Apple 订阅）。查看现有订阅可以说“我有哪些订阅”。", ["取消 Apple 订阅", "我有哪些订阅"]),
])
def test_examples_in_a_reply_become_chips(reply, chips):
    assert reply_examples(reply) == chips


@pytest.mark.parametrize("reply", [
    "未找到收款人“张大千”。",            # a quoted name, not something to say
    "如无问题，请回复“确认”。",           # approval wording is never a chip
    "可一次性回复全部答案（例如：1A 2B 3C 4D 5C 6C 7C 8C）。",  # too long for a chip
])
def test_quotes_that_are_not_requests_are_ignored(reply):
    assert reply_examples(reply) == []


def test_the_turn_carries_the_examples_so_the_app_shows_them_at_once(gateway):  # noqa: F811
    client, _ = gateway
    holdings = _send(client, "查看我的持仓", conversation_id="examples")
    assert holdings["result"]["suggestions"] == ["有哪些理财产品"]
    compare = _send(client, "比较理财产品", conversation_id="examples")
    assert compare["result"]["suggestions"] == ["比较 LX00118 和 HQ30060"]


def test_without_a_model_the_suggestion_endpoint_still_returns_the_examples(gateway):  # noqa: F811
    client, _ = gateway
    _send(client, "比较理财产品", conversation_id="examples-endpoint")
    body = client.get("/api/v1/agent/conversations/examples-endpoint/suggestions", headers=_headers("user_zhang")).json()
    assert body == {"code": 200, "suggestions": ["比较 LX00118 和 HQ30060"], "source": "reply"}
