"""Successful personal transfers populate contacts without granting AA consent."""
from copy import deepcopy

import pytest

import main
from bank_core import persistence
from bank_core.contacts import backfill_transfer_contacts, _contact_link_status
from bank_core.ledger import ledger_transaction
from bank_core.payments import resolve_payee
from security import SecurityError
from test_contact_book import client, _add, _transfer  # noqa: F401
from test_task_settlement import scheduled, approve_task, h


def book(http_client, owner='user_zhang'):
    response = http_client.get(f'/api/v1/contacts/{owner}/book', headers={'X-User-Id': owner})
    assert response.status_code == 200
    return response.json()['data']


def test_default_demo_accounts_are_in_every_demo_book_except_self(client):  # noqa: F811
    main.reset_state()
    ids = {'user_zhang','user_li','user_wife'}
    for owner in ids:
        assert {row['account_id'] for row in book(client, owner)} == ids - {owner}
        assert all(row['source'] == 'DEMO_SEED' for row in main.USER_CONTACTS[owner])


def test_old_empty_demo_books_are_seeded_once_and_deletions_survive_restart(client, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv('BANK_LEDGER_STORE_PATH', str(tmp_path/'legacy-empty.json'))
    for account in main.ACCOUNTS.values():
        account.pop('demo_contacts_seeded', None)
    main.persist_ledger()
    main.reset_state()
    assert main.load_ledger_store()['demo_contacts_seeded'] == 6
    row = next(row for row in book(client) if row['account_id']=='user_li')
    assert client.post(f"/api/v1/contacts/user_zhang/book/{row['contact_id']}/remove", headers={'X-User-Id':'user_zhang'}).status_code == 200
    main.reset_state()
    assert main.load_ledger_store()['demo_contacts_seeded'] == 0
    assert all(row['account_id']!='user_li' for row in book(client))


def test_completed_transfer_saves_both_parties_once_and_get_is_read_only(client):  # noqa: F811
    assert not book(client)
    for _ in range(2):
        assert _transfer(client, '李四', amount=10).status_code == 200
    assert [(r['name'], r['account_id']) for r in book(client)] == [('李四', 'user_li')]
    assert [(r['name'], r['account_id']) for r in book(client, 'user_li')] == [('张三', 'user_zhang')]
    before = deepcopy((main.ACCOUNTS, main.USER_CONTACTS, main.TRANSACTIONS))
    assert len(book(client)) == 1
    assert (main.ACCOUNTS, main.USER_CONTACTS, main.TRANSACTIONS) == before
    for owner in ['user_zhang', 'user_li']:
        assert all(tx.get('counterparty_id') for tx in main.TRANSACTIONS if tx.get('account_id') == owner and tx.get('category') in {'转账支出', '转账收入'})


def test_precheck_and_failed_execution_do_not_add_contacts(client):  # noqa: F811
    headers = {'X-User-Id': 'user_zhang'}
    assert client.post('/api/v1/risk/precheck', json={'from_user':'user_zhang','target_name_or_id':'李四','amount':10}, headers=headers).status_code == 200
    refused = client.post('/api/v1/transfers/execute', json={'from_user':'user_zhang','target_name_or_id':'李四','amount':10}, headers=headers)
    assert refused.status_code == 403
    assert not book(client) and not book(client, 'user_li')


def test_manual_alias_relationship_and_birthday_are_preserved(client):  # noqa: F811
    added = _add(client, {'name':'老李','phone':'13900139000','relationship':'朋友','birthday':'1990-01-01'}).json()['data']
    assert _transfer(client, '李四', amount=10).status_code == 200
    saved = book(client)
    assert len(saved) == 1
    assert {key:saved[0][key] for key in ('contact_id','name','relationship','birthday')} == {key:added[key] for key in ('contact_id','name','relationship','birthday')}


def test_automatic_reciprocal_rows_are_not_owner_consent(client):  # noqa: F811
    assert _transfer(client, '李四', amount=10).status_code == 200
    for owner in ['user_zhang','user_li']:
        row = main.USER_CONTACTS[owner][0]
        assert row['source'] == 'TRANSFER_HISTORY'
        assert _contact_link_status(row, caller_id=owner) == main.LINK_UNVERIFIED
        assert book(client, owner)[0]['account_name'] is None


def test_scheduled_transfer_saves_contacts_only_after_actual_settlement(client):  # noqa: F811
    task_id = scheduled(client, amount=10)
    assert not book(client) and not book(client, 'user_li')
    token = approve_task(client, task_id)
    result = client.post(f'/api/v1/tasks/{task_id}/execute', json={'user_id':'user_zhang','auth_token':token}, headers={**h(),'Idempotency-Key':'auto-contact-scheduled'})
    assert result.status_code == 200, result.text
    assert book(client)[0]['account_id'] == 'user_li' and book(client, 'user_li')[0]['account_id'] == 'user_zhang'


def test_failed_memory_transaction_rolls_back_contacts_and_migration_marker(client):  # noqa: F811
    from bank_core.contacts import remember_transfer_parties
    before = deepcopy((main.ACCOUNTS, main.USER_CONTACTS))
    with pytest.raises(RuntimeError):
        with ledger_transaction():
            remember_transfer_parties('user_zhang', 'user_li')
            raise RuntimeError('later settlement step failed')
    assert (main.ACCOUNTS, main.USER_CONTACTS) == before


def test_failed_disk_commit_rolls_back_auto_contacts_together_with_the_money(client, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv('BANK_LEDGER_STORE_PATH', str(tmp_path/'ledger.json'))
    def broken(*args):
        raise OSError('write failed')
    monkeypatch.setattr(persistence.os, 'replace', broken)
    before = deepcopy((main.ACCOUNTS, main.TRANSACTIONS, main.USER_CONTACTS))
    assert _transfer(client, '李四', amount=10).status_code == 503
    assert (main.ACCOUNTS, main.TRANSACTIONS, main.USER_CONTACTS) == before


def test_legacy_history_backfill_and_delete_survive_reload_without_resurrection(client, tmp_path, monkeypatch):  # noqa: F811
    path = tmp_path/'ledger.json'
    monkeypatch.setenv('BANK_LEDGER_STORE_PATH', str(path))
    for account in main.ACCOUNTS.values():
        account.pop('transfer_contacts_backfilled', None)
    main.TRANSACTIONS.extend([
        {'tx_id':'OLD-OUT','account_id':'user_zhang','merchant':'转账给 李四','category':'转账支出','amount':10,'time':'2026-10-01 10:00:00'},
        {'tx_id':'OLD-IN','account_id':'user_li','merchant':'来自 张三 的转账','category':'转账收入','direction':'IN','amount':10,'time':'2026-10-01 10:00:00'},
    ])
    main.persist_ledger()
    main.reset_state()
    assert main.load_ledger_store()['contacts_backfilled'] == 2
    contact_id = book(client)[0]['contact_id']
    assert book(client, 'user_li')[0]['name'] == '张三'
    result = client.post(f'/api/v1/contacts/user_zhang/book/{contact_id}/remove', headers={'X-User-Id':'user_zhang'})
    assert result.status_code == 200
    main.reset_state()
    assert main.load_ledger_store()['loaded_state']
    assert not book(client)
    assert _transfer(client, '李四', amount=10).status_code == 200
    assert book(client)[0]['contact_id'] == contact_id


def test_backfill_skips_merchants_failures_ambiguous_names_and_missing_phone(client):  # noqa: F811
    main.ACCOUNTS['other_li'] = {**main.ACCOUNTS['user_li'], 'phone':'13999990000'}
    main.ACCOUNTS['no_phone'] = {**main.ACCOUNTS['user_wife'], 'name':'无电话用户', 'phone':''}
    for account in main.ACCOUNTS.values():
        account.pop('transfer_contacts_backfilled', None)
    for i, row in enumerate([
        {'merchant':'转账给 李四','category':'转账支出'},
        {'merchant':'李四','category':'餐饮','counterparty_id':'user_li'},
        {'merchant':'转账给 王美（爱人）','category':'转账支出','status':'FAILED'},
        {'merchant':'转账给 无电话用户','category':'转账支出','counterparty_id':'no_phone'},
        {'merchant':'转账给 张三','category':'转账支出','counterparty_id':'user_zhang'},
    ]):
        main.TRANSACTIONS.append({'tx_id':f'OLD-{i}','account_id':'user_zhang','amount':10,**row})
    assert backfill_transfer_contacts() == 0 and not book(client)


def test_same_named_contacts_require_an_explicit_phone(client):  # noqa: F811
    first = _add(client, {'name':'同名','phone':'13911110001','relationship':'其他'}).json()['data']
    _add(client, {'name':'同名','phone':'13911110002','relationship':'其他'})
    with pytest.raises(SecurityError) as error:
        resolve_payee('同名', from_user='user_zhang')
    assert error.value.code == 'CONTACT_TARGET_AMBIGUOUS'
    assert resolve_payee('13911110001', from_user='user_zhang')[0] == first['account_id']


def test_auto_save_respects_capacity_without_failing_the_transfer(client, monkeypatch):  # noqa: F811
    from bank_core import contacts
    monkeypatch.setattr(contacts, 'CONTACT_MAX_PER_USER', 1)
    assert _add(client, {'name':'朋友甲','phone':'13911112222','relationship':'朋友'}).status_code == 200
    assert _transfer(client, '李四', amount=10).status_code == 200
    assert [row['name'] for row in book(client)] == ['朋友甲']
    assert book(client, 'user_li')[0]['name'] == '张三'


def test_replaying_a_completed_transfer_does_not_restore_a_deleted_contact(client):  # noqa: F811
    from test_ledger_persistence import _yellow_transfer
    first = _yellow_transfer(client, idempotency_key='transfer-contact-replay')
    contact_id = book(client)[0]['contact_id']
    assert client.post(f'/api/v1/contacts/user_zhang/book/{contact_id}/remove', headers={'X-User-Id':'user_zhang'}).status_code == 200
    assert _yellow_transfer(client, idempotency_key='transfer-contact-replay') == first
    assert not book(client)
