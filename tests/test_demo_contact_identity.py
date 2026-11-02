from copy import deepcopy

from bank_core.demo_contacts import normalize_demo_contact_identity


def test_legacy_identity_and_reset_baseline_are_corrected_without_changing_money_or_user_edits():
    legacy = {'account_id': 'user_wife', 'phone': '13700137000', 'name': '王美（爱人）',
              'source': 'DEMO_SEED', 'relationship': 'OTHER', 'contact_id': 'old', 'birthday': '1995-06-18'}
    state = {'ACCOUNTS': {'user_wife': {'name': '王美（爱人）', 'phone': '13700137000', 'balance': 5678}},
             'USER_CONTACTS': {'user_zhang': [deepcopy(legacy)],
                               'user_li': [dict(legacy, name='我的同事', relationship='COLLEAGUE')],
                               'deleted_owner': []},
             'ACCOUNT_INITIAL_STATES': {'user_zhang': {'saved_at': 'old-date', 'data': {
                 'account': {'name': '张三', 'balance': 15000}, 'contacts': [deepcopy(legacy)]}}},
             'TRANSACTIONS': [{'tx_id': 'historic', 'receiver_name': '王美（爱人）', 'amount': 88}]}
    before = deepcopy(state)
    assert normalize_demo_contact_identity(state) == 3
    assert state['ACCOUNTS']['user_wife']['name'] == '王美'
    assert state['USER_CONTACTS']['user_zhang'][0] == dict(legacy, name='王美', relationship='SPOUSE')
    assert state['USER_CONTACTS']['user_li'] == before['USER_CONTACTS']['user_li']
    assert state['USER_CONTACTS']['deleted_owner'] == []
    assert state['TRANSACTIONS'] == before['TRANSACTIONS']
    baseline = state['ACCOUNT_INITIAL_STATES']['user_zhang']
    assert baseline['saved_at'] == 'old-date'
    assert baseline['data']['account'] == before['ACCOUNT_INITIAL_STATES']['user_zhang']['data']['account']
    assert baseline['data']['contacts'][0] == dict(legacy, name='王美', relationship='SPOUSE')
    assert state['ACCOUNTS']['user_wife']['balance'] == 5678
    assert normalize_demo_contact_identity(state) == 0


def test_signed_legacy_ledger_load_exposes_clean_name_and_relationship(tmp_path, monkeypatch):
    import main
    from bank_core import persistence
    from fastapi.testclient import TestClient

    main.reset_state()
    monkeypatch.setenv('BANK_LEDGER_STORE_PATH', str(tmp_path / 'ledger.json'))
    try:
        main.ACCOUNTS['user_wife']['name'] = '王美（爱人）'
        row = next(row for row in main.USER_CONTACTS['user_zhang'] if row['account_id'] == 'user_wife')
        row.update(name='王美（爱人）', relationship='OTHER')
        main.persist_ledger()
        assert persistence.load_ledger_store()['loaded_state']
        with TestClient(main.app) as client:
            response = client.get('/api/v1/contacts/user_zhang/book', headers={'X-User-Id': 'user_zhang'})
        contact = next(row for row in response.json()['data'] if row['account_id'] == 'user_wife')
        assert contact['name'] == '王美'
        assert contact['relationship'] == 'SPOUSE'
        assert contact['relationship_label'] == '爱人'
        assert main.ACCOUNTS['user_wife']['name'] == '王美'
    finally:
        main.reset_state()
