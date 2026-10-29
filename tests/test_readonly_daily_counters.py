"""Queries across midnight cannot reset durable accounting or mask a failed write."""
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

import main
from bank_core import payments, persistence
from test_ledger_persistence import client, store_path, _yellow_transfer  # noqa: F401


def snapshot():
    return {name: deepcopy(persistence.LEDGER_COLLECTIONS[name]) for name in persistence.LEDGER_STATE_NAMES}


def seed(day):
    main.DAILY_TRANSFERS['user_zhang'] = 4900.0
    main.CARD_DAILY_SPENT['CARD-001'] = 4900.0
    for dates, key in [(main.DAILY_TRANSFER_DATE, 'user_zhang'), (main.CARD_DAILY_SPENT_DATE, 'CARD-001')]:
        if day is None:
            dates.pop(key, None)
        else:
            dates[key] = day


@pytest.mark.parametrize('bucket', ['missing', 'expired', 'today'])
def test_context_and_cards_gets_never_mutate_daily_buckets(client, store_path, bucket):  # noqa: F811
    today = datetime.now().strftime('%Y-%m-%d')
    seed(None if bucket == 'missing' else '2000-01-01' if bucket == 'expired' else today)
    main.persist_ledger()
    before, disk = snapshot(), store_path.read_bytes()
    headers = {'X-User-Id': 'user_zhang'}
    assert client.get('/api/v1/assistant/context', headers=headers).status_code == 200
    rows = client.get('/api/v1/cards/user_zhang', headers=headers).json()['data']
    assert rows[0]['daily_spent_today'] == (0.0 if bucket == 'expired' else 4900.0)
    assert payments.daily_spent('user_zhang') == (0.0 if bucket == 'expired' else 4900.0)
    assert snapshot() == before and store_path.read_bytes() == disk


def test_same_query_crossing_midnight_only_changes_its_projected_values(client, store_path, monkeypatch):  # noqa: F811
    class Clock(datetime):
        current = datetime(2026, 10, 28, 23, 59, 59)
        @classmethod
        def now(cls, tz=None):
            return cls.current
    monkeypatch.setattr(payments, 'datetime', Clock)
    seed('2026-10-28')
    main.persist_ledger()
    before, disk = snapshot(), store_path.read_bytes()
    headers = {'X-User-Id': 'user_zhang'}
    first = client.get('/api/v1/cards/user_zhang', headers=headers).json()['data'][0]
    Clock.current += timedelta(seconds=2)
    assert client.get('/api/v1/assistant/context', headers=headers).status_code == 200
    second = client.get('/api/v1/cards/user_zhang', headers=headers).json()['data'][0]
    assert first['daily_spent_today'] == 4900.0 and second['daily_spent_today'] == 0.0
    assert snapshot() == before and store_path.read_bytes() == disk


def test_settlement_after_rollover_records_both_counters_and_dates_once(client, store_path):  # noqa: F811
    seed('2000-01-01')
    _yellow_transfer(client)
    _yellow_transfer(client)
    today = datetime.now().strftime('%Y-%m-%d')
    assert main.DAILY_TRANSFERS['user_zhang'] == main.CARD_DAILY_SPENT['CARD-001'] == 600.0
    assert main.DAILY_TRANSFER_DATE['user_zhang'] == main.CARD_DAILY_SPENT_DATE['CARD-001'] == today
    assert payments.daily_spent('user_zhang') == payments.card_daily_spent('CARD-001') == 600.0
    main.reset_state()
    assert main.load_ledger_store()['loaded_state']
    assert payments.daily_spent('user_zhang') == payments.card_daily_spent('CARD-001') == 600.0


def test_failed_rollover_settlement_restores_old_day_and_amount_before_retry(client, store_path, monkeypatch):  # noqa: F811
    seed('2000-01-01')
    main.persist_ledger()
    before, disk = snapshot(), store_path.read_bytes()
    def fail(*args, **kwargs):
        raise OSError('simulated storage failure')
    with monkeypatch.context() as storage:
        storage.setattr(persistence.os, 'replace', fail)
        rejected = _yellow_transfer(client, expected_status=503)
    assert rejected['detail']['code'] == 'LEDGER_PERSISTENCE_FAILED'
    assert snapshot() == before and store_path.read_bytes() == disk
    _yellow_transfer(client)
    assert payments.daily_spent('user_zhang') == payments.card_daily_spent('CARD-001') == 300.0
