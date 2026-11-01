"""Offline demo refresh must not rewrite money, receipts or execution state."""
from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest

from bank_core.demo_dates import BANK_TIMEZONE, refresh_demo_dates

NOW = datetime(2026, 11, 1, 21, 0, tzinfo=BANK_TIMEZONE)


def sample():
    return {
        'ACCOUNTS': {'user_zhang': {'balance': 1234, 'locked_amount': 40}},
        'TRANSACTIONS': [
            {'tx_id': 'A', 'time': '2026-08-01 12:00:00', 'amount': 100, 'account_id': 'user_zhang'},
            {'tx_id': 'B', 'time': '2026-10-25 10:00:00', 'amount': 20, 'account_id': 'user_zhang'},
            {'tx_id': 'C', 'time': '2026-10-25T02:00:00Z', 'amount': 20, 'account_id': 'user_li'},
        ],
        'DISPUTE_CASES': [{'created_at': '2026-10-24T10:00:00+00:00', 'updated_at': '2026-10-25T02:00:00+00:00', 'status': 'OPEN'}],
        'USER_CONTACTS': {'user_zhang': [{'created_at': '2026-10-17T20:00:00+00:00', 'birthday': '1990-08-15'}]},
        'WEALTH_RISK_PROFILES': {'user_zhang': {'assessed_at': '2026-10-30T13:00:00+00:00', 'risk_level': 'R2'}},
        'SUBSCRIPTIONS': {'user_zhang': [
            {'next_charge': '2026-10-03', 'status': 'ACTIVE', 'amount': 15},
            {'next_charge': '2026-11-17', 'status': 'ACTIVE', 'amount': 25},
        ]},
        'OPERATION_RECEIPTS': {'receipt': {'time': '2026-08-01 12:00:00', 'auth_expires_at': '2026-10-01'}},
        'CARD_DAILY_SPENT_DATE': {'CARD-001': '2026-10-30'}, 'RISK_ATTEMPTS': {'user_zhang': [1234567.0]},
        'TASK_STORE': [],
    }


def parsed(text):
    value = datetime.fromisoformat(text.replace('Z', '+00:00'))
    return value if value.tzinfo else value.replace(tzinfo=BANK_TIMEZONE)


def test_refresh_preserves_money_identity_security_and_original_state():
    original = sample()
    saved = deepcopy(original)
    fresh, changes = refresh_demo_dates(original, NOW)
    assert original == saved
    for key in ('ACCOUNTS', 'OPERATION_RECEIPTS', 'CARD_DAILY_SPENT_DATE', 'RISK_ATTEMPTS', 'TASK_STORE'):
        assert fresh[key] == original[key]
    assert fresh['USER_CONTACTS']['user_zhang'][0]['birthday'] == '1990-08-15'
    for old, new in zip(original['TRANSACTIONS'], fresh['TRANSACTIONS']):
        assert {k: v for k, v in old.items() if k != 'time'} == {k: v for k, v in new.items() if k != 'time'}
    for change in changes:
        value = parsed(change['after']).astimezone(BANK_TIMEZONE)
        if change['path'].startswith('SUBSCRIPTIONS'):
            assert 1 <= (value.date() - NOW.date()).days <= 3
        else:
            assert NOW.date() - timedelta(days=6) <= value.date() <= NOW.date() and value <= NOW
    assert fresh['SUBSCRIPTIONS']['user_zhang'][0]['amount'] == 15


def test_equal_transfer_instants_and_dispute_order_are_preserved():
    fresh, _ = refresh_demo_dates(sample(), NOW)
    rows = fresh['TRANSACTIONS']
    assert abs((parsed(rows[1]['time']) - parsed(rows[2]['time'])).total_seconds()) < 1
    case = fresh['DISPUTE_CASES'][0]
    assert parsed(rows[0]['time']) <= parsed(case['created_at']) <= parsed(case['updated_at'])
    assert parsed(case['updated_at']) == parsed(rows[2]['time'])


@pytest.mark.parametrize('key', ['TASK_STORE', 'SCHEDULED_TRANSFERS', 'LOCKS', 'MOCK_ORDERS', 'SPLIT_REQUESTS'])
def test_active_schedules_are_not_silently_retimed_or_executed(key):
    state = sample()
    state[key] = [{'status': 'PENDING', 'run_at': '2099-01-01T09:00:00+08:00'}]
    before = deepcopy(state)
    with pytest.raises(ValueError, match='separate date plan'):
        refresh_demo_dates(state, NOW)
    assert state == before


def test_midnight_and_single_historical_instant_remain_valid():
    now = NOW.replace(hour=0, minute=0)
    state = {'TRANSACTIONS': [{'time': '2026-01-01 08:00:00'}], 'SUBSCRIPTIONS': {'user_zhang': [{'status': 'ACTIVE', 'next_charge': '2026-01-02'}]}}
    fresh, _ = refresh_demo_dates(state, now)
    assert parsed(fresh['TRANSACTIONS'][0]['time']) < now
    assert fresh['SUBSCRIPTIONS']['user_zhang'][0]['next_charge'] == '2026-11-02'
    with pytest.raises(ValueError, match='timezone'):
        refresh_demo_dates(state, NOW.replace(tzinfo=None))


def test_refreshed_dates_survive_normal_signed_persistence(tmp_path, monkeypatch):
    import main
    from bank_core import persistence

    path = tmp_path / 'ledger.json'
    monkeypatch.setenv('BANK_LEDGER_STORE_PATH', str(path))
    main.reset_state()
    try:
        main.load_ledger_store(str(path))
        persistence.persist_ledger()
        before = json.loads(path.read_text(encoding='utf-8'))['state']
        fresh, _ = refresh_demo_dates(before, NOW)
        for name in ('TRANSACTIONS', 'DISPUTE_CASES', 'USER_CONTACTS', 'WEALTH_RISK_PROFILES', 'SUBSCRIPTIONS'):
            container = persistence.LEDGER_COLLECTIONS[name]
            if isinstance(container, dict):
                container.clear()
                container.update(fresh[name])
            else:
                container[:] = fresh[name]
        persistence.persist_ledger()
        main.reset_state()
        status = main.load_ledger_store(str(path))
        assert status['loaded_state'] and not status['rejected']
        assert main.TRANSACTIONS == fresh['TRANSACTIONS']
        assert main.ACCOUNTS == before['ACCOUNTS']
        assert persistence.OPERATION_RECEIPTS == before['OPERATION_RECEIPTS']
    finally:
        main.reset_state()
