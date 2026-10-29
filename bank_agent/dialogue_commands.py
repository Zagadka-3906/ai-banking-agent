"""Validated discussion commands; they never resolve an execution approval."""
from __future__ import annotations

from typing import Any, Mapping
from unicodedata import normalize

from bank_agent.dialogue_state import dialogue_view

COMMANDS = {'RESUME', 'FILL', 'CORRECT', 'PAUSE', 'CANCEL'}


def _withdrawal_input(text: str) -> dict[str, Any]:
    from bank_agent.extraction import PRODUCT_ALIASES, PRODUCT_IDS, target_from_text
    from bank_agent.parsing import _local_parse_rules
    from intent_language import ambiguous_target, bare_withdrawal_clause, business_text, pending_withdrawal_clause

    whole = business_text(text)
    clause = pending_withdrawal_clause(text, current_discussion=True) or bare_withdrawal_clause(text)
    source = clause or whole
    raw = _local_parse_rules(source)
    products = {product for alias, product in PRODUCT_ALIASES.items() if alias in whole}
    products.update(product for product in PRODUCT_IDS if product in whole.upper())
    raw['_ambiguous'] = ambiguous_target(whole) or len(products) > 1
    if clause and raw.get('intent') == 'UNDO_OPERATION':
        # Here undo_kind identifies only the discussion's business type. It
        # cannot cancel a Core task or supply an execution approval.
        raw = {**raw, 'intent': (raw.get('slots') or {}).get('undo_kind'), 'target': target_from_text(clause)}
    raw['target'] = raw.get('target') or target_from_text(whole)
    return raw


def atomic_action_input(parsed: Mapping[str, Any], text: str) -> dict[str, Any] | None:
    """Compile one understood action from the whole utterance, not its prefix.

    Multiple complete operations, conflicting values or consultation goals
    remain outside this path. It supplies banking inputs, never authorization.
    """
    from bank_agent.parsing import _local_parse_rules
    from bank_planning.intents import GREEN_INTENTS
    from bank_planning.requests import request_goals
    from intent_language import ambiguous_amount, ambiguous_target, business_text, execution_constraint, split_tasks
    goals = request_goals(parsed)
    parts = split_tasks(text)
    intent = parsed.get('intent')
    if (len(parts) <= 1 or len(goals) != 1 or goals[0]['act'] != 'ACTION'
            or (parsed.get('parse_meta') or {}).get('dialogue_act') not in {'ACTION', 'SLOT_REPLY'}
            or intent in GREEN_INTENTS | {'UNKNOWN', 'SUBSCRIPTION_QUERY'}
            or execution_constraint(text) or ambiguous_amount(text) or ambiguous_target(text)):
        return None
    actions = [_local_parse_rules(business_text(part)) for part in parts]
    if any(item['intent'] in GREEN_INTENTS for item in actions):
        return None
    actions = [item for item in actions if item['intent'] not in GREEN_INTENTS | {'UNKNOWN', 'SUBSCRIPTION_QUERY'}]
    if not actions or any(item['intent'] != intent for item in actions):
        return None
    # Two explicit same-value payments still mean two actions.
    if sum(bool(item.get('target') or item.get('amount') or item.get('product_ids')) for item in actions) > 1:
        return None
    whole = _local_parse_rules(business_text(text))
    if whole['intent'] != intent:
        return None
    updates = {key: whole[key] for key in ('target', 'amount', 'scheduled_at', 'event_at', 'product_ids', 'participants')
               if whole.get(key) not in (None, [], '')}
    return {**parsed, **updates, 'slots': {**(parsed.get('slots') or {}), **(whole.get('slots') or {})}}


def normalize_command(value: Any, text: str, known_ids: set[str]) -> dict[str, str] | None:
    if value is None:
        return None
    raw_span = value.get('evidence') if isinstance(value, dict) else None
    span = normalize('NFKC', raw_span) if isinstance(raw_span, str) else None
    if (not isinstance(value, dict) or set(value) != {'type', 'task_id', 'evidence'}
            or value.get('type') not in COMMANDS or value.get('task_id') not in known_ids
            or not isinstance(value.get('evidence'), str) or not 1 <= len(value['evidence']) <= 300
            or span is None or span not in normalize('NFKC', text)):
        raise ValueError('INVALID_DIALOGUE_COMMAND')
    return {**value, 'evidence': span}


