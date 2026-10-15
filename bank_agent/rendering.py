"""Customer-facing text for verified wealth, holdings, subscription and card collections."""

from __future__ import annotations

from typing import Any, Mapping


from planner import (
    Intent,
)
from bank_agent.verification import (
    _finite_number,
    _valid_text_field,
)


def _format_yield(value: Any) -> str:
    if value is None:
        return "暂未提供"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "暂未提供"


def _render_product_line(item: Mapping[str, Any]) -> str:
    return (
        f"- `{item.get('product_id')}` {item.get('name')} ｜ 风险 {item.get('risk_level')} ｜ "
        f"参考年化 {_format_yield(item.get('annualized_yield'))} ｜ 起购 ¥{float(item.get('min_amount') or 0):,.2f} ｜ {item.get('liquidity')}"
    )


def _render_wealth(data: Mapping[str, Any], intent: str) -> str:
    """Render wealth products as a readable list instead of a raw JSON blob."""

    rows = data.get("data") or []
    if intent == Intent.WEALTH_COMPARE.value:
        header = f"⚖️ 产品对比（{len(rows)} 款，按您指定的顺序）："
    elif intent == Intent.WEALTH_RECOMMEND.value:
        profile = str(data.get("risk_profile_label") or data.get("risk_profile") or "")
        header = f"📈 按风险偏好 **{profile}** 为您匹配到 {len(rows)} 款产品：" if profile else f"📈 为您匹配到 {len(rows)} 款产品："
    else:
        header = f"📊 在售理财产品共 {len(rows)} 款："
    lines = [header]
    lines.extend(_render_product_line(item) for item in rows)
    if not rows:
        lines.append("- 暂无可展示的产品。")
    disclaimer = data.get("disclaimer")
    if _valid_text_field(disclaimer, max_length=300):
        lines.append(f"> {disclaimer}")
    if intent != Intent.WEALTH_RECOMMEND.value:
        lines.append("> 年化为产品资料展示值，不代表实际收益；申购前需完成风险测评。")
    return "\n".join(lines)


def _render_holdings(data: Mapping[str, Any]) -> str:
    """Render the customer's own positions; never the bank's product shelf."""

    rows = data.get("data") or []
    if not rows:
        return "📊 您当前没有理财持仓。想看看在售产品可以说“有哪些理财产品”。"
    total = sum(float(row.get("principal") or 0) for row in rows)
    lines = [f"📊 您持有 {len(rows)} 笔理财，本金合计 ¥{total:,.2f}："]
    for item in rows:
        line = (
            f"- `{item.get('holding_id')}` {item.get('product_name') or item.get('product_id')} ｜ "
            f"本金 ¥{float(item.get('principal') or 0):,.2f}"
        )
        if _finite_number(item.get("estimated_value"), nonnegative=True):
            line += f" ｜ 参考市值 ¥{float(item.get('estimated_value')):,.2f}"
        lines.append(line)
    lines.append("> 市值为演示估算值，实际收益以产品结算为准。")
    return "\n".join(lines)


def _render_subscriptions(data: Mapping[str, Any]) -> str:
    rows = data.get("data") or []
    lines = [f"🔁 订阅/代扣共 {len(rows)} 项："]
    for item in rows:
        status = str(item.get("status") or "")
        status_label = {"ACTIVE": "生效中", "CANCELLED": "已取消", "CANDIDATE": "疑似周期扣费，待核实"}.get(status, status)
        charge_label = "预计下次扣款" if status == "CANDIDATE" else ("下次扣款" if status == "ACTIVE" else "原计划扣款")
        lines.append(
            f"- {item.get('merchant')} ｜ 每期 ¥{float(item.get('amount') or 0):,.2f} ｜ "
            f"{charge_label} {item.get('next_charge')} ｜ 状态 {status_label}"
        )
    active_rows = [item for item in rows if item.get("status") == "ACTIVE"]
    if active_rows:
        merchant = str(active_rows[0].get("merchant") or "")
        subscription_name = merchant if merchant.endswith("订阅") else f"{merchant} 订阅"
        lines.append(f'> 如需停用，可以说“取消 {subscription_name}”，取消前会请您确认。')
    else:
        lines.append("- 当前没有进行中的订阅。")
    return "\n".join(lines)


def _render_cards(data: Mapping[str, Any]) -> str:
    rows = data.get("data") or []
    lines = [f"💳 卡片共 {len(rows)} 张："]
    for item in rows:
        line = f"- `{item.get('card_id')}`（{item.get('type')}）｜ 状态 {item.get('status')} ｜ 日限额 ¥{float(item.get('daily_limit') or 0):,.2f}"
        if _finite_number(item.get("daily_spent_today"), nonnegative=True):
            line += f" ｜ 今日已用 ¥{float(item.get('daily_spent_today')):,.2f}"
        if _finite_number(item.get("daily_remaining"), nonnegative=True):
            line += f" ｜ 今日剩余 ¥{float(item.get('daily_remaining')):,.2f}"
        lines.append(line)
    if not rows:
        lines.append("- 当前没有绑定卡片。")
    return "\n".join(lines)


def _bill_scope_label(result: Mapping[str, Any]) -> str:
    if result.get("date_scope") == "CUSTOM":
        bounds = result.get("date_range") or {}
        return f"{bounds['start']} 至 {bounds['end']}"
    return {"TODAY": "今天", "MONTH": "本月", "LAST_MONTH": "上月", "YEAR": "今年", "ALL": "全部时间"}.get(result.get("date_scope"), "指定时间范围内")
