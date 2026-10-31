"""Regression tests for card limit rules consultation and credit card boundary."""
import types
from banking_rules import search_rules
from bank_agent.parsing import local_parse
from bank_agent.understanding import help_topic
from scripts.dialogue_eval.bank_environment import BankEnvironment


def test_search_rules_top4_includes_finite_daily_limit_for_credit_card_query():
    query = "我想问一下，本产品信用卡的额度限制规则是怎样的？"
    matches = search_rules(query, limit=4)
    match_ids = [m["id"] for m in matches]
    assert "guide.limits.finite" in match_ids, f"guide.limits.finite missing from top-4: {match_ids}"


def test_parsing_and_topic_routes_card_limit_query_to_limits():
    query = "我想问一下，本产品信用卡的额度限制规则是怎样的？"
    assert help_topic(query) == "limits"
    parsed = local_parse(query)
    assert parsed["intent"] == "BANKING_HELP"
    assert parsed.get("slots", {}).get("help_topic") == "limits"


def test_dialogue_turn_explains_daily_limit_and_maintains_credit_boundary(tmp_path, monkeypatch):
    from scripts.evaluate_dialogue_tasks import isolated_settings
    settings = isolated_settings(tmp_path)
    for k, v in settings.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("BANK_EVAL_ISOLATED_PROCESS", "1")

    task = types.SimpleNamespace(id="card_limit_boundary_test", setup="default")
    env = BankEnvironment(task)
    resp = env.gateway.post(
        "/api/v1/agent/messages",
        json={"conversation_id": env.cid, "text": "我想问一下，本产品信用卡的额度限制规则是怎样的？", "message_id": "msg-001"},
        headers=env.headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("intent") == "BANKING_HELP"
    assert data.get("approval") is None

    reply = data.get("reply", "")
    assert "日额度" in reply or "限额" in reply
    assert "信用卡" in reply
    assert "已为您开通信用卡" not in reply
    assert "您的信用卡授信" not in reply
