# ruff: noqa: F811
"""Regressions reproduced through the Android user simulation matrix."""
from datetime import datetime, timedelta

import pytest

import agent
import main
from test_natural_bills_subscriptions_events import bills, dialogue  # noqa: F401


@pytest.mark.parametrize("day", ["昨天", "前天", "昨晚"])
def test_past_transfer_never_becomes_an_immediate_approval(dialogue, day):
    response = dialogue.say(f"{day}上午十点给李四转20元")
    assert dialogue.state["intent"] == "SCHEDULED_TRANSFER"
    assert response["approval"] is None
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000
    assert not main.SCHEDULED_TRANSFERS


@pytest.mark.parametrize("answer,scope", [("本月", "MONTH"), ("上个月", "LAST_MONTH"), ("今年", "YEAR"), ("全部", "ALL")])
def test_invalid_date_can_be_corrected_to_a_named_scope(bills, answer, scope):
    bills.say("分析2026年13月的账单")
    assert bills.state["clarification_required"]
    bills.say(answer)
    assert not bills.state["clarification_required"], bills.diagnostic()
    assert bills.execution("BILL_ANALYSIS")["date_scope"] == scope


def test_category_only_followups_preserve_custom_date_range(bills):
    now = datetime.now()
    bills.say(f"分析{now.year}年{now.month}月1日至{now.day}日账单")
    original = bills.state["date_range"]
    bills.say("只看餐饮")
    result = bills.execution("BILL_ANALYSIS")
    assert result["date_range"] == original
    assert result["category"] == "餐饮"
    assert all(row["category"] == "餐饮" for row in result["records"])
    bills.say("再看交通")
    result = bills.execution("BILL_ANALYSIS")
    assert result["date_range"] == original
    assert result["category"] == "交通"
    assert not result["records"]


def test_comparison_ordinal_uses_the_literal_comparison_order(dialogue):
    main.WEALTH_RISK_PROFILES["user_zhang"] = {"risk_profile": "C3", "loss_tolerance": "MEDIUM", "horizon_years": 3, "experience": "BASIC"}
    dialogue.say("比较LX00118和HQ30060")
    response = dialogue.say("那买前一个50元")
    assert response["approval"] is not None, dialogue.diagnostic()
    assert dialogue.state["target"] == "LX00118"
    assert dialogue.state["amount"] == 50
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000


def test_singular_holding_reference_is_resolved_but_multiple_holdings_are_not():
    parsed = agent.local_parse("把这个产品全部赎回")
    prior = {"intent": "WEALTH_HOLDINGS", "product_ids": []}
    result = {"type": "WEALTH_HOLDINGS", "data": {"data": [{"product_id": "LX00118", "principal": 100}]}}
    resolved = agent.resolve_ellipsis(parsed, prior, "把这个产品全部赎回", result)
    assert resolved["target"] == "LX00118"
    assert resolved["slots"]["redeem_all"] is True
    result["data"]["data"].append({"product_id": "HQ30060", "principal": 1000})
    assert agent.resolve_ellipsis(parsed, prior, "把这个产品全部赎回", result)["target"] is None


def test_holding_reference_survives_the_gateway_new_turn_reset(dialogue):
    main.WEALTH_RISK_PROFILES["user_zhang"] = {"risk_profile": "C3", "loss_tolerance": "MEDIUM", "horizon_years": 3, "experience": "BASIC"}
    main.WEALTH_HOLDINGS["user_zhang"] = [{"holding_id": "HLD-SIM", "product_id": "LX00118", "product_name": "零星宝·活钱管家", "principal": 100, "estimated_value": 100, "purchased_at": datetime.now().isoformat()}]
    dialogue.say("查看我的持仓")
    assert dialogue.state["context"]["verified_product_ids"] == ["LX00118"]
    response = dialogue.say("把这个产品全部赎回")
    assert response["approval"] is not None, dialogue.diagnostic()
    assert dialogue.state["target"] == "LX00118"
    assert dialogue.state["amount"] == 100


def test_literal_ordinal_overrides_only_model_confidence_not_product_facts():
    parsed = {**agent.local_parse("那买前一个50元"), "confidence": 0.4}
    prior = {"intent": "WEALTH_COMPARE", "product_ids": ["LX00118", "HQ30060"]}
    result = agent.resolve_ellipsis(parsed, prior, "那买前一个50元")
    assert result["target"] == "LX00118" and result["confidence"] is None


def test_purchase_over_available_balance_is_refused_before_approval(dialogue):
    main.WEALTH_RISK_PROFILES["user_zhang"] = {"risk_profile": "C3", "loss_tolerance": "MEDIUM", "horizon_years": 3, "experience": "BASIC"}
    response = dialogue.say("申购LX00118 20000元")
    assert response["approval"] is None
    assert "余额不足" in response["reply"]
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000


def test_all_cards_blocked_cannot_fall_back_to_an_unbound_payment(dialogue):
    main.CARDS["user_zhang"][0]["status"] = "FROZEN"
    response = dialogue.say("给李四转10元")
    assert response["approval"] is None
    assert "卡片" in response["reply"]
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000


def test_implicit_cancel_of_multiple_subscriptions_asks_which(dialogue):
    response = dialogue.say("Apple和视频会员我都不想续费了")
    assert response["approval"] is None
    assert dialogue.state["clarification_required"]
    assert "商户" in response["reply"]
    response = dialogue.say("Apple")
    assert response["approval"] is not None, dialogue.diagnostic()
    assert dialogue.state["intent"] == "SUBSCRIPTION_CANCEL"
    assert dialogue.state["target"] == "Apple"
    assert all(row["status"] == "ACTIVE" for row in main.SUBSCRIPTIONS["user_zhang"])


def test_two_independent_reminders_are_not_silently_dropped(dialogue):
    for row in main.SUBSCRIPTIONS["user_zhang"]:
        row["next_charge"] = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
    response = dialogue.say("Apple订阅提前两天提醒我，视频会员提前三天提醒我")
    # Each yellow subscription step gets its own confirmation; none is dropped.
    approvals = 0
    while response["approval"] is not None:
        assert response["risk_level"] == "YELLOW", response
        approvals += 1
        response = dialogue.approve(response)
    assert approvals == 2, dialogue.diagnostic()
    reminders = dialogue.tasks()
    assert {(row["payload"]["merchant"], row["payload"]["days_before"]) for row in reminders} == {
        ("Apple 订阅", 2), ("视频会员", 3),
    }, dialogue.diagnostic()


def test_short_birthday_purchase_followup_does_not_claim_purchase(dialogue):
    response = dialogue.say("我爱人生日是2099-10-03，锁定1000元提醒订花和蛋糕")
    confirmed = dialogue.approve(response)
    assert "2099-10-01 09:00（北京时间）" in confirmed["reply"]
    assert "T01:00:00+00:00" not in confirmed["reply"]
    response = dialogue.say("已经买好了吗")
    assert response["approval"] is None
    assert "尚未购买" in response["reply"]
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000
    assert main.ACCOUNTS["user_zhang"]["locked_amount"] == 1000


def test_no_suspicious_transactions_is_not_reported_as_multiple(dialogue):
    main.TRANSACTIONS[:] = []
    with pytest.raises(main.SecurityError, match="未找到"):
        main.fraud_transaction("user_zhang")
