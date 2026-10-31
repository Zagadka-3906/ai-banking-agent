"""Bind conversational payee suffixes only against current Core identities."""
from __future__ import annotations

import re
from typing import Any, Mapping, cast

from bank_agent import runtime
from bank_agent.state import BankingAgentState
from intent_language import ambiguous_target, business_text, execution_constraint, recipient_references

_PAYEE_SUFFIX = r'(?:吧|呀|啊|的|这笔|那笔|的事|这件事|那件事)'
# The suffix may stack ("李四的那笔"); at most three discourse particles are stripped.
_PAYEE_SUFFIX_RUN = r'(?:' + _PAYEE_SUFFIX + r'){1,3}'
_PAYEE_BUSINESS_SUFFIX = r'(?:办|办理|安排)(?:一笔)?'
_PAYEE_RESUME_SUFFIX = r'(?:恢复|继续|接着|重新办理)(?:(?:刚才|之前)?(?:这|那))?(?:一)?笔'
# The resume verb may precede the object ("恢复转给某人的转账提案")
# or follow it ("把给某人的提案恢复出来"). Both still need current
# Core identity agreement before removing a lexical ambiguity.
_FRAME_ACTION_SUFFIX = r'(?:转账)?(?:提案|操作)(?:恢复(?:出来)?|继续(?:办理)?|重新办理)?'
_PAYEE_FRAME_SUFFIX = '的' + _FRAME_ACTION_SUFFIX


def _frame_references(source: str) -> dict[str, str]:
    result = {}
    for value in recipient_references(source):
        # The lexical extractor can stop just before 转账, leaving possessive 的.
        extended = re.search(re.escape(value) + _FRAME_ACTION_SUFFIX, source) if value.endswith('的') else None
        result[value] = extended.group() if extended else value
    return result


def _bind_frame_references(parsed: dict[str, Any], source: str, rows: list[dict[str, Any]], owner: str) -> dict[str, Any]:
    if re.search(r'不是(?:给|向|转给)|收款人[^，,。]*(?:不对|错了)|或者|还是', source):
        return parsed
    labels: dict[str, set[str]] = {}
    for row in rows:
        name = row.get('name') or row.get('display_name')
        identity = row.get('account_id') or row.get('contact_id')
        if isinstance(name, str) and name and isinstance(identity, str) and identity != owner:
            labels.setdefault(name, set()).add(identity)
    resolved: dict[str, str] = {}
    for reference, extended in _frame_references(source).items():
        # A literal real label wins, including a label containing business words.
        matches = labels.get(reference) or labels.get(extended) or {identity for name, ids in labels.items()
            if re.fullmatch(re.escape(name) + _PAYEE_FRAME_SUFFIX, extended) for identity in ids}
        if len(matches) != 1:
            return parsed
        identity = next(iter(matches))
        resolved[identity] = next(name for name, ids in labels.items() if identity in ids)
    if len(resolved) != 1:
        return parsed
    identity, name = next(iter(resolved.items()))
    return {**parsed, 'target': name,
        'slots': {key: value for key, value in (parsed.get('slots') or {}).items() if key != 'target_ambiguous'},
        'parse_meta': {**(parsed.get('parse_meta') or {}), 'target_binding': {
            'source': 'core://allowed-payees', 'span': '、'.join(sorted(recipient_references(source)))[:120],
            'entity_id': identity, 'name': name}}}


def bind_payee(state: Mapping[str, Any], parsed: dict[str, Any], text: str) -> dict[str, Any]:
    raw = parsed.get('target')
    source = business_text(text)
    # "给联系人李四转账" prefixes the category word onto the name; the model
    # sometimes copies it into the target.  The category word is discourse
    # glue, never part of an identity, so strip it against the current source.
    if isinstance(raw, str):
        category = re.fullmatch(r'(?:联系人|收款人)(.{1,24})', raw)
        if category and category.group(1) in source:
            raw = category.group(1)
            parsed = {**parsed, 'target': raw}
    frame_ambiguity = (parsed.get('slots') or {}).get('target_ambiguous') is True and any(
        re.search(_PAYEE_FRAME_SUFFIX + '$', value) for value in _frame_references(source).values())
    reference = isinstance(raw, str) and bool(re.fullmatch(r'(?:他|她|同一位|同一个收款人)(?:办|办理|安排)(?:一笔)?', raw))
    verb_suffix = isinstance(raw, str) and bool(re.search(_PAYEE_BUSINESS_SUFFIX + '$', raw)
        and re.search(re.escape(raw) + r'(?:一笔)?(?:预约|定时)?(?:转账|汇款)', source))
    resume_suffix = isinstance(raw, str) and bool(re.search(_PAYEE_RESUME_SUFFIX + '$', raw)
        and re.search(re.escape(raw) + r'[^，,。；;!?！？]{0,24}(?:转账|汇款)', source))
    if (parsed.get('intent') not in {'TRANSFER', 'SCHEDULED_TRANSFER'}
            or not frame_ambiguity and (not isinstance(raw, str) or raw not in source
                or not (reference or verb_suffix or resume_suffix or re.search(_PAYEE_SUFFIX + '$', raw)) or ambiguous_target(source))
            or execution_constraint(source)
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
    if frame_ambiguity:
        return _bind_frame_references(parsed, source, rows, owner)
    if not isinstance(raw, str):
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
        # "恢复给李四的那笔300元转账" puts the verb before the name and stacks
        # the possessive onto the measure word; both are discourse glue, and a
        # real label still wins before any of this stripping runs.
        if (isinstance(name, str) and isinstance(identity, str) and identity != owner
                and (re.fullmatch(re.escape(name) + _PAYEE_SUFFIX_RUN, raw)
                     or verb_suffix and re.fullmatch(re.escape(name) + _PAYEE_BUSINESS_SUFFIX, raw)
                     or resume_suffix and re.fullmatch(re.escape(name) + _PAYEE_RESUME_SUFFIX, raw)
                     or reference and any(value in previous_names for value in (name, row.get('contact_id'), identity)))):
            matches[identity] = name
    if len(matches) != 1:
        return {**parsed, 'target': None} if reference else parsed
    identity, name = next(iter(matches.items()))
    return {**parsed, 'target': name, 'parse_meta': {**(parsed.get('parse_meta') or {}),
        'target_binding': {'source': 'core://allowed-payees', 'span': raw, 'entity_id': identity, 'name': name}}}
