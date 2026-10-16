"""Bill analytics: date scopes, transaction filtering and the sandboxed category aggregation."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import math
import os
from typing import Any, Iterable, Mapping, Optional

try:  # security.py is available in the application, but keep this module reusable.
    from security import InjectionError, sanitize_text
except Exception:  # pragma: no cover - only used when imported in isolation
    class InjectionError(ValueError):
        pass

    def sanitize_text(value: Any, *, field: str = "文本", max_length: int = 2000) -> str:
        text = str(value or "").strip()
        if len(text) > max_length:
            raise InjectionError(f"{field}长度超过安全限制")
        return text

try:  # The read-only expression sandbox is optional: bill aggregation prefers it
    # but deterministically falls back to identical local aggregation when it is
    # unavailable (e.g. standalone planner usage or a blocked process spawn).
    from sandbox import SandboxError, evaluate_expression
except Exception:  # pragma: no cover - only used when imported in isolation
    SandboxError = ValueError
    evaluate_expression = None


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
    return None


def _local_timezone() -> Any:
    """Wall-clock timezone used for late-night rules and calendar-day filters.

    Timestamps without timezone info are produced by the mock core in local
    wall time and are left untouched.  Timestamps carrying a UTC offset are
    converted to the configured business timezone (default Asia/Shanghai) so
    that "late night" and monthly buckets are evaluated on the user's clock,
    not on UTC.
    """

    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(os.getenv("BANK_LOCAL_TZ") or "Asia/Shanghai")
    except Exception:  # no tz database / invalid name: fall back to system local
        return datetime.now().astimezone().tzinfo


def _local_date(parsed: Optional[datetime]) -> Optional[date]:
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.date()
    return parsed.astimezone(_local_timezone()).date()


def _wall_hour(parsed: Optional[datetime]) -> Optional[int]:
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.hour
    return parsed.astimezone(_local_timezone()).hour


def _row_amount(value: Any) -> float:
    """Coerce a record amount to a finite rounded float, otherwise 0.0.

    Negative values are preserved so refunds/stornos are not silently counted
    as zero spending.
    """

    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return round(number, 2)


def date_bounds(scope: str, *, now: Optional[datetime] = None) -> tuple[Optional[date], Optional[date]]:
    """Return inclusive start/exclusive end dates for a bill query."""

    current = now or datetime.now()
    today = current.date()
    normalized = str(scope or "ALL").upper()
    if normalized == "TODAY":
        return today, today + timedelta(days=1)
    first_this_month = today.replace(day=1)
    if normalized == "MONTH":
        if first_this_month.month == 12:
            next_month = date(first_this_month.year + 1, 1, 1)
        else:
            next_month = date(first_this_month.year, first_this_month.month + 1, 1)
        return first_this_month, next_month
    if normalized == "LAST_MONTH":
        last_end = first_this_month
        last_start = (first_this_month - timedelta(days=1)).replace(day=1)
        return last_start, last_end
    if normalized == "YEAR":
        return date(today.year, 1, 1), date(today.year + 1, 1, 1)
    return None, None


def filter_transactions(records: Iterable[Mapping[str, Any]], scope: str, *, now: Optional[datetime] = None, date_range: Optional[Mapping[str, str]] = None) -> list[dict[str, Any]]:
    """Filter transactions without trusting model-generated dates.

    Calendar-day comparisons happen in the business timezone so that a UTC
    timestamp near midnight falls into the correct local day.
    """

    if scope == "CUSTOM":
        if not isinstance(date_range, Mapping):
            raise ValueError("自定义时间范围缺失")
        start = date.fromisoformat(date_range["start"])
        last = date.fromisoformat(date_range["end"])
        if start > last or (last - start).days > 3660:
            raise ValueError("自定义时间范围无效")
        end = last + timedelta(days=1)
    else:
        start, end = date_bounds(scope, now=now)
    output: list[dict[str, Any]] = []
    for record in records:
        parsed = _parse_timestamp(record.get("time"))
        if start is not None:
            local_day = _local_date(parsed)
            if local_day is None or not (start <= local_day < end):
                continue
        output.append(dict(record))
    return output


_SANDBOX_AGGREGATION_EXPRESSION = (
    '{"categories": group_spend(data, "category", "amount"), '
    '"spent": spend_total(data, "amount"), "refunds": refund_total(data, "amount")}'
)


def _aggregate_bill_amounts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure local aggregation: spending, refunds and per-category spending."""

    spent = 0.0
    refunds = 0.0
    by_category: dict[str, float] = {}
    for row in rows:
        amount = _row_amount(row.get("amount"))
        if amount > 0:
            spent += amount
            category = str(row.get("category") or "其他")
            by_category[category] = round(by_category.get(category, 0.0) + amount, 2)
        elif amount < 0:
            refunds -= amount
    return {"spent": round(spent, 2), "refunds": round(refunds, 2), "categories": by_category}


