"""Transfer-history queries, "who?" before "how much?", and specific payee errors."""

from __future__ import annotations

import pytest

import main
import security
from bank_agent.parsing import local_parse
from bank_core.payments import resolve_payee
from test_agent_gateway import _headers, _send, gateway  # noqa: F401

PRINCIPAL = "user_zhang"


@pytest.mark.parametrize("text", ["查询最近转账", "查看最近的转账记录", "最近转账记录", "查一下转账记录", "看看我的转账明细"])
def test_transfer_history_is_a_query_not_a_new_transfer(text):
    # "查询最近转账" is also an Android follow-up chip; it used to start a transfer.
    parsed = local_parse(text)
    assert parsed["intent"] == "BILL_QUERY"
    assert parsed["slots"]["category"] == "转账支出"
    assert parsed["amount"] is None


@pytest.mark.parametrize("text", ["给李四转300元", "转给李四200", "明天给李四转100元", "撤销刚才的转账"])
def test_real_transfers_and_rollbacks_keep_their_intent(text):
    assert local_parse(text)["intent"] in {"TRANSFER", "SCHEDULED_TRANSFER", "UNDO_OPERATION"}


@pytest.mark.parametrize("text", ["最近的交易记录", "最近收支明细", "查询最近转账"])
def test_plain_recently_means_the_last_thirty_days(text):
    parsed = local_parse(text)
    assert parsed["date_scope"] == "CUSTOM"
    assert parsed["date_range"] and not (parsed["slots"] or {}).get("date_range_ambiguous")


@pytest.mark.parametrize("text", ["给联系人转账", "转账给联系人1000元", "给收款人转500元", "给对方转200"])
def test_a_placeholder_is_not_a_payee(text):
    assert local_parse(text)["target"] is None


def test_unknown_payee_error_names_the_payee_and_the_options():
    main.reset_state()
    with pytest.raises(security.SecurityError) as refused:
        resolve_payee("张大千", from_user=PRINCIPAL)
    detail = refused.value.detail
    assert refused.value.code == "TARGET_NOT_FOUND"
    assert "张大千" in detail and "李四" in detail and "收款方账户不存在" not in detail


def test_generic_payee_asks_who_before_the_amount(gateway):  # noqa: F811
    client, _ = gateway
    first = _send(client, "给联系人转账", conversation_id="who-first")
    assert first["approval"] is None
    assert "转给哪位" in first["reply"] and "李四" in first["reply"]
    assert "金额" not in first["reply"]
    second = _send(client, "李四", conversation_id="who-first")
    assert second["approval"] is None and "金额" in second["reply"]


def test_unknown_payee_reply_is_specific(gateway):  # noqa: F811
    client, _ = gateway
    reply = _send(client, "给张大千转300元", conversation_id="unknown-payee")
    assert reply["approval"] is None
    assert "张大千" in reply["reply"] and "资金没有变动" in reply["reply"]
    assert main.ACCOUNTS[PRINCIPAL]["balance"] == 15000.0


def test_recent_transfers_lists_settled_transfers_only(gateway):  # noqa: F811
    client, _ = gateway
    pending = _send(client, "给李四转 300 元", conversation_id="history")
    done = client.post(f"/api/v1/agent/approvals/{pending['approval']['approval_id']}", json={"decision": "approve"}, headers=_headers())
    assert done.status_code == 200
    history = _send(client, "查询最近转账", conversation_id="history-query")
    assert history["approval"] is None
    assert "李四" in history["reply"] and "300" in history["reply"]
    assert "咖啡店" not in history["reply"]
