"""Natural language boundaries tested against real approvals and bill records."""
from datetime import datetime, timezone, timedelta
import json
from types import SimpleNamespace

import pytest

import agent
import conversation
import main
from intent_language import requested_date_range, split_tasks
from planner import filter_transactions, validate_intent_payload
from security import sanitize_text
from test_agent_gateway import gateway as _gateway_fixture, _headers, _send
from bank_agent import runtime


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setenv("BANK_AGENT_RATE_LIMIT_PER_MINUTE", "0")
    yield from _gateway_fixture.__wrapped__(monkeypatch)


def state(cid):
    thread = conversation.conversation_thread_id("user_zhang", cid)
    return conversation.get_graph().get_state(conversation.SERVICE._config(thread)).values


@pytest.mark.parametrize("text", [
    "给李四转100元或200元", "给李四转100或200元", "给李四转100元，不对，200元",
    "给李四转100元，不对，改成200元", "给李四转一百五元", "给李四转一千二元",
])
def test_ambiguous_money_never_issues_approval_and_exact_answer_recovers(gateway, text):
    client, _ = gateway
    cid = "money-conflict"
    pending = _send(client, text, conversation_id=cid)
    assert pending["business_status"] == "NEEDS_INPUT" and pending["approval"] is None
    assert state(cid)["amount"] is None
    assert "最终金额" in pending["reply"]
    repeated = _send(client, "100或200元", conversation_id=cid)
    assert repeated["approval"] is None and state(cid)["amount"] is None
    exact = _send(client, "200元", conversation_id=cid)
    assert exact["approval"] and state(cid)["amount"] == 200
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000


@pytest.mark.parametrize("text", ["不是给李四，是给王美转100元", "给李四或王美转100元", "给李四和王美转100元"])
def test_ambiguous_recipient_is_not_first_match_and_recovers(gateway, text):
    client, _ = gateway
    cid = "target-conflict"
    pending = _send(client, text, conversation_id=cid)
    assert pending["approval"] is None and "最终收款人" in pending["reply"]
    assert state(cid)["target"] is None
    clarified = _send(client, "王美", conversation_id=cid)
    assert clarified["approval"]
    assert state(cid)["target"] in {"王美", "user_wife"}
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000


@pytest.mark.parametrize("remark", ["查询余额", "不是我的钱", "不要转账", "冻结CARD-001", "然后给王美转200元", "下个月的账单"])
def test_memo_cannot_select_intent_or_recipient_or_schedule(gateway, remark):
    client, _ = gateway
    cid = "memo-boundary"
    text = "给李四转100元，备注：" + remark
    envelope = _send(client, text, conversation_id=cid)
    assert envelope["approval"]
    parsed = state(cid)
    assert parsed["intent"] == "TRANSFER" and parsed["amount"] == 100
    assert parsed["scheduled_at"] is None
    assert not parsed["context"].get("compound_workflow")
    assert parsed["extra_info"] == sanitize_text(text, field="指令", max_length=2000)
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000


def test_explicit_transfer_wins_over_payment_source_and_inquiries_stay_read_only(gateway):
    client, _ = gateway
    pending = _send(client, "用余额给李四转100元", conversation_id="source-balance")
    assert pending["approval"] and state("source-balance")["intent"] == "TRANSFER"
    inquiry = _send(client, "为什么我的卡被冻结了", conversation_id="card-question")
    assert inquiry["approval"] is None and state("card-question")["intent"] == "CARD_QUERY"
    assert inquiry["result"]["execution_result"]["type"] == "CARD_QUERY"


@pytest.mark.parametrize("text", ["如果余额够，就给李四转100元", "如何给李四转100元", "怎么给李四转100元"])
def test_condition_and_how_to_questions_do_not_authorize_a_transfer(gateway, text):
    client, _ = gateway
    envelope = _send(client, text, conversation_id="nonexecuting-question")
    assert envelope["approval"] is None
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000


@pytest.mark.parametrize("text", ["余额和本月账单都查一下", "查询余额，查看本月账单", "查一下余额、账单、银行卡"])
def test_natural_joined_reads_do_not_drop_tasks(gateway, text):
    client, _ = gateway
    envelope = _send(client, text, conversation_id="joined-reads")
    assert envelope["business_status"] == "SUCCEEDED"
    types = [item["type"] for item in envelope["response"]["sections"]]
    assert "BALANCE" in types and "BILL_QUERY" in types
    assert len(types) == (3 if "银行卡" in text else 2)


