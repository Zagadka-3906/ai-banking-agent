"""Explicit, offline refresh of mutable demo dates; never a startup migration."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping

BANK_TIMEZONE = timezone(timedelta(hours=8))


def refresh_expired_demo_subscriptions(state: Mapping[str, Any], now: datetime) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Offline repair of expired built-in demo plans, not a query-time rollover.

    Future/today plans, custom subscriptions, reset baselines and monetary data
    remain unchanged. Linked unfinished reminders must be handled separately.
    The caller must stop writers, verify and back up the signed ledger first.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('An explicit timezone is required')
    result = deepcopy(dict(state))
    today = now.astimezone(BANK_TIMEZONE).date()
    changes = []
    tasks = result.get('TASK_STORE', [])
    if not isinstance(tasks, list):
        raise ValueError('Invalid task snapshot')
    for index, row in enumerate(result.get('SUBSCRIPTIONS', {}).get('user_zhang', [])):
        sid = row.get('subscription_id')
        offset = {'SUB-APPLE': 1, 'SUB-VIDEO': 3}.get(sid)
        if offset is None or row.get('status') != 'ACTIVE':
            continue
        old = row.get('next_charge')
        if not isinstance(old, str) or date.fromisoformat(old) >= today:
            continue
        if any((task.get('payload') or {}).get('subscription_id') == sid
               and task.get('status') not in {'SUCCEEDED', 'CANCELLED', 'FAILED'} for task in tasks):
            raise ValueError('Linked subscription reminders need a separate date plan')
        row['next_charge'] = (today + timedelta(days=offset)).isoformat()
        changes.append({'path': f'SUBSCRIPTIONS.user_zhang.{index}.next_charge', 'before': old, 'after': row['next_charge']})
    return result, changes


def refresh_demo_dates(state: Mapping[str, Any], now: datetime) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Return a copy with historical display dates in seven days and near plans.

    Keep chronological order, equal instants and each timestamp's format. Do
    not rewrite receipts, counters, birthdays, authorizations or task schedules.
    An operator must stop services, verify the signed snapshot, back it up and
    commit the result through the normal persistence writer separately.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('An explicit timezone is required')
    if any(state.get(key) for key in ('TASK_STORE', 'SCHEDULED_TRANSFERS', 'LOCKS', 'MOCK_ORDERS', 'SPLIT_REQUESTS')):
        raise ValueError('Scheduled or reserved business needs a separate date plan')
    result = deepcopy(dict(state))
    history: list[tuple[dict[str, Any], str, str, datetime]] = []
    plans: list[tuple[dict[str, Any], str, str]] = []

    def collect(row: dict[str, Any], fields: tuple[str, ...], path: str) -> None:
        for key in fields:
            raw = row.get(key)
            if raw is None:
                continue
            if not isinstance(raw, str):
                raise ValueError('Invalid demo timestamp: ' + path + '.' + key)
            parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=BANK_TIMEZONE)
            history.append((row, key, path + '.' + key, parsed))

    for index, row in enumerate(result.get('TRANSACTIONS', [])):
        collect(row, ('time',), f'TRANSACTIONS.{index}')
    for index, row in enumerate(result.get('DISPUTE_CASES', [])):
        collect(row, ('created_at', 'updated_at'), f'DISPUTE_CASES.{index}')
    for owner, rows in result.get('USER_CONTACTS', {}).items():
        for index, row in enumerate(rows):
            collect(row, ('created_at', 'updated_at', 'deleted_at'), f'USER_CONTACTS.{owner}.{index}')
    for owner, row in result.get('WEALTH_RISK_PROFILES', {}).items():
        if row:
            collect(row, ('assessed_at',), f'WEALTH_RISK_PROFILES.{owner}')
    for owner, rows in result.get('SUBSCRIPTIONS', {}).items():
        for index, row in enumerate(rows):
            if row.get('next_charge') is None:
                continue
            path = f'SUBSCRIPTIONS.{owner}.{index}'
            if row.get('status') == 'ACTIVE':
                date.fromisoformat(row['next_charge'])
                plans.append((row, 'next_charge', path + '.next_charge'))
            else:
                collect(row, ('next_charge',), path)

    bank_now = now.astimezone(BANK_TIMEZONE)
    end = bank_now - timedelta(seconds=1)
    start = datetime.combine(bank_now.date() - timedelta(days=6), time.min, tzinfo=BANK_TIMEZONE)
    changes = []
    if history:
        earliest = min(value for _, _, _, value in history)
        latest = max(value for _, _, _, value in history)
        span = latest - earliest
        for row, key, path, value in history:
            mapped = end if span == timedelta() else start + (end - start) * ((value - earliest) / span)
            raw = row[key]
            if len(raw) == 10:
                replacement = mapped.date().isoformat()
            elif raw.endswith('Z'):
                replacement = mapped.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
            elif datetime.fromisoformat(raw).tzinfo is None:
                replacement = mapped.replace(tzinfo=None).isoformat(sep=' ' if ' ' in raw else 'T', timespec='microseconds' if '.' in raw else 'seconds')
            else:
                replacement = mapped.astimezone(value.tzinfo).isoformat()
            row[key] = replacement
            if raw != replacement:
                changes.append({'path': path, 'before': raw, 'after': replacement})
    dates = sorted({row[key] for row, key, _ in plans})
    for row, key, path in plans:
        old = row[key]
        offset = 1 + (2 * dates.index(old) // max(1, len(dates) - 1))
        row[key] = (bank_now.date() + timedelta(days=offset)).isoformat()
        if old != row[key]:
            changes.append({'path': path, 'before': old, 'after': row[key]})
    return result, changes
