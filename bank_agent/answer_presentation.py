"""Readable customer prose; evidence, protocol states and receipts stay unchanged."""
from __future__ import annotations

import re
from typing import Any


ANSWER_STYLE = (
    '对用户使用直白中文，不输出PENDING、WAITING_USER、SCHEDULED_TRANSFER等内部状态或类型代码；'
    '状态按实际含义解释，等待处理、等待确认、正在处理和处理完成不能混同，处理完成也不一定表示已付款。'
    '同一回答内，每项事实、办理前提和风险提示只说明一次，不用换一种说法重复；'
    '例如解释需要先完成测评后，不再重复无档案无法申购及需要完成测评。'
    '先用简短的话回答当前问题，只有必要时补充相关条件；不复述读取、匹配、投影等取证过程，'
    '不因为检索到规则就整段讲解，也不附带未询问的其他能力限制或办理建议。'
    '问“为什么”先解释原因，不自动改成完整办理教程；问单个产品字段只回答该字段，不附带申购流程。'
    'required=true的记录和提示由服务端补齐，正文不要再改写同一内容。'
)
REVIEW_STYLE = (
    '核对用户可读性：内部状态与类型应解释为直白中文，不应向用户输出代码。'
    '同一回答内若重复相同事实、前提或风险提示，即使措辞不同也应拒绝并要求改写；'
    '例如重复联系人数量、风险提示或先测评再申购的条件。保留必要事实与限制，不因精简扩大结论。'
    '无关的内置规则、取证过程或能力介绍不能充当回答；要求删除无关展开，直接解释用户所问。'
    '特别核对为什么需要设备验证的回答：应说明验证原因，而不是罗列额度、通道、其他业务或完整办理步骤。'
)

_LABELS = {
    'PENDING': '等待处理', 'WAITING_USER': '等待你确认', 'RUNNING': '正在处理',
    'SUCCEEDED': '处理完成', 'FAILED': '处理失败', 'CANCELLED': '已取消',
    'HUMAN_TAKEOVER': '需要人工处理', 'ACTIVE': '生效中', 'FROZEN': '冻结',
    'LOST': '挂失', 'CLOSED': '已关闭', 'CANDIDATE': '待核实',
    'SCHEDULED_TRANSFER': '预约转账', 'ORDER_DRAFT': '订单草稿', 'REMINDER': '提醒',
}
_TOKEN = re.compile(r'[A-Za-z][A-Za-z0-9_-]*')


def covered_contact_listing(text: str, facts: list[dict[str, Any]]) -> set[str]:
    """An exact positive list may satisfy the same server-owned list facts.

    Match count and the complete multiset of displayed names together. Merely
    mentioning names, a partial list, negation or a hypothetical never suffices.
    Truncation limitations remain required even when displayed names match.
    """
    covered: set[str] = set()
    for row in facts:
        listing = row.get('contact_listing')
        if row.get('source') != 'core://contacts' or not isinstance(listing, dict):
            continue
        names, total = listing.get('names'), listing.get('total')
        if not isinstance(names, list) or not names or type(total) is not int:
            continue
        name_pattern = '|'.join(re.escape(name) for name in sorted(set(names), key=len, reverse=True))
        start = (r'(?:已保存的?联系人(?:有|共)|(?:你|您|本人)的?联系人簿(?:当前|目前|现在)?(?:共)?保存)'
                 r'\s*' + str(total) + r'\s*位(?:联系人)?\s*[:：]\s*')
        body = r'(?:' + name_pattern + r')(?:\s*[、，,]\s*(?:' + name_pattern + r'))*'
        for match in re.finditer(r'(?:^|(?<=[。！？\n]))\s*' + start + '(' + body + r')\s*[。]', text):
            shown = re.findall(name_pattern, match[1])
            if sorted(shown) != sorted(names):
                continue
            covered.add(row['id'])
            for fact in facts:
                if (fact.get('source') == 'core://contacts'
                        and fact['text'] in {'已保存联系人：' + name + '。' for name in names}):
                    covered.add(fact['id'])
    return covered


def question_facts(facts: list[dict[str, Any]], question: str) -> list[dict[str, Any]]:
    """Keep a frequency policy out of unrelated contact answers, not Core reads."""
    if re.search(r'常用|频率|经常|频繁|次数|多次|常转|多不多', question):
        return facts
    return [row for row in facts if row['id'] != 'guide.contacts.common_flag']


