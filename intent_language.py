"""Deterministic language boundaries; no model, credentials or banking calls."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any


def business_text(text: str) -> str:
    """A memo is data, and cannot select an intent, a payee or an amount."""
    return re.split(r"备注|附言", text, maxsplit=1)[0].strip("，,；; ")


def inquiry_focus(text: str) -> str:
    """Exclude a leading asserted cause only when choosing a help topic.

    Callers must retain the original text for entities, model understanding,
    execution restrictions and authorization. Questions and conditions are
    never removed by this presentation/routing boundary.
    """
    source = business_text(text)
    parts = re.split(r'(?<=[，,。；;!?！？])', source)
    while len(parts) > 1 and re.match(r'^\s*(?:既然|因为|由于|鉴于|考虑到)', parts[0]):
        if re.search(r'是否|是不是|有没有|能否|能不能|怎么|如何|为什么|为何|请|帮我|[吗么？?“”\"‘’]', parts[0]):
            break
        if not ''.join(parts[1:]).strip('，,。；;!?！？ '):
            break
        parts.pop(0)
    return ''.join(parts).strip()


_TASK_START = r"(?:请|麻烦|帮我|再|另外|同时)?\s*(?:给|向|查询|查看|查|看看|看|分析|冻结|解冻|挂失|解挂|申购|赎回|取消|关闭|申请|锁定|预留|(?:本月|上月|这个月|上个月|今天|今年)?(?:余额|账单|银行卡|卡片|订阅|持仓))"
_EXPLICIT_SPLIT = re.compile(r"然后|接着(?!\s*(?:原来|刚才|之前|上次)的?(?:转账|预约转账|申购|赎回|调额))|之后|[，,]再(?!(?:说|看(?:看)?|确认)(?:[，,。；;!?！？\s]|$))|；|;|并且|，并|同时|以及|顺便|(?=再(?:帮我|查一下|看一下|分析))")
_NO_CONFIRM = r'(?:我)?(?:(?:先|暂时|暂|现在|目前)\s*)*(?:不|别|不要)(?:急着)?(?:点(?:击)?)?确认(?:了|吧)?'
_WAIT_APPROVAL = (r'等(?:我)?(?:看到|看见|看过|核对)?(?:确认|审批)(?:卡片|卡|信息|界面)'
                  r'(?:出来|出现|显示)?(?:之后|以后|后)?(?:[，,]\s*)?(?:我)?(?:再(?:说|看(?:看)?|确认))?')
_APPROVAL_QUALIFIER = re.compile(r'(?:' + _NO_CONFIRM + '|' + _WAIT_APPROVAL + r')(?:[，,]\s*(?:' + _NO_CONFIRM + '|' + _WAIT_APPROVAL + '))*')


def split_tasks(text: str) -> list[str]:
    segments = re.split(r"(备注|附言)", text, maxsplit=1)
    source = segments[0]
    # Do not split a contact list, product comparison, number or partial slot.
    def separator(match):
        before, after = source[:match.start()], source[match.end():]
        # A standalone continuation reference is context for the following
        # request, not an incomplete first operation in a compound workflow.
        if re.fullmatch(r'\s*(?:那|就)?(?:先)?(?:回到|继续|接着|恢复)(?:原来|刚才|之前|上次)?的?(?:转账|预约转账|申购|赎回|调额)(?:任务)?\s*', before):
            return match.group()
        # A procedural qualifier does not name a second banking operation.
        if (re.match(r"\s*(?:请|麻烦|帮我|替我|先|再|我想)*\s*(?:看(?:一?下|看)?|了解|说明|解释)?\s*(?:怎么|如何|怎样|办理流程|操作步骤)", after)
                and not re.search(r"余额|账单|卡片|银行卡|订阅|理财|申购|赎回|冻结|解冻|挂失|转账|预约|生日", after)):
            return match.group()
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
    qualified: list[str] = []
    for part in parts:
        # Waiting to inspect an approval is a condition on the preceding
        # request, not another banking task or authority to confirm it.
        # Full matching retains any independent query or operation as a task.
        if qualified and _APPROVAL_QUALIFIER.fullmatch(part):
            qualified[-1] += '；' + part
        else:
            qualified.append(part)
    parts = qualified
    if parts and len(segments) == 3:
        parts[-1] += segments[1] + segments[2]
    return parts


def normalize_money_separators(text: str) -> str:
    # Sanitization folds Chinese commas into ASCII. A clause separator before
    # "300元" is not part of the number; a digit-comma-digit thousands group is.
    return re.sub(r"(?<!\d),(?=\s*[+\-]?\d)", " ", text)


def money_source(text: str) -> str:
    source = normalize_money_separators(business_text(text).replace("−", "-").replace("－", "-"))
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


_PAYEE_PREFIX = (
    r"(?:我的|我(?=老婆|爱人|媳妇|妻子|丈夫|老公|夫人|儿子|女儿|闺女|兄弟|兄妹|姐妹|哥哥|弟弟|姐姐|妹妹"
    r"|爸爸|妈妈|父亲|母亲|父母|朋友|同事|同学|家人|对象|通讯录|联系人)|通讯录里(?:的)?|联系人里(?:的)?)*"
)
_PAYEE_TOKEN = r"((?:我(?=\s*(?:转账|转|汇款|打钱|\d+(?:元|块)))|(?!我)[一-龥A-Za-z0-9·_-]{1,24}?))"


def recipient_source(text: str) -> str:
    """Separate an instruction to the assistant from a payment recipient."""
    source = business_text(text)
    source = re.sub(r'(?:给|向)我(?=(?:先|再|只|仅)?(?:核对|看(?:看|一?下)?|展示|解释|说明|查(?:询|看)?|列出|显示|生成))', '请', source)
    source = re.sub(r'(?:^|[，,。；;!?！？])\s*(?:关于)?(?:给|向)[一-龥A-Za-z0-9·_-]{1,12}的\s*(?:知道|明白|了解|看到|清楚|收到)[了吧啊]*[，,。；;!?！？]?\s*', '；', source)
    return source.strip('；')


def recipient_references(text: str) -> set[str]:
    """Literal candidate spans, without treating a grammatical guess as identity."""
    source = recipient_source(text)
    references = re.findall(
        r"(?:转账给|转给|汇给|打给|给|(?<![意方倾])向|收款人(?:改成|改为|换成|是|为|就是|定为|设为)?(?:\s*给)?)\s*"
        + _PAYEE_PREFIX + _PAYEE_TOKEN
        + r"(?=\s*(?:转账|转|汇款|打钱|\d+(?:元|块)|[，,。\s]|$))",
        source,
    )
    names: set[str] = set()
    for reference in references:
        for name in re.split(r"或(?:者)?|还是|和|与|、", reference):
            name = re.sub(r"^(?:就是|也是|还是|给|向)", "", name).strip()
            if name and not re.search(r"麻烦|核对|确认|帮我|请帮|一下|查看|查询|解释|说明|展示|办理|看看|是否|要求|信息|金额|流水|记录|这[个两笔位项点次样]|那[个两笔位项点次样]|上述|以上|两者|两者都|两样|前述", name):
                names.add(name)
    return names


def ambiguous_target(text: str) -> bool:
    source = recipient_source(text)
    names = recipient_references(source)
    if len(names) > 1:
        return True
    if re.fullmatch(r"(?:[一-龥A-Za-z·_-]{2,16}|1\d{10})(?:或(?:者)?|还是)(?:[一-龥A-Za-z·_-]{2,16}|1\d{10})", source.strip()) and not re.search(r"元|块|[吗么?？]|是否|是不是|现在|仍", source):
        # In "想了想还是先取消这次申购", the right side is an
        # imperative about the current business, not a second payee name.
        # Explicit recipient alternatives were already checked above.
        continuation = re.search(r'还是([^，,。；;!?！？]+)$', source)
        if continuation and pending_withdrawal_clause(continuation.group(1), current_discussion=True):
            return False
        return True
    return bool(re.search(r"不是(?:给|向|转给)|收款人[^，,。]*(?:不对|错了)", source))


def nonexecuting_action(text: str) -> bool:
    source = business_text(text)
    mutation = r"转账|给.{1,20}转|付款|支付|扣款|申购|赎回|买入|冻结|解冻|挂失|解挂|锁定|(?:取消|查看|查询).{0,12}(?:订阅|代扣)"
    if re.search(r"(?:假设|如果|若是|要是)", source) and re.search(mutation, source):
        return True
    if re.search(r"(?:如何|怎么(?:样)?(?:才能|操作|办理|给|把)|怎样|有什么(?:要求|条件)|需要什么)", source) and re.search(mutation, source):
        return True
    return bool(re.search(r"(?:能否|是否|能不能|会不会|可以.*吗|能.*吗|会.*吗|是不是|需要.*[吗么？?]|需要先确认)", source) and re.search(mutation, source))


def action_request_clause(text: str) -> str | None:
    """Find an affirmative request preceding a question about how to continue.

    A procedure question containing an action verb is not such a request.
    This supplies conversational structure, never consent or banking facts.
    """
    for clause in re.split(r"[，,。；;!?！？]", business_text(text)):
        clause = clause.strip()
        clause = re.sub(r'^(?:不过|但是|那么|好的|接下来)\s*', '', clause)
        if re.search(r"如何|怎么|怎样|为什么|为何|多久|多长|何时|什么时候|进度|流程|步骤|时效|规则|影响|费用|需要|假设|如果|要是|若是|能否|能不能|是否|什么|不要|别|不必|不想|不打算|[吗么“”\"‘’]", clause):
            continue
        if re.match(r"^(?:那|就|现在)?(?:我(?:想|要|需要)?|请|麻烦|帮我|替我|给我)?\s*(?:先|现在|直接)?(?:预约(?:一笔)?转账|(?:安排|发起)(?:一笔)?预约转账|给.{1,24}转|向.{1,24}转|转账|转给|汇给|申购|赎回|冻结|解冻|挂失|解挂|把.{0,24}(?:限额|额度).{0,4}(?:改|调|设|补充|明确))", clause):
            return clause
    return None


_WITHDRAW_COMMAND = re.compile(
    r'^(?:(?:那(?:么)?|就|好的|对|所以|因此|但是|但|不过|还是|最终|最后|我(?:想|要|决定)?|决定|请|麻烦|帮我|替我|先|现在)\s*)*(?:取消|撤销|放弃|停止)')
_WITHDRAW_QUESTION = re.compile(
    r'如何|怎么|怎样|为什么|为何|什么|多久|何时|能否|能不能|是否|会不会|可以|可不可以'
    r'|(?:吗|(?<![那这什怎])么)(?=\s*(?:[，,。；;!?！？]|$))')
_WITHDRAW_NEGATED = re.compile(
    r'(?:不(?:用|必|想|打算|再|需要)?|别|不要|无需)(?:再|先|现在|立即|继续|帮我|替我)*(?:取消|撤销|放弃|停止)')
_WITHDRAW_REQUEST_SPAN = re.compile(
    r'(?:请|麻烦|帮我|替我|决定|先|现在|最终|最后|还是)'
    r'(?:(?:先|现在|暂时|就|还是|帮我|替我)\s*)*(?:取消|撤销|放弃|停止)')
_WITHDRAW_SAVED_OBJECT = re.compile(r'任务|工单|审批|授权|已完成|已执行|已扣款|已提交|已成交')


def _withdrawal_command_span(clause: str) -> re.Match[str] | None:
    """Validate an imperative span without enumerating its thought preamble.

    The caller separately requires a pending/current object in this clause
    and validates the entire utterance before binding a discussion.
    """
    direct = _WITHDRAW_COMMAND.match(clause)
    if direct:
        return direct
    command = _WITHDRAW_REQUEST_SPAN.search(clause)
    if command is None:
        return None
    prefix = clause[:command.start()]
    if re.search(r'解释|说明|分析|介绍|描述|谈谈|提醒|告诉|复述|转述|翻译|讨论|商量|说|建议|声称|表示|提到'
                 r'|不支持|无法|禁止|不能|不可|不允许|不想|不希望|不打算|不准备|无需|不必|未决定|没(?:有)?决定'
                 r'|昨天|前天|上周|上个月|去年|当时|明天|后天|下周|下次', clause[:command.end()]):
        return None
    if re.search(r'考虑(?:中|着)?$|^(?:我)?(?:以后|稍后|晚点|等会|到时候)', prefix):
        return None
    return command


def _withdrawal_command_in_clause(clause: str) -> bool:
    return _withdrawal_command_span(clause) is not None


def workflow_abandonment_clause(text: str) -> str | None:
    """A terminal decision to stop proceeding, rather than a temporary hold.

    This supplies no business identity or undo authority. Callers must still
    reject questions/history and bind the original text to one discussion.
    """
    clauses = [part.strip() for part in re.split(r'[，,。；;!?！？]', business_text(text)) if part.strip()]
    if clauses:
        terminal = re.search(r'(?:我)?(?:这次|本次|现在|当前)?(?:不再(?:继续)?|不继续)'
                             r'(?:提交|办理|办|处理)(?:下去)?了(?:吧)?$', clauses[-1])
        if terminal:
            prefix = clauses[-1][:terminal.start()].strip()
            if not prefix or _withdrawal_command_in_clause(prefix):
                # Keep the full clause, including any named object before
                # the terminal stop, for the caller's binding checks.
                return clauses[-1]
    return None


def bare_withdrawal_clause(text: str) -> str | None:
    """A closing imperative may omit the object; binding belongs to the caller."""
    clauses = [part.strip() for part in re.split(r'[，,。；;!?！？]', business_text(text)) if part.strip()]
    if not clauses:
        return None
    clause = clauses[-1]
    if _WITHDRAW_SAVED_OBJECT.search(clause):
        return None
    command = _withdrawal_command_span(clause)
    if command:
        tail = clause[command.end():]
        if re.fullmatch(r'(?:掉|一下)?(?:吧|即可|就好)?', tail):
            return clause
        # Sentence-final 了 can mark a present change of mind. Require an
        # explicit decision in this same command clause; plain "取消了" is
        # still a report of a past cancellation, never a fresh command.
        if re.search(r'决定', clause[:command.end()]) and re.fullmatch(r'(?:掉)?了(?:吧|即可|就好)?', tail):
            return clause
    return None


def pending_withdrawal_clause(text: str, *, current_discussion: bool = False) -> str | None:
    """Literal withdrawal of an unfinished intention, never an undo authority.

    The cancellation and pending qualifier or current-operation reference
    must occur in one affirmative clause. A current reference still needs a
    matching existing discussion; it never authorizes undoing a saved task.
    """
    source = business_text(text)
    if re.search(r'假设|如果|若是|要是|[“”"‘’？?]', source):
        return None
    for clause in re.split(r'[，,。；;!?！？]', source):
        clause = clause.strip()
        if (_WITHDRAW_QUESTION.search(clause) or _WITHDRAW_NEGATED.search(clause)
                or _withdrawal_past_assertion(clause) or re.search(r'取消了|撤销了', clause)):
            continue
        pending = re.search(r'待确认|未确认|尚未确认|未完成|想法|意向|讨论|提案', clause)
        command = _withdrawal_command_in_clause(clause)
        current = (re.search(r'(?:这次|本次|这笔|这一笔|这个|当前|刚才那笔)[^，,。；;!?！？]{0,24}'
                             r'(?:申购|赎回|转账|调额|限额|冻结|解冻|挂失|解挂|预约|锁定|办理)', clause)
                   and not _WITHDRAW_SAVED_OBJECT.search(clause))
        terminal = ((pending or current_discussion and current) and re.search(r'(?:取消|撤销|放弃|停止)(?:掉|吧)?$', clause)
                    and not re.search(r'解释|说明|分析|介绍|描述|谈谈|提醒|告诉|复述|转述|翻译', clause))
        pending_reference = (current_discussion and pending and (terminal or command
                             and re.search(r'(?:意向|讨论|提案)(?:吧|即可|就好)?$', clause)))
        if ((command or terminal) and (pending or current_discussion and current)
                and (pending_reference or re.search(r'申购|赎回|转账|调额|限额|冻结|解冻|挂失|解挂|预约|锁定|办理', clause))):
            return clause
    return None


def _policy_understanding_acknowledgement(clause: str) -> bool:
    """A completed understanding of policy is not completed banking activity.

    Match a whole acknowledgement, retaining the original utterance for all
    other guards. Extra past assertions or an action after it stay outside.
    """
    clause = clause.strip()
    if len(re.findall(r'已经|曾经', clause)) != 1:
        return False
    topic = r'[^，,。；;!?！？]{1,80}(?:规则|流程|步骤|条件|要求|影响|费用|手续费|时效|说明|限制|方法|政策)'
    understood = r'(?:都|也|完全|基本|很)?(?:了解|理解|弄清|搞清|听懂|看懂|掌握|熟悉|清楚|明白|知道)(?:清楚|明白)?(?:了)?'
    return bool(re.fullmatch(topic + r'(?:我|本人)?(?:已经|曾经)' + understood, clause)
                or re.fullmatch(r'(?:我|本人)?(?:已经|曾经)' + understood + r'(?:关于|有关)?' + topic + r'(?:了)?', clause))


def _withdrawal_past_assertion(clause: str) -> bool:
    """Scope completion to policy understanding, a decision, or banking activity.

    A single completed decision immediately governing the validated current
    stop is not an executed business. Keep the original clause for every
    command/object/history check, and never discount a second past marker.
    """
    markers = list(re.finditer(r'已经|曾经', clause))
    if not markers or _policy_understanding_acknowledgement(clause):
        return False
    if (len(markers) == 1 and markers[0].group() == '已经'
            and re.search(r'现在|这次|本次|这笔|这一笔|这个|当前|刚才那笔', clause)):
        marker = markers[0]
        command = _withdrawal_command_span(clause)
        if (command and command.start() == marker.end()
                and clause[command.start():].startswith('决定')):
            return False
    return True


def withdrawal_statement(text: str, *, pending_discussion: bool = False) -> bool:
    """A restrictive execution constraint alone is not a cancellation command."""
    source = business_text(text)
    if re.search(r'假设|如果|若是|要是|已(?:经)?(?:完成|执行|扣款|提交|成交)|[“”"‘’？?]', source):
        return False
    # Scope a past marker to its predicate: understanding banking rules does
    # not execute that business. Completed actions and cancellations remain
    # protected, including elsewhere in the same original utterance.
    for clause in re.split(r'[，,。；;!?！？]', source):
        if (_withdrawal_past_assertion(clause)
                and re.search(r'申购|赎回|买|卖|转账|汇款|付款|支付|扣|冻结|解冻|挂失|解挂|预约|锁定|调额|限额|办理|办好|执行|提交|成交|取消|撤销|放弃|停止', clause)):
            return False
    if _WITHDRAW_NEGATED.search(source):
        return False
    # Questions are judged per clause: a question about cancelling ("取消这个
    # 申购意向会怎样") never withdraws, but a question earlier in the message
    # ("之前只是问能不能撤单") must not poison a current stop in another
    # clause.  Each clean clause goes through the SAME validators as before
    # (span guards, saved-object, pending/current reference), plus the
    # negated-continuation shape ("这笔申购我不办了") that no single-clause
    # command pattern covered.
    clauses = [part.strip() for part in re.split(r'[，,。；;!?！？]', source) if part.strip()]
    for part in clauses:
        if (_WITHDRAW_QUESTION.search(part) or _withdrawal_past_assertion(part)
                or re.search(r'取消了|撤销了', part)
                or re.search(r'明天|后天|下周|稍后|以后|等会|到时候|下次|客服|经理|听说|据说', part)):
            continue
        if pending_withdrawal_clause(part, current_discussion=True):
            return True
        if (re.search(r'申购|赎回|转账|汇款|付款|支付|办理|测评|预约|锁定|调额|限额|冻结|解冻|挂失|解挂', part)
                and re.search(r'不办|不再|不想|不打算|不准备|不继续|不做|不进行', part)):
            return True
    if any(_WITHDRAW_QUESTION.search(part) for part in clauses):
        return False
    clause = pending_withdrawal_clause(source, current_discussion=True)
    if clause:
        return True
    bare = bare_withdrawal_clause(source)
    if bare and (pending_discussion or bare == source.strip('，,。；;!?！？ ')
                 or re.search(r'申购|赎回|转账|调额|限额|冻结|解冻|挂失|解挂|预约|锁定|办理', source)):
        return True
    if execution_constraint(source) == 'NEGATED_ACTION':
        return True
    # A negated continuation of the business itself ("现在决定先取消，不再继续
    # 这笔申购了") abandons the intention; the guards above already excluded
    # questions, past assertions and negated cancellations.
    if (re.search(r'申购|赎回|转账|汇款|付款|支付|办理|测评|预约|锁定|调额|限额', source)
            and re.search(r'(?:不再|不想|不打算|不准备)(?:继续|办理|做|进行)(?:这|那)?(?:一)?笔?', source)
            and not re.search(r'明天|后天|下周|稍后|以后|等会|到时候|下次|客服|经理|听说|据说|他们|她|他(?!们)', source)):
        return True
    # A completed cancellation in one clause must not erase a later explicit
    # current command. Without that command, retain the historical safeguard.
    if re.search(r'取消了|撤销了', source):
        return False
    if re.search(r'待确认|未确认|尚未确认|未完成|想法|意向|讨论|提案', source):
        return False
    return bool(_WITHDRAW_COMMAND.match(source))


def finite_limit_change(text: str) -> bool:
    """Recognize a new affirmative clause, not an earlier unlimited-limit quote."""
    # A contextual shortcut must never discard a question in another clause.
    # Let the full model distinguish requesting a change from asking about it.
    if re.search(r'如何|怎么|怎样|能否|能不能|是否|多少|[吗么？?]', business_text(text)):
        return False
    for clause in re.split(r"[，,。；;!?！？]", business_text(text)):
        if re.search(r"如何|怎么|怎样|假设|如果|要是|能否|能不能|是否|多少|[吗么？?]|不要|不必|不想|不能|取消|无限|不限额", clause):
            continue
        if re.search(r"(?:限额|额度).{0,8}(?:改|调|设|补充|明确)(?:成|为|到|至|置为)?\s*[¥￥]?\s*\d|(?:那|就|直接)?(?:改成|改为|调到|设为)\s*[¥￥]?\s*\d", clause):
            return True
        if re.search(r'(?:按|为|到)\s*(?:每日|每天|日)?\s*[¥￥]?\d[\d,]*(?:\.\d+)?\s*(?:元|块)?[^，,。；;]{0,12}(?:额度|限额)[^，,。；;]{0,8}(?:改|调|设)', clause):
            return True
    return False


def prior_inquiry_clause(text: str) -> str | None:
    """A current information question can follow an uncommitted action desire."""
    source = business_text(text)
    match = re.search(
        r'(?:^|[，,。；;!?！？])\s*(?:在)?(?:继续|办理|确认|执行|提交|操作|申购|赎回|转账)'
        r'[^，,。；;!?！？]{0,20}?(?:之)?前[，,]?\s*(?:我(?:想|要)?\s*)?先'
        r'(?:问(?:一下|问)?|了解(?:一下)?|咨询(?:一下)?|确认(?:一下)?|查(?:询|一下)?|核对(?:一下)?)', source)
    if not match:
        clauses = [part.strip() for part in re.split(r'[，,。；;!?！？]', source) if part.strip()]
        if len(clauses) < 2:
            return None
        background, inquiry = '，'.join(clauses[:-1]), clauses[-1]
        question = r'多久|何时|什么时候|多长时间|多少钱|多少费用|手续费|到账时间|有什么(?:条件|要求|影响)|是否|会不会|支持.{0,12}[吗么]'
        directive = r'(?:请|帮我|替我|给我).{0,12}(?:办理|执行|提交|发起|创建|转账|转给|申购|赎回|冻结|取消|修改)|(?:现在|立即|马上)(?:就)?(?:办理|执行|提交)'
        if (not action_request_clause(background) or not re.search(question, inquiry)
                or re.search(question, background) or re.search(directive, source)
                or sum(bool(action_request_clause(part)) for part in split_tasks(background)) != 1):
            return None
    else:
        inquiry = source[match.end():].strip('，,。；;!?！？：: ')
    # Preserve a genuinely independent later action as a mixed request.
    if not inquiry or any(action_request_clause(part) for part in split_tasks(inquiry)):
        return None
    return inquiry


def execution_constraint(text: str, *, current_discussion: bool = False) -> str | None:
    """Restrict executable actions without preventing read-only understanding."""
    source = business_text(text)
    mutation = r"执行|操作|预约(?:转账)?|转账|给.{1,8}转|申购|赎回|买入|冻结|解冻|挂失|解挂|申请.{0,4}卡|取消.{0,8}订阅|锁定|调额|修改限额"
    if prior_inquiry_clause(source):
        return 'PRIOR_INQUIRY'
    if (re.search(r'(?:先|仅|只)(?:想|是)?(?:了解|咨询|问问|问一下|核对|看看)[^。；;!?！？]{0,40}(?:行不行|可行|能否|是否|能不能|可以吗|可不可以)', source)
            or re.search(r'(?:先|仅|只)(?:想|是)?(?:了解|咨询|问问|问一下)[^。；;!?！？]{0,16}可行性', source)
            or re.search(r'(?:在(?:允许|支持|可设置|可接受)(?:的)?范围(?:里|内)?|符合[^，,。；;!?！？]{0,12}(?:要求|条件))\s*(?:吗|么|不)', source)
            or re.search(r'(?:是否|是不是)[^，,。；;!?！？]{0,16}(?:允许范围|符合.{0,8}(?:要求|条件))', source)):
        return 'FEASIBILITY_QUESTION'
    # A withdrawal can mention the operation affirmatively in a later clause
    # ("不冻结了，放弃冻结卡片"). Never let that mention become a new request.
    # Keep this restriction at the execution boundary, so a negated operation
    # can still accompany a genuine read or explanation in the same turn.
    if (pending_withdrawal_clause(source, current_discussion=current_discussion)
            or workflow_abandonment_clause(source)
            or re.search(r"(?:放弃|暂停|暂缓|停止|搁置)[^，,。；;!?！？]{0,12}(?:" + mutation + r"|办理)", source)
            or re.search(r'取消(?:这次|这个|本次|当前|刚才)?的?(?:申购|转账|调额|冻结)(?:意向|计划|讨论|提案|办理)', source)
            # An auxiliary verb and current-operation reference do not turn
            # "不办理这笔申购" into an affirmative purchase request.
            or re.search(r"不(?:再|会|继续)?(?:帮我|替我|实际|真的)?(?:(?:办(?:理)?|做)(?:这次|本次|这笔|这一笔|这个|当前|刚才那笔)?的?)?(?:" + mutation + r")", source)
            or re.search(r"(?:不办了|不做了|不用(?:再)?(?:往下|继续)(?:办|处理))", source)
            or re.search(r'(?:^|[，,。；;!?！？])\s*(?:我)?(?:这次|本次|现在|当前)?(?:先|暂时|目前|暂)?'
                         r'(?:不|不要|不用|别)(?:再|继续)*办(?:理)?(?:了|吧)?\s*(?:[，,。；;!?！？]|$)', source)):
        return "NEGATED_ACTION"
    if re.search(r"(?:不要|别|不用|不必|不想|不打算)(?:再|帮我|替我|现在|立即|马上|真的|实际|先)*[^，,。；;!?！？]{0,12}(?:" + mutation + r")", source):
        return "NEGATED_ACTION"
    if re.search(r"(?:解释|翻译|分析).{0,12}(?:引文|示例|这句话)", source):
        return "QUOTED_ACTION"
    if re.search(r"假设|如果|若是|要是", source):
        return "CONDITIONAL_ACTION"
    if nonexecuting_action(source) and not action_request_clause(source):
        return "PROCEDURE_QUESTION"
    return None


def requested_date_range(text: str, *, now: datetime | None = None) -> dict[str, Any] | None:
    """Resolve stated local calendar bounds; inclusive start and end dates."""
    source = business_text(text)
    # Monetary decimals and percentages are quantities, not calendar atoms.
    # Keep actual date expressions intact, including dotted month/day ranges.
    source = re.sub(r'[¥￥]\s*[+-]?\d[\d,]*(?:\.\d+)?(?:\s*(?:万元|元|块钱|块))?', ' ', source)
    source = re.sub(r'(?:[¥￥]\s*)?-?\d[\d,]*(?:\.\d+)?\s*(?:万元|元|块钱|块|%)', ' ', source)
    source = re.sub(r'((?:金额|数额)(?:为|是)?\s*)\d+\.\d{1,2}(?![\d.])', r'\1 ', source)
    def amount_reference(match):
        prefix = match.group(1)
        if re.search(r'(?:在|从|到|至|发生于|日期(?:为|是)?|时间(?:为|是)?)\s*$', prefix):
            return match.group(0)
        return prefix + ' '
    source = re.sub(r'([这那一几两]笔[^，,。；;!?！？\d]{0,24})\d+\.\d{1,2}(?![\d.])'
                    r'(?=\s*的?(?:订阅(?:代扣)?|代扣|扣款|消费|支出|收入|付款|支付|交易金额))', amount_reference, source)
    current = now or datetime.now(timezone(timedelta(hours=8)))
    if current.tzinfo is not None:
        current = current.astimezone(timezone(timedelta(hours=8)))
    today = current.date()
    date_choice = r'今天|今日|昨天|前天|明天|本月|这个月|上月|上个月|今年|去年|最近|近\d|\d{1,2}月|\d{4}-|\d+天'
    if re.search(r'(?:' + date_choice + r').{0,8}(?:或(?:者)?|还是|不对).{0,8}(?:' + date_choice + r')', source):
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
    recent_query = re.search(
        r'(?:(?:最近|近期)(?:的)?(?:这|那)?(?:些|几天|几笔|两笔|[0-9一二两三四五六七八九十]+笔|一些)?(?:的)?'
        r'|(?:这|那)(?:些|几笔|两笔|[0-9一二两三四五六七八九十]+笔)(?:最近|近期)(?:的)?)\s*'
        r'(?:交易|流水|账单|消费|收支|转账|支出|明细|记录)',
        source,
    )
    if re.search(r'今天|今日|本月|这个月|上月|上个月|今年|年度|全年|全部|所有历史', source):
        return None
    if recent_query and not re.search(unresolved, source):
        # A plain "最近" ("recently") is a common, not an ambiguous, request:
        # answer it for the last 30 days; the reply states the range used.
        return resolved(today - timedelta(days=29), today)
    if re.search(unresolved, source):
        return {"error": "DATE_RANGE_UNRESOLVED"}
    return None
