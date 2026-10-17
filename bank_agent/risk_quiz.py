"""The 8-question suitability questionnaire shown before a first wealth request (scored by Core)."""

from __future__ import annotations

import re
import uuid
from typing import Any, Mapping, Optional

import requests

from planner import (
    Intent,
    build_task_plan,
)
from security import audit_event
from bank_agent import runtime
from bank_agent.state import (
    BankingAgentState,
    empty_state,
)
from bank_agent.runtime import (
    error_message,
    response_json,
)
from bank_agent.extraction import (
    format_hint,
)
from bank_agent.rendering import (
    render_wealth,
)


# ---------------------------------------------------------------------------
# 8 题风险测评问卷（C1-C5）：用户首次咨询理财业务时自动触发。
# 评分与定档在核心服务端完成（客户端/Agent 只提交选项字母，绝不展示分数）；
# Agent 只负责展示题目、收集答案、呈现类型与匹配产品。
# ---------------------------------------------------------------------------

RISK_QUIZ_INTENTS = {
    Intent.WEALTH_RECOMMEND.value,
    Intent.WEALTH_PURCHASE.value,
    Intent.WEALTH_REDEEM.value,
    Intent.WEALTH_RISK_ASSESSMENT.value,
}
RISK_QUIZ_OPTIONS = {1: "ABCD", 2: "ABCD", 3: "ABCD", 4: "ABCDE", 5: "ABCD", 6: "ABCDE", 7: "ABCDE", 8: "ABCDE"}

# 结构化题库：App 逐题渲染成可点选的选项组件；选项为完整业务表述（不含分值）。
RISK_QUIZ_STRUCTURE: list[dict[str, Any]] = [
    {"number": 1, "text": "您的年龄处于以下哪个区间？", "options": [
        {"key": "A", "label": "18~30岁"}, {"key": "B", "label": "31~50岁"},
        {"key": "C", "label": "51~60岁"}, {"key": "D", "label": "60岁以上"}]},
    {"number": 2, "text": "您的家庭年收入状况大致为？", "options": [
        {"key": "A", "label": "10万元以下"}, {"key": "B", "label": "10万~30万元"},
        {"key": "C", "label": "30万~100万元"}, {"key": "D", "label": "100万元以上"}]},
    {"number": 3, "text": "在您目前的总资产中，可用于金融投资（扣除自住用房及应急金）的比例约为？", "options": [
        {"key": "A", "label": "小于10%"}, {"key": "B", "label": "10%~30%"},
        {"key": "C", "label": "30%~50%"}, {"key": "D", "label": "50%以上"}]},
    {"number": 4, "text": "您过往的主要投资经验是？", "options": [
        {"key": "A", "label": "除银行活期、定期存款或国债外，基本无其他投资经验"},
        {"key": "B", "label": "购买过低风险银行理财、货币基金"},
        {"key": "C", "label": "购买过债券基金、黄金、固收+等中低风险产品"},
        {"key": "D", "label": "投资过股票型基金、A股、港股、外汇等权益类产品"},
        {"key": "E", "label": "参与过期货、期权、私募股权或杠杆交易等复杂衍生品"}]},
    {"number": 5, "text": "您计划进行理财投资的主要期限通常是多久？", "options": [
        {"key": "A", "label": "3个月以内"}, {"key": "B", "label": "3个月至1年"},
        {"key": "C", "label": "1年至3年"}, {"key": "D", "label": "3年以上"}]},
    {"number": 6, "text": "您的首要投资目标通常是什么？", "options": [
        {"key": "A", "label": "确保资产安全，本金绝对不能有损失，略高于通胀即可"},
        {"key": "B", "label": "追求稳定增值，能承受极小的净值波动"},
        {"key": "C", "label": "在控制下行风险的前提下，追求平衡的资本增值"},
        {"key": "D", "label": "追求较高的资本长期增值，愿承担显著市场波动"},
        {"key": "E", "label": "追求短期或长期超额高回报，愿意承担本金大比例亏损风险"}]},
    {"number": 7, "text": "假设您持有一笔10万元的投资，在未来半年内出现浮亏，您能接受的最大亏损幅度是？", "options": [
        {"key": "A", "label": "无法容忍任何亏损"}, {"key": "B", "label": "亏损5%以内"},
        {"key": "C", "label": "亏损5%~15%"}, {"key": "D", "label": "亏损15%~30%"},
        {"key": "E", "label": "亏损30%以上"}]},
    {"number": 8, "text": "当您的投资在短期内发生超过预期幅度的下跌时，您通常会采取的操作是？", "options": [
        {"key": "A", "label": "立即全部赎回止损，彻底退出该类资产"},
        {"key": "B", "label": "减仓一半以上，以规避进一步下行风险"},
        {"key": "C", "label": "保持观望，等待净值回本或根据市场走势决定"},
        {"key": "D", "label": "视基本面情况分批逢低加仓，摊薄成本"},
        {"key": "E", "label": "大幅加仓博取反弹收益"}]},
]


