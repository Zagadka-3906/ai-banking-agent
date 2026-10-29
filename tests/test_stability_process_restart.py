"""Exercise committed payment replay across an actual Core process death."""
from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

import requests


def test_committed_transfer_survives_process_kill_and_replay(tmp_path):
    root = Path(__file__).resolve().parents[1]
    with socket.socket() as available:
        available.bind(('127.0.0.1', 0))
        port = available.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('BANK_', 'DEEPSEEK_', 'DASHSCOPE_'))}
    env.update({'PYTHON_DOTENV_DISABLED': '1', 'BANK_AUTH_SECRET': secrets.token_hex(32),
                'BANK_REQUIRE_SESSION': '1', 'BANK_DEMO_MODE': '1', 'BANK_LOGIN_REQUIRE_OTP': '0',
                'BANK_LEDGER_STORE_PATH': str(tmp_path/'ledger.json'),
                'BANK_DEVICE_STORE_PATH': str(tmp_path/'devices.json'),
                'BANK_AUDIT_LOG_PATH': '', 'BANK_AUDIT_LOG_DIR': 'off',
                'BANK_TASK_SCHEDULER_SECONDS': '0', 'BANK_RATE_LIMIT_PER_MINUTE': '0',
                'BANK_REQUIRE_CLIENT_AUTH': '0', 'BANK_FEISHU_ENABLED': '0'})

    @contextmanager
    def core():
        with (tmp_path/'core.log').open('ab') as log:
            child = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1',
                                      '--port', str(port), '--no-access-log'], cwd=root, env=env,
                                     stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic()+30
                while True:
                    assert child.poll() is None, 'Isolated Core exited before readiness'
                    try:
                        if requests.get(base+'/healthz', timeout=1).status_code == 200:
                            break
                    except requests.RequestException:
                        pass
                    assert time.monotonic() < deadline, 'Isolated Core readiness timeout'
                    time.sleep(.1)
                yield child
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=10)

    def request(method, path, *, headers=None, body=None):
        response = requests.request(method, base+path, headers=headers, json=body, timeout=10)
        assert response.status_code == 200, f'Unexpected isolated Core status: {response.status_code}'
        return response.json()

    def login(principal):
        result = request('POST', '/api/v1/auth/login', body={'user_id': principal, 'password': 'Bank@2026'})
        return {'X-User-Id': principal, 'X-Session-Token': result['session_token']}

    def balance(principal, headers):
        return request('GET', f'/api/v1/accounts/{principal}/balance', headers=headers)['total_balance']

    def transactions(principal, headers):
        return request('GET', f'/api/v1/accounts/{principal}/transactions', headers=headers)['data']

    payload = {'from_user': 'user_zhang', 'target_name_or_id': '李四', 'amount': 10}
    key = 'actual-process-restart-transfer'
    with core() as first:
        payer, payee = login('user_zhang'), login('user_li')
        before = balance('user_zhang', payer), balance('user_li', payee)
        original_ids = {row['tx_id'] for row in transactions('user_zhang', payer)+transactions('user_li', payee)}
        precheck = request('POST', '/api/v1/risk/precheck', headers=payer, body=payload)
        authorized = {**payer, 'X-Auth-Token': precheck['auth_token'], 'Idempotency-Key': key}
        request('POST', '/api/v1/risk/confirm', headers=authorized, body={})
        receipt = request('POST', '/api/v1/transfers/execute', headers=authorized, body=payload)
        first_pid = first.pid
        # No lifespan shutdown or import-time reset: kill the actual process
        # after the HTTP success, then start another process on the same store.
        first.kill()
        first.wait(timeout=10)
    with core() as restarted:
        assert restarted.pid != first_pid
        payer, payee = login('user_zhang'), login('user_li')
        assert (balance('user_zhang', payer), balance('user_li', payee)) == (before[0]-10, before[1]+10)
        # A receipt is replayed for the authenticated owner without reusing
        # the pre-restart authorization or causing a second settlement.
        replay = request('POST', '/api/v1/transfers/execute', headers={**payer, 'Idempotency-Key': key}, body=payload)
        assert replay == receipt
        assert (balance('user_zhang', payer), balance('user_li', payee)) == (before[0]-10, before[1]+10)
        added = [row for row in transactions('user_zhang', payer)+transactions('user_li', payee)
                 if row['tx_id'] not in original_ids]
        assert len(added) == 2 and all(row['amount'] == 10 for row in added)
        assert {row['account_id'] for row in added} == {'user_zhang', 'user_li'}
        health = request('GET', '/api/v1/security/health')['ledger_persistence']
        assert health['file_loaded'] and not health['last_write_failed'] and not health['writes_frozen']
