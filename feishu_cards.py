"""Read-only Feishu message cards built solely from verified Mock Core data."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from planner import Intent


@dataclass(frozen=True)
class FeishuReply:
    msg_type: str
    content: dict[str, Any]

    @classmethod
    def text(cls, value: str) -> "FeishuReply":
        return cls("text", {"text": value})


_SCOPE = {"TODAY": "今日", "MONTH": "本月", "LAST_MONTH": "上月", "YEAR": "今年", "ALL": "全部"}


def _safe(value: Any, limit: int = 100) -> str:
    """Escape Core-sourced labels before embedding them in lark_md."""
    raw = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or "")).strip()[:limit]
    raw = raw.replace("<", "＜").replace(">", "＞")
    return re.sub(r"([\\*_`\[\]~|])", r"\\\1", raw)


def _money(value: Any) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite amount from Core")
    return f"¥{number:,.2f}"


def _div(content: str) -> dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _card(title: str, elements: list[dict[str, Any]], *, color: str = "blue") -> FeishuReply:
    elements.append({"tag": "hr"})
    elements.append(_div("仅供演示 · 数据来自本机 Mock Core · 查询结果"))
    return FeishuReply("interactive", {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": color},
        "elements": elements,
    })


def _rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("data") or []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("invalid Core list")
    return rows


def handoff_card() -> FeishuReply:
    """Informational card only: IM cannot initiate or authorize writes."""
    return _card("请在 Android 应用办理", [
        _div("**这项操作需要在 Android 重新发起**"),
        _div("飞书私聊仅提供余额、账单、卡片、订阅和理财产品查询。请在 Android 登录后查看操作详情，并按风险等级完成确认或验证。"),
    ], color="orange")


def query_card(intent: str, scope: str, result: dict[str, Any]) -> FeishuReply:
    """Mirror Android's information hierarchy using Feishu's native card parts."""
    period = _SCOPE[scope]
    if intent == Intent.BALANCE_QUERY.value:
        return _card("账户余额", [
            _div(f"**可用余额**\n**{_money(result['available_balance'])}**"),
            {"tag": "hr"},
            _div(f"总余额  **{_money(result['total_balance'])}**\n已锁定  **{_money(result['locked_amount'])}**"),
        ])
    if intent == Intent.BILL_QUERY.value:
        rows = _rows(result)
        count = int(result.get("total_records", len(rows)))
        elements = [_div(f"**{period}流水 · 共 {count} 笔**")]
        for row in rows[:5]:
            elements.append({"tag": "hr"})
            label = _safe(row.get("merchant") or "交易")
            elements.append(_div(f"{label}\n**{_money(row.get('amount'))}**"))
        if not rows:
            elements.append(_div("暂无交易记录"))
        elif count > 5:
            elements.append(_div("仅展示前 5 笔，完整流水请在 Android 查看。"))
        return _card(f"{period}账单", elements)
    if intent == Intent.BILL_ANALYSIS.value:
        facts = result.get("facts") or {}
        if not isinstance(facts, dict):
            raise ValueError("invalid Core facts")
        elements = [_div(f"**{period}账单概览**\n{int(facts['record_count'])} 笔交易 · 支出 **{_money(facts['total_spent'])}**")]
        for key, label in (("refund_total", "退款"), ("net_spent", "净支出")):
            if facts.get(key) is not None:
                elements.append(_div(f"{label}  **{_money(facts[key])}**"))
        categories = facts.get("category_breakdown") or []
        if not isinstance(categories, list):
            raise ValueError("invalid Core categories")
        if categories:
            elements.append({"tag": "hr"})
            elements.append(_div("**分类支出**"))
            for row in categories[:5]:
                if not isinstance(row, dict):
                    raise ValueError("invalid Core category")
                elements.append(_div(f"{_safe(row.get('category') or '其他')}  **{_money(row.get('amount'))}**"))
        return _card(f"{period}账单分析", elements)
    if intent == Intent.WEALTH_QUERY.value:
        rows = _rows(result)
        elements = [_div(f"**理财产品 · 共 {len(rows)} 款**")]
        for row in rows[:8]:
            elements.append({"tag": "hr"})
            name = _safe(row.get("name") or row.get("product_id") or "产品")
            category = _safe(row.get("category") or "未分类")
            risk = _safe(row.get("risk_level") or "—")
            elements.append(_div(f"**{name}**\n{category} · 风险等级 {risk}"))
            if row.get("benchmark"):
                elements.append(_div(f"业绩比较基准：{_safe(row['benchmark'], 160)}"))
            details = []
            if row.get("min_amount") is not None:
                details.append(f"起购 {_money(row['min_amount'])}")
            if row.get("term"):
                details.append(_safe(row["term"], 120))
            if details:
                elements.append(_div(" · ".join(details)))
        if not rows:
            elements.append(_div("暂无产品"))
        elements.append(_div("业绩比较基准不代表实际收益；申购请在 Android 完成风险评估和验证。"))
        return _card("理财产品", elements, color="green")
    if intent == Intent.CARD_QUERY.value:
        rows = _rows(result)
        elements = [_div(f"**我的卡片 · 共 {len(rows)} 张**")]
        for row in rows[:8]:
            masked = str(row.get("number_masked") or "")
            # Never display a Core card identifier as a substitute for masked PAN.
            if not re.fullmatch(r"[\d*\s•·Xx-]{4,32}", masked) or not any(ch in masked for ch in "*•·Xx"):
                masked = "卡号未提供"
            label = _safe(row.get("label") or row.get("type") or "卡片")
            status = {"ACTIVE": "正常使用", "FROZEN": "已冻结", "LOST": "已挂失"}.get(
                row.get("status"), _safe(row.get("status") or "状态未知"))
            elements.append({"tag": "hr"})
            elements.append(_div(f"**{label}**\n{_safe(masked)} · {status}"))
        if not rows:
            elements.append(_div("暂无卡片"))
        return _card("卡片查询", elements)
    if intent == Intent.SUBSCRIPTION_QUERY.value:
        rows = _rows(result)
        elements = [_div(f"**订阅与周期扣费 · 共 {len(rows)} 项**")]
        for row in rows[:8]:
            status = {"CANDIDATE": "待核实", "ACTIVE": "扣费中", "CANCELLED": "已取消"}.get(
                row.get("status"), _safe(row.get("status") or "状态未知"))
            elements.append({"tag": "hr"})
            elements.append(_div(f"**{_safe(row.get('merchant') or '商户')}**\n{_money(row.get('amount'))} · {status}"))
            if row.get("next_charge"):
                elements.append(_div(f"下次扣款 {_safe(row['next_charge'])}"))
        if not rows:
            elements.append(_div("暂无订阅记录"))
        return _card("订阅与代扣", elements)
    raise ValueError("unsupported read-only intent")
