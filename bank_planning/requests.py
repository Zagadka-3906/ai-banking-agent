"""Bounded semantic requests. These describe questions, never authorize actions."""
from __future__ import annotations

from typing import Any, Mapping
import re

RESOURCES: dict[str, dict[str, Any]] = {
    'balance': {'topic': 'general', 'fields': ['total_balance', 'available_balance', 'locked_amount']},
    'transactions': {'topic': 'transfer', 'fields': ['records', 'amount', 'merchant', 'time', 'category', 'analysis']},
    'cards': {'topic': 'card', 'fields': ['records', 'status', 'daily_limit', 'channels', 'card_id']},
    'contacts': {'topic': 'contacts', 'fields': ['records', 'saved', 'name']},
    'disputes': {'topic': 'dispute', 'fields': ['records', 'status', 'created_at', 'transaction_id', 'timing']},
    'wealth_products': {'topic': 'wealth', 'fields': ['records', 'name', 'risk_level', 'min_amount', 'liquidity', 'annualized_yield']},
    'wealth_holdings': {'topic': 'wealth', 'fields': ['records', 'product_id', 'principal', 'estimated_value']},
    'risk_profile': {'topic': 'wealth', 'fields': ['risk_profile', 'risk_type', 'assessed_at', 'matching_levels']},
    'tasks': {'topic': 'tasks', 'fields': ['records', 'status', 'task_type', 'run_at', 'amount']},
    'subscriptions': {'topic': 'subscription', 'fields': ['records', 'amount', 'status', 'next_charge']},
    'general': {'topic': 'general', 'fields': []},
}
ACTS = {'QUERY', 'EXPLAIN', 'ACTION', 'UNSUPPORTED', 'UNCLEAR'}
STATUSES = {'ANSWERED', 'NEEDS_DATA', 'NEEDS_CLARIFICATION', 'NEEDS_CONFIRMATION', 'UNSUPPORTED', 'TOOL_FAILED'}
CALCULABLE_FIELDS = {'total_balance', 'available_balance', 'locked_amount', 'amount', 'principal', 'estimated_value', 'min_amount', 'daily_limit'}


