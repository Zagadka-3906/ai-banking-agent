"""Actionable, goal-bound feedback; presentation errors do not trigger reads."""
from __future__ import annotations

from typing import Any


READ_ERRORS = {'MISSING_GOAL_RECORD', 'MISSING_SUBJECT_RECORD', 'UNGROUNDED_GOAL_ANSWER',
               'TOOL_FAILURE_MISCLASSIFIED', 'UNREPORTED_GOAL_GAP'}


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


def validation_feedback(reason: str, goals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    action = 'READ' if reason in READ_ERRORS else 'CALCULATE' if reason == 'MISSING_CALCULATION_EVIDENCE' else 'REWRITE'
    advice = {
        'UNANCHORED_MONEY': '金额必须选择本次values里的值并引用来源；差额或合计只引用calculation结果，不在正文自行计算。',
        'UNKNOWN_NUMBER': '去掉未被本次引用证据支持的数字；日期、计数、比例也须来源。步骤使用中文序号，不输出目标或证据编号。',
        'FINANCIAL_FIELD_MISMATCH': '按values对应字段准确标明总余额、可用余额或锁定金额，不能混用字段。',
        'MISSING_CALCULATION_EVIDENCE': '这项问题需要本次calculation结果及其全部操作数来源；只列出原始金额不算答完计算问题。',
        'GOAL_REVIEW_REJECTED': '依照逐项目标反馈重写未答内容，保留已经核验的事实，不扩展无关业务。',
    }.get(reason, reason[:120])
    return [{'goal_id': goal['id'], 'status': 'NEEDS_DATA', 'action': action, 'reason': advice} for goal in goals]
