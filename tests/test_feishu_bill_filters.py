"""Real parser -> Feishu fixed GET -> in-process Core -> card, without a bot."""

from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import feishu_im
import main
from security import SecurityError


@pytest.fixture
def bill_core(monkeypatch):
    main.reset_state()
    now = datetime.now()
    previous = now.replace(day=1) - timedelta(days=1)
    rows = [
        ("FOOD-1", "user_zhang", "餐饮", "午餐店", 28, now),
        ("FOOD-2", "user_zhang", "餐饮", "晚餐店", 45, now),
        ("SHOP", "user_zhang", "购物", "书店", 300, now),
        ("OLD-FOOD", "user_zhang", "餐饮", "上月餐厅", 999, previous),
        ("OTHER-OWNER", "user_li", "餐饮", "他人商户", 9000, now),
    ]
    monkeypatch.setattr(main, "TRANSACTIONS", [
        {"tx_id": tx_id, "account_id": owner, "category": category,
         "merchant": merchant, "amount": amount, "time": date.strftime("%Y-%m-%d 12:00:00")}
        for tx_id, owner, category, merchant, amount, date in rows
    ])
    calls = []
    with TestClient(main.app) as core:
        def read(bank, method, path, *, read_only=False):
            assert method == "GET" and read_only is True
            assert bank.user_id == "user_zhang" and bank.session_token == "feishu-session"
            response = core.get("/api/v1/" + path, headers={"X-User-Id": bank.user_id})
            assert response.status_code == 200, response.text
            calls.append((path, response.json()))
            return response

        monkeypatch.setattr(feishu_im.BankClient, "_request", read)
        yield core, calls
    main.reset_state()


def body(reply):
    assert reply.msg_type == "interactive"
    return "\n".join(item["text"]["content"] for item in reply.content["elements"] if item["tag"] == "div")


@pytest.mark.parametrize(("message", "endpoint"), [
    ("查看本月餐饮账单", "transactions"),
    ("分析本月餐饮支出", "bill-analysis"),
    ("这个月吃饭花了多少", "bill-analysis"),
])
def test_category_and_month_reach_core_before_rendering(bill_core, message, endpoint):
    _, calls = bill_core
    status, reply = feishu_im._read_only_reply("user_zhang", "feishu-session", message)
    assert status == "COMPLETED"
    path, result = calls[0]
    assert urlsplit(path).path.endswith("/" + endpoint)
    assert parse_qs(urlsplit(path).query) == {"date_scope": ["MONTH"], "category": ["餐饮"]}
    assert "category=%E9%A4%90%E9%A5%AE" in path and "餐饮" not in path
    records = result["data"] if endpoint == "transactions" else result["records"]
    assert {row["tx_id"] for row in records} == {"FOOD-1", "FOOD-2"}
    rendered = body(reply)
    assert "时间：本月 · 类别：餐饮" in rendered
    assert all(text not in rendered for text in ("购物", "书店", "300.00", "999.00", "9,000.00", "他人商户"))
    if endpoint == "bill-analysis":
        assert result["facts"]["total_spent"] == 73
        assert result["facts"]["record_count"] == 2
        assert set(result["facts"]["source_tx_ids"]) == {"FOOD-1", "FOOD-2"}
        assert "¥73.00" in rendered
    else:
        assert result["total_records"] == 2
        assert "午餐店" in rendered and "晚餐店" in rendered


@pytest.mark.parametrize("message", ["查看本月交通账单", "分析本月交通支出"])
def test_empty_category_does_not_fall_back_to_all_bills(bill_core, message):
    _, calls = bill_core
    status, reply = feishu_im._read_only_reply("user_zhang", "feishu-session", message)
    assert status == "COMPLETED"
    result = calls[0][1]
    assert result["category"] == "交通"
    assert result.get("data", result.get("records")) == []
    rendered = body(reply)
    assert "时间：本月 · 类别：交通" in rendered and "暂无交通交易记录" in rendered
    assert all(text not in rendered for text in ("午餐店", "书店", "73.00", "300.00"))
    if "facts" in result:
        assert result["facts"]["source_tx_ids"] == []
        assert result["facts"]["total_spent"] == 0


@pytest.mark.parametrize("message", [
    "查看本月账单", "分析本月账单", "这个月钱都花哪儿去了",
    "给我看看本月账单", "帮忙查一下本月账单", "查询我的本月交易记录", "看看本月的消费记录",
])
def test_unfiltered_bill_queries_keep_working(bill_core, message):
    _, calls = bill_core
    status, reply = feishu_im._read_only_reply("user_zhang", "feishu-session", message)
    assert status == "COMPLETED"
    result = calls[0][1]
    records = result.get("data", result.get("records"))
    assert {row["tx_id"] for row in records} == {"FOOD-1", "FOOD-2", "SHOP"}
    assert "时间：本月 · 类别：全部类别" in body(reply)
    assert "category=" not in calls[0][0]