def normalize_goals(value: Any, text: str, context: Mapping[str, Any]) -> list[dict[str, Any]]:
    from banking_knowledge import TOPICS
    if not isinstance(value, list) or not 1 <= len(value) <= 5:
        raise ValueError('INVALID_REQUEST_GOALS')
    # Context entities are verified observations, never arbitrary past prose.
    observed = [name for row in context.get('recent_observations', [])
                for key in ('entity_names', 'transaction_ids', 'case_ids', 'suspicious_transaction_ids') for name in row.get(key, [])
                if isinstance(name, str)]
    goals = []
    for index, item in enumerate(value):
        if (not isinstance(item, dict) or set(item) - {'act', 'resource', 'question', 'fields', 'subject', 'subjects', 'topic', 'requires_record', 'calculation'}
                or item.get('act') not in ACTS or item.get('resource') not in RESOURCES):
            raise ValueError('INVALID_REQUEST_GOAL')
        question, fields, subject = item.get('question'), item.get('fields', []), item.get('subject')
        subjects = item.get('subjects') or []
        if not isinstance(subjects, list) or len(subjects) > 5 or any(not isinstance(name, str) or not 1 <= len(name) <= 120 for name in subjects):
            raise ValueError('INVALID_GOAL_SUBJECTS')
        if item.get('requires_record') is not None and type(item['requires_record']) is not bool:
            raise ValueError('INVALID_RECORD_REQUIREMENT')
        calculation = item.get('calculation')
        if calculation is not None:
            if (not isinstance(calculation, dict) or set(calculation) != {'operation', 'field'}
                    or calculation.get('operation') not in {'difference', 'sum', 'count'}
                    or calculation.get('field') not in RESOURCES[item['resource']]['fields']
                    or item['resource'] == 'general' or item['act'] not in {'QUERY', 'EXPLAIN'}):
                raise ValueError('INVALID_CALCULATION')
            if calculation['operation'] != 'count' and calculation['field'] not in CALCULABLE_FIELDS:
                raise ValueError('INVALID_CALCULATION_FIELD')
            if calculation['operation'] == 'count' and calculation['field'] != 'records':
                raise ValueError('INVALID_CALCULATION_FIELD')
        topic = item.get('topic', RESOURCES[item['resource']]['topic'])
        if topic not in TOPICS:
            raise ValueError('INVALID_GOAL_TOPIC')
        if (not isinstance(question, str) or not 1 <= len(question.strip()) <= 300 or not isinstance(fields, list)
                or len(fields) > 8 or any(not isinstance(field, str) or field not in RESOURCES[item['resource']]['fields'] for field in fields)
                or len(fields) != len(set(fields))):
            raise ValueError('INVALID_GOAL_FIELDS')
        if subject is not None and (not isinstance(subject, str) or not 1 <= len(subject.strip()) <= 120):
            raise ValueError('INVALID_GOAL_SUBJECT')
        aliases = context.get('grounded_aliases') or {}
        known = {*observed, *aliases.keys(), *aliases.values()}
        if subject and subject not in known:
            parts = [part.strip() for part in re.split(r'和|与|、|,|，', subject)]
            if 2 <= len(parts) <= 5 and all(part in known for part in parts):
                subjects, subject = [*subjects, *parts], None
        subjects = list(dict.fromkeys([*subjects, *([subject] if subject else [])]))
        if calculation and calculation['operation'] == 'difference' and len(subjects) != 2:
            raise ValueError('CALCULATION_OPERANDS_REQUIRED')
        resolved, unclear_reference = [], False
        for name in subjects:
            if re.fullmatch(r'(?:这|那|该|上述|刚才)(?:一|个|张|笔|项|款)?(?:的)?(?:卡|卡片|银行卡|产品|交易|流水|任务|预约|工单|订阅|会员|联系人|收款人)?', name):
                candidates: list[str] = next((row.get('resource_entities', {}).get(item['resource'], [])
                                   for row in reversed(context.get('recent_observations', []))
                                   if item['resource'] in row.get('resource_entities', {})), [])
                if len(candidates) == 1:
                    resolved.append(candidates[0])
                    known.add(candidates[0])
                else:
                    unclear_reference = True
            else:
                resolved.append(name)
        subjects = list(dict.fromkeys(resolved))
        subject = subjects[0] if len(subjects) == 1 else None
        bound = not unclear_reference and all(name in text or name in known for name in subjects)
        # An ungrounded selector becomes a clarification, never a whole-list query.
        goals.append({'id': f'q{index + 1}', 'act': item['act'] if bound else 'UNCLEAR',
                      'resource': item['resource'], 'question': question.strip(), 'fields': fields,
                      'topic': topic,
                      'subject': subject.strip() if subject and bound else None,
                      'subjects': subjects if bound else [],
                      'requires_record': item['act'] == 'QUERY' or item.get('requires_record') is True or calculation is not None,
                      'needs_subject': not bound})
        if calculation is not None:
            goals[-1]['calculation'] = dict(calculation)
            if calculation['field'] not in fields:
                goals[-1]['fields'] = [*fields, calculation['field']]
    return goals


def request_goals(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = (state.get('parse_meta') or {}).get('goals')
    try:
        return validated_goals(value)
    except ValueError:
        return []


def validated_goals(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 5:
        raise ValueError('INVALID_REQUEST_GOALS')
    result = []
    for index, item in enumerate(value):
        required = {'id', 'act', 'resource', 'question', 'fields', 'subject', 'subjects', 'needs_subject', 'topic', 'requires_record'}
        if (not isinstance(item, dict) or not required <= set(item) or set(item) - required - {'calculation'}
                or item.get('id') != f'q{index + 1}' or type(item.get('needs_subject')) is not bool):
            raise ValueError('INVALID_NORMALIZED_GOAL')
        raw = {key: item[key] for key in ('act', 'resource', 'question', 'fields', 'subject', 'subjects', 'topic', 'requires_record')}
        if 'calculation' in item:
            raw['calculation'] = item['calculation']
        normalized = normalize_goals([raw], ' '.join(item['subjects']), {})[0]
        result.append({**normalized, 'id': item['id'], 'needs_subject': item['needs_subject']})
    return result


def read_only_goals(goals: list[dict[str, Any]]) -> bool:
    return bool(goals) and all(item['act'] != 'ACTION' for item in goals)


def needs_record(goal: Mapping[str, Any]) -> bool:
    return goal.get('act') == 'QUERY' or goal.get('act') == 'EXPLAIN' and goal.get('requires_record') is True


def goal_read(goal: Mapping[str, Any]) -> dict[str, Any] | None:
    if goal.get('act') in {'UNCLEAR', 'UNSUPPORTED'} or not needs_record(goal) or goal.get('resource') in {'general', 'subscriptions'}:
        return None
    return {key: value for key, value in {'tool': goal['resource'], 'subjects': goal.get('subjects') or None,
                                         'fields': goal.get('fields') or ['records']}.items() if value is not None}
