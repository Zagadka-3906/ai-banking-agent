"""Regression test for difference calculation operand resolution from text context."""
from bank_planning.requests import normalize_goals


def test_difference_calculation_operands_inferred_from_mentioned_aliases():
    text = "我刚才看到零星宝·活钱管家和恒泉稳利·60天持有这两个产品的起购金额了，能帮我再对比一下，看哪个起购门槛更低吗？"
    ctx = {
        "grounded_aliases": {"零星宝": "LX00118", "恒泉稳利": "HQ30060"},
        "recent_observations": [{"resource_entities": {"wealth_products": ["LX00118", "HQ30060"]}}],
    }
    goals = [
        {
            "act": "QUERY",
            "resource": "wealth_products",
            "question": "比较零星宝·活钱管家与恒泉稳利·60天持有的起购金额，哪个更低",
            "fields": ["min_amount"],
            "subject": None,
            "topic": "wealth",
        },
        {
            "act": "QUERY",
            "resource": "wealth_products",
            "question": "两个产品起购金额的差额",
            "fields": ["min_amount"],
            "subject": None,
            "topic": "wealth",
            "calculation": {"operation": "difference", "field": "min_amount"},
        },
    ]
    res = normalize_goals(goals, text, ctx)
    assert len(res) == 2
    # Goal 2 should have its operands inferred from mentioned products
    assert set(res[1]["subjects"]) == {"LX00118", "HQ30060"}
    assert res[1]["calculation"]["operation"] == "difference"


def test_difference_calculation_gracefully_degrades_when_operands_missing():
    text = "随便看看产品差额"
    goals = [
        {
            "act": "QUERY",
            "resource": "wealth_products",
            "question": "差额",
            "fields": ["min_amount"],
            "subject": None,
            "topic": "wealth",
            "calculation": {"operation": "difference", "field": "min_amount"},
        }
    ]
    # When operands cannot be resolved, calculation degrades to None instead of raising an error
    res = normalize_goals(goals, text, {})
    assert len(res) == 1
    assert "calculation" not in res[0]
