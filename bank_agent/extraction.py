"""Deterministic slot extraction from Chinese text: amounts, payees, schedule times and participants."""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from typing import Optional


from intent_language import ambiguous_amount, ambiguous_target, normalize_money_separators, split_tasks, recipient_source


_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CHINESE_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10_000, "亿": 100_000_000}


def chinese_number(value: str) -> Optional[int]:
    """Parse a bounded Chinese integer without guessing free-form text.

    The offline parser only uses this for explicit amount/term expressions
    followed by a unit (for example ``一百元`` or ``三年``).  Returning
    ``None`` for unsupported colloquialisms is intentional: the caller then
    asks a clarification instead of inventing a number.
    """

    token = str(value or "").strip()
    if not token or any(char not in _CHINESE_DIGITS and char not in _CHINESE_UNITS for char in token):
        return None
    if token.isdigit():
        return int(token)
    total = 0
    section = 0
    number = 0
    for char in token:
        if char in _CHINESE_DIGITS:
            number = _CHINESE_DIGITS[char]
            continue
        unit = _CHINESE_UNITS[char]
        if unit < 10_000:
            # ``十``/``百`` may omit the leading 一 (十 = 10, 十二 = 12).
            section += (number or 1) * unit
        else:
            section += number
            number = 0
            if section == 0:
                section = 1
            total += section * unit
            section = 0
        number = 0
    result = total + section + number
    return result if result >= 0 else None


def amount_from_text(text: str) -> Optional[float]:
    if len(split_tasks(text)) == 1 and ambiguous_amount(text):
        return None
    # Object identifiers and calendar fields never supply a missing amount.
    money_text = normalize_money_separators(re.split(r"备注|附言", text, maxsplit=1)[0].replace("−", "-").replace("－", "-"))
    money_text = re.sub(r"(?i)\b(?:card-[0-9a-z]+|[a-z]{1,8}\d{3,})\b", " ", money_text)
    money_text = re.sub(r"(?<!\d)\d{11,19}(?!\d)", " ", money_text)
    money_text = re.sub(r"(?:尾号|末四位|后四位)\s*\d{4}", " ", money_text)
    money_text = re.sub(r"20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?", " ", money_text)
    money_text = re.sub(r"\d{1,2}[:：]\d{2}(?::\d{2})?|\d{1,2}\s*(?:点|时|号|日|月|年)", " ", money_text)
    if re.search(r"大概|大约|差不多|左右|上下|至少|最多|不超过|不少于|以上|以下|不到|百来|千把|几百|几千|两三|三四|四五|五六|(?:\d|[十百千万])\s*[多余来]", money_text):
        return None
    if re.search(r"\d\s*(?:到|至|~|～|-)\s*\d", money_text):
        return None
    number = r"([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)"
    match = re.search(r"(?<![A-Za-z0-9.,+\-])" + number + r"\s*([万千]?)\s*(?:元钱|元|块钱|块|人民币)(?![A-Za-z0-9])", money_text)
    multiplier = 1
    if match:
        multiplier = {"万": 10000, "千": 1000}.get(match.group(2), 1)
    if not match:
        match = re.search(r"(?:¥|￥)\s*" + number + r"(?!\d|[.,]\d)", money_text)
    if not match and re.search(
        # A bare number is money only next to a money verb.  The measure word
        # 笔 and the limit verbs 调到/调至/限额 are part of that context: users
        # say "再转一笔300" and "限额调到8000" without repeating 元.
        r"转账|转给|汇给|申购|赎回|锁定|预留|分摊|调额|额度|限额|改成|改为|改到|调到|调至|设为|设置为|调整为"
        r"|转\s*[+\-]?\d|(?:一|1)?笔\s*[+\-]?\d",
        money_text,
    ):
        match = re.search(r"(?<![A-Za-z0-9.,+\-])" + number + r"(?![A-Za-z0-9]|[.,]\d)", money_text)
    if match:
        # Do not reinterpret the positive suffix of a signed/approximate value.
        if re.search(r"(?:负|-|约|大致)\s*$", money_text[:match.start()]):
            return None
        try:
            value = round(float(match.group(1).replace(",", "")) * multiplier, 2)
            return value if math.isfinite(value) and 0 < value <= 100_000_000 else None
        except ValueError:
            return None
    match = re.search(r"([负+-]?[零〇一二两三四五六七八九十百千万亿]+)\s*(?:元|块|人民币)", money_text)
    if not match:
        return None
    numeral = match.group(1)
    if numeral.startswith(("负", "-")) or re.search(r"[一二两三四五六七八九]{2}", numeral):
        return None
    if re.search(r"(?:约|大致)\s*$", money_text[:match.start()]):
        return None
    parsed = chinese_number(numeral.lstrip("+"))
    return float(parsed) if parsed is not None and 0 < parsed <= 100_000_000 else None