@pytest.mark.parametrize("message", [
    "查看本月餐饮和购物账单",
    "查看本月医疗账单",
    "查看本月账单，分类为医疗",
    "查看本月账单，只看医疗",
    "查看本月账单，按医疗分类",
    "分析本月不含餐饮的账单",
    "查看2026年9月1日到2026年9月15日的餐饮账单",
    "查看今天或昨天餐饮账单",
    "查看最近7天餐饮账单",
    "查余额和本月餐饮账单",
])
def test_unrepresentable_filters_handoff_without_any_core_read(monkeypatch, message):
    read = Mock()
    monkeypatch.setattr(feishu_im.BankClient, "_request", read)
    status, reply = feishu_im._read_only_reply("user_zhang", "feishu-session", message)
    assert status == "APP_HANDOFF"
    assert "尚未提交" in body(reply)
    assert not any(item["tag"] == "action" for item in reply.content["elements"])
    read.assert_not_called()


@pytest.mark.parametrize("message", ["我有哪些订阅", "帮我看看有没有乱扣费", "Apple续费前两天提醒我"])
def test_yellow_subscription_query_hands_off_without_any_core_read(monkeypatch, message):
    read = Mock()
    monkeypatch.setattr(feishu_im.BankClient, "_request", read)
    status, reply = feishu_im._read_only_reply("user_zhang", "feishu-session", message)
    assert status == "APP_HANDOFF"
    assert "确认" in body(reply) and "App" in body(reply)
    assert not any(item["tag"] == "action" for item in reply.content["elements"])
    read.assert_not_called()


@pytest.mark.parametrize("endpoint", ["transactions", "bill-analysis"])
def test_core_reserved_characters_are_one_exact_category(bill_core, endpoint):
    core, _ = bill_core
    category = "餐饮&date_scope=ALL+其他#"
    matching = dict(main.TRANSACTIONS[0], tx_id="RESERVED", category=category, amount=7)
    old = dict(main.TRANSACTIONS[3], tx_id="RESERVED-OLD", category=category)
    main.TRANSACTIONS.extend([matching, old])
    response = core.get(f"/api/v1/accounts/user_zhang/{endpoint}",
                        params={"date_scope": "MONTH", "category": category},
                        headers={"X-User-Id": "user_zhang"})
    assert response.status_code == 200
    result = response.json()
    assert result["category"] == category and result["date_scope"] == "MONTH"
    assert [row["tx_id"] for row in result.get("data", result.get("records"))] == ["RESERVED"]
    if "facts" in result:
        assert result["facts"]["total_spent"] == 7
        assert result["facts"]["source_tx_ids"] == ["RESERVED"]


@pytest.mark.parametrize("endpoint", ["transactions", "bill-analysis"])
@pytest.mark.parametrize("category", ["", "   ", "餐\x00饮", "类" * 121])
def test_core_rejects_invalid_category_instead_of_expanding_it(bill_core, endpoint, category):
    core, _ = bill_core
    response = core.get(f"/api/v1/accounts/user_zhang/{endpoint}",
                        params={"category": category}, headers={"X-User-Id": "user_zhang"})
    assert response.status_code == 400


@pytest.mark.parametrize("endpoint", ["transactions", "bill-analysis"])
def test_category_filter_cannot_read_another_principal(bill_core, endpoint):
    core, _ = bill_core
    response = core.get(f"/api/v1/accounts/user_li/{endpoint}", params={"category": "餐饮"},
                        headers={"X-User-Id": "user_zhang"})
    assert response.status_code == 403
    assert "他人商户" not in response.text


@pytest.mark.parametrize("message", ["查看本月餐饮账单", "分析本月餐饮支出"])
def test_old_core_that_ignores_category_cannot_be_rendered(monkeypatch, message):
    read = Mock()
    read.return_value.json.return_value = {"code": 200, "date_scope": "MONTH", "category": None,
        "total_records": 1, "data": [{"merchant": "不应展示", "category": "购物", "amount": 300}],
        "facts": {"record_count": 1, "total_spent": 300}}
    monkeypatch.setattr(feishu_im.BankClient, "_request", read)
    with pytest.raises(SecurityError, match="未确认账单查询条件"):
        feishu_im._read_only_reply("user_zhang", "feishu-session", message)