def _render_risk_quiz_text() -> str:
    """由结构化题库派生的纯文本版（聊天兜底路径仍支持分次文字作答）。"""
    lines = [
        "【风险测评】您首次咨询理财业务，请先完成以下风险评估（共 8 题）。",
        "可一次性回复全部答案（例如：1A 2B 3C 4D 5C 6C 7C 8C），也可分次作答；回复“退出测评”可随时放弃。",
        "",
    ]
    for question in RISK_QUIZ_STRUCTURE:
        lines.append(f"{question['number']}. {question['text']}")
        options = "  ".join(f"{option['key']}. {option['label']}" for option in question["options"])
        lines.append("  " + options)
        lines.append("")
    return chr(10).join(lines)

RISK_QUIZ_QUESTIONS = _render_risk_quiz_text()
RISK_QUIZ_PAYLOAD: dict[str, Any] = {"active": True, "questions": RISK_QUIZ_STRUCTURE}


def _quiz_question_text(number: int) -> str:
    start = RISK_QUIZ_QUESTIONS.index(f"{number}. ")
    end = RISK_QUIZ_QUESTIONS.find("\n\n", start)
    return RISK_QUIZ_QUESTIONS[start:end if end > 0 else len(RISK_QUIZ_QUESTIONS)]


def _parse_quiz_answers(text: str, answers: Mapping[str, Any]) -> tuple[dict[str, str], list[str]]:
    """从回复里抽取题号->选项。支持“1A 2B”式成对写法和按未答题顺序的裸字母序列。"""

    found: dict[str, str] = {}
    invalid: list[str] = []
    for match in re.finditer(r"(?:第\s*)?([1-8])\s*[、.．,:：\)）]?\s*([A-Za-z])(?![A-Za-z0-9])", text):
        question, letter = match.group(1), match.group(2).upper()
        if letter in RISK_QUIZ_OPTIONS[int(question)]:
            found[question] = letter
        else:
            invalid.append(f"第 {question} 题没有 {letter} 选项")
    if not found and not invalid:
        letters = [item.upper() for item in re.findall(r"[A-Za-z]", text)]
        missing = [str(q) for q in range(1, 9) if str(q) not in answers]
        if letters and len(letters) in {1, len(missing)} and re.fullmatch(r"[\s,，、A-Za-z]+", text.strip() or " "):
            for question, letter in zip(missing, letters):
                if letter in RISK_QUIZ_OPTIONS[int(question)]:
                    found[question] = letter
                else:
                    invalid.append(f"第 {question} 题没有 {letter} 选项")
    return found, invalid


QUIZ_EXIT_WORDS = ("退出测评", "取消测评", "暂不测评", "不测了")


def is_quiz_reply(text: str, quiz: Mapping[str, Any]) -> bool:
    """Whether a message answers (or leaves) the questionnaire, as opposed to a new request."""

    if any(word in text for word in QUIZ_EXIT_WORDS):
        return True
    found, invalid = _parse_quiz_answers(text, quiz.get("answers") or {})
    return bool(found or invalid)


