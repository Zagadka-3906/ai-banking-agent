"""Bind conversational payee suffixes only against current Core identities."""
from __future__ import annotations

import re
from typing import Any, Mapping, cast

from bank_agent import runtime
from bank_agent.state import BankingAgentState
from intent_language import ambiguous_target, business_text, execution_constraint

_PAYEE_SUFFIX = r'(?:吧|呀|啊|的|这笔|那笔|的事|这件事|那件事)'
_PAYEE_BUSINESS_SUFFIX = r'(?:办|办理|安排)(?:一笔)?'


def bind_payee(state: Mapping[str, Any], parsed: dict[str, Any], text: str) -> dict[str, Any]:
    raw = parsed.get('target')
    source = business_text(text)
    reference = isinstance(raw, str) and bool(re.fullmatch(r'(?:他|她|同一位|同一个收款人)(?:办|办理|安排)(?:一笔)?', raw))
    verb_suffix = isinstance(raw, str) and bool(re.search(_PAYEE_BUSINESS_SUFFIX + '$', raw)
        and re.search(re.escape(raw) + r'(?:一笔)?(?:预约|定时)?(?:转账|汇款)', source))
    if (parsed.get('intent') not in {'TRANSFER', 'SCHEDULED_TRANSFER'} or not isinstance(raw, str)
            or raw not in source or not (reference or verb_suffix or re.search(_PAYEE_SUFFIX + '$', raw))
            or ambiguous_target(source) or execution_constraint(source)
            or (parsed.get('parse_meta') or {}).get('dialogue_act') in {'QUERY', 'EXPLAIN', 'SOCIAL', 'UNSUPPORTED', 'WITHDRAW'}):
        return parsed
    owner = state.get('user_id')
    if not owner:
        return parsed
    rows: list[dict[str, Any]] = []
    try:
        for path in (f'contacts/{owner}/book', f'contacts/{owner}'):
            response = runtime._http('GET', path, state=cast(BankingAgentState, state), headers={'X-User-Id': owner})
            data = response.json().get('data') if response.status_code < 400 else None
            if not isinstance(data, list):
                return parsed
            rows.extend(row for row in data if isinstance(row, dict))
    except Exception:
        return parsed
    # A real label ending in a discourse particle wins over grammatical
    # interpretation, even when another contact has its shorter prefix.
    if any(raw == (row.get('name') or row.get('display_name')) for row in rows):
        return parsed
    matches = {}
    previous_names: list[str] = []
    if reference and state.get('memory_principal') == owner:
        for observation in reversed(list((state.get('observations') or {}).values())):
            names = (observation.get('resource_entities') or {}).get('contacts')
            requested = (observation.get('requested_subjects') or {}).get('contacts')
            if names is not None or requested is not None:
                selectors = requested or names or []
                previous_names = selectors if len(selectors) == 1 else []
                break
    for row in rows:
        name = row.get('name') or row.get('display_name')
        identity = row.get('account_id') or row.get('contact_id')
        if (isinstance(name, str) and isinstance(identity, str) and identity != owner
                and (re.fullmatch(re.escape(name) + _PAYEE_SUFFIX, raw)
                     or verb_suffix and re.fullmatch(re.escape(name) + _PAYEE_BUSINESS_SUFFIX, raw)
                     or reference and any(value in previous_names for value in (name, row.get('contact_id'), identity)))):
            matches[identity] = name
    if len(matches) != 1:
        return {**parsed, 'target': None} if reference else parsed
    identity, name = next(iter(matches.items()))
    return {**parsed, 'target': name, 'parse_meta': {**(parsed.get('parse_meta') or {}),
        'target_binding': {'source': 'core://allowed-payees', 'span': raw, 'entity_id': identity, 'name': name}}}
