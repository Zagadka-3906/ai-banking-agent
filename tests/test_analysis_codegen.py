# ruff: noqa: F811  # imported pytest fixtures are injected by parameter name
"""Open bill questions are answered by generated code run in the sandbox."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import sandbox
from bank_agent import analysis_codegen, runtime
from security import get_audit_logs

pytest.importorskip("langgraph")

from test_natural_bills_subscriptions_events import bills, dialogue  # noqa: F401,E402

ROWS = [
    {"tx_id": "1", "amount": 28.0, "category": "餐饮", "merchant": "咖啡店", "time": "2026-10-01 09:15:00"},
    {"tx_id": "2", "amount": 45.0, "category": "餐饮", "merchant": "美团外卖", "time": "2026-08-01 12:30:00"},
    {"tx_id": "3", "amount": 15.0, "category": "订阅代扣", "merchant": "Apple 订阅", "time": "2026-08-03 08:00:00"},
    {"tx_id": "4", "amount": 999.0, "category": "可疑交易", "merchant": "境外未知商户", "time": "2026-08-07 03:15:00"},
    {"tx_id": "5", "amount": -10.0, "category": "退款", "merchant": "书店退款", "time": "2026-10-02 12:00:00"},
]


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    monkeypatch.setattr(runtime, "llm", None)


@pytest.mark.parametrize("question, expected", [
    ("餐饮和订阅代扣一共花了多少", "餐饮、订阅代扣合计支出：¥88.00"),
    ("哪个月花得最多", "支出最多的月份：2026-08（¥1,059.00）"),
    ("哪天花得最少", "支出最少的一天：2026-08-03（¥15.00）"),
    ("最大一笔消费是什么", "单笔最大的消费：2026-08-07 境外未知商户 ¥999.00"),
    ("超过30元的有几笔", "超过 ¥30.00 的交易：2 笔"),
    ("平均每笔花多少", "平均每笔支出：¥271.75"),
    ("在美团花了多少", "在“美团”的支出：¥45.00"),
    ("哪个商户花得最多", "花费最多的商户：境外未知商户（¥999.00）"),
])
def test_rule_templates_answer_in_the_sandbox(question, expected):
    answer = analysis_codegen.answer_bill_question(question, ROWS)
    assert answer is not None
    assert answer["answer"] == expected
    assert answer["source"] == "rule"
    assert answer["engine"] == "sandbox"


@pytest.mark.parametrize("question", ["我的余额", "查一下账单", "在不存在的商户花了多少"])
def test_questions_without_a_template_get_no_answer_offline(question):
    assert analysis_codegen.answer_bill_question(question, ROWS) is None


class _FakeModel:
    def __init__(self, content: str):
        self.content = content

    def invoke(self, messages):
        del messages
        return SimpleNamespace(content=self.content)


def test_model_expression_runs_in_the_sandbox(monkeypatch):
    monkeypatch.setattr(runtime, "llm", _FakeModel(
        '{"expression": "len(filter_rows(data, \'category\', \'餐饮\'))", "kind": "count", "label": "餐饮交易笔数"}'))
    answer = analysis_codegen.answer_bill_question("餐饮一共消费了几次", ROWS)
    assert answer is not None
    assert answer["source"] == "model"
    assert answer["answer"] == "餐饮交易笔数：2 笔"
    assert any(event["action"] == "ANALYSIS_CODEGEN" and event["outcome"] == "SUCCESS"
               and event["metadata"]["source"] == "model" for event in get_audit_logs(limit=20))


@pytest.mark.parametrize("expression", [
    "__import__('os').system('whoami')",
    "data[0].__class__",
    "[row for row in data]",
    "open('x')",
])
def test_model_expression_outside_policy_is_rejected_and_audited(monkeypatch, expression):
    monkeypatch.setattr(runtime, "llm", _FakeModel(
        '{"expression": %s, "kind": "amount", "label": "攻击"}' % __import__("json").dumps(expression)))
    assert analysis_codegen.answer_bill_question("帮我算一下一共多少", ROWS) is None
    assert any(event["action"] == "ANALYSIS_CODEGEN" and event["reason"] == "EXPRESSION_POLICY"
               for event in get_audit_logs(limit=20))


def test_model_result_with_wrong_shape_is_not_rendered(monkeypatch):
    # The model claims "amount" but the expression yields rows: no sentence is
    # produced rather than a guessed number.
    monkeypatch.setattr(runtime, "llm", _FakeModel(
        '{"expression": "top_rows(data, \'amount\', 1)", "kind": "amount", "label": "金额"}'))
    assert analysis_codegen.answer_bill_question("一共多少", ROWS) is None


def test_unparseable_model_output_is_ignored(monkeypatch):
    monkeypatch.setattr(runtime, "llm", _FakeModel("不是 JSON"))
    assert analysis_codegen.answer_bill_question("一共多少", ROWS) is None


def test_sandbox_helpers_are_pure_and_bounded():
    assert sandbox.evaluate_expression("max_key(group_spend(data, 'category', 'amount'))", [
        {"category": "a", "amount": 1}, {"category": "b", "amount": 3}]) == ["b", 3.0]
    assert sandbox.evaluate_expression("min_key(group_spend(data, 'category', 'amount'))", []) is None
    assert len(sandbox.evaluate_expression("top_rows(data, 'amount', 999)", [{"amount": i} for i in range(30)])) == 20
    assert sandbox.evaluate_expression("len(match_rows(data, 'merchant', ''))", [{"merchant": "x"}]) == 0


def test_bill_reply_leads_with_the_sandbox_answer(bills):
    response = bills.say("这个月哪天花得最多")
    execution = bills.execution("BILL_ANALYSIS")
    custom = execution.get("custom_analysis")
    assert custom is not None, bills.diagnostic()
    assert custom["source"] == "rule"
    assert custom["kind"] == "key_amount"
    # Only this month's rows (food 40 + shop 100 on the same day) are counted.
    assert custom["value"][1] == 140.0, bills.diagnostic()
    assert response["reply"].startswith("🧮 支出最多的一天"), bills.diagnostic()
    assert "由受限沙箱计算" in response["reply"]


def test_tampered_answer_text_is_not_shown():
    from bank_agent.nodes.responder import _with_custom_analysis

    state = {"execution_result": {"type": "BILL_ANALYSIS", "custom_analysis": {
        "kind": "amount", "label": "合计", "value": 10.0, "answer": "合计：¥999,999.00"}}}
    response = _with_custom_analysis(state, {"final_response": "报告"})
    assert response["final_response"] == "报告"