# The demo bank's product shelf as users name it; mirrors bank_core/wealth_data.py.
PRODUCT_IDS = ("LX00118", "HQ30060", "SG20270", "YH50365", "XH70730")
PRODUCT_ALIASES = {
    "零星宝": "LX00118", "活钱管家": "LX00118",
    "恒泉稳利": "HQ30060", "恒泉": "HQ30060",
    "双桂平衡": "SG20270", "双桂": "SG20270",
    "远航智造": "YH50365", "远航": "YH50365",
    "星汉": "XH70730", "全球多策略": "XH70730",
}
PRODUCT_ID_PATTERN = r"(?<![A-Za-z0-9])(?:" + "|".join(PRODUCT_IDS) + r")(?![A-Za-z0-9])"


# Placeholders that name no one in particular ("给联系人转账"). Taking them as a
# payee name skipped the "who?" question and ended in a vague not-found error.
GENERIC_PAYEE_WORDS = frozenset({
    "联系人", "收款人", "对方", "别人", "某人", "某个人", "一个人", "人", "谁", "他们", "她们",
    "你", "您", "我", "自己", "账户", "某个联系人", "一位联系人", "我的联系人", "通讯录",
})


def target_from_text(text: str) -> Optional[str]:
    # Preserve the original alias; the core resolves it against a contact
    # allow-list.  Never default to a person when the user omitted one.
    # 亲属称呼（爱人/儿子/兄弟…）是“关系引用”：原样传给核心，由核心到
    # 联系人簿里解析成具体收款人，离线解析器不猜测具体是谁。
    #
    # A card id is an object reference, not an alias: it must be recognised before
    # the alias table, otherwise "冻结卡片 CARD-002" parses to no target at all and
    # the core, seeing an empty card_id, applies the operation to whichever card
    # it lists first - freezing the salary card when the user pointed at another.
    text = recipient_source(text)
    if len(split_tasks(text)) == 1 and ambiguous_target(text):
        return None
    card = re.search(r"(?i)(?<![A-Za-z0-9])(card-[0-9a-z]{2,})(?![A-Za-z0-9])", text)
    if card:
        return card.group(1).upper()
    # Extract a stated name even when it is a saved contact's arbitrary label.
    # Resolution/ambiguity checks still belong to the core's contact allow-list.
    explicit = re.search(
        r"(?:转账给|转给|汇给|打给|给|向|收款人(?:改成|改为|换成|是|为))\s*"
        r"(?:我(?:的)?|通讯录里(?:的)?|联系人里(?:的)?)*"
        r"([一-龥A-Za-z0-9·_-]{1,24}?)\s*(?=转账|转|汇款|打钱|\d+(?:元|块)|[，,。\s]|$)", text,
    )
    if explicit:
        candidate = re.sub(r"再$", "", explicit.group(1))
        if candidate not in {"他", "她", "它", "同一位", "同一个", "同一个收款人", "刚才那位", "刚才那个", "那个人", "那个", "上次那个"} \
                and candidate not in GENERIC_PAYEE_WORDS:
            return candidate
    for alias in (
        "老婆", "爱人", "媳妇", "妻子", "丈夫", "老公", "夫人",
        "儿子", "女儿", "闺女", "兄弟", "兄妹", "姐妹", "哥哥", "弟弟", "姐姐", "妹妹",
        "爸爸", "妈妈", "父亲", "母亲", "父母",
        "王美", "李四", "张三", "Apple", "苹果", "视频会员",
        *PRODUCT_ALIASES, *PRODUCT_IDS,
    ):
        if alias in text:
            return {"苹果": "Apple", **PRODUCT_ALIASES}.get(alias, alias)
    phone = re.search(r"(?<!\d)(1\d{10})(?!\d)", text)
    return phone.group(1) if phone else None