def validate_answer_focus(text: str, question: str) -> None:
    if (re.search(r'常用频率|频率标记', text)
            and not re.search(r'常用|频率|经常|频繁|次数|多次|常转|多不多', question)):
        raise ValueError('UNREQUESTED_CONTACT_FREQUENCY')


def customer_language(text: str, facts: list[dict[str, Any]]) -> str:
    """Translate whole known tokens, protecting verified names and identifiers."""
    protected = {value for row in facts for value in
                 [row.get('entity_id'), row.get('subject'), *row.get('entity_names', [])]
                 if isinstance(value, str) and value}
    resources = {row.get('source') for row in facts}
    labels = {**_LABELS, **({'ACTIVE': '正常'} if 'core://cards' in resources and 'core://subscriptions' not in resources else {})}
    # Protect whole labels as well: an English merchant/product name can
    # legitimately contain a word that also happens to be a protocol state.
    pattern = '|'.join(re.escape(value) for value in sorted(protected, key=len, reverse=True))
    pattern = '(?<![A-Za-z0-9_-])(?:' + pattern + ')(?![A-Za-z0-9_-])' if pattern else r'(?!)'
    pieces = re.split('(' + pattern + ')', text)
    for index in range(0, len(pieces), 2):
        pieces[index] = _TOKEN.sub(lambda match: labels.get(match.group(), match.group()), pieces[index])
        for label in set(labels.values()):
            pieces[index] = re.sub(re.escape(label) + r'\s*[（(]\s*' + re.escape(label) + r'\s*[）)]', label, pieces[index])
        pieces[index] = pieces[index].replace('生效中（正常）', '正常').replace('生效中(正常)', '正常')
    return ''.join(pieces)


def required_fact_present(text: str, fact: dict[str, Any]) -> bool:
    """Accept only narrowly equivalent positive counts and the same disclaimer.

    Lists, monetary values, partial-result warnings and all other required
    evidence continue to require the original server-owned text.
    """
    required = fact['text']
    if required in text:
        return True
    if fact.get('source') == 'core://contacts':
        count = re.fullmatch(r'本人的联系人簿共保存(\d+)位联系人。', required)
        if count:
            for sentence in re.split(r'[。！？\n：:]', text):
                if re.search(r'如果|假如|假设|不是|并非|是否|吗|[?？]', sentence):
                    continue
                if re.fullmatch(
                    r'\s*(?:本次(?:读取|查询)(?:结果)?显示[，,]?\s*)?'
                    r'(?:你|您|本人)(?:本人)?的?联系人簿(?:目前|当前|现在)?共?保存\s*'
                    + re.escape(count[1]) + r'\s*位联系人(?:[，,](?:因此)?没有可列出的联系人姓名)?\s*', sentence
                ):
                    return True
    return False


def append_required_fact(text: str, fact: dict[str, Any]) -> str:
    """Canonicalize narrowly equivalent notices without losing other caveats."""
    required = fact['text']
    pattern = None
    if (fact.get('source') == 'core://wealth_products'
            and required == '以上为演示产品信息，不是未来收益预测或收益保证。'):
        pattern = (r'(?:^|(?<=[。！？\n]))[ \t]*以上(?:为|是)(?:本次)?'
                   r'(?:产品货架中展示的公开产品|演示产品(?:货架)?)信息[，,]'
                   r'不是未来收益预测或收益保证[。]?')
    elif (fact.get('source') == 'core://subscriptions'
            and required == '以上为本系统订阅登记状态与原计划日期，不能证明真实会员权益、扣款结果或商户重试。'):
        pattern = (r'(?:^|(?<=[。！？\n]))[ \t]*以上(?:为|是)本系统(?:的)?'
                   r'订阅登记状态(?:与|和)原计划日期[，,]不能证明真实会员权益[、，,]'
                   r'扣款结果或商户(?:是否)?重试[。]?')
    if pattern and re.search(pattern, text):
        added = False
        def replace(match: re.Match[str]) -> str:
            nonlocal added
            result = '' if added else required
            added = True
            return result
        return re.sub(pattern, replace, text).strip()
    return text if required_fact_present(text, fact) else text + '\n' + required
