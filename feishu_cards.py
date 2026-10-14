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


def _card(title: str, elements: list[dict[str, Any]], *, color: str = "purple", status: str = "🟢 查询已完成 · 绿 · 只读") -> FeishuReply:
    return FeishuReply("interactive", {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": color},
        "elements": [_div(f"**安言 · 智能管家**\n{status}"), {"tag": "hr"}, *elements,
                     {"tag": "hr"}, _div("模拟银行数据 · 资金办理请在安言 App 确认")],
    })


def _rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("data") or []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("invalid Core list")
    return rows


def handoff_card(reason: str | None = None) -> FeishuReply:
    """Informational card only: IM cannot initiate or authorize writes."""
    return _card("请在安言 App 办理", [
        _div("**这项请求尚未提交办理**"),
        *([_div(_safe(reason, 500))] if reason else []),
        _div("打开安言 App → 管家，重新发起这项请求。核对办理对象、金额与具体内容，再按风险等级确认或验证。"),
        _div("飞书私聊可查询余额、账单、卡片、订阅和在售理财。聊天内不会收集银行密码或短信验证码。"),
    ], status="🟡 转 App 办理 · 尚未提交")


def binding_card(kind: str) -> FeishuReply:
    copy = {
        "required": ("账户未绑定", "先绑定银行账户，再查看您的查询结果。", "🟡 待绑定"),
        "bound": ("绑定成功", "现在可以在私聊查询余额、账单、卡片、订阅与在售理财产品。", "🟢 绑定已完成"),
        "invalid": ("绑定码不可用", "绑定码无效或已过期，请在 Android 重新登录并获取绑定码。", "🔴 绑定未完成"),
        "expired": ("银行登录已失效", "银行登录会话已失效，请在 Android 重新登录并绑定。", "🔴 需要重新绑定"),
        "changed": ("绑定账户已变化", "请核对 App 登录账户，重新获取绑定码。", "🔴 绑定未完成"),
        "capacity": ("暂时无法绑定", "绑定人数已达上限，请稍后重试。", "🔴 绑定未完成"),
        "unbound": ("已解除绑定", "已解除飞书与银行账户的查询绑定。需要查询时可重新绑定。", "🟢 解绑已完成"),
    }
    title, message, status = copy[kind]
    elements = [_div(message)]
    if kind not in {"unbound", "capacity"}:
        elements.extend([_div("**App 入口**\n安言 App → 管家右上角「设备管理」→「飞书私聊查询」→「获取绑定码」"),
                         _div("仅在机器人私聊发送 `/绑定 绑定码`。会话过期后需重新绑定。")])
    if kind == "bound":
        elements.append(_div("**试着说**\n查余额 · 查看本月账单 · 查看我的订阅\n发送 `/帮助` 查看可用入口，发送 `/解绑` 解除绑定。"))
    return _card(title, elements, status=status)


def failure_card(message: str, *, title: str = "查询未完成") -> FeishuReply:
    return _card(title, [_div(f"**原因**\n{_safe(message, 500)}"),
                         _div("**下一步**\n核对请求后重新发送查询；如需办理资金或卡片操作，请回安言 App 确认。"),
                         _div("本卡片不是资金执行回执，不表示扣款、退款或回滚。")], color="red", status="🔴 请求未完成")


def help_card() -> FeishuReply:
    return _card("私聊查询入口", [_div("**查询直接回复，资金办理回 App 确认**"),
        _div("余额：`查余额`\n账单：`查看本月账单` / `分析上月消费`\n卡片：`查看我的卡片`\n订阅：`查看我的订阅` / `自动识别订阅`\n理财：`有哪些在售理财`"),
        _div("**绑定与设备入口**\n安言 App → 管家右上角「设备管理」→「飞书私聊查询」"),
        _div("发送 `/解绑` 解除绑定。银行卡操作、转账、申购与赎回请在 App 发起。")], status="🟣 可用入口 · 私聊查询")


def query_card(intent: str, scope: str, result: dict[str, Any], *, category: str | None = None) -> FeishuReply:
    """Mirror Android's information hierarchy using Feishu's native card parts."""
    period = _SCOPE[scope]
    bill_scope = f"时间：{period} · 类别：{_safe(category) if category else '全部类别'}"
    if intent == Intent.BALANCE_QUERY.value:
        return _card("账户余额", [
            _div(f"**可用余额**\n**{_money(result['available_balance'])}**"),
            {"tag": "hr"},
            _div(f"总余额  **{_money(result['total_balance'])}**\n已锁定  **{_money(result['locked_amount'])}**"),
        ])
    if intent == Intent.BILL_QUERY.value:
        rows = _rows(result)
        count = int(result.get("total_records", len(rows)))
        elements = [_div(bill_scope), _div(f"**{period}流水 · 共 {count} 笔**")]
        for row in rows[:5]:
            elements.append({"tag": "hr"})
            label = _safe(row.get("merchant") or "交易")
            elements.append(_div(f"{label}\n**{_money(row.get('amount'))}**"))
        if not rows:
            elements.append(_div(f"暂无{_safe(category) if category else ''}交易记录"))
        elif count > 5:
            elements.append(_div("仅展示前 5 笔，完整流水请在 Android 查看。"))
        return _card(f"{period}账单", elements)
    if intent == Intent.BILL_ANALYSIS.value:
        facts = result.get("facts") or {}
        if not isinstance(facts, dict):
            raise ValueError("invalid Core facts")
        elements = [_div(bill_scope), _div(f"**{period}账单概览**\n{int(facts['record_count'])} 笔交易 · 支出 **{_money(facts['total_spent'])}**")]
        if int(facts['record_count']) == 0:
            elements.append(_div(f"暂无{_safe(category) if category else ''}交易记录"))
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
        return _card("理财产品", elements)
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
                label = "历史扣款日期" if row.get("status") == "CANCELLED" else "预计下次扣款" if row.get("status") == "CANDIDATE" else "下次扣款"
                elements.append(_div(f"{label} {_safe(row['next_charge'])}"))
        if not rows:
            elements.append(_div("暂无订阅记录"))
        return _card("订阅与代扣", elements)
    raise ValueError("unsupported read-only intent")
