"""Windows-compatible local lifecycle, isolated import and real panel roundtrip."""
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time

from dotenv import dotenv_values
import httpx
import pytest

from deploy.panel import local_runtime as runtime


def free_ports():
    sockets = [socket.socket() for _ in range(4)]
    try:
        for sock in sockets:
            sock.bind(('127.0.0.1', 0))
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def test_settings_imports_existing_stores_without_changing_originals(tmp_path):
    root, data = tmp_path / 'original', tmp_path / 'managed'
    root.mkdir()
    (root / '.env').write_text('BANK_AUTH_SECRET=existing-signing-secret\nBANK_FEISHU_ENABLED=1\n', encoding='utf-8')
    devices = root / 'agent_devices.json'
    devices.write_text('{"preserve":"signed-device-registry"}', encoding='utf-8')
    db = root / 'agent_conversations.sqlite3'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE conversations (body TEXT)')
        conn.execute("INSERT INTO conversations VALUES ('existing-history')")
    originals = {p: p.read_bytes() for p in (root / '.env', devices, db)}
    ports = dict(zip(runtime.UNITS, free_ports()[:3]))
    env_file = runtime.prepare_settings(data, root, ports)
    settings = dotenv_values(env_file)
    assert settings['BANK_AUTH_SECRET'] == 'existing-signing-secret'
    assert settings['BANK_FEISHU_ENABLED'] == '0'
    assert Path(settings['BANK_LEDGER_STORE_PATH']) == data / 'ledger.json'
    assert (data / 'devices.json').read_bytes() == originals[devices]
    with sqlite3.connect(data / 'conversations.sqlite3') as conn:
        assert conn.execute('SELECT body FROM conversations').fetchone()[0] == 'existing-history'
    assert all(p.read_bytes() == content for p, content in originals.items())
    token = (data / 'panel.token').read_bytes()
    (data / 'devices.json').write_text('new-local-enrollment', encoding='utf-8')
    runtime.prepare_settings(data, root, ports)
    assert (data / 'devices.json').read_text() == 'new-local-enrollment'
    assert (data / 'panel.token').read_bytes() == token


def test_foreign_port_is_never_adopted_or_stopped(tmp_path):
    with socket.socket() as foreign:
        foreign.bind(('127.0.0.1', 0))
        foreign.listen()
        port = foreign.getsockname()[1]
        stack = runtime.LocalStack(tmp_path, tmp_path, {'anyan-core': port}, {})
        with pytest.raises(RuntimeError, match='unmanaged'):
            stack.start('anyan-core')
        stack.stop('anyan-core')
        assert not stack.children and not runtime.port_free(port)


@pytest.mark.parametrize('url', ['https://127.0.0.1', 'http://example.com', 'http://127.0.0.1/other'])
def test_control_refuses_nonlocal_config(tmp_path, url):
    config = tmp_path / 'controller.json'
    config.write_text(json.dumps({'url': url, 'token': 'secret'}))
    with pytest.raises(ValueError, match='control address'):
        runtime.control_request(config, 'status')


@pytest.fixture
def local_stack(tmp_path):
    root = Path(__file__).resolve().parents[1]
    data, source = tmp_path / 'data', tmp_path / 'empty-source'
    source.mkdir()
    (source / '.env').write_text('BANK_REQUIRE_SESSION=1\nDEEPSEEK_API_KEY=\nDASHSCOPE_API_KEY=\nBANK_TASK_SCHEDULER_SECONDS=0\n')
    allocated = free_ports()
    ports = dict(zip(runtime.UNITS, allocated[:3]))
    runtime.prepare_settings(data, source, ports)
    args = [sys.executable, str(root / 'scripts/serve_local_panel.py'), '--data-dir', str(data), '--no-browser',
            '--core-port', str(allocated[0]), '--gateway-port', str(allocated[1]),
            '--panel-port', str(allocated[2]), '--control-port', str(allocated[3])]
    config = data / 'local-control.json'
    try:
        result = subprocess.run([*args, 'start'], cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=85)
        assert result.returncode == 0, result.stderr + (data / 'logs/supervisor.log').read_text(encoding='utf-8')
        yield data, ports, args
    finally:
        try:
            runtime.control_request(config, 'shutdown')
        except (OSError, ValueError):
            pass
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and any(not runtime.port_free(p) for p in allocated):
            time.sleep(.2)
        assert all(runtime.port_free(p) for p in allocated), 'Local test services did not stop cleanly'


