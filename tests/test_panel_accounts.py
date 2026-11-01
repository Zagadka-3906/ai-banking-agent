"""Account scenarios: signed persistence, reset isolation and guarded panel API."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import main
from bank_core import persistence
from bank_core.admin_accounts import (AccountEditError, account_snapshot, change_account, initialize_baselines,
                                      new_account_template)
from bank_security.passwords import verify_password
from scheduler import TASK_STORE
from test_ops_panel import panel, _login  # noqa: F401


@pytest.fixture
def ledger():
    main.reset_state()
    result = {name: deepcopy(persistence.LEDGER_COLLECTIONS[name]) for name in persistence.LEDGER_STATE_NAMES}
    result['TASK_STORE'] = TASK_STORE.export_records()
    yield result
    main.reset_state()


def new_view():
    view = new_account_template()
    view['user_id'] = 'demo_new'
    view['account'].update(name='新增用户', phone='13600009999', card_no='62220200999')
    return view


def test_create_edit_reset_restores_original_password_and_preserves_others(ledger):
    created = change_account(ledger, 'demo_new', 'create', new_view(), 'Example@2026')
    assert verify_password('Example@2026', created['ACCOUNTS']['demo_new']['password_hash'])
    baseline = deepcopy(created['ACCOUNT_INITIAL_STATES']['demo_new'])
    edited = account_snapshot(created, 'demo_new')
    edited['account'].update(balance=700, name='改后姓名')
    edited['cards'][0]['daily_limit'] = 999
    saved = change_account(created, 'demo_new', 'save', edited, 'Changed@2026')
    restored = change_account(saved, 'demo_new', 'reset')
    assert restored['ACCOUNTS']['demo_new']['balance'] == 15000
    assert restored['ACCOUNTS']['demo_new']['name'] == '新增用户'
    assert verify_password('Example@2026', restored['ACCOUNTS']['demo_new']['password_hash'])
    assert not verify_password('Changed@2026', restored['ACCOUNTS']['demo_new']['password_hash'])
    assert restored['ACCOUNTS']['demo_new']['credential_version'] > saved['ACCOUNTS']['demo_new']['credential_version']
    assert restored['ACCOUNT_INITIAL_STATES']['demo_new'] == baseline
    for key in ('ACCOUNTS', 'CARDS', 'USER_CONTACTS', 'WEALTH_HOLDINGS', 'SUBSCRIPTIONS'):
        for uid, data in ledger[key].items():
            assert restored[key][uid] == data
    assert '改后姓名' not in restored['CONTACTS']
    assert restored['OPERATION_RECEIPTS'] == ledger['OPERATION_RECEIPTS']
    assert 'password_hash' not in json.dumps(account_snapshot(restored, 'demo_new'))


def test_existing_baseline_is_first_saved_state_and_can_be_replaced(ledger):
    assert initialize_baselines(ledger) == 3
    first = deepcopy(ledger['ACCOUNT_INITIAL_STATES']['user_zhang'])
    view = account_snapshot(ledger, 'user_zhang')
    view['account']['balance'] = 777
    saved = change_account(ledger, 'user_zhang', 'save', view)
    assert saved['ACCOUNT_INITIAL_STATES']['user_zhang'] == first
    replaced = change_account(saved, 'user_zhang', 'baseline')
    assert change_account(replaced, 'user_zhang', 'reset')['ACCOUNTS']['user_zhang']['balance'] == 777
    assert change_account(saved, 'user_zhang', 'reset')['ACCOUNTS']['user_zhang']['balance'] == 15000


def test_reset_can_restore_removed_vault_card_and_keeps_receipts(ledger):
    initialize_baselines(ledger)
    view = account_snapshot(ledger, 'user_zhang')
    view['cards'] = []
    view['transactions'] = []
    saved = change_account(ledger, 'user_zhang', 'save', view)
    reset = change_account(saved, 'user_zhang', 'reset')
    assert reset['CARDS']['user_zhang'][0]['pan_ref'] == 'MOCK-PAN-001'


def test_contact_book_shadow_accounts_keep_disabled_login_and_placeholder(ledger):
    for uid,phone in [('ext123','13600001111'),('ext456','13600002222')]:
        ledger['ACCOUNTS'][uid]={'name':uid,'phone':phone,'card_no':'----','balance':0,'locked_amount':0,
                                'status':'ACTIVE','source':'CONTACT_BOOK','credential_version':1,'password_hash':'!contact-book-no-login'}
    initialized=deepcopy(ledger)
    initialize_baselines(initialized)
    result=change_account(initialized,'ext123','reset')
    assert result['ACCOUNTS']['ext123']['password_hash']=='!contact-book-no-login'
    assert result['ACCOUNTS']['ext456']==ledger['ACCOUNTS']['ext456']
    assert result['CONTACTS']==ledger['CONTACTS']


@pytest.mark.parametrize('change', [
    lambda v: v['account'].update(balance=-1),
    lambda v: v['account'].update(balance=float('nan')),
    lambda v: v['account'].update(locked_amount=999999),
    lambda v: v['account'].update(password_hash='injected'),
    lambda v: v['account'].update(phone='13900139000'),
    lambda v: v['cards'][0].update(card_id='CARD-002'),
    lambda v: v['cards'][0].update(daily_limit=200001),
    lambda v: v['cards'][0].update(pan_ref='MOCK-PAN-002'),
    lambda v: v['transactions'][0].update(account_id='user_li'),
    lambda v: v['transactions'][0].update(time='invalid'),
    lambda v: v.update(card_spent={'CARD-002': 1}),
    lambda v: v.update(daily_spent_date='not-date'),
    lambda v: v.update(locks={'bad': {'lock_id':'bad','user_id':'user_li','amount':1}}),
])
def test_invalid_or_cross_account_edits_leave_input_unchanged(ledger, change):
    view = account_snapshot(ledger, 'user_zhang')
    change(view)
    before = deepcopy(ledger)
    with pytest.raises(AccountEditError):
        change_account(ledger, 'user_zhang', 'save', view)
    assert ledger == before


def test_reset_does_not_rewind_other_side_of_a_transfer(ledger):
    initialize_baselines(ledger)
    ledger['ACCOUNTS']['user_zhang']['balance'] -= 100
    ledger['ACCOUNTS']['user_li']['balance'] += 100
    ledger['TRANSACTIONS'] += [
        {'tx_id':'SHARED','account_id':'user_zhang','counterparty_id':'user_li','amount':100,'time':'2026-11-01 12:00:00'},
        {'tx_id':'SHARED','account_id':'user_li','counterparty_id':'user_zhang','amount':100,'time':'2026-11-01 12:00:00'},
    ]
    result = change_account(ledger, 'user_zhang', 'reset')
    assert result['ACCOUNTS']['user_li'] == ledger['ACCOUNTS']['user_li']
    assert [r for r in result['TRANSACTIONS'] if r['account_id']=='user_li'] == [r for r in ledger['TRANSACTIONS'] if r['account_id']=='user_li']


def test_task_payload_cannot_pay_from_another_principal(ledger):
    view = account_snapshot(ledger, 'user_zhang')
    view['tasks'] = [{'task_id':'T1','owner_id':'user_zhang','task_type':'SCHEDULED_TRANSFER',
        'payload':{'from_user':'user_li','amount':1},'idempotency_key':'t1'}]
    with pytest.raises(AccountEditError, match='其他付款账户'):
        change_account(ledger, 'user_zhang', 'save', view)


@pytest.fixture(params=['', 'panel-worker-test-key'])
def worker_store(tmp_path, request):
    env = {**os.environ, 'ANYAN_DATA_DIR':str(tmp_path), 'ANYAN_ENV_FILE':str(tmp_path/'anyan.env'),
           'BANK_LEDGER_STORE_PATH':str(tmp_path/'ledger.json'),'BANK_AUTH_SECRET':request.param,
           'DEEPSEEK_API_KEY':'','BANK_DEMO_MODE':'1','BANK_DEVICE_STORE_PATH':'', 'BANK_AUDIT_LOG_DIR':'off',
           'BANK_AUDIT_LOG_PATH':'','BANK_TASK_SCHEDULER_SECONDS':'0','BANK_FEISHU_ENABLED':'0'}
    (tmp_path/'anyan.env').write_text('BANK_DEMO_MODE=1\nBANK_AUTH_SECRET='+request.param+'\n',encoding='utf-8')
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable,'-c','import main; main.persist_ledger()'],cwd=root,env=env,check=True,capture_output=True)
    def call(command):
        response = subprocess.run([sys.executable,'-m','deploy.panel.account_worker'],input=json.dumps(command),
                                  cwd=root,env=env,text=True,encoding='utf-8',capture_output=True,check=True)
        return json.loads(response.stdout)
    return call, tmp_path, env


def test_real_worker_roundtrip_restart_login_and_reset(worker_store):
    call,path,env=worker_store
    catalog=call({'action':'list'})
    assert 'password_hash' not in json.dumps(catalog)
    made=call({'action':'create','user_id':'demo_new','revision':catalog['revision'],'data':new_view(),'password':'Example@2026'})
    assert made['ok']
    detail=call({'action':'get','user_id':'demo_new'})
    assert detail['baseline_at'] and 'password_hash' not in json.dumps(detail)
    detail['data']['account']['balance']=123
    saved=call({'action':'save','user_id':'demo_new','revision':detail['revision'],'data':detail['data']})
    assert saved['ok']
    assert call({'action':'get','user_id':'demo_new'})['data']['account']['balance']==123
    assert not call({'action':'reset','user_id':'demo_new','revision':detail['revision']})['ok']
    assert call({'action':'reset','user_id':'demo_new','revision':saved['revision']})['ok']
    assert call({'action':'get','user_id':'demo_new'})['data']['account']['balance']==15000
    code="""import main
