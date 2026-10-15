# ruff: noqa: F811  # imported pytest fixtures are injected by parameter name
"""Human dialogue acceptance: real graph + real in-process core, no live services.

Each scenario states the desired behaviour, including failures found during the
audit.  No failing behaviour is blessed with xfail or made into an expectation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

import agent
import conversation
import main
from planner import build_task_plan, validate_intent_payload
from test_agent_gateway import gateway, _headers, _send  # noqa: F401
from support import patch_everywhere
from bank_agent import runtime


PRINCIPAL = "user_zhang"


class _InProcessClient:
    def __init__(self, core: TestClient):
        self.core = core

    def confirm(self, auth_token, *, request_id=None):
        response = self.core.post(
            "/api/v1/auth/confirm", json={},
            headers={"X-User-Id": PRINCIPAL, "X-Auth-Token": auth_token, "X-Request-Id": request_id or "dialogue-confirm"},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def cancel(self, auth_token, *, request_id=None):
        response = self.core.post(
            "/api/v1/risk/cancel", json={},
            headers={"X-User-Id": PRINCIPAL, "X-Auth-Token": auth_token, "X-Request-Id": request_id or "dialogue-cancel"},
        )
        assert response.status_code == 200, response.text
        return response.json()


@dataclass
class Dialogue:
    core: TestClient
    graph: object
    service: conversation.ConversationService
    conversation_id: str = field(default_factory=lambda: f"human-{uuid4().hex}")
    turns: list[dict] = field(default_factory=list)

    @property
    def state(self):
        thread = conversation.conversation_thread_id(PRINCIPAL, self.conversation_id)
        return self.graph.get_state({"configurable": {"thread_id": thread}}).values

    def say(self, text: str):
        response = self.service.start_turn(
            principal=PRINCIPAL, conversation_id=self.conversation_id,
            message_id=f"m-{len(self.turns)}", text=text,
        )
        self.turns.append({
            "input": text, "status": response.get("status"), "intent": self.state.get("intent"),
            "date_scope": self.state.get("date_scope"), "reply": response.get("reply"),
            "target": self.state.get("target"), "amount": self.state.get("amount"),
        })
        return response

    def diagnostic(self):
        return json.dumps(self.turns, ensure_ascii=False, indent=2)

    def execution(self, expected_type: str):
        execution = self.state.get("execution_result") or {}
        assert execution.get("type") == expected_type, self.diagnostic()
        return execution

    def approve(self, response):
        assert response.get("status") == "APPROVAL_REQUIRED", self.diagnostic()
        return self.service.resolve_approval(
            principal=PRINCIPAL,
            approval_id=response["approval"]["approval_id"], decision="approve",
        )

    def tasks(self):
        response = self.core.get("/api/v1/tasks", headers={"X-User-Id": PRINCIPAL})
        assert response.status_code == 200, response.text
        return response.json()["data"]


@pytest.fixture
def dialogue(monkeypatch):
    monkeypatch.setenv("BANK_CONVERSATION_DB", "memory")
    monkeypatch.setenv("BANK_APPROVAL_STORE_PATH", "memory")
    monkeypatch.setattr(runtime, "llm", None)
    main.reset_state()
    graph = agent.build_graph()
    monkeypatch.setattr(conversation, "_GRAPH", graph)
    monkeypatch.setattr(conversation, "_GRAPH_BACKEND", "memory")
    with TestClient(main.app) as core:
        def fake_http(method, path, *, state, json_body=None, headers=None):
            merged = {"X-Request-Id": state.get("request_id") or "human-dialogue"}
            merged.update(headers or {})
            return core.request(method, "/api/v1/" + path.lstrip("/"), json=json_body, headers=merged)

        monkeypatch.setattr(runtime, "_http", fake_http)
        service = conversation.ConversationService(client_factory=lambda *_: _InProcessClient(core))
        value = Dialogue(core, graph, service)
        yield value
        service.reset()
    main.reset_state()


@pytest.fixture
def bills(dialogue):
    now = datetime.now()
    previous = (now.replace(day=1) - timedelta(days=1)).replace(day=15)
    rows = [
        {"tx_id": "H-NOW-FOOD", "category": "餐饮", "merchant": "餐饮商户", "amount": 40.0, "time": now.strftime("%Y-%m-%d 10:00:00")},
        {"tx_id": "H-NOW-SHOP", "category": "购物", "merchant": "书店", "amount": 100.0, "time": now.strftime("%Y-%m-%d 11:00:00")},
        {"tx_id": "H-NOW-REFUND", "category": "退款", "merchant": "书店退款", "amount": -10.0, "time": now.strftime("%Y-%m-%d 12:00:00")},
        {"tx_id": "H-PREV-FOOD", "category": "餐饮", "merchant": "上月餐饮商户", "amount": 70.0, "time": previous.strftime("%Y-%m-%d 10:00:00")},
        {"tx_id": "H-PREV-RISK", "category": "可疑交易", "merchant": "境外未知商户", "amount": 999.0, "time": previous.strftime("%Y-%m-%d 03:00:00"), "country": "US", "card_id": "CARD-001"},
        {"tx_id": "H-OLD", "category": "餐饮", "merchant": "往年商户", "amount": 777.0, "time": f"{now.year - 1}-07-15 10:00:00"},
    ]
    main.TRANSACTIONS[:] = [{"account_id": PRINCIPAL, **row} for row in rows]
    main.TRANSACTIONS.append({"account_id": "user_li", "tx_id": "H-OTHER-OWNER", "category": "餐饮", "merchant": "他人商户", "amount": 9000.0, "time": now.strftime("%Y-%m-%d 10:00:00")})
    return dialogue


def _assert_scope(dialogue, scope):
    execution = dialogue.execution("BILL_ANALYSIS")
    assert execution["date_scope"] == scope, dialogue.diagnostic()
    now = datetime.now()
    previous = now.replace(day=1) - timedelta(days=1)
    expected = {
        "MONTH": {"H-NOW-FOOD", "H-NOW-SHOP", "H-NOW-REFUND"},
        "LAST_MONTH": {"H-PREV-FOOD", "H-PREV-RISK"},
        "YEAR": {"H-NOW-FOOD", "H-NOW-SHOP", "H-NOW-REFUND"} | ({"H-PREV-FOOD", "H-PREV-RISK"} if previous.year == now.year else set()),
    }[scope]
    assert set(execution["facts"]["source_tx_ids"]) == expected, dialogue.diagnostic()
    return execution["facts"]


def test_month_last_month_and_year_can_change_without_repeating_business(bills):
    for text, scope in [("分析本月账单", "MONTH"), ("那上个月呢", "LAST_MONTH"), ("今年整体呢", "YEAR")]:
        bills.say(text)
        _assert_scope(bills, scope)


def test_query_can_be_followed_by_category_analysis_without_losing_month(bills):
    first = bills.say("查看本月交易记录")
    bills.execution("BILL_QUERY")
    assert "没有声称" not in first["reply"], bills.diagnostic()
    assert "书店退款" in first["reply"] and "10.00" in first["reply"], bills.diagnostic()
    bills.say("按类别分析一下")
    facts = _assert_scope(bills, "MONTH")
    assert facts["total_spent"] == 140.0
    assert facts["refund_total"] == 10.0
    assert facts["net_spent"] == 130.0


def test_food_followup_uses_the_same_month_and_verified_amount(bills):
    bills.say("这个月钱都花哪儿去了")
    _assert_scope(bills, "MONTH")
    response = bills.say("餐饮花了多少")
    execution = bills.execution("BILL_ANALYSIS")
    assert execution["date_scope"] == "MONTH", bills.diagnostic()
    food = [row for row in execution["facts"]["category_breakdown"] if row["category"] == "餐饮"]
    assert food and food[0]["amount"] == 40.0, bills.diagnostic()
    assert "餐饮" in response["reply"] and "40.00" in response["reply"], bills.diagnostic()
    assert execution["category"] == "餐饮"
    assert execution["facts"]["total_spent"] == 40.0
    assert execution["facts"]["source_tx_ids"] == ["H-NOW-FOOD"]
    assert "140.00" not in response["reply"] and "130.00" not in response["reply"]


@pytest.mark.parametrize("utterance", ["餐饮花了多少", "查看餐饮交易记录"])
def test_category_answer_reports_200_instead_of_whole_month_3555(dialogue, utterance):
    now = datetime.now()
    previous = now.replace(day=1) - timedelta(days=1)
    main.TRANSACTIONS[:] = [
        {"account_id": PRINCIPAL, "tx_id": tx_id, "category": category, "merchant": merchant,
         "amount": amount, "time": date.strftime("%Y-%m-%d 12:00:00")}
        for tx_id, category, merchant, amount, date in (
            ("FOOD-120", "餐饮", "午餐", 120, now),
            ("FOOD-80", "餐饮", "晚餐", 80, now),
            ("OTHER-3355", "购物", "购物商户", 3355, now),
            ("OLD-50", "餐饮", "上月餐饮", 50, previous),
        )
    ]
    dialogue.say("分析本月账单")
    response = dialogue.say(utterance)
    execution = dialogue.state["execution_result"]
    assert execution["date_scope"] == "MONTH", dialogue.diagnostic()
    assert execution["category"] == "餐饮", dialogue.diagnostic()
    assert execution["facts"]["total_spent"] == 200
    assert execution["facts"]["source_tx_ids"] == ["FOOD-120", "FOOD-80"]
    assert {row["category"] for row in execution["records"]} == {"餐饮"}
    assert "餐饮" in response["reply"] and "200.00" in response["reply"], dialogue.diagnostic()
    assert all(text not in response["reply"] for text in ("3,555", "3555", "3,355", "3355", "上月餐饮"))


@pytest.mark.parametrize("utterance", ["分析本月交通支出", "查看本月交通交易记录"])
def test_empty_category_reports_no_matching_transactions_without_other_totals(bills, utterance):
    response = bills.say(utterance)
    execution = bills.state["execution_result"]
    assert execution["date_scope"] == "MONTH"
    assert execution["category"] == "交通"
    assert execution["records"] == []
    assert execution["facts"]["total_spent"] == 0
    assert all(text in response["reply"] for text in ("本月", "交通", "暂无")), bills.diagnostic()
    assert "140.00" not in response["reply"]


def test_explicit_food_scope_does_not_include_current_month_or_other_categories(bills):
    response = bills.say("分析上个月餐饮花了多少")
    execution = bills.execution("BILL_ANALYSIS")
    assert execution["date_scope"] == "LAST_MONTH"
    assert execution["category"] == "餐饮"
    assert execution["facts"]["source_tx_ids"] == ["H-PREV-FOOD"]
    assert execution["facts"]["total_spent"] == 70
    assert "上月" in response["reply"] and "70.00" in response["reply"]
    assert "999" not in response["reply"] and "777" not in response["reply"]


def test_anomaly_followup_does_not_widen_last_month_to_all_history(bills):
    bills.say("分析上月账单")
    facts = _assert_scope(bills, "LAST_MONTH")
    assert [row["tx_id"] for row in facts["anomalies"]] == ["H-PREV-RISK"]
    bills.say("里面有没有异常交易")
    _assert_scope(bills, "LAST_MONTH")


def test_question_about_overseas_spend_does_not_imply_fraud_report(bills):
    bills.say("分析上月账单")
    response = bills.say("那笔境外消费怎么回事")
    assert response["status"] != "APPROVAL_REQUIRED", bills.diagnostic()
    assert bills.state["intent"] in {"BILL_QUERY", "BILL_ANALYSIS"}, bills.diagnostic()
    assert "999" in response["reply"] or "H-PREV-RISK" in response["reply"], bills.diagnostic()
    assert all(card["status"] == "ACTIVE" for card in main.CARDS[PRINCIPAL])
    assert not main.DISPUTE_CASES


def test_repeat_read_reuses_the_last_month_scope(bills):
    bills.say("分析上月账单")
    bills.say("再看一遍")
    _assert_scope(bills, "LAST_MONTH")


def test_annual_report_can_switch_back_to_current_month(bills):
    bills.say("请给我今年年度账单报告")
    _assert_scope(bills, "YEAR")
    bills.say("换成本月")
    _assert_scope(bills, "MONTH")


def test_vague_recurring_charge_question_lists_real_subscriptions(dialogue):
    response = dialogue.approve(dialogue.say("帮我看看有没有乱扣费"))
    execution = dialogue.execution("SUBSCRIPTION_QUERY")
    rows = execution["data"]["data"]
    assert {row["subscription_id"] for row in rows if row["status"] == "ACTIVE"} == {"SUB-APPLE", "SUB-VIDEO"}
    assert any(row["merchant"] == "云端存储" and row["status"] == "CANDIDATE" for row in rows)
    assert "Apple" in response["reply"] and "视频会员" in response["reply"]
    assert "15.00" in response["reply"] and "25.00" in response["reply"]
    assert "下次扣款" in response["reply"]


def test_ambiguous_subscription_cancel_asks_then_accepts_merchant_fragment(dialogue):
    dialogue.approve(dialogue.say("我有哪些订阅"))
    ambiguous = dialogue.say("把它取消掉")
    assert ambiguous["status"] != "APPROVAL_REQUIRED", dialogue.diagnostic()
    assert dialogue.state["clarification_required"] is True, dialogue.diagnostic()
    assert dialogue.state.get("target") is None
    resolved = dialogue.say("Apple那个")
    assert resolved["status"] == "APPROVAL_REQUIRED", dialogue.diagnostic()
    assert dialogue.state["target"] == "Apple", dialogue.diagnostic()
    assert all(row["status"] == "ACTIVE" for row in main.SUBSCRIPTIONS[PRINCIPAL])


def test_generic_cancel_phrase_is_not_mistaken_for_a_merchant_name(dialogue):
    first = dialogue.say("取消自动扣费")
    assert dialogue.state["clarification_required"] is True, dialogue.diagnostic()
    assert dialogue.state.get("target") is None, dialogue.diagnostic()
    assert first["status"] != "APPROVAL_REQUIRED"
    next_turn = dialogue.say("视频会员")
    assert next_turn["status"] == "APPROVAL_REQUIRED", dialogue.diagnostic()
    assert dialogue.state["target"] == "视频会员"


def test_renewal_followup_stays_in_subscription_context(dialogue):
    dialogue.approve(dialogue.say("我有哪些订阅"))
    response = dialogue.approve(dialogue.say("下次什么时候续费"))
    dialogue.execution("SUBSCRIPTION_QUERY")
    for row in main.SUBSCRIPTIONS[PRINCIPAL]:
        assert row["merchant"] in response["reply"] and row["next_charge"] in response["reply"], dialogue.diagnostic()


def test_requested_apple_renewal_reminder_is_a_real_task_not_a_false_claim(dialogue):
    response = dialogue.approve(dialogue.say("Apple续费前两天提醒我"))
    reminders = [task for task in dialogue.tasks() if task["task_type"] == "RENEWAL_REMINDER"]
    assert reminders, dialogue.diagnostic()
    assert {task["payload"]["subscription_id"] for task in reminders} == {"SUB-APPLE"}, dialogue.diagnostic()
    charge = datetime.fromisoformat(main.SUBSCRIPTIONS[PRINCIPAL][0]["next_charge"])
    assert datetime.fromisoformat(reminders[0]["run_at"]).date() == (charge - timedelta(days=2)).date()
    assert "提醒" in response["reply"]
    assert main.ACCOUNTS[PRINCIPAL]["balance"] == 15000.0


def test_cancel_then_refresh_shows_cancelled_merchant_without_affecting_other(dialogue):
    pending = dialogue.say("取消Apple订阅")
    completed = dialogue.approve(pending)
    assert "已取消" in completed["reply"]
    response = dialogue.approve(dialogue.say("再看看我的订阅"))
    execution = dialogue.execution("SUBSCRIPTION_QUERY")
    subscriptions = {row["subscription_id"]: row for row in execution["data"]["data"]}
    assert subscriptions["SUB-APPLE"]["status"] == "CANCELLED"
    assert subscriptions["SUB-VIDEO"]["status"] == "ACTIVE"
    assert "Apple" in response["reply"] and ("CANCELLED" in response["reply"] or "已取消" in response["reply"])
    apple_line = next(line for line in response["reply"].splitlines() if line.startswith("- Apple"))
    assert "已取消" in apple_line and "原计划扣款" in apple_line
    assert "下次扣款" not in apple_line
    assert "取消 Apple 订阅" not in response["reply"]


def test_missing_birthday_asks_for_date_and_keeps_budget_when_answered(dialogue):
    dialogue.say("我爱人生日快到了，帮我锁定1000元，生日前两天提醒我订花和蛋糕")
    assert dialogue.state["clarification_required"] is True, dialogue.diagnostic()
    assert "生日日期" in dialogue.state["final_response"]
    assert main.ACCOUNTS[PRINCIPAL]["locked_amount"] == 0
    response = dialogue.say("2099-10-03")
    assert response["status"] == "APPROVAL_REQUIRED", dialogue.diagnostic()
    assert dialogue.state["amount"] == 1000.0
    assert str(dialogue.state["event_at"]).startswith("2099-10-03")
    assert main.ACCOUNTS[PRINCIPAL]["locked_amount"] == 0


def test_missing_birthday_budget_is_asked_for_instead_of_defaulting(dialogue):
    dialogue.say("我爱人生日是2099-10-03，提前两天提醒订花和蛋糕")
    assert dialogue.state["clarification_required"] is True, dialogue.diagnostic()
    assert dialogue.state.get("amount") is None, dialogue.diagnostic()
    assert "金额" in dialogue.state["final_response"] or "预算" in dialogue.state["final_response"]
    response = dialogue.say("1000元")
    assert response["status"] == "APPROVAL_REQUIRED", dialogue.diagnostic()
    assert dialogue.state["amount"] == 1000.0
    assert str(dialogue.state["event_at"]).startswith("2099-10-03")


def test_missing_birthday_recipient_does_not_silently_assume_spouse(dialogue):
    response = dialogue.say("过生日用，锁定1000元，2099-10-03前两天提醒订花和蛋糕")
    assert dialogue.state["clarification_required"] is True, dialogue.diagnostic()
    assert response["status"] != "APPROVAL_REQUIRED", dialogue.diagnostic()
    assert any(word in response["reply"] for word in ("谁", "对象", "哪位")), dialogue.diagnostic()
    clarified = dialogue.say("是我爱人")
    assert clarified["status"] == "APPROVAL_REQUIRED", dialogue.diagnostic()
    assert dialogue.state["target"] in {"爱人", "王美"}


def test_birthday_completion_creates_two_day_drafts_and_followup_never_claims_purchased(dialogue):
    pending = dialogue.say("我爱人生日是2099-10-03，锁定1000元，提前两天提醒我买花和蛋糕")
    completed = dialogue.approve(pending)
    tasks = dialogue.tasks()
    reminders = [task for task in tasks if task["task_type"] == "REMINDER"]
    drafts = [task for task in tasks if task["task_type"] == "ORDER_DRAFT"]
    assert len(reminders) == 1 and reminders[0]["run_at"].startswith("2099-10-01")
    assert {task["payload"]["item"] for task in drafts} == {"鲜花", "蛋糕"}
    assert all(task["status"] == "PENDING" for task in drafts)
    assert main.ACCOUNTS[PRINCIPAL]["locked_amount"] == 1000.0
    assert main.ACCOUNTS[PRINCIPAL]["balance"] == 15000.0
    assert not any(phrase in completed["reply"] for phrase in ("已购买", "已下单", "已经买好", "已支付"))
    followup = dialogue.say("花和蛋糕已经买好了吗")
    assert any(word in followup["reply"] for word in ("草稿", "待确认", "尚未", "没有购买", "未购买")), dialogue.diagnostic()
    assert not any(phrase in followup["reply"] for phrase in ("已经买好", "已经购买", "已支付")), dialogue.diagnostic()
    assert main.ACCOUNTS[PRINCIPAL]["balance"] == 15000.0


def test_bare_number_cannot_satisfy_the_missing_birthday_date(dialogue):
    dialogue.say("我爱人生日快到了，锁定1000元提醒订花")
    response = dialogue.say("3000")
    assert dialogue.state["clarification_required"] is True, dialogue.diagnostic()
    assert "生日日期" in response["reply"]
    assert response["status"] != "APPROVAL_REQUIRED"
    assert not dialogue.state.get("event_at")
    assert main.ACCOUNTS[PRINCIPAL]["locked_amount"] == 0


def test_plain_language_cancellation_withdraws_pending_birthday_approval(dialogue):
    pending = dialogue.say("我爱人生日是2099-10-03，锁定1000元提前两天提醒订花和蛋糕")
    assert pending["status"] == "APPROVAL_REQUIRED", dialogue.diagnostic()
    stopped = dialogue.say("先别办了")
    assert stopped["status"] != "APPROVAL_REQUIRED", dialogue.diagnostic()
    assert any(word in stopped["reply"] for word in ("取消", "暂不", "停止")), dialogue.diagnostic()
    assert main.ACCOUNTS[PRINCIPAL]["locked_amount"] == 0
    assert not dialogue.tasks()


def test_next_week_weekday_after_friday_does_not_skip_an_extra_week(dialogue, monkeypatch):
    anchor = datetime.now(timezone(timedelta(hours=8))) + timedelta(days=3650)
    friday = (anchor + timedelta(days=(4 - anchor.weekday()) % 7)).replace(hour=12, minute=0, second=0, microsecond=0)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return friday if tz is None else friday.astimezone(tz)

    patch_everywhere(monkeypatch, "bank_agent", "datetime", FixedDatetime)
    dialogue.say("我爱人生日快到了，锁定1000元生日前两天提醒订花和蛋糕")
    response = dialogue.say("下周三")
    assert response["status"] == "APPROVAL_REQUIRED", dialogue.diagnostic()
    expected = (friday + timedelta(days=5)).date()
    assert datetime.fromisoformat(dialogue.state["event_at"]).date() == expected, dialogue.diagnostic()


def test_selected_renewal_endpoint_honors_days_and_is_idempotent(dialogue):
    path = "/api/v1/subscriptions/user_zhang/reminders?merchant=Apple&days_before=3"
    first = dialogue.core.post(path, headers={"X-User-Id": PRINCIPAL})
    assert first.status_code == 200, first.text
    tasks = first.json()["data"]
    assert len(tasks) == 1 and tasks[0]["payload"]["subscription_id"] == "SUB-APPLE"
    charge = datetime.fromisoformat(main.SUBSCRIPTIONS[PRINCIPAL][0]["next_charge"])
    assert datetime.fromisoformat(tasks[0]["run_at"]).date() == (charge - timedelta(days=3)).date()
    repeated = dialogue.core.post(path, headers={"X-User-Id": PRINCIPAL})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["data"][0]["task_id"] == tasks[0]["task_id"]
    assert len(dialogue.tasks()) == 1
    assert main.ACCOUNTS[PRINCIPAL]["balance"] == 15000.0


@pytest.mark.parametrize("query", ["merchant=不存在的商户", "merchant=Apple&days_before=-1", "merchant=Apple&days_before=366"])
def test_invalid_renewal_request_creates_no_tasks(dialogue, query):
    response = dialogue.core.post(f"/api/v1/subscriptions/user_zhang/reminders?{query}", headers={"X-User-Id": PRINCIPAL})
    assert response.status_code in {400, 404, 422}, response.text
    assert not dialogue.tasks()


def test_reminder_slots_are_opt_in_and_plan_matches_the_requested_action():
    parsed = validate_intent_payload({"intent": "SUBSCRIPTION_QUERY", "target": "Apple", "slots": {"renewal_reminder": True, "days_before": 3}})
    plan = build_task_plan(parsed)
    assert plan.tasks[0].action == "SUBSCRIPTION_REMINDER"
    assert plan.tasks[0].details["days_before"] == 3
    for slots in ({"renewal_reminder": "true", "days_before": 2}, {"renewal_reminder": False, "days_before": 2}):
        plain = validate_intent_payload({"intent": "SUBSCRIPTION_QUERY", "slots": slots})
        assert "renewal_reminder" not in plain["slots"]
    for days in (-1, 366, True, "2"):
        with pytest.raises(ValueError):
            validate_intent_payload({"intent": "SUBSCRIPTION_QUERY", "slots": {"renewal_reminder": True, "days_before": days}})


def test_reminder_reply_requires_real_task_fields(dialogue):
    response = dialogue.core.post("/api/v1/subscriptions/user_zhang/reminders?merchant=Apple", headers={"X-User-Id": PRINCIPAL})
    assert response.status_code == 200, response.text
    payload = response.json()
    state = {"user_id": PRINCIPAL, "execution_result": {"type": "SUBSCRIPTION_REMINDER", "data": payload}}
    reply = agent.responder_node(state)["final_response"]
    assert payload["data"][0]["task_id"] in reply
    assert payload["data"][0]["run_at"] in reply
    assert "不会自动扣费" in reply
    payload["data"][0].pop("run_at")
    malformed = agent.responder_node(state)["final_response"]
    assert "没有声称" in malformed


def test_repeated_same_subscription_reminder_keeps_merchant_days_and_single_task(dialogue):
    dialogue.approve(dialogue.say("Apple订阅续费前两天提醒我"))
    original = [task for task in dialogue.tasks() if task["task_type"] == "RENEWAL_REMINDER"]
    assert len(original) == 1, dialogue.diagnostic()
    response = dialogue.approve(dialogue.say("同样的提醒再设置一次"))
    execution = dialogue.execution("SUBSCRIPTION_REMINDER")
    assert dialogue.state["target"] == "Apple", dialogue.diagnostic()
    assert dialogue.state["slots"]["days_before"] == 2, dialogue.diagnostic()
    repeated = [task for task in dialogue.tasks() if task["task_type"] == "RENEWAL_REMINDER"]
    assert len(repeated) == 1
    assert repeated[0]["task_id"] == original[0]["task_id"]
    assert repeated[0]["payload"]["merchant"] == "Apple 订阅"
    assert repeated[0]["payload"]["days_before"] == 2
    assert execution["data"]["data"][0]["task_id"] == original[0]["task_id"]
    assert original[0]["task_id"] in response["reply"]
    assert main.ACCOUNTS[PRINCIPAL]["balance"] == 15000.0


def _send_confirmed(client, message, *, conversation_id):
    """Subscription queries are yellow: confirm the approval card, then read the result."""
    pending = _send(client, message, conversation_id=conversation_id)
    assert pending["status"] == "APPROVAL_REQUIRED", pending
    assert pending["risk_level"] == "YELLOW", pending
    completed = client.post(f"/api/v1/agent/approvals/{pending['approval']['approval_id']}", json={"decision": "approve"}, headers=_headers())
    assert completed.status_code == 200, completed.text
    return completed.json()


@pytest.fixture
def display_gateway(gateway, monkeypatch):
    import agent_api

    monkeypatch.setattr(agent_api, "RATE_LIMIT_STATE", {})
    monkeypatch.setattr(runtime, "llm", None)
    return gateway


def test_gateway_reminder_plan_completes_while_real_delivery_stays_pending(display_gateway):
    client, core = display_gateway
    task_ids = []
    for message in ("Apple续费前两天提醒我", "同样的提醒再设置一次"):
        response = _send_confirmed(client, message, conversation_id="reminder-plan-display")
        assert response["status"] == "COMPLETED", response
        execution = response["result"]["execution_result"]
        assert execution["type"] == "SUBSCRIPTION_REMINDER"
        assert execution["data"]["status"] == "CREATED"
        tasks = response["result"]["plan"]["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["action"] == "SUBSCRIPTION_REMINDER"
        assert tasks[0]["status"] == "SUCCEEDED", tasks
        created = execution["data"]["data"]
        assert len(created) == 1 and created[0]["status"] == "PENDING"
        task_ids.append(created[0]["task_id"])
        assert task_ids[-1] in response["reply"]
    assert task_ids[0] == task_ids[1]
    stored = core.get("/api/v1/tasks", headers=_headers()).json()["data"]
    reminders = [task for task in stored if task["task_type"] == "RENEWAL_REMINDER"]
    assert len(reminders) == 1 and reminders[0]["status"] == "PENDING"
    assert main.ACCOUNTS[PRINCIPAL]["balance"] == 15000.0


def test_gateway_rejected_reminder_plan_is_failed_without_creating_a_task(display_gateway):
    client, core = display_gateway
    main.SUBSCRIPTIONS[PRINCIPAL] = [row for row in main.SUBSCRIPTIONS[PRINCIPAL] if row["subscription_id"] != "SUB-APPLE"]
    response = _send_confirmed(client, "Apple续费前两天提醒我", conversation_id="reminder-plan-failed")
    task = response["result"]["plan"]["tasks"][0]
    assert task["action"] == "SUBSCRIPTION_REMINDER"
    assert task["status"] == "FAILED", response
    assert task["error"]
    assert core.get("/api/v1/tasks", headers=_headers()).json()["data"] == []
    assert main.ACCOUNTS[PRINCIPAL]["balance"] == 15000.0


@pytest.mark.parametrize("cancel_all", [False, True])
def test_gateway_cancelled_subscriptions_show_historical_dates_and_active_only_suggestions(display_gateway, cancel_all):
    client, _ = display_gateway
    cid = f"cancelled-subscription-display-{cancel_all}"
    for merchant in (["Apple", "视频会员"] if cancel_all else ["Apple"]):
        pending = _send(client, f"取消{merchant}订阅", conversation_id=cid)
        assert pending["status"] == "APPROVAL_REQUIRED", pending
        completed = client.post(
            f"/api/v1/agent/approvals/{pending['approval']['approval_id']}",
            json={"decision": "approve"}, headers=_headers(),
        )
        assert completed.status_code == 200 and completed.json()["status"] == "COMPLETED"
    response = _send_confirmed(client, "再看看我的订阅", conversation_id=cid)
    reply = response["reply"]
    apple_line = next(line for line in reply.splitlines() if line.startswith("- Apple"))
    assert "已取消" in apple_line and "原计划扣款" in apple_line
    assert "下次扣款" not in apple_line and "取消 Apple 订阅" not in reply
    if cancel_all:
        assert "当前没有进行中的订阅" in reply and "下次扣款" not in reply
        assert "如需停用" not in reply
        assert all(row["status"] == "CANCELLED" for row in main.SUBSCRIPTIONS[PRINCIPAL])
    else:
        video_line = next(line for line in reply.splitlines() if line.startswith("- 视频会员"))
        assert "生效中" in video_line and "下次扣款" in video_line
        assert "取消 视频会员 订阅" in reply
        assert main.SUBSCRIPTIONS[PRINCIPAL][1]["status"] == "ACTIVE"
    assert main.ACCOUNTS[PRINCIPAL]["balance"] == 15000.0


@pytest.mark.parametrize("clarify_date", [False, True])
def test_gateway_birthday_approval_displays_beijing_time_without_changing_event(display_gateway, clarify_date):
    client, _ = display_gateway
    cid = f"birthday-display-{clarify_date}"
    if clarify_date:
        first = _send(client, "我爱人生日快到了，锁定1000元，提前两天提醒买花和蛋糕", conversation_id=cid)
        assert first["approval"] is None
        response = _send(client, "2099-10-03", conversation_id=cid)
    else:
        response = _send(client, "我爱人生日是2099-10-03，锁定1000元，提前两天提醒买花和蛋糕", conversation_id=cid)
    assert response["status"] == "APPROVAL_REQUIRED", response
    for text in (response["reply"], response["approval"]["summary"]):
        assert "2099-10-03 09:00" in text and "北京时间" in text, text
        assert "T01:00:00+00:00" not in text
        assert "尚未购买" in text
    thread = conversation.conversation_thread_id(PRINCIPAL, cid)
    graph = conversation.get_graph()
    config = {"configurable": {"thread_id": thread}}
    assert graph.get_state(config).values["event_at"] == "2099-10-03T01:00:00+00:00"
    assert main.ACCOUNTS[PRINCIPAL]["locked_amount"] == 0
    approved = client.post(
        f"/api/v1/agent/approvals/{response['approval']['approval_id']}",
        json={"decision": "approve"}, headers=_headers(),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "COMPLETED"
    assert main.ACCOUNTS[PRINCIPAL]["locked_amount"] == 1000
    assert graph.get_state(config).values["event_at"] == "2099-10-03T01:00:00+00:00"


def _future_month_day() -> tuple[str, datetime]:
    """A spoken month-day ("11月10号") that is still ahead of today."""

    upcoming = datetime.now(timezone(timedelta(hours=8))) + timedelta(days=40)
    return f"{upcoming.month}月{upcoming.day}号", upcoming


def test_birthday_stated_as_month_day_needs_no_year(dialogue):
    phrase, upcoming = _future_month_day()
    response = dialogue.say(f"{phrase}是我爱人生日，帮我锁定1000元，生日前两天提醒我订花和蛋糕")
    assert response["status"] == "APPROVAL_REQUIRED", dialogue.diagnostic()
    event_at = datetime.fromisoformat(str(dialogue.state["event_at"]))
    assert (event_at.month, event_at.day) == (upcoming.month, upcoming.day)
    assert dialogue.state["amount"] == 1000.0
    assert main.ACCOUNTS[PRINCIPAL]["locked_amount"] == 0


def test_bare_month_day_answer_fills_the_missing_birthday_date(dialogue):
    phrase, upcoming = _future_month_day()
    dialogue.say("我爱人生日快到了，帮我锁定1000元，生日前两天提醒我订花和蛋糕")
    assert dialogue.state["clarification_required"] is True, dialogue.diagnostic()
    # The question must say what an acceptable answer looks like.
    assert "生日日期" in dialogue.state["final_response"]
    assert "10月5号" in dialogue.state["final_response"]
    assert main.ACCOUNTS[PRINCIPAL]["locked_amount"] == 0

    response = dialogue.say(phrase)
    assert response["status"] == "APPROVAL_REQUIRED", dialogue.diagnostic()
    assert dialogue.state["amount"] == 1000.0
    event_at = datetime.fromisoformat(str(dialogue.state["event_at"]))
    assert (event_at.month, event_at.day) == (upcoming.month, upcoming.day)
    assert main.ACCOUNTS[PRINCIPAL]["locked_amount"] == 0