def apply_command(state: Mapping[str, Any], parsed: Mapping[str, Any], text: str) -> dict[str, Any] | None:
    from bank_agent.extraction import amount_from_text, scheduled_at_from_text
    from bank_agent.parsing import local_parse
    from bank_planning.intents import validate_intent_payload
    from intent_language import ambiguous_amount, ambiguous_target, business_text, execution_constraint, split_tasks, withdrawal_statement

    view = dialogue_view(state)
    tasks = view['tasks']
    command = (parsed.get('parse_meta') or {}).get('task_command')
    if not command or (parsed.get('parse_meta') or {}).get('scope') == 'OUT_OF_SCOPE':
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
    questionnaire = task.get('kind') == 'questionnaire'
    if kind in {'PAUSE', 'CANCEL'}:
        local_intent = local_parse(text).get('intent')
        raw = _withdrawal_input(text)
        if kind == 'CANCEL' and (raw['_ambiguous'] or not withdrawal_statement(text, pending_discussion=True)):
            return None
        origin = (task.get('quiz') or {}).get('origin_intent') if questionnaire else None
        withdrawing = (act == 'WITHDRAW' and withdrawal_statement(text, pending_discussion=True)
                       and raw.get('intent') in {'UNKNOWN', intent, origin})
        saved_target = pending['payload'].get('target') or (task.get('quiz') or {}).get('origin_target')
        if kind == 'CANCEL' and raw.get('target') and saved_target and raw['target'] != saved_target:
            return None
        siblings = [item for item in tasks if (item.get('pending') or {}).get('intent') == intent and item.get('status') != 'OFFERED']
        if kind == 'CANCEL' and len(siblings) > 1 and not raw.get('target'):
            return None
        # Cancelling a different named service does not cancel this discussion.
        if kind == 'CANCEL' and local_intent not in {'UNKNOWN', intent} and not withdrawing:
            from bank_planning.intents import GREEN_INTENTS
            from bank_agent.risk_quiz import RISK_QUIZ_INTENTS
            if not questionnaire or local_intent not in GREEN_INTENTS | RISK_QUIZ_INTENTS:
                return None
        changed = [{**item, 'status': 'PAUSED'} if item['id'] == task['id'] else item
                   for item in tasks if kind != 'CANCEL' or item['id'] != task['id']]
        if (context.get('pending_clarification') or {}).get('intent') == intent:
            context.pop('pending_clarification', None)
        if questionnaire:
            context.pop('risk_quiz', None)
        return {'parsed': dict(parsed), 'context': context, 'view': {**view, 'tasks': changed},
                'type': kind, 'updated_fields': [], 'proposal_input': False, 'questionnaire': questionnaire,
                'origin_intent': (task.get('quiz') or {}).get('origin_intent') if questionnaire else None}
    if questionnaire:
        if kind != 'RESUME' or act not in {'ACTION', 'SLOT_REPLY'} or parsed.get('intent') not in {'UNKNOWN', intent}:
            return None
        from bank_agent.risk_quiz import RISK_QUIZ_OPTIONS
        answers = (task.get('quiz') or {}).get('answers')
        if (not isinstance(answers, dict) or any(key not in {str(index) for index in range(1,9)}
                or answer not in RISK_QUIZ_OPTIONS[int(key)] for key, answer in answers.items())):
            return None
        context['risk_quiz'] = {**(task.get('quiz') or {}), 'answers': dict(answers)}
        resumed = validate_intent_payload({'intent': intent, 'parse_meta': parsed.get('parse_meta')})
        return {'parsed': resumed, 'context': context, 'view': view, 'type': kind, 'updated_fields': [], 'proposal_input': True}
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


def withdraw_known_task(state: Mapping[str, Any], parsed: Mapping[str, Any], text: str) -> dict[str, Any] | None:
    """An unambiguous abandoned discussion need not have a model-supplied ID."""
    from intent_language import business_text, withdrawal_statement
    meta = parsed.get('parse_meta') or {}
    view = dialogue_view(state)
    if (meta.get('dialogue_act') != 'WITHDRAW' or meta.get('scope') == 'OUT_OF_SCOPE'
            or not withdrawal_statement(text, pending_discussion=bool(view['tasks']))):
        return None
    raw = _withdrawal_input(text)
    if raw['_ambiguous']:
        return None
    candidates = []
    for task in view['tasks']:
        pending = task.get('pending') or {}
        origin = (task.get('quiz') or {}).get('origin_intent')
        target = pending.get('payload', {}).get('target') or (task.get('quiz') or {}).get('origin_target')
        if (task.get('status') != 'OFFERED' and raw.get('intent') in {'UNKNOWN', pending.get('intent'), origin}
                and (not raw.get('target') or not target or raw['target'] == target)):
            candidates.append(task)
    if len(candidates) != 1:
        return None
    return apply_command(state, {**parsed, 'parse_meta': {**(parsed.get('parse_meta') or {}),
        'task_command': {'type': 'CANCEL', 'task_id': candidates[0]['id'], 'evidence': business_text(text)[:300]}}}, text)