def fetch_risk_profile(state: BankingAgentState) -> Optional[dict[str, Any]]:
    """读取核心里该用户的风险档案与测评余量；读不到按未测评处理。"""

    try:
        response = runtime._http("GET", "wealth/risk-profile", state=state, headers={"X-User-Id": state.get("user_id")})
        if response.status_code >= 400:
            return None
        return response_json(response)
    except (requests.RequestException, RuntimeError, ValueError):
        return None


def _quiz_result(
    state: BankingAgentState,
    response_text: str,
    quiz: Optional[dict[str, Any]],
    execution_result: Optional[dict[str, Any]] = None,
    quiz_payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """问卷回合的统一出口：quiz 为 None 表示已结束（完成或放弃）。

    完成回合可携带结构化的匹配产品列表（execution_result），让原生客户端
    把回复渲染成理财卡片而不是纯文本清单。
    """

    result = empty_state(final_response=response_text)
    result.update({
        "intent": Intent.WEALTH_RISK_ASSESSMENT.value,
        # The gateway owns correlation for a turn.  Keep the fallback for
        # direct graph callers, but never replace an id supplied in state.
        "request_id": state.get("request_id") or f"agent_{uuid.uuid4().hex}",
        "clarification_required": quiz is not None,
        "plan": build_task_plan({"intent": Intent.WEALTH_RISK_ASSESSMENT.value, "date_scope": "ALL"}).to_dict(),
        "context": {"risk_quiz": quiz, "compound_workflow": (state.get("context") or {}).get("compound_workflow") if quiz else None},
        "business_status": "NEEDS_INPUT" if quiz is not None else "SUCCEEDED" if execution_result else "CANCELLED",
        "execution_result": execution_result,
        # 缺省关闭载荷（None 会被 langgraph 视为“未写入”而丢失）；
        # 答题回合若没解析到答案则重发题目结构，让客户端丢失的作答组件
        # （重装/重启后）自动恢复，而不是把用户困在文字兜底里。
        "risk_quiz": quiz_payload if quiz_payload is not None else {"active": False},
        "sub_requests": None,
    })
    return result


def start_risk_quiz(state: BankingAgentState, retake: bool = False) -> dict[str, Any]:
    lead = (
        "您可以重新完成风险评估以更新您的风险档案"
        if retake
        else "您首次咨询理财业务，请先完成风险评估"
    )
    result = _quiz_result(state, f"【风险测评】{lead}（共 8 题），逐题选择即可；完成后将为您更新匹配的产品。", {"answers": {}})
    audit_event("RISK_QUIZ", user_id=state.get("user_id"), decision="CHALLENGE", outcome="STARTED", request_id=result["request_id"], metadata={"retake": retake})
    # 顶层载荷：渠道信封据此在输入框上方打开逐题作答组件。
    result["risk_quiz"] = RISK_QUIZ_PAYLOAD
    return result


def remind_risk_quiz(state: BankingAgentState, quiz: dict[str, Any], message: str) -> dict[str, Any]:
    """Keep an unfinished questionnaire open (answers kept) and tell the user why."""

    return _quiz_result(state, message, dict(quiz), quiz_payload=RISK_QUIZ_PAYLOAD)


def handle_risk_quiz(state: BankingAgentState, text: str, quiz: dict[str, Any]) -> dict[str, Any]:
    user_id = state.get("user_id")
    request_id = state.get("request_id") or f"agent_{uuid.uuid4().hex}"
    state = {**state, "request_id": request_id}
    answers: dict[str, str] = {str(k): str(v).upper() for k, v in (quiz.get("answers") or {}).items()}
    if any(word in text for word in QUIZ_EXIT_WORDS):
        audit_event("RISK_QUIZ", user_id=user_id, decision="ALLOW", outcome="CANCELLED", request_id=request_id)
        return _quiz_result(state, "已退出风险测评，本次未记录任何答案。需要时再说“我想做风险测评”即可。", None)

    found, invalid = _parse_quiz_answers(text, answers)
    answers.update(found)
    if invalid:
        return _quiz_result(state, "以下答案无效：" + "；".join(invalid) + "。请检查后重新回复该题。", {**quiz, "answers": answers}, quiz_payload=RISK_QUIZ_PAYLOAD)
    missing = [q for q in range(1, 9) if str(q) not in answers]
    if missing:
        numbers = "、".join(str(q) for q in missing)
        remaining = "\n\n".join(_quiz_question_text(q) for q in missing)
        reply = (
            f"已记录 {len(answers)}/8 题，还差第 {numbers} 题：\n\n"
            f"{remaining}\n\n可继续作答（如 {format_hint(missing)}），或回复“退出测评”。"
        )
        audit_event("RISK_QUIZ", user_id=user_id, decision="ALLOW", outcome="PARTIAL", request_id=request_id, metadata={"answered": len(answers)})
        return _quiz_result(state, reply, {**quiz, "answers": answers}, quiz_payload=RISK_QUIZ_PAYLOAD)

    # 全部作答：提交核心评分定档（服务端是唯一权威），完成后推荐匹配产品
    try:
        response = runtime._http(
            "POST", "wealth/risk-assessment", state=state,
            json_body={"user_id": user_id, "answers": answers},
            headers={"X-User-Id": user_id},
        )
        if response.status_code >= 400:
            raise RuntimeError(error_message(response))
        graded = response_json(response)
    except (requests.RequestException, RuntimeError) as exc:
        retry = " ".join(f"{q}{letter}" for q, letter in sorted(answers.items()))
        return _quiz_result(state, f"测评结果暂时无法保存（{exc}）。您的答案已保留，稍后可重新发送：{retry}", {**quiz, "answers": answers})

    grade = str(graded.get("risk_profile") or "")
    product_text = ""
    execution_result: Optional[dict[str, Any]] = None
    try:
        rec = runtime._http("GET", f"wealth/recommend?user_id={user_id}&risk_profile={grade}", state=state, headers={"X-User-Id": user_id})
        if rec.status_code < 400:
            rec_payload = response_json(rec)
            product_text = "\n\n" + render_wealth(rec_payload, Intent.WEALTH_RECOMMEND.value)
            # 结构化产品列表随回复下发，原生客户端渲染为理财卡片。
            execution_result = {"type": Intent.WEALTH_RECOMMEND.value, "data": rec_payload}
    except (requests.RequestException, RuntimeError, ValueError):
        product_text = ""
    audit_event("RISK_QUIZ", user_id=user_id, decision="ALLOW", outcome="COMPLETED", request_id=request_id, metadata={"grade": grade})
    profile_result = {
        "grade": grade,
        "type": str(graded.get("risk_type") or ""),
        "traits": str(graded.get("traits") or ""),
        "matching_levels": str(graded.get("matching_levels") or ""),
    }
    matched = [row for row in ((execution_result or {}).get("data") or {}).get("data") or [] if isinstance(row, dict) and row.get("product_id")]
    first = matched[0] if matched else None
    buy = f"申购 {first['product_id']} {max(float(first.get('min_amount') or 0), 500):g} 元" if first else None
    suggestions = [item for item in ("查看理财产品", buy, "查看我的持仓") if item]
    reply = (
        f"✅ 风险测评完成，您的投资者风险类型为 **{grade} {graded.get('risk_type', '')}**。\n\n"
        f"特点：{graded.get('traits', '')}\n\n"
        f"匹配理财产品级别：{graded.get('matching_levels', '')}。以下是与您风险等级匹配的在售产品："
        f"{product_text}\n\n现在可以直接说出需求（例如“查看理财产品”或“申购 LX00118 500 元”）。"
    )
    result = _quiz_result(state, reply, None, execution_result=execution_result)
    # 档案结果卡片与快捷输入建议随信封下发，由客户端渲染。
    result["profile_result"] = profile_result
    result["business_status"] = "SUCCEEDED"
    result["suggestions"] = suggestions
    return result