def _aggregate_bill_amounts_sandboxed(rows: list[dict[str, Any]], *, user_id: Optional[str] = None,
                                     request_id: Optional[str] = None) -> tuple[dict[str, Any], str]:
    """Run the bill aggregation inside the read-only expression sandbox.

    Returns ``(aggregation, engine)``.  When the sandbox cannot run (standalone
    planner import, blocked process spawn, timeout) the identical pure-Python
    aggregation is used and the engine tag reports the downgrade so the audit
    trail stays honest about where the computation ran.
    """

    if evaluate_expression is None:
        return _aggregate_bill_amounts(rows), "sandbox_unavailable"
    try:
        result = evaluate_expression(_SANDBOX_AGGREGATION_EXPRESSION, rows,
                                     user_id=user_id, request_id=request_id)
        if not isinstance(result, dict) or not isinstance(result.get("categories"), dict):
            raise SandboxError("沙箱聚合结果结构异常")
        spent = _row_amount(result.get("spent"))
        refunds = _row_amount(result.get("refunds"))
        categories = {str(key): _row_amount(value) for key, value in result["categories"].items() if _row_amount(value) > 0}
        return {"spent": spent, "refunds": refunds, "categories": categories}, "sandbox"
    except Exception:
        return _aggregate_bill_amounts(rows), "sandbox_fallback"


def analyze_transactions(records: Iterable[Mapping[str, Any]], *, user_id: Optional[str] = None,
                         request_id: Optional[str] = None) -> dict[str, Any]:
    """Compute auditable bill facts and conservative anomaly flags.

    The result contains only values derived from the supplied records.  A
    generative model may phrase these facts, but it must not invent numbers or
    transaction IDs.

    Aggregation (totals + category breakdown) is executed as a whitelisted
    expression inside the read-only sandbox and falls back to the identical
    local computation when the sandbox is unavailable; ``analysis_engine``
    records which path actually ran.  Refunds (negative amounts) are reported
    separately instead of being rounded to zero, and late-night rules use the
    business timezone's wall clock.
    """

    rows = [dict(r) for r in records]
    aggregation, engine = _aggregate_bill_amounts_sandboxed(rows, user_id=user_id, request_id=request_id)
    spent = aggregation["spent"]
    refunds = aggregation["refunds"]
    by_category = aggregation["categories"]
    anomalies: list[dict[str, Any]] = []
    for row in rows:
        amount = _row_amount(row.get("amount"))
        if amount <= 0:
            continue  # refunds/stornos are reported separately, not as anomalies
        hour = _wall_hour(_parse_timestamp(row.get("time")))
        merchant = str(row.get("merchant") or "")
        category = str(row.get("category") or "")
        reasons: list[str] = []
        if hour is not None and (hour < 6 or hour >= 23) and amount >= 500:
            reasons.append("深夜大额")
        if any(word in merchant for word in ("境外", "海外", "国外")):
            reasons.append("境外商户")
        if category in {"可疑交易", "异常", "盗刷"}:
            reasons.append("标记为可疑类别")
        if reasons:
            anomalies.append({"tx_id": row.get("tx_id"), "amount": amount, "merchant": merchant, "reasons": reasons})
    denominator = spent or 1.0
    category_breakdown = [
        {"category": category, "amount": amount, "share": round(amount / denominator, 4)}
        for category, amount in sorted(by_category.items(), key=lambda item: item[1], reverse=True)
    ]
    return {
        "record_count": len(rows),
        "total_spent": round(spent, 2),
        "refund_total": round(refunds, 2),
        "net_spent": round(spent - refunds, 2),
        "analysis_engine": engine,
        "category_breakdown": category_breakdown,
        "anomalies": anomalies,
        "source_tx_ids": [row.get("tx_id") for row in rows if row.get("tx_id")],
    }


def safe_analysis_text(facts: Mapping[str, Any]) -> str:
    """Render a deterministic Chinese bill summary from verified facts."""

    total = float(facts.get("total_spent") or 0)
    count = int(facts.get("record_count") or 0)
    lines = [f"共核验 {count} 笔交易，支出合计 ¥{total:,.2f}。"]
    refunds = float(facts.get("refund_total") or 0)
    if refunds > 0:
        net = float(facts.get("net_spent") or (total - refunds))
        lines.append(f"其中退款 ¥{refunds:,.2f}，净支出 ¥{net:,.2f}。")
    breakdown = facts.get("category_breakdown") or []
    if breakdown:
        items = "、".join(f"{x['category']} ¥{float(x['amount']):,.2f}（{float(x['share'])*100:.1f}%）" for x in breakdown)
        lines.append(f"分类统计：{items}。")
    anomalies = facts.get("anomalies") or []
    if anomalies:
        refs = "、".join(str(x.get("tx_id")) for x in anomalies if x.get("tx_id"))
        lines.append(f"风险提示：发现 {len(anomalies)} 笔需复核的交易（流水号：{refs}）。")
    else:
        lines.append("未在已提供流水中发现规则命中的明显异常；如有疑问请联系银行客服。")
    return "\n".join(lines)