def test_real_local_panel_create_edit_reset_restart_and_control_auth(local_stack):
    data, ports, args = local_stack
    config = data / 'local-control.json'
    control = json.loads(config.read_text())
    panel_url = f'http://127.0.0.1:{ports["anyan-panel"]}'
    core_url = f'http://127.0.0.1:{ports["anyan-core"]}'
    with httpx.Client(base_url=panel_url, trust_env=False, timeout=100) as client:
        assert client.post(control['url'] + '/control', json={'operation': 'shutdown'}).status_code == 403
        assert client.post(control['url'] + '/control', headers={'X-Local-Control': control['token']},
                           json={'operation': 'stop', 'units': ['sshd']}).status_code == 503
        assert client.get('/api/accounts').status_code == 401
        info = client.get('/api/panel-info').json()
        assert info['mode'] == 'local' and info['services'] == ['anyan-core', 'anyan-gateway']
        assert client.post('/api/login', json={'token': (data / 'panel.token').read_text().strip()}).status_code == 200
        overview = client.get('/api/overview')
        assert overview.status_code == 200, overview.text
        assert all(row['active'] == 'active' for row in overview.json()['services'])
        assert overview.json()['traffic']['available'] is False
        if os.name == 'nt':
            assert overview.json()['system']['memory']['total'] > 0
            assert overview.json()['system']['uptime_hours'] > 0
        catalog = client.get('/api/accounts').json()
        assert all(row['baseline_at'] for row in catalog['accounts'])
        original = client.get('/api/accounts/user_zhang').json()['data']
        view = deepcopy(catalog['template'])
        view['user_id'] = 'local_created'
        view['account'].update(name='本地新增', phone='13600009999', card_no='62220200999')
        headers = {'X-Requested-By': 'anyan-panel'}
        create = client.post('/api/accounts/local_created/create', headers=headers,
                             json={'revision': catalog['revision'], 'data': view, 'password': 'Example@2026'})
        assert create.status_code == 200, create.text
        assert (Path(create.json()['backup']) / 'ledger.json').is_file()
        detail = client.get('/api/accounts/local_created').json()
        detail['data']['account']['balance'] = 321
        saved = client.post('/api/accounts/local_created/save', headers=headers,
                            json={'revision': detail['revision'], 'data': detail['data']})
        assert saved.status_code == 200, saved.text
        assert client.post('/api/accounts/local_created/reset', headers=headers,
                           json={'revision': detail['revision']}).status_code == 409
        login = client.post(core_url + '/api/v1/auth/login', json={'user_id': 'local_created', 'password': 'Example@2026'})
        assert login.status_code == 200, login.text
        balance = client.get(core_url + '/api/v1/accounts/local_created/balance',
                             headers={'X-Session-Token': login.json()['session_token']})
        assert balance.json()['available_balance'] == 321
        reset = client.post('/api/accounts/local_created/reset', headers=headers, json={'revision': saved.json()['revision']})
        assert reset.status_code == 200, reset.text
        assert client.get('/api/accounts/local_created').json()['data']['account']['balance'] == 15000
        assert client.get('/api/accounts/user_zhang').json()['data'] == original
        assert client.post('/api/restart/anyan-core', headers=headers).status_code == 200
        assert client.get('/api/accounts/local_created').json()['data']['account']['balance'] == 15000
        assert client.post('/api/restart/nginx', headers=headers).status_code == 400
        assert 'Uvicorn' in client.get('/api/logs?unit=anyan-core').json()['text']
        assert 'Example@2026' not in (data / 'panel-actions.jsonl').read_text(encoding='utf-8')
        runtime.control_request(config, 'stop', ('anyan-gateway',))
        # Reopening the launcher recovers its own stopped service, retaining data.
        reopened = subprocess.run([*args, 'start'], capture_output=True, text=True, encoding='utf-8', timeout=65)
        assert reopened.returncode == 0, reopened.stderr
        assert runtime.control_request(config, 'status')['services']['anyan-gateway']['active'] == 'active'
        stopped = subprocess.run([*args, 'stop'], capture_output=True, text=True, encoding='utf-8', timeout=65)
        assert stopped.returncode == 0, stopped.stderr
        assert all(runtime.port_free(p) for p in ports.values())
        restarted = subprocess.run([*args, 'start'], capture_output=True, text=True, encoding='utf-8', timeout=85)
        assert restarted.returncode == 0, restarted.stderr
        # The panel session resets with its process; durable business data stays.
        client.post('/api/login', json={'token': (data / 'panel.token').read_text().strip()})
        assert client.get('/api/accounts/local_created').json()['data']['account']['balance'] == 15000
