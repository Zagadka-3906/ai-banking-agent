"""Validated discussion commands; they never resolve an execution approval."""
from __future__ import annotations

from typing import Any, Mapping

from bank_agent.dialogue_state import dialogue_view

COMMANDS = {'RESUME', 'FILL', 'CORRECT', 'PAUSE', 'CANCEL'}


def normalize_command(value: Any, text: str, known_ids: set[str]) -> dict[str, str] | None:
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {'type', 'task_id', 'evidence'}
            or value.get('type') not in COMMANDS or value.get('task_id') not in known_ids
            or not isinstance(value.get('evidence'), str) or not 1 <= len(value['evidence']) <= 300
            or value['evidence'] not in text):
        raise ValueError('INVALID_DIALOGUE_COMMAND')
    return dict(value)


def apply_command(state: Mapping[str, Any], parsed: Mapping[str, Any], text: str) -> dict[str, Any] | None:
    from bank_agent.extraction import amount_from_text, scheduled_at_from_text
    from bank_agent.parsing import local_parse
    from bank_planning.intents import validate_intent_payload
    from intent_language import ambiguous_amount, ambiguous_target, business_text, execution_constraint, split_tasks

    view = dialogue_view(state)
    tasks = view['tasks']
    command = (parsed.get('parse_meta') or {}).get('task_command')
    if not command:
        return None
    try:
        command = normalize_command(command, business_text(text), {task['id'] for task in tasks})
    except (ValueError, KeyError):
        return None
    if not command:
        return None
    task = next(task for task in tasks if task['id'] == command['task_id'])
    pending = task.get('pending')
    if not isinstance(pending, dict) or not isinstance(pending.get('payload'), dict):
        return None
    kind, intent = command['type'], pending.get('intent')
    context = dict(state.get('context') or {})
    act = (parsed.get('parse_meta') or {}).get('dialogue_act')
    if kind in {'PAUSE', 'CANCEL'}:
        local_intent = local_parse(text).get('intent')
        # Cancelling a different named service does not cancel this discussion.
        if kind == 'CANCEL' and local_intent not in {'UNKNOWN', intent}:
            return None
        changed = [{**item, 'status': 'PAUSED'} if item['id'] == task['id'] else item
                   for item in tasks if kind != 'CANCEL' or item['id'] != task['id']]
        if (context.get('pending_clarification') or {}).get('intent') == intent:
            context.pop('pending_clarification', None)
        return {'parsed': dict(parsed), 'context': context, 'view': {**view, 'tasks': changed},
                'type': kind, 'updated_fields': [], 'proposal_input': False}
    if act not in {'ACTION', 'SLOT_REPLY'} or execution_constraint(text) or ambiguous_amount(text) or ambiguous_target(text):
        return None
    from bank_planning.requests import request_goals
    if any(goal['act'] in {'QUERY', 'EXPLAIN', 'UNSUPPORTED', 'UNCLEAR'} for goal in request_goals(parsed)):
        return None
    if parsed.get('intent') not in {'UNKNOWN', intent}:
        return None
    # Every additional executable clause must describe the same operation.
    # A discussion command cannot flatten a new multi-operation request.
    from bank_planning.intents import GREEN_INTENTS
    operations = {local_parse(part).get('intent') for part in split_tasks(text)} - GREEN_INTENTS - {'UNKNOWN', 'SUBSCRIPTION_QUERY'}
    if operations - {intent}:
        return None
    source = business_text(text)
    payload = dict(pending['payload'])
    target = payload.get('target')
    same_operation = [item for item in tasks if (item.get('pending') or {}).get('intent') == intent]
    if len(same_operation) > 1 and (not isinstance(target, str) or target not in source):
        return None
    incoming_target = parsed.get('target')
    if incoming_target and incoming_target != target and kind != 'CORRECT':
        return None
    updates: dict[str, Any] = {}
    amount = amount_from_text(source)
    if parsed.get('amount') is not None and parsed.get('amount') != amount:
        return None
    if amount is not None and amount > 0 and amount == parsed.get('amount'):
        updates['amount'] = amount
    if isinstance(incoming_target, str) and incoming_target in source:
        updates['target'] = incoming_target
    scheduled = scheduled_at_from_text(source)
    if scheduled is not None and scheduled == parsed.get('scheduled_at'):
        updates['scheduled_at'] = scheduled
    if kind == 'FILL':
        updates = {key: value for key, value in updates.items() if key in pending.get('missing', [])}
    if not updates and kind != 'RESUME':
        return None
    if any((parsed.get('slots') or {}).get(field + '_ambiguous') for field in updates):
        return None
    # Reconstruct only banking inputs. Tokens, approval IDs and confirmation
    # flags can never enter through a discussion command.
    allowed = {'intent', 'target', 'amount', 'date_scope', 'date_range', 'participants', 'product_ids', 'slots', 'scheduled_at', 'event_at'}
    payload = {key: value for key, value in payload.items() if key in allowed}
    payload.update(updates)
    payload['confidence'] = None
    payload['parse_meta'] = {**(parsed.get('parse_meta') or {}), 'source': 'rules_context', 'dialogue_act': 'SLOT_REPLY',
                             'resolution': 'dialogue_command', 'task_transition': kind, 'fast_path_eligible': False}
    payload = validate_intent_payload(payload)
    context['pending_clarification'] = pending
    return {'parsed': payload, 'context': context, 'view': {**view, 'tasks': [
        {**item, 'status': 'WAITING_INPUT'} if item['id'] == task['id'] else item for item in tasks]},
            'type': kind, 'updated_fields': sorted(updates), 'proposal_input': True}
