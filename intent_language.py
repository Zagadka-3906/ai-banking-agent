"""Deterministic language boundaries; no model, credentials or banking calls."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any


def business_text(text: str) -> str:
    """A memo is data, and cannot select an intent, a payee or an amount."""
    return re.split(r"备注|附言", text, maxsplit=1)[0].strip("，,；; ")


_TASK_START = r"(?:请|麻烦|帮我|再|另外|同时)?\s*(?:给|向|查询|查看|查|看看|看|分析|冻结|解冻|挂失|解挂|申购|赎回|取消|关闭|申请|锁定|预留|(?:本月|上月|这个月|上个月|今天|今年)?(?:余额|账单|银行卡|卡片|订阅|持仓))"
_EXPLICIT_SPLIT = re.compile(r"然后|接着|之后|，再|,再|；|;|并且|，并|同时|以及|顺便|(?=再(?:帮我|查一下|看一下|分析))")


def split_tasks(text: str) -> list[str]:
    segments = re.split(r"(备注|附言)", text, maxsplit=1)
    source = segments[0]
    # Do not split a contact list, product comparison, number or partial slot.
    def separator(match):
        before, after = source[:match.start()], source[match.end():]
        left_request = re.search(r"查询|查看|查一下|看一下|看看|分析|给.{1,24}(?:转|汇)|转账|转给|申购|赎回|(?:冻结|解冻|挂失|解挂).*(?:卡|CARD)|取消|关闭|申请|锁定|预留", before, re.IGNORECASE)
        bare_read = re.fullmatch(r"(?:本月|上月|这个月|上个月|今天|今年)?(?:余额|账单|银行卡|卡片|订阅|持仓)", before.strip())
        if not left_request and not bare_read:
            return match.group()
        if "生日" in before and re.match(r"\s*(?:请|帮我|麻烦)?(?:锁定|预留)", after):
            return match.group()
        return "；"
    if "提醒" in business_text(source):
        source = re.sub(r"[，,](?=\s*(?:Apple(?:订阅)?|苹果(?:订阅)?|视频会员).{0,16}提醒)", "；", source)
    source = re.sub(r"(?:[，,、。]|和|与|及)(?=\s*" + _TASK_START + r")", separator, source)
    parts = [part.strip("，,。；; ") for part in _EXPLICIT_SPLIT.split(source)]
    parts = [part for part in parts if part]
    if parts and len(segments) == 3:
        parts[-1] += segments[1] + segments[2]
    return parts


def money_source(text: str) -> str:
    source = business_text(text).replace("−", "-").replace("－", "-")
    source = re.sub(r"(?i)\b(?:card-[0-9a-z]+|[a-z]{1,8}\d{3,})\b", " ", source)
    source = re.sub(r"(?<!\d)\d{11,19}(?!\d)|(?:尾号|末四位|后四位)\s*\d{4}", " ", source)
    source = re.sub(r"20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?", " ", source)
    return re.sub(r"\d{1,2}[:：]\d{2}(?::\d{2})?|\d+\s*(?:点|时|号|日|月|年|天|笔|人)", " ", source)


def ambiguous_amount(text: str) -> bool:
    source = money_source(text)
    number = r"(?<![A-Za-z0-9.+\-])([+\-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*([万千]?)(?![A-Za-z0-9.])"
    values: set[float] = set()
    for match in re.finditer(number, source):
        values.add(float(match.group(1).replace(",", "")) * {"万": 10000, "千": 1000}.get(match.group(2), 1))
    chinese = re.findall(r"[零〇一二两三四五六七八九十百千万亿]+\s*(?:元|块)", source)
    if re.fullmatch(r"[零〇一二两三四五六七八九十百千万亿]+", source.strip()):
        chinese.append(source.strip() + "元")
    # Missing place values in “一百五/一千二” are colloquial and ambiguous.
    if any(re.search(r"[百千万][一二两三四五六七八九]\s*(?:元|块)$", item) for item in chinese):
        return True
    return len(values) > 1 or bool(values and chinese) or len(set(chinese)) > 1


def ambiguous_target(text: str) -> bool:
    source = business_text(text)
    references = re.findall(r"(?:转账给|转给|汇给|打给|给|向|收款人(?:改成|改为|换成|是|为))\s*(?:我(?:的)?|通讯录里(?:的)?|联系人里(?:的)?)*([一-龥A-Za-z0-9·_-]{1,24}?)(?=\s*(?:转账|转|汇款|打钱|\d+(?:元|块)|[，,。\s]|$))", source)
    names: set[str] = set()
    for reference in references:
        names.update(name.strip() for name in re.split(r"或(?:者)?|还是|和|与|、", reference) if name.strip())
    if len(names) > 1:
        return True
    if re.fullmatch(r"(?:[一-龥A-Za-z·_-]{2,16}|1\d{10})(?:或(?:者)?|还是)(?:[一-龥A-Za-z·_-]{2,16}|1\d{10})", source.strip()) and not re.search(r"元|块", source):
        return True
    return bool(re.search(r"不是(?:给|向|转给)|收款人[^，,。]*(?:不对|错了)", source))


def nonexecuting_action(text: str) -> bool:
    source = business_text(text)
    mutation = r"转账|给.{1,20}转|申购|赎回|买入|冻结|解冻|挂失|解挂|锁定|取消.{0,12}订阅"
    if re.search(r"(?:假设|如果|若是|要是)", source) and re.search(mutation, source):
        return True
    return bool(re.search(r"如何|怎么(?:样)?(?:才能|操作|办理|给|把)|怎样|有什么(?:要求|条件)|需要什么", source) and re.search(mutation, source))


def requested_date_range(text: str, *, now: datetime | None = None) -> dict[str, Any] | None:
    """Resolve stated local calendar bounds; inclusive start and end dates."""
    source = business_text(text)
    current = now or datetime.now(timezone(timedelta(hours=8)))
    if current.tzinfo is not None:
        current = current.astimezone(timezone(timedelta(hours=8)))
    today = current.date()
    if re.search(r"或(?:者)?|还是|不对", source) and re.search(r"今天|昨天|本月|上月|今年|去年|最近|近\d|\d{1,2}月|\d{4}-", source):
        return {"error": "DATE_RANGE_AMBIGUOUS"}

    def resolved(start: date, end: date):
        if start > end or (end - start).days > 3660:
            return {"error": "DATE_RANGE_INVALID"}
        return {"start": start.isoformat(), "end": end.isoformat()}

    recent = re.search(r"(?:最近|近|过去|这)\s*(\d+|[一二两三四五六七八九十]+)\s*(天|周|星期)", source)
    if recent:
        numerals = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        token = recent.group(1)
        days = int(token) if token.isdigit() else numerals.get(token, 0)
        if "十" in token and len(token) > 1:
            left, right = token.split("十", 1)
            days = numerals.get(left, 1) * 10 + numerals.get(right, 0)
        days *= 7 if recent.group(2) != "天" else 1
        if not 1 <= days <= 3660:
            return {"error": "DATE_RANGE_INVALID"}
        return resolved(today - timedelta(days=days - 1), today)
    if re.search(r"最近一周|近一周", source):
        return resolved(today - timedelta(days=6), today)
    weekday = re.search(r"(上周|上星期|本周|这周)([一二三四五六日天])", source)
    if weekday:
        offset = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}[weekday.group(2)]
        selected = today - timedelta(days=today.weekday()) + timedelta(days=offset - (7 if weekday.group(1) in {"上周", "上星期"} else 0))
        return resolved(selected, selected)
    if re.search(r"(?:上周|上星期|本周|这周)(?![一二三四五六日天])", source):
        monday = today - timedelta(days=today.weekday())
        if "上周" in source or "上星期" in source:
            return resolved(monday - timedelta(days=7), monday - timedelta(days=1))
        return resolved(monday, today)

    atom = r"(?<!\d)(?:(20\d{2})[-/.年])?(\d{1,2})[-/.月](\d{1,2})(?:日|号)?"
    interval = re.search(atom + r"\s*(?:到|至|~|～|—|-)\s*" + atom, source)
    try:
        if interval:
            y1, m1, d1, y2, m2, d2 = interval.groups()
            year = today.year - 1 if "去年" in source else today.year
            return resolved(date(int(y1 or y2 or year), int(m1), int(d1)), date(int(y2 or y1 or year), int(m2), int(d2)))
        short = re.search(atom + r"\s*(?:到|至|~|～|—)\s*(\d{1,2})(?:日|号)", source)
        if short:
            year_text, month_text, first, last = short.groups()
            selected_year = int(year_text or (today.year - 1 if "去年" in source else today.year))
            return resolved(date(selected_year, int(month_text), int(first)), date(selected_year, int(month_text), int(last)))
        if re.search(r"(?:月\d{1,2}(?:日|号)?|\d{4}-\d{1,2}-\d{1,2})\s*(?:到|至|~|～|—)", source):
            return {"error": "DATE_RANGE_UNRESOLVED"}
        month = re.search(r"(?:(20\d{2})年|去年|今年)?\s*(\d{1,2})月(?:份)?(?!\d)", source)
        if month:
            year = int(month.group(1) or (today.year - 1 if "去年" in source else today.year))
            number = int(month.group(2))
            start = date(year, number, 1)
            end = date(year + (number == 12), 1 if number == 12 else number + 1, 1) - timedelta(days=1)
            return resolved(start, end)
        single = re.search(atom, source)
        if single:
            year_text, month_text, day = single.groups()
            selected = date(int(year_text or (today.year - 1 if "去年" in source else today.year)), int(month_text), int(day))
            return resolved(selected, selected)
        if "昨天" in source:
            return resolved(today - timedelta(days=1), today - timedelta(days=1))
        if "前天" in source:
            return resolved(today - timedelta(days=2), today - timedelta(days=2))
        if "明天" in source:
            return resolved(today + timedelta(days=1), today + timedelta(days=1))
        if "后天" in source:
            return resolved(today + timedelta(days=2), today + timedelta(days=2))
        if "去年" in source:
            return resolved(date(today.year - 1, 1, 1), date(today.year - 1, 12, 31))
    except ValueError:
        return {"error": "DATE_RANGE_INVALID"}
    unresolved = r"过去|季度|半年|前年|半个月|(?:周|星期)[一二三四五六日天]|\d{1,2}月\d{1,2}|\d{4}[-/年]|\d+天|到.*(?:日|号)"
    if re.search(r"最近|近期", source) and not re.search(unresolved, source):
        # A plain "最近" ("recently") is a common, not an ambiguous, request:
        # answer it for the last 30 days; the reply states the range used.
        return resolved(today - timedelta(days=29), today)
    if re.search(r"最近|" + unresolved, source):
        return {"error": "DATE_RANGE_UNRESOLVED"}
    return None
