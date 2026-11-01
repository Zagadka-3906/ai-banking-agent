"""Principal-scoped discussion state, separate from immutable execution approvals."""
from __future__ import annotations

from typing import Any, Mapping


def _refine_topic(current: str, proposed: str) -> str:
    """A broader label cannot erase an already identified policy subtopic."""
    parent = current
    while parent != 'general':
        parent = {'limits': 'card'}.get(parent, 'general')
        if proposed == parent:
            return current
    return proposed


def dialogue_view(state: Mapping[str, Any]) -> dict[str, Any]:
    value = state.get("dialogue_state")
    owner = state.get('user_id')
    owned = (not isinstance(value, dict) or value.get('principal') == owner) and state.get('memory_principal') in {None, owner}
    if not isinstance(value, dict) or value.get("principal") != state.get("user_id"):
        value = {"version": 1, "principal": owner, "active_topic": "general", "tasks": []}
    turn = value.get('turn', 0)
    tasks = [task for task in list(value.get("tasks") or [])[-4:]
             if isinstance(task, dict) and task.get('expires_at', turn) >= turn]
    quiz = (state.get('context') or {}).get('risk_quiz')
    if owned and isinstance(quiz, dict):
        tasks = [task for task in tasks if task.get('id') != 'quiz:WEALTH_RISK_ASSESSMENT']
        tasks.append({'id': 'quiz:WEALTH_RISK_ASSESSMENT', 'intent': 'WEALTH_RISK_ASSESSMENT', 'kind': 'questionnaire',
                      'status': 'WAITING_INPUT', 'quiz': {**quiz, 'answers': dict(quiz.get('answers') or {})},
                      'pending': {'intent': 'WEALTH_RISK_ASSESSMENT', 'missing': ['risk_answers'],
                                  'payload': {'intent': 'WEALTH_RISK_ASSESSMENT'}}})
    return {**value, 'tasks': tasks[-4:]}