def test_comma_separated_writes_get_two_separate_approvals(gateway):
    client, _ = gateway
    first = _send(client, "给李四转100元，给王美转200元", conversation_id="comma-writes")
    flow = state("comma-writes")["context"]["compound_workflow"]
    assert len(flow["steps"]) == 2 and first["approval"]
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000
    second = client.post(f"/api/v1/agent/approvals/{first['approval']['approval_id']}", json={"decision": "approve"}, headers=_headers()).json()
    assert second["approval"] and second["approval"]["approval_id"] != first["approval"]["approval_id"]
    assert state("comma-writes")["amount"] == 200
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14900


def test_unknown_second_task_is_not_silently_dropped(gateway):
    client, _ = gateway
    result = _send(client, "查一下余额，然后帮我订机票", conversation_id="unsupported-task")
    assert result["business_status"] == "NEEDS_INPUT"
    assert not result["response"]["sections"]


@pytest.mark.parametrize("text,expected", [
    ("最近7天账单", {"start": "2026-09-03", "end": "2026-09-09"}),
    ("最近两周账单", {"start": "2026-08-27", "end": "2026-09-09"}),
    ("9月1日到9月15日账单", {"start": "2026-09-01", "end": "2026-09-15"}),
    ("2025-12-29到2026-01-03账单", {"start": "2025-12-29", "end": "2026-01-03"}),
    ("9月1日至15日账单", {"start": "2026-09-01", "end": "2026-09-15"}),
    ("上周账单", {"start": "2026-08-31", "end": "2026-09-06"}),
    ("昨天账单", {"start": "2026-09-08", "end": "2026-09-08"}),
    ("上周五账单", {"start": "2026-09-04", "end": "2026-09-04"}),
    ("明天账单", {"start": "2026-09-10", "end": "2026-09-10"}),
    ("去年9月账单", {"start": "2025-09-01", "end": "2025-09-30"}),
])
def test_calendar_range_is_deterministic(text, expected):
    assert requested_date_range(text, now=datetime(2026, 9, 9, 12, tzinfo=timezone(timedelta(hours=8)))) == expected


@pytest.mark.parametrize("text", ["9月15日到9月1日账单", "2月30日账单", "最近0天账单", "最近4000天账单", "上季度账单", "下季度账单", "前年账单", "周五账单", "今天或昨天账单", "9月1日到某天的账单"])
def test_invalid_or_unsupported_date_range_never_becomes_all_history(gateway, text):
    client, _ = gateway
    result = _send(client, text, conversation_id="invalid-dates")
    assert result["business_status"] == "NEEDS_INPUT"
    assert result["approval"] is None and not result["response"]["sections"]
    assert state("invalid-dates")["date_scope"] == "CUSTOM"
    fixed = _send(client, "2026-09-01到2026-09-15", conversation_id="invalid-dates")
    assert fixed["business_status"] == "SUCCEEDED"
    assert state("invalid-dates")["date_range"] == {"start": "2026-09-01", "end": "2026-09-15"}


def test_range_filters_records_and_survives_category_followup(gateway):
    client, _ = gateway
    main.TRANSACTIONS[:] = [
        {"tx_id": "R-IN", "account_id": "user_zhang", "time": "2026-09-02 12:00:00", "amount": 10, "category": "餐饮", "merchant": "午餐"},
        {"tx_id": "R-UTC-END", "account_id": "user_zhang", "time": "2026-09-15T15:59:59Z", "amount": 20, "category": "餐饮", "merchant": "晚餐"},
        {"tx_id": "R-OUT", "account_id": "user_zhang", "time": "2026-09-15T16:00:00Z", "amount": 999, "category": "餐饮", "merchant": "范围外"},
    ]
    first = _send(client, "查看2026年9月1日到2026年9月15日的账单", conversation_id="real-range")
    assert first["business_status"] == "SUCCEEDED"
    rows = first["result"]["execution_result"]["records"]
    assert {row["tx_id"] for row in rows} == {"R-IN", "R-UTC-END"}
    assert "2026-09-01 至 2026-09-15" in first["reply"]
    follow = _send(client, "餐饮花了多少", conversation_id="real-range")
    assert follow["business_status"] == "SUCCEEDED"
    assert follow["result"]["execution_result"]["facts"]["total_spent"] == 30
    assert state("real-range")["date_range"] == {"start": "2026-09-01", "end": "2026-09-15"}