def scheduled_at_from_text(text: str) -> Optional[str]:
    """Keep the user's date and clock time in the bank's Asia/Shanghai zone."""

    from datetime import timezone

    now = datetime.now(timezone(timedelta(hours=8)))
    date_value = None
    explicit = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日|号)?", text)
    month_day = re.search(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*(?:日|号)?", text)
    try:
        if explicit:
            date_value = now.replace(year=int(explicit.group(1)), month=int(explicit.group(2)), day=int(explicit.group(3)))
        elif month_day:
            # A bare month-day ("10月5号") carries no year. Take its next
            # occurrence: an anniversary or birthday that has already passed
            # this year means the one next year, never a date in the past.
            for year in (now.year, now.year + 1):
                try:
                    candidate = now.replace(year=year, month=int(month_day.group(1)), day=int(month_day.group(2)))
                except ValueError:
                    continue
                if candidate.date() >= now.date():
                    date_value = candidate
                    break
        else:
            weekday = re.search(r"(下下周|下周|下星期|本周|这周|周|星期)([一二三四五六日天])", text)
            if weekday:
                day = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}[weekday.group(2)]
                offset = day - now.weekday()
                if weekday.group(1) in {"下周", "下星期"}:
                    offset += 7
                elif weekday.group(1) == "下下周":
                    offset += 14
                elif weekday.group(1) in {"周", "星期"} and offset < 0:
                    offset += 7
                date_value = now + timedelta(days=offset)
            else:
                for marker, offset in (("大后天", 3), ("后天", 2), ("明天", 1), ("前天", -2), ("昨天", -1), ("昨晚", -1), ("今天", 0), ("今日", 0), ("下周", 7)):
                    if marker in text:
                        date_value = now + timedelta(days=offset)
                        break
        if date_value is None:
            return None
        hour, minute = 9, 0
        clock = re.search(r"(凌晨|早上|上午|中午|下午|晚上|傍晚)?\s*(\d{1,2}|[零〇一二两三四五六七八九十]+)\s*(?:点|时|[:：])\s*(半|\d{1,2}|[零〇一二两三四五六七八九十]+)?(?:分)?", text)
        if clock:
            raw_hour, raw_minute = clock.group(2), clock.group(3)
            parsed_hour = int(raw_hour) if raw_hour.isdigit() else chinese_number(raw_hour)
            parsed_minute = 30 if raw_minute == "半" else (int(raw_minute) if raw_minute and raw_minute.isdigit() else (chinese_number(raw_minute) if raw_minute else 0))
            if parsed_hour is None or parsed_minute is None:
                return None
            hour, minute = parsed_hour, parsed_minute
            if clock.group(1) in {"下午", "晚上", "傍晚", "中午"} and hour < 12:
                hour += 12
            if clock.group(1) == "凌晨" and hour == 12:
                hour = 0
        return date_value.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()
    except (ValueError, OverflowError):
        return None


def participants_from_text(text: str) -> list[str]:
    """Extract explicit AA names; never treat instructions/headcounts as names."""

    body = re.split(r"(?i)AA|分摊|平摊|备注|附言", text, maxsplit=1)[0].strip()
    if not re.search(r"和|与|跟|、|参与人", body):
        return []
    # A total can precede the recipient list ("300元，和李四、王美AA").
    body = re.sub(r"^.*?(?:元|块)[，,\s]*", "", body)
    body = re.sub(r"^(?:请|帮我)?(?:和|跟|与|参与人(?:是|为|有|：|:)?|我和)", "", body)
    result = []
    for part in re.split(r"[、,，]|和|与|跟", body):
        candidate = re.sub(r"(?:一起|两个|三个人)$", "", part.strip())
        if candidate in {"我", "本人", "我们", "大家"}:
            continue
        if not re.fullmatch(r"[一-龥A-Za-z·_-]{2,16}|1\d{10}", candidate):
            continue
        if re.search(r"帮|请|转|吃饭|一共|总共|钱|个人|元|分摊|平摊|提醒", candidate):
            continue
        if candidate not in result:
            result.append(candidate)
    return result


def format_hint(missing: list[int]) -> str:
    return " ".join(f"{q}A" for q in missing[:3]) + (" …" if len(missing) > 3 else "")
