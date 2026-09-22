"""Specific transaction questions through the real in-process gateway/core."""

from copy import deepcopy
from datetime import datetime, timedelta

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("fastapi")

import agent
import main
from test_agent_gateway import gateway, _send  # noqa: F401


@pytest.fixture
def foreign_bill(gateway, monkeypatch):
    monkeypatch.setattr(agent, "llm", None)
    client, _ = gateway
    today = datetime.now()
    previous = today.replace(day=1) - timedelta(days=1)
    rows = [
        {"tx_id": "EXPLAIN-FOREIGN", "merchant": "境外未知商户", "category": "境外消费",
         "amount": 2000.0, "time": today.strftime("%Y-%m-%d 12:00:00"), "card_id": "CARD-001"},
        {"tx_id": "EXPLAIN-FOOD", "merchant": "午餐店", "category": "餐饮",
         "amount": 80.0, "time": today.strftime("%Y-%m-%d 12:30:00"), "card_id": "CARD-001"},
        {"tx_id": "EXPLAIN-LAST-MONTH", "merchant": "海外旅行商户", "category": "旅行",
         "amount": 650.0, "time": previous.strftime("%Y-%m-%d 03:00:00"), "card_id": "CARD-002"},
    ]
    main.TRANSACTIONS[:] = [{"account_id": "user_zhang", **row} for row in rows]
    main.TRANSACTIONS.append({"account_id": "user_li", "tx_id": "EXPLAIN-OTHER-OWNER",
        "merchant": "境外其他用户商户", "category": "境外消费", "amount": 9000.0,
        "time": today.strftime("%Y-%m-%d 12:00:00"), "card_id": "PRIVATE-CARD"})
    return client, rows


def _financial_state():
    return deepcopy((main.ACCOUNTS, main.CARDS, main.TRANSACTIONS, main.DISPUTE_CASES))


@pytest.mark.parametrize("question", ["那笔境外消费怎么回事", "查看那笔境外交易记录"])
def test_foreign_transaction_explanation_uses_record_details_and_rule_evidence(foreign_bill, question):
    client, rows = foreign_bill
    before = _financial_state()
    _send(client, "分析本月账单", conversation_id="specific-foreign")
    answer = _send(client, question, conversation_id="specific-foreign")
    assert answer["status"] == "COMPLETED" and answer["approval"] is None
    reply = answer["reply"]
    for fact in ("境外未知商户", rows[0]["time"], "2,000.00", "CARD-001", "EXPLAIN-FOREIGN", "境外商户"):
        assert fact in reply, reply
    assert "规则" in reply and "不能据此确认盗刷" in reply
    assert all(text not in reply for text in ("午餐店", "2,080.00", "海外旅行商户", "PRIVATE-CARD", "其他用户商户"))
    assert answer["result"]["execution_result"]["facts"]["source_tx_ids"] == ["EXPLAIN-FOREIGN"]
    assert _financial_state() == before


def test_foreign_explanation_preserves_previous_month_and_all_matching_rule_reasons(foreign_bill):
    client, rows = foreign_bill
    before = _financial_state()
    _send(client, "分析上月账单", conversation_id="specific-last-month")
    answer = _send(client, "那笔境外消费怎么回事", conversation_id="specific-last-month")
    reply = answer["reply"]
    for fact in ("海外旅行商户", rows[2]["time"], "650.00", "CARD-002", "深夜大额", "境外商户"):
        assert fact in reply, reply
    assert "EXPLAIN-FOREIGN" not in reply and "2,000.00" not in reply
    assert answer["result"]["execution_result"]["date_scope"] == "LAST_MONTH"
    assert answer["approval"] is None and _financial_state() == before


def test_no_matching_foreign_transaction_does_not_explain_unrelated_or_other_owner_rows(foreign_bill):
    client, _ = foreign_bill
    main.TRANSACTIONS[:] = [row for row in main.TRANSACTIONS
                            if row["tx_id"] not in {"EXPLAIN-FOREIGN", "EXPLAIN-LAST-MONTH"}]
    before = _financial_state()
    answer = _send(client, "那笔境外消费怎么回事", conversation_id="specific-no-match")
    assert "未找到" in answer["reply"] and "境外" in answer["reply"]
    assert all(text not in answer["reply"] for text in ("午餐店", "80.00", "其他用户商户", "9,000.00"))
    assert answer["result"]["execution_result"]["records"] == []
    assert answer["approval"] is None and _financial_state() == before


def test_multiple_matching_transactions_are_candidates_not_an_invented_unique_match(foreign_bill):
    client, _ = foreign_bill
    before = _financial_state()
    answer = _send(client, "那笔境外消费怎么回事", conversation_id="specific-ambiguous")
    reply = answer["reply"]
    assert "2 笔" in reply and "流水号" in reply and "请" in reply
    assert "EXPLAIN-FOREIGN" in reply and "EXPLAIN-LAST-MONTH" in reply
    assert "午餐店" not in reply and "其他用户商户" not in reply
    assert answer["approval"] is None and _financial_state() == before


def test_regular_bill_analysis_still_includes_all_requested_categories(foreign_bill):
    client, _ = foreign_bill
    answer = _send(client, "分析本月账单", conversation_id="ordinary-bill")
    facts = answer["result"]["execution_result"]["facts"]
    assert facts["source_tx_ids"] == ["EXPLAIN-FOREIGN", "EXPLAIN-FOOD"]
    assert facts["total_spent"] == 2080
    assert "2,080.00" in answer["reply"] and "餐饮" in answer["reply"]
