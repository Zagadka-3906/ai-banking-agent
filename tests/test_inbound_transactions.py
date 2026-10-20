"""A transfer appears in the payee's statement as money received, not spending."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
from planner import analyze_transactions, is_income


@pytest.fixture()
def client():
    main.reset_state()
    with TestClient(main.app) as value:
        yield value
    main.reset_state()


def _transfer(client: TestClient, amount: float, remark: str | None = None) -> dict:
    headers = {"X-User-Id": "user_zhang"}
    body = {"from_user": "user_zhang", "target_name_or_id": "李四", "amount": amount}
    if remark:
        body["remark"] = remark
    pre = client.post("/api/v1/risk/precheck", json=body, headers=headers)
    assert pre.status_code == 200, pre.text
    token = pre.json()["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers={**headers, "X-Auth-Token": token}).status_code == 200
    done = client.post("/api/v1/transfers/execute", json=body, headers={**headers, "X-Auth-Token": token})
    assert done.status_code == 200, done.text
    return done.json()


def test_payee_statement_records_the_credit(client):
    _transfer(client, 300, remark="房租")
    rows = client.get("/api/v1/accounts/user_li/transactions", headers={"X-User-Id": "user_li"}).json()["data"]
    credits = [row for row in rows if is_income(row)]
    assert len(credits) == 1
    assert credits[0]["amount"] == 300.0
    assert credits[0]["category"] == "转账收入"
    assert credits[0]["merchant"] == "来自 张三 的转账"
    assert credits[0]["remark"] == "房租"
    # The payer still sees one debit.
    payer = client.get("/api/v1/accounts/user_zhang/transactions", headers={"X-User-Id": "user_zhang"}).json()["data"]
    assert sum(1 for row in payer if row.get("category") == "转账支出") == 1


def test_received_money_is_not_counted_as_spending(client):
    _transfer(client, 300)
    analysis = client.get("/api/v1/accounts/user_li/bill-analysis", headers={"X-User-Id": "user_li"}).json()["facts"]
    assert analysis["total_spent"] == 0.0
    assert analysis["income_total"] == 300.0
    assert analysis["anomalies"] == []
    assert all(item["category"] != "转账收入" for item in analysis["category_breakdown"])


def test_analysis_text_mentions_income_separately():
    from planner import safe_analysis_text

    facts = analyze_transactions([
        {"tx_id": "a", "amount": 50, "category": "餐饮", "time": "2026-10-01 10:00:00"},
        {"tx_id": "b", "amount": 200, "category": "转账收入", "direction": "IN", "time": "2026-10-01 11:00:00"},
    ])
    assert facts["total_spent"] == 50.0
    assert facts["income_total"] == 200.0
    text = safe_analysis_text(facts)
    assert "支出合计 ¥50.00" in text and "同期入账 ¥200.00" in text
