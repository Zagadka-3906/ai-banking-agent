"""Actionable, goal-bound feedback; presentation errors do not trigger reads."""
from __future__ import annotations

from typing import Any
import json


READ_ERRORS = {'MISSING_GOAL_RECORD', 'MISSING_SUBJECT_RECORD',
               'TOOL_FAILURE_MISCLASSIFIED'}


def normalize_feedback(value: Any, goals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 5:
        raise ValueError('INVALID_COVERAGE_FEEDBACK')
    ids = {goal['id'] for goal in goals}
    result = []
    for item in value:
        if (not isinstance(item, dict) or item.get('goal_id') not in ids
                or not isinstance(item.get('reason'), str) or not 1 <= len(item['reason']) <= 500):
            raise ValueError('INVALID_COVERAGE_FEEDBACK')
        action = item.get('action', 'READ' if item.get('status') == 'NEEDS_DATA' else 'REWRITE')
        if action not in {'READ', 'CALCULATE', 'REWRITE', 'CLARIFY'}:
            raise ValueError('INVALID_REPAIR_ACTION')
        result.append({'goal_id': item['goal_id'], 'status': item.get('status', 'NEEDS_DATA'),
                       'action': action, 'reason': item['reason'][:300]})
    return result


def validation_feedback(reason: str, goals: list[dict[str, Any]], details: dict[str, str] | None = None) -> list[dict[str, Any]]:
    action = 'READ' if reason in READ_ERRORS else 'CALCULATE' if reason == 'MISSING_CALCULATION_EVIDENCE' else 'REWRITE'
    advice = {
        'DRAFT_ANSWER_INVALID': 'answer必须是非空字符串，正文不超过3000字；保留直接回答本轮问题的内容。',
        'DRAFT_CITATION_SCHEMA': 'evidence_ids必须是含1至35项的JSON字符串数组；按实际引用选择facts里的id，不能为null或对象。',
        'UNKNOWN_EVIDENCE_ID': 'evidence_ids和coverage.evidence_ids只能使用本轮facts里实际出现的id，不得引用目录编号、旧证据或自行拼凑编号；检查本轮facts列表中的确切id并精确对照替换，彻底去除任何不在本轮facts中的编号；对应金额也须引用其values来源。',
        'DUPLICATE_EVIDENCE_ID': '同一evidence_ids数组中的id去重，保留实际引用来源，不增加新编号。',
        'DRAFT_UNANSWERED_SCHEMA': 'unanswered必须是至多五项的JSON字符串数组，每项不超过300字；没有真正未答问题时用[]，不能为null、对象或对象数组。',
        'DRAFT_SCHEMA': '严格输出answer字符串、evidence_ids字符串数组、unanswered字符串数组三个字段，不增加其他字段。',
        'GOAL_DRAFT_SCHEMA': '严格输出answer字符串、evidence_ids字符串数组、unanswered字符串数组、coverage目标数组四个字段，不增加其他字段。',
        'GOAL_COVERAGE_FIELDS': 'coverage中的evidence_ids必须全部是本轮facts里已有的有效id，且与本目标字段匹配。去除不在本轮facts中的编号，检查本轮facts列表中的确切id并精确对照，只保留实际引用的已知来源。',
        'UNGROUNDED_GOAL_ANSWER': '目标仍为UNCLEAR时应明确需要澄清的问题，coverage使用NEEDS_CLARIFICATION，不得声称已答完。明确目标的ANSWERED必须引用本轮已有证据；补足实际引用，不因引用遗漏额外查询账户。',
        'UNANCHORED_MONEY': '金额必须选择本次values里的值并引用来源；差额或合计只引用calculation结果，不在正文自行计算。没有calculation结果时引用逐笔记录或字段值回答，不自行汇总。',
        'UNKNOWN_NUMBER': '去掉未被本次引用证据支持的数字；日期、计数、比例也须来源。问题涉及的合计、差额或数量若没有本轮calculation结果，不要自行算出该数值，改为引用逐笔记录或对应字段回答并说明口径。步骤使用中文序号，不输出目标或证据编号。',
        'UNVERIFIED_OPERATION_CLAIM': '没有有效核心回执或实际状态证据时，不能声称已冻结、已转账或已完成其他操作。当前只问影响或规则时，直接解释“冻结后会……”等一般条件，不将条件状态写成已替用户办理，也不把说明误写成当前卡片状态；保留所问限制与影响，不用无关流程替代。已有实际成功结果也须精确引用本轮证据。“不代表已下单／不等于已扣款”是明确否定完成，不应因此改成已办理；仍须直接解释当前问题和执行前提。',
        'PROJECTED_FIELD_ABSENCE': '本轮同条记录证明该字段存在；fields/context_fields已展示值可用于回答，omitted_fields只表示未展示值，不能说原始记录缺失。删除虚假的缺字段声明，直接围绕当前问题解释；未展示值仍未知，不推断状态或另行展开无关字段。',
        'FINANCIAL_FIELD_MISMATCH': '按values对应字段准确标明总余额、可用余额或锁定金额，不能混用字段。',
        'MISSING_CALCULATION_EVIDENCE': '这项问题需要本次calculation结果及其全部操作数来源；只列出原始金额不算答完计算问题。',
        'GOAL_REVIEW_REJECTED': '依照逐项目标反馈重写回答：若涉及不支持的能力，明确说明本演示无该功能或设置入口并澄清相关现有能力；针对目标问题正面回答，保留已核验事实，不扩展无关业务。',
        'REQUEST_VALUE_AS_ACCOUNT_FACT': 'request.amount是用户输入，每个引用金额在本回答至少一次标为“你询问的金额”或“拟设额度”，也可用括号后置说明；后文可自然承接同一输入，不必反复标注。假设场景须明确条件。不能写成当前余额、实际额度、批准或执行结果，省略不必要的重复金额。',
        'UNREPORTED_GOAL_GAP': '先对照本轮目标：EXPLAIN只问流程、规则或如何准备，已说明所需条件就可完成解释；未办理、未给预算日期或未确认不是解释未答，把这些条件放正文，勿放unanswered。若所问规则或事实确实仍缺证据，才将对应目标标NEEDS_DATA；不能盲目改状态掩盖漏问。',
    }.get(reason, reason[:120])
    if details:
        # These excerpts come only from our validator, never provider errors.
        excerpt = {key: value[:160] for key, value in details.items()
                   if key in {'evidence_id', 'fragment'} and isinstance(value, str) and value}
        if excerpt:
            advice += ' 本次拒绝的位置（待修正文本，不是事实）：' + json.dumps(excerpt, ensure_ascii=False)
    return [{'goal_id': goal['id'],
             'status': 'NEEDS_CLARIFICATION' if reason == 'UNGROUNDED_GOAL_ANSWER' and goal['act'] == 'UNCLEAR' else 'NEEDS_DATA',
             'action': 'CLARIFY' if reason == 'UNGROUNDED_GOAL_ANSWER' and goal['act'] == 'UNCLEAR' else action,
             'reason': advice} for goal in goals]
