"""Read-only arithmetic over validated, current evidence, with operand provenance."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from bank_agent.evidence import LABELS, MONEY_FIELDS


def calculation_evidence(goals: list[dict[str, Any]], facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for goal in goals:
        spec = goal.get('calculation')
        if not spec or goal['act'] in {'UNCLEAR', 'UNSUPPORTED'}:
            continue
        resource, field = goal['resource'], spec['field']
        candidates = [row for row in facts if row.get('source') == 'core://' + resource
                      and not row.get('derived') and field in row.get('fields', [])]
        selectors = goal.get('subjects') or []
        operands = []
        summary_ids: list[str] = []
        collection_count = None
        if selectors:
            for name in selectors:
                matched = [row for row in candidates if row.get('subject') == name or name in row.get('entity_names', [])]
                # An absent-record statement is not a numeric operand. A named
                # collection matching multiple records is not a singular value.
                matched = ([row for row in matched if row.get('entity_id')] if spec['operation'] == 'count'
                           else [row for row in matched if field in row.get('values', {})])
                if len(matched) != 1:
                    break
                operands.append(matched[0])
            if len(operands) != len(selectors):
                continue
        else:
            summaries = [row for row in facts if row.get('source') == 'core://' + resource and 'collection_size' in row
                         and not row.get('selection') and (spec['operation'] == 'count' or row.get('complete') is True)]
            if len(summaries) != 1:
                continue
            summary_ids = [summaries[0]['id']]
            collection_count = summaries[0]['collection_size']
            if type(collection_count) is not int or not 0 <= collection_count <= 5000:
                continue
            operands = [row for row in candidates if row.get('entity_id') and (spec['operation'] == 'count' or field in row.get('values', {}))]
            if spec['operation'] != 'count' and len(operands) != collection_count:
                continue
        if len({row['id'] for row in operands}) != len(operands):
            continue
        try:
            numbers = [] if spec['operation'] == 'count' else [Decimal(row['values'][field]) for row in operands]
            if any(not value.is_finite() for value in numbers):
                continue
            if spec['operation'] == 'difference' and len(numbers) == 2:
                value = abs(numbers[0] - numbers[1])
            elif spec['operation'] == 'sum':
                value = sum(numbers, Decimal(0))
            elif spec['operation'] == 'count':
                value = Decimal(collection_count if collection_count is not None else len(operands))
            else:
                continue
        except (InvalidOperation, KeyError, TypeError, ValueError):
            continue
        if not value.is_finite():
            continue
        label = {'difference': '差额', 'sum': '合计', 'count': '数量'}[spec['operation']]
        text = ('与'.join(selectors) if selectors else '当前匹配记录') + '的' + LABELS.get(field, field) + label
        text += f'为¥{value:,.2f}。' if field in MONEY_FIELDS and spec['operation'] != 'count' else f'为{value}条。'
        results.append({'id': f'read.{resource}.calculation.{goal["id"]}', 'source': 'calculation://' + spec['operation'],
                        'text': text, 'fields': [field], 'entity_names': list(selectors), 'derived': True,
                        'goal_id': goal['id'], 'input_evidence_ids': [*summary_ids, *[row['id'] for row in operands]],
                        'values': {field: str(value)}, 'required': False})
    return results


def calculation_for(goal: Mapping[str, Any], facts: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((row for row in facts if row.get('goal_id') == goal['id'] and row.get('source', '').startswith('calculation://')), None)


def expand_calculation_citations(plan: dict[str, Any], facts: list[dict[str, Any]]) -> dict[str, Any]:
    """A calculation's server-owned provenance is an inseparable citation."""
    known = {row['id']: row for row in facts}
    def expand(ids: Any) -> Any:
        if not isinstance(ids, list) or any(not isinstance(key, str) for key in ids):
            return ids
        return list(dict.fromkeys([*ids, *(key for item in ids for key in known.get(item, {}).get('input_evidence_ids', []))]))
    coverage = plan.get('coverage')
    if isinstance(coverage, list):
        coverage = [{**item, 'evidence_ids': expand(item.get('evidence_ids'))} if isinstance(item, dict) else item for item in coverage]
    return {**plan, 'evidence_ids': expand(plan.get('evidence_ids')), 'coverage': coverage}