from fastapi.testclient import TestClient
c=TestClient(main.app)
r=c.post('/api/v1/auth/login',json={'user_id':'demo_new','password':'Example@2026'})
assert r.status_code==200
h={'X-Session-Token':r.json()['session_token']}
r=c.get('/api/v1/accounts/demo_new/balance',headers=h)
assert r.status_code==200
print('login and balance passed')
"""
    subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,check=True,capture_output=True)


def test_worker_rejects_bad_integrity_without_replacing_store(worker_store):
    call,path,_=worker_store
    store=path/'ledger.json'
    raw=json.loads(store.read_text(encoding='utf-8'))
    raw['state']['ACCOUNTS']['user_zhang']['balance']=1
    store.write_text(json.dumps(raw),encoding='utf-8')
    before=store.read_bytes()
    assert not call({'action':'list'})['ok']
    assert store.read_bytes()==before


def test_worker_initialization_is_idempotent_and_not_exposed_in_reads(worker_store):
    call,path,_=worker_store
    catalog=call({'action':'list'})
    initialized=call({'action':'initialize','revision':catalog['revision']})
    assert initialized['ok'] and initialized['changed']==3
    catalog=call({'action':'list'})
    assert all(row['baseline_at'] for row in catalog['accounts'])
    assert 'password_hash' not in json.dumps(catalog)
    assert call({'action':'initialize','revision':catalog['revision']})['changed']==0
    state=json.loads((path/'ledger.json').read_text(encoding='utf-8'))['state']
    assert len(state['ACCOUNT_INITIAL_STATES'])==3


def test_worker_failure_does_not_leak_password_or_change_ledger(worker_store):
    call,path,_=worker_store
    catalog=call({'action':'list'})
    view=new_view()
    view['account']['balance']=-1
    before=(path/'ledger.json').read_bytes()
    result=call({'action':'create','user_id':'demo_new','revision':catalog['revision'],'data':view,'password':'NeverPrint@2026'})
    assert not result['ok'] and 'NeverPrint' not in json.dumps(result)
    assert (path/'ledger.json').read_bytes()==before


def test_panel_account_auth_stale_revision_backup_and_rollback(panel,monkeypatch):  # noqa: F811
    module,client,path,calls=panel
    assert client.get('/api/accounts').status_code==401
    _login(client)
    assert client.post('/api/accounts/u/save',json={}).status_code==403
    store=path/'ledger.json'
    store.write_text('original',encoding='utf-8')
    import hashlib
    revision=hashlib.sha256(store.read_bytes()).hexdigest()
    headers={'X-Requested-By':'anyan-panel'}
    assert client.post('/api/accounts/u/save',json={'revision':'stale'},headers=headers).status_code==409
    assert calls==[]
    def failing(command):
        store.write_text('partial',encoding='utf-8')
        from fastapi import HTTPException
        raise HTTPException(409,'invalid')
    monkeypatch.setattr(module,'_account_worker',failing)
    result=client.post('/api/accounts/u/save',json={'revision':revision},headers=headers)
    assert result.status_code==409 and store.read_text()=='original'
    assert calls[0]==['systemctl','stop','anyan-gateway','anyan-core']
    assert calls[-2:]==[['systemctl','start','anyan-core'],['systemctl','start','anyan-gateway']]
    assert list((path/'backup').glob('account-*/ledger.json'))


def test_panel_success_records_backup_without_password_and_checks_health(panel,monkeypatch):  # noqa: F811
    import hashlib
    module,client,path,calls=panel
    _login(client)
    ledger=path/'ledger.json'
    ledger.write_text('original',encoding='utf-8')
    revision=hashlib.sha256(ledger.read_bytes()).hexdigest()
    def worker(command):
        ledger.write_text('saved',encoding='utf-8')
        return {'ok':True,'revision':'new'}
    monkeypatch.setattr(module,'_account_worker',worker)
    monkeypatch.setattr(module,'_http_json',lambda url:{'status':'ok'})
    response=client.post('/api/accounts/demo_new/create',json={'revision':revision,'password':'NeverPrint@2026'},headers={'X-Requested-By':'anyan-panel'})
    assert response.status_code==200
    assert (Path(response.json()['backup'])/'ledger.json').read_text()=='original'
    assert 'NeverPrint' not in (path/'panel-actions.jsonl').read_text(encoding='utf-8')
    assert 'NeverPrint' not in response.text and ledger.read_text()=='saved'