def remember_discussion(state: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, Any]:
    from bank_agent.understanding import _safe_text, help_topic

    view = dialogue_view(state)
    context = state.get("context") or {}
    pending = context.get("pending_clarification")
    tasks = list(view["tasks"])
    turn = int(view.get('turn', 0)) + 1
    intent = state.get("intent")
    if isinstance(pending, dict):
        payload = {key: value for key, value in (pending.get("payload") or {}).items()
                   if key in {"intent", "target", "amount", "date_scope", "date_range", "participants", "product_ids", "slots", "scheduled_at", "event_at"}}
        selected = ((state.get('parse_meta') or {}).get('task_command') or {}).get('task_id')
        existing = next((task for task in tasks if task.get('id') == selected or task.get('intent') == pending.get('intent')
                         and ((task.get('pending') or {}).get('payload') or {}).get('target') == payload.get('target')), None)
        frame_id = existing['id'] if existing else str(pending.get('intent'))
        if not existing and any(task['id'] == frame_id for task in tasks):
            frame_id += ':' + str(turn)
        tasks = [task for task in tasks if task.get('id') != frame_id]
        tasks.append({'id': frame_id, 'intent': pending.get('intent'), 'status': 'WAITING_INPUT' if intent == pending.get('intent') else 'PAUSED',
                      'pending': {**pending, 'payload': payload}})
    if state.get("business_status") in {"SUCCEEDED", "CANCELLED"} or response.get("business_status") in {"SUCCEEDED", "CANCELLED"}:
        selected = ((state.get('parse_meta') or {}).get('task_command') or {}).get('task_id')
        tasks = [task for task in tasks if not (task.get('intent') == intent and
                 (task.get('id') == selected or ((task.get('pending') or {}).get('payload') or {}).get('target') == state.get('target')))]
    if intent == 'BANKING_HELP' and response.get('business_status') != 'FAILED':
        from bank_agent.parsing import _local_parse_rules
        from bank_planning.intents import missing_slots
        from intent_language import business_text, execution_constraint, withdrawal_statement
        import re

        source = business_text(str(state.get('user_input') or ''))
        raw = _local_parse_rules(source)
        # Remember an incomplete intention, including unknown recipient/date.
        # It is not the active clarification or an authorization; a later turn
        # must explicitly resume it and independently supply executable values.
        if (raw['intent'] in {'TRANSFER', 'SCHEDULED_TRANSFER', 'WEALTH_PURCHASE', 'CARD_LIMIT'}
                and raw.get('amount') is None and missing_slots(raw)
                and execution_constraint(source) != 'NEGATED_ACTION'
                and not withdrawal_statement(source, pending_discussion=bool(tasks))
                and re.search(r'我想|我要|我打算|我准备|帮我|给.{1,24}(?:转|汇)', source)):
            existing = next((task for task in tasks if task.get('intent') == raw['intent']
                             and ((task.get('pending') or {}).get('payload') or {}).get('target') == raw['target']), None)
            if existing is None:
                payload = {key: raw.get(key) for key in ('intent', 'target', 'amount', 'slots', 'scheduled_at', 'product_ids')}
                tasks.append({'id': 'discussed:' + raw['intent'] + ':' + str(turn), 'intent': raw['intent'],
                              'status': 'PAUSED', 'kind': 'discussed_intention',
                              'pending': {'intent': raw['intent'], 'missing': missing_slots(raw), 'payload': payload}})
    topic = help_topic(str(state.get("user_input") or ""))
    selected = (state.get("slots") or {}).get("help_topic")
    if (topic == "general" or intent == "BANKING_HELP") and selected:
        topic = _refine_topic(topic, selected)
    from bank_planning.requests import request_goals
    goal_topics = {goal['topic'] for goal in request_goals(state) if goal['act'] != 'UNSUPPORTED'}
    if intent == 'BANKING_HELP' and len(goal_topics) == 1:
        topic = _refine_topic(topic, next(iter(goal_topics)))
    relation = (state.get("parse_meta") or {}).get("topic_relation")
    if topic == "general" and relation == "CONTINUE":
        topic = view.get("active_topic", "general")
    result = {**view, 'version': 2, 'turn': turn, "active_topic": topic, "question": _safe_text(state.get("user_input"), 500), "tasks": tasks[-4:]}
    # A finite-limit explanation can ask for a value without pretending that
    # a write was requested. This typed, principal-bound input expires on the
    # next topic/turn; the value itself must be stated anew, then re-approved.
    execution = state.get("execution_result") or {}
    result["next_input"] = ({"intent": "CARD_LIMIT", "field": "amount"}
                            if intent == "BANKING_HELP" and topic == "limits"
                            and execution.get("type") == "BANKING_HELP" and (execution.get("data") or {}).get("documents")
                            else None)
    if result["next_input"]:
        import re
        from intent_language import business_text

        text = business_text(str(state.get("user_input") or ""))
        cards = set(re.findall(r"(?i)(?<![A-Za-z0-9])CARD-[0-9a-z]{2,}(?![A-Za-z0-9])", text))
        if len(cards) == 1:
            # This is the user's literal selector, not proof of ownership.
            # Core validates it again before issuing the new approval.
            result["next_input"]["target"] = next(iter(cards)).upper()
        elif len(cards) > 1 or re.search(r"尾号|末四位|后四位|那张|这张", text):
            # A bare amount must not silently choose a different primary card.
            result["next_input"] = None
    # A policy explanation offers an input type, not an executable request.
    # It expires after the immediate next turn and stores no previous amount.
    if result['next_input']:
        option = result['next_input']
        frame_id = 'option:' + option['intent']
        result['tasks'] = [task for task in result['tasks'] if task['id'] != frame_id]
        result['tasks'].append({'id': frame_id, 'intent': option['intent'], 'status': 'OFFERED', 'expires_at': turn,
                                'pending': {'intent': option['intent'], 'missing': [option['field']],
                                            'payload': {'intent': option['intent'], 'target': option.get('target')}}})
        result['tasks'] = result['tasks'][-4:]
    return result