def test_model_cannot_resolve_conflicting_facts_or_change_date_bounds(monkeypatch):
    monkeypatch.setenv("BANK_MODEL_FAST_PATH", "0")
    monkeypatch.setattr(runtime, "llm", SimpleNamespace(invoke=lambda _: SimpleNamespace(content=json.dumps({"intent": "TRANSFER", "target": "李四", "amount": 100, "confidence": 1, "date_scope": "ALL"}))))
    money = validate_intent_payload(agent.model_parse("给李四转100元或200元"))
    assert money["amount"] is None and money["slots"]["amount_ambiguous"] is True
    payee = validate_intent_payload(agent.model_parse("不是给李四，是给王美转100元"))
    assert payee["target"] is None
    query = validate_intent_payload(agent.model_parse("查询2026-09-01到2026-09-15的账单"))
    assert query["date_scope"] == "CUSTOM" and query["date_range"]["start"] == "2026-09-01"


def test_rules_report_evidence_without_a_fabricated_confidence():
    parsed = agent.local_parse("给李四转100元")
    assert parsed["confidence"] is None and parsed["parse_meta"]["fast_path_eligible"] is True
    uncertain = agent.local_parse("给李四转100元或200元")
    assert uncertain["parse_meta"]["fast_path_eligible"] is False
    assert "AMOUNT_AMBIGUOUS" in uncertain["parse_meta"]["reason_codes"]


def test_custom_filter_requires_valid_bounds_instead_of_widening_query():
    with pytest.raises(ValueError):
        filter_transactions([], "CUSTOM")
    with pytest.raises(ValueError):
        filter_transactions([], "CUSTOM", date_range={"start": "2026-09-15", "end": "2026-09-01"})


def test_contact_lists_product_pairs_and_thousand_separators_are_not_tasks():
    for text in ("和李四、王美AA300元", "比较LX00118和HQ30060", "给李四转1,000元", "给李四，转100元"):
        assert len(split_tasks(text)) == 1
    assert len(split_tasks("卡找到了，取消挂失CARD-001")) == 1


@pytest.mark.parametrize("text", ["冻结CARD-001和CARD-002", "申购LX00118或HQ30060 1000元"])
def test_multiple_business_objects_require_a_choice(gateway, text):
    client, _ = gateway
    result = _send(client, text, conversation_id="object-choice")
    assert result["approval"] is None
    assert "只选一" in result["reply"]
    assert ("卡片" if text.startswith("冻结") else "理财产品") in result["reply"]


@pytest.mark.parametrize("text,intent,target", [
    ("用CARD-001给李四转100元", "TRANSFER", "李四"),
    ("用CARD-001申购LX00118 500元", "WEALTH_PURCHASE", "LX00118"),
])
def test_paying_card_is_not_the_recipient_or_product(text, intent, target):
    parsed = agent.local_parse(text)
    assert parsed["intent"] == intent and parsed["target"] == target


@pytest.mark.parametrize("text", ["推荐适合我的理财，然后查一下余额", "我要做风险测评，然后查一下余额"])
def test_quiz_preserves_and_resumes_later_read_task(gateway, text):
    client, _ = gateway
    cid = "quiz-keeps-plan"
    first = _send(client, text, conversation_id=cid)
    assert first["business_status"] == "NEEDS_INPUT"
    assert first["response"]["workflow_plan"]["current_step"] == 1
    completed = _send(client, "1A 2A 3B 4A 5B 6B 7A 8A", conversation_id=cid)
    assert completed["business_status"] == "SUCCEEDED"
    assert completed["result"]["risk_quiz"].get("active") is False
    assert completed["response"]["workflow_plan"]["steps"][1]["status"] == "SUCCEEDED"
    assert "BALANCE" in [item["type"] for item in completed["response"]["sections"]]
