"""The wealth conversation end to end: questionnaire, level, purchase, holdings."""

from __future__ import annotations

import main
from bank_agent.parsing import local_parse
from test_agent_gateway import _send, gateway  # noqa: F401

ANSWERS_C4 = "1B 2C 3C 4D 5D 6D 7D 8D"


def _finish_quiz(client, conversation_id):
    _send(client, "我想做风险测评", conversation_id=conversation_id)
    return _send(client, ANSWERS_C4, conversation_id=conversation_id)


def test_the_level_card_and_its_chips_stay_on_the_turn_that_finished_the_quiz(gateway):  # noqa: F811
    client, _ = gateway
    done = _finish_quiz(client, "leak")
    assert done["result"]["profile_result"]["grade"] == "C4"
    later = _send(client, "查一下余额", conversation_id="leak")
    assert not (later["result"] or {}).get("profile_result")
    assert not (later["result"] or {}).get("suggestions")


def test_quiz_completion_suggests_reads_without_choosing_a_purchase_amount(gateway):  # noqa: F811
    client, _ = gateway
    done = _finish_quiz(client, "chips")
    assert done["result"]["execution_result"]["data"]["data"]
    assert done["result"]["suggestions"] == ["查看理财产品", "查看我的持仓"]


def test_a_clear_request_leaves_an_unfinished_quiz_instead_of_being_read_as_an_answer(gateway):  # noqa: F811
    client, _ = gateway
    _send(client, "我想做风险测评", conversation_id="trap")
    holdings = _send(client, "我的持仓", conversation_id="trap")
    assert "持仓" in holdings["reply"] and "已记录" not in holdings["reply"]
    assert holdings["result"]["risk_quiz"] == {"active": False}


def test_a_request_that_needs_the_quiz_keeps_its_answers_and_asks_to_finish(gateway):  # noqa: F811
    client, _ = gateway
    _send(client, "我想做风险测评", conversation_id="keep-quiz")
    _send(client, "1B 2C", conversation_id="keep-quiz")
    buy = _send(client, "申购LX00118 500元", conversation_id="keep-quiz")
    assert buy["approval"] is None and "已记录 2/8 题" in buy["reply"]
    again = _send(client, "重新做风险测评", conversation_id="keep-quiz")
    assert "正在进行" in again["reply"] and "已记录 2/8 题" in again["reply"]
    finished = _send(client, "3C 4D 5D 6D 7D 8D", conversation_id="keep-quiz")
    assert finished["result"]["profile_result"]["grade"]


def test_asking_for_the_level_shows_the_level_not_products(gateway):  # noqa: F811
    client, _ = gateway
    none_yet = _send(client, "我的风险等级是什么", conversation_id="level")
    assert "还没有完成风险测评" in none_yet["reply"] and not (none_yet["result"] or {}).get("profile_result")
    _finish_quiz(client, "level")
    level = _send(client, "我的风险等级是什么", conversation_id="level")
    assert "C4" in level["reply"] and level["result"]["profile_result"]["type"] == "进取型"
    assert not (level["result"].get("execution_result") or {})


def test_a_purchase_over_the_card_limit_is_refused_before_any_verification(gateway):  # noqa: F811
    client, _ = gateway
    _finish_quiz(client, "limit")
    refused = _send(client, "申购SG20270 10000元", conversation_id="limit")
    assert refused["approval"] is None
    assert "当日支付限额" in refused["reply"] and "今日还可用" in refused["reply"]
    assert main.WEALTH_HOLDINGS.get("user_zhang", []) == []


def test_redeem_everything_can_be_said_after_the_product():
    assert local_parse("赎回SG20270 全部")["slots"].get("redeem_all") is True
    assert local_parse("全部赎回LX00118")["slots"].get("redeem_all") is True


def test_a_new_request_while_an_approval_waits_is_told_why_nothing_happened(gateway):  # noqa: F811
    client, _ = gateway
    _send(client, "给李四转300元", conversation_id="waiting")
    other = _send(client, "我的持仓", conversation_id="waiting")
    assert other["approval"] is not None and "待确认的操作" in other["reply"]


def test_short_ways_of_asking_to_retake_the_questionnaire_are_understood():
    for text in ("重新测评", "重做测评", "再做一次测评", "开始测评", "风险问卷"):
        assert local_parse(text)["intent"] == "WEALTH_RISK_ASSESSMENT", text