def resume_task(state: Mapping[str, Any], parsed: Mapping[str, Any], text: str) -> dict[str, Any] | None:
    import re
    from intent_language import business_text

    # The model may identify a known task, never manufacture its stored slots.
    # Resuming a discussion is not resuming/approving a transaction nonce.
    task_id = (parsed.get("parse_meta") or {}).get("resume_task_id")
    source = business_text(text)
    resume = re.search(r'继续|接着|恢复|回到|补(?:上|充|全)|补(?=\s*(?:[¥￥]?\s*\d|[零〇一二两三四五六七八九十百千万]))|填(?:写|上|入)', source)
    from bank_agent.parsing import local_parse
    from bank_planning.requests import request_goals
    from planner import Intent

    act = (parsed.get('parse_meta') or {}).get('dialogue_act')
    literal_intent = local_parse(source).get('intent')
    # The model may identify an amount-slot reply without using a restore verb.
    # Require the same business in the current words, then compile only the
    # literal new amount into one known pending frame; never carry an approval.
    typed_fill = (resume is None and act in {'SLOT_REPLY', 'ACTION'} and parsed.get('intent') == literal_intent
                  and literal_intent not in {None, Intent.UNKNOWN.value}
                  and (parsed.get('parse_meta') or {}).get('scope') != 'OUT_OF_SCOPE'
                  and not any(goal['act'] in {'QUERY', 'EXPLAIN', 'UNCLEAR', 'UNSUPPORTED'} for goal in request_goals(parsed)))
    if (resume or typed_fill) and act not in {'SOCIAL', 'UNSUPPORTED'}:
        from bank_agent.extraction import amount_from_text, target_from_text
        from intent_language import ambiguous_amount, ambiguous_target, execution_constraint
        active = source[resume.end():] if resume else source
        filling = typed_fill or bool(resume and resume.group().startswith(('补', '填')))
        operation = literal_intent if filling else local_parse(active).get('intent')
        if filling and resume:
            # The business around the literal fill verb can be more specific
            # than an earlier parked topic. Require classifier agreement and
            # retain all whole-message safety/parameter checks below.
            left = max((match.end() for match in re.finditer(r'[，,。；;!?！？]', source[:resume.start()])), default=0)
            right = re.search(r'[，,。；;!?！？]', source[resume.end():])
            end = resume.end() + right.start() if right else len(source)
            clause = source[left:end]
            current = local_parse(clause).get('intent')
            goals = request_goals(parsed)
            if (current in {'TRANSFER', 'SCHEDULED_TRANSFER', 'WEALTH_PURCHASE', 'WEALTH_REDEEM', 'CARD_LIMIT'}
                    and parsed.get('intent') == current and all(goal['act'] == 'ACTION' for goal in goals)):
                from bank_planning.intents import GREEN_INTENTS
                others = [part for part in re.split(r'[，,。；;!?！？]', source[:left] + source[end:]) if part.strip()]
                conflicts = [part for part in others if local_parse(part).get('intent') not in GREEN_INTENTS | {'UNKNOWN', current}
                             and not re.fullmatch(r'\s*(?:那|就)?(?:我)?(?:先|暂时)?(?:不管|不谈|不聊|不考虑).{1,40}', part)]
                if conflicts:
                    return None
                operation = current
        value = amount_from_text(source)
        recipient = target_from_text(source)
        candidates = [task for task in dialogue_view(state)['tasks'] if isinstance(task.get('pending'), dict)
                      and (not task_id or task['id'] == task_id)
                      and task['pending'].get('intent') == operation
                      and task['pending'].get('missing') == ['amount']
                      and isinstance((task['pending'].get('payload') or {}).get('target'), str)
                      and (task['pending']['payload']['target'] == recipient
                           or recipient is None and (filling or re.search(r'刚才|原来|之前|上次', source)))]
        siblings = [task for task in dialogue_view(state)['tasks'] if (task.get('pending') or {}).get('intent') == operation]
        if len(siblings) > 1 and recipient is None and not (task_id and task_id in source):
            return None
        if (len(candidates) == 1 and value is not None and value > 0 and not ambiguous_amount(text) and not ambiguous_target(text)
                and not execution_constraint(text) and not re.search(r'解释|说明|查询|核对|查看|分析|了解|为什么|为何|怎么|如何|是否|是不是|能否|能不能|可不可以|行不行|多少|费用|手续费|会怎样|[吗？?]', text)):
            return {**candidates[0]['pending'], '_verified_slot_resume': value}
    if not task_id or not re.search(r"继续|接着|恢复|回到|原来|刚才|之前|上次", text):
        return None
    for task in dialogue_view(state)["tasks"]:
        if task.get("id") == task_id and isinstance(task.get("pending"), dict):
            return dict(task["pending"])
    return None
