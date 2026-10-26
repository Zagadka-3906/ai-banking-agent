"""Question-scoped projections of verified records, never inferred account facts."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re
from typing import Any, Mapping


def banking_today() -> date:
    return datetime.now(timezone(timedelta(hours=8))).date()


def subscription_subject(text: str) -> str | None:
    """Literal selectors survive even when the understanding model omits them."""
    subjects = set(re.findall(r"(?i)(?<![A-Za-z0-9])SUB-[A-Za-z0-9_-]+|视频会员|Apple|苹果", text))
    normalized = {"Apple" if value.casefold() in {'apple', '苹果'} else value.upper() if value.upper().startswith('SUB-') else value for value in subjects}
    return next(iter(normalized)) if len(normalized) == 1 else None


def contact_question(text: str) -> bool:
    if re.search(r"转账|到账|流水|账单", text) and not re.search(r"保存|通讯录|联系人簿|常用收款人|收款人列表|联系人列表", text):
        return False
    return bool(re.search(r"常用收款人|联系人(?:簿|列表|管理)?|通讯录|收款人列表|(?:是否|是不是|有没有|怎么确认).{0,20}(?:已保存|保存过)|是否已保存", text))


def needs_contact_records(text: str) -> bool:
    return not bool(re.search(r"(?:怎么|如何|哪里|哪儿).{0,8}(?:查看|打开|找到|进入).{0,8}(?:列表|联系人|通讯录)|(?:列表|联系人|通讯录).{0,6}(?:在哪|入口)", text))


def literal_records(rows: list[dict[str, Any]], question: str, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    matches = [(index, match.start(), match.end()) for index, row in enumerate(rows) for field in fields
               if isinstance(row.get(field), str) and row[field]
               for match in re.finditer(re.escape(row[field]), question, re.I)]
    selected = {index for index, start, end in matches
                if not any(other_start <= start and end <= other_end and other_end - other_start > end - start
                           for _, other_start, other_end in matches)}
    return [row for index, row in enumerate(rows) if index in selected]


def contact_documents(data: Mapping[str, Any], state: Mapping[str, Any]) -> list[dict[str, Any]]:
    from bank_agent.verification import valid_text_field

    rows = data.get("data")
    if (data.get("code") != 200 or not isinstance(rows, list) or len(rows) > 500
            or any(not isinstance(row, dict) or not all(valid_text_field(row.get(key), max_length=120)
                                                       for key in ("contact_id", "name", "phone")) for row in rows)
            or len({row["contact_id"] for row in rows}) != len(rows)):
        raise ValueError("Invalid contact book")
    question = str(state.get("user_input") or "")
    selected = literal_records(rows, question, ('name', 'contact_id'))
    listing = bool(re.search(r"(?:哪些|所有|全部|我的).{0,5}(?:联系人|收款人)|联系人列表|收款人列表|通讯录", question))
    if selected:
        texts = [f"{row['name']}已保存在本人联系人簿，可在侧边栏 → 联系人中查看和选择为收款人。" for row in selected]
    elif listing:
        texts = [f"本人的联系人簿共保存{len(rows)}位联系人。"]
        texts += [f"已保存联系人：{row['name']}。" for row in rows[:45]]
        if len(rows) > 45:
            texts.append("这里只展示前45位，请在侧边栏 → 联系人查看完整列表。")
    else:
        texts = ["已读取本人的完整联系人簿，未找到与本轮所问姓名匹配的已保存联系人；这不表示该人没有银行账户或不能收款。"]
    return [{"id": f"read.contacts.{index}", "source": "core://contacts", "text": text, "required": True}
            for index, text in enumerate(texts)]


def subscription_view(data: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    """Select only from an already validated, confirmed Core query result."""
    question = str(state.get("user_input") or "")
    target = subscription_subject(question) or state.get("target")
    rows = list(data["data"])
    literal_rows = literal_records(rows, question, ('merchant', 'subscription_id'))
    if literal_rows:
        rows = literal_rows
        target = '、'.join(row['merchant'] for row in rows)
    elif target:
        def key(value: str) -> str:
            return re.sub(r"\s|订阅$", "", value).casefold()
        rows = [row for row in rows if str(target) == row["subscription_id"] or key(str(target)) == key(row["merchant"])]
    focus = ("amount" if re.search(r"多少钱|扣多少|每期金额|每期.{0,5}多少", question)
             else "charge" if re.search(r"什么时候|何时|下次|扣款日期|扣款时间", question)
             else "status" if re.search(r"生效|状态|有效|还在|是否.*取消", question) else "list")
    return {**data, "data": rows, "query_target": str(target or ""), "query_focus": focus, "as_of": banking_today().isoformat()}


def charge_description(row: Mapping[str, Any], as_of: date) -> str:
    value = str(row.get("next_charge") or "")
    if row.get("status") == "CANCELLED":
        return f"原计划扣款 {value}"
    try:
        planned = date.fromisoformat(value)
    except ValueError:
        return "登记的扣款日期无效，无法确认下一次扣款时间"
    if planned < as_of:
        return f"原记录计划扣款 {value}（已过期；尚未更新，无法确认下一次扣款时间或是否已扣款）"
    return f"{'预计下次扣款' if row.get('status') == 'CANDIDATE' else '登记的下次扣款'} {value}（计划日期，不是已扣款凭证）"
