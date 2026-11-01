"""Owned local Core/Gateway/panel processes with authenticated loopback control.

Runs on Windows as hidden processes. It never adopts/kills an existing server
by port or PID; only its own Popen handles and per-start shutdown files are used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from typing import Any
import urllib.error
from urllib.parse import urlsplit
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[2]
UNITS = {'anyan-core': 'main:app', 'anyan-gateway': 'agent_api:app', 'anyan-panel': 'deploy.panel.server:app'}


def default_data_dir(root: Path = ROOT) -> Path:
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local/state')))
    identity = hashlib.sha256(str(root.resolve()).casefold().encode()).hexdigest()[:12]
    return base / 'bank-agent' / 'local-panel' / identity


def http_json(url: str, *, body: dict | None = None, token: str | None = None, timeout: float = 3) -> dict:
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['X-Local-Control'] = token
    request = urllib.request.Request(url, headers=headers, data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as response:
        return json.load(response)


def control_request(control_file: Path, operation: str, units: tuple[str, ...] = ()) -> dict:
    config = json.loads(control_file.read_text(encoding='utf-8'))
    target = urlsplit(config['url'])
    if target.scheme != 'http' or target.hostname != '127.0.0.1' or target.path not in ('', '/'):
        raise ValueError('Invalid local control address')
    return http_json(config['url'] + '/control', body={'operation': operation, 'units': list(units)}, token=config['token'], timeout=100)


def port_free(port: int) -> bool:
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def child_options() -> dict[str, Any]:
    return {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}


def prepare_settings(data: Path, root: Path, ports: dict[str, int]) -> Path:
    """One-time import keeps original stores intact and uses SQLite backup."""
    from dotenv import dotenv_values
    data.mkdir(parents=True, exist_ok=True)
    (data / 'logs').mkdir(exist_ok=True)
    (data / 'control').mkdir(exist_ok=True)
    env_file = data / 'local.env'
    if not env_file.exists():
        values = dotenv_values(root / '.env')
        imports = {}
        def source(key: str, fallback: str) -> Path | None:
            value = values.get(key) or fallback
            if value.lower() in {'memory', ':memory:'}:
                return None
            result = Path(value)
            return result if result.is_absolute() else root / result
        for key, fallback, name in [('BANK_LEDGER_STORE_PATH', '', 'ledger.json'),
                                    ('BANK_DEVICE_STORE_PATH', 'agent_devices.json', 'devices.json')]:
            original = source(key, fallback) if values.get(key) or fallback else None
            if original and original.is_file() and not (data / name).exists():
                shutil.copy2(original, data / name)
                imports[name] = str(original)
        original_db = source('BANK_CONVERSATION_DB', 'agent_conversations.sqlite3')
        if original_db and original_db.is_file() and not (data / 'conversations.sqlite3').exists():
            with sqlite3.connect(original_db.as_uri() + '?mode=ro', uri=True) as origin, sqlite3.connect(data / 'conversations.sqlite3') as target:
                origin.backup(target)
            imports['conversations.sqlite3'] = str(original_db)
        approval = source('BANK_APPROVAL_STORE_PATH', str(original_db) + '.approvals.json' if original_db else 'memory')
        if approval and approval.is_file() and not (data / 'conversations.sqlite3.approvals.json').exists():
            shutil.copy2(approval, data / 'conversations.sqlite3.approvals.json')
        overrides = {'BANK_LEDGER_STORE_PATH': str(data / 'ledger.json'), 'BANK_DEVICE_STORE_PATH': str(data / 'devices.json'),
            'BANK_CONVERSATION_DB': str(data / 'conversations.sqlite3'), 'BANK_APPROVAL_STORE_PATH': str(data / 'conversations.sqlite3.approvals.json'),
            'BANK_AUDIT_LOG_DIR': str(data / 'audit'), 'BANK_AUDIT_LOG_PATH': '', 'BANK_FEISHU_ENABLED': '0',
            'BANK_API_BASE': f'http://127.0.0.1:{ports["anyan-core"]}/api/v1', 'BANK_DEMO_MODE': '1'}
        if not values.get('BANK_AUTH_SECRET'):
            overrides['BANK_AUTH_SECRET'] = secrets.token_hex(32)
        content = (root / '.env').read_text(encoding='utf-8') if (root / '.env').is_file() else ''
        # dotenv parses later definitions last. JSON string quoting preserves
        # paths with spaces; normalize slashes to avoid dotenv escape ambiguity.
        content += '\n# Local panel managed data and loopback wiring\n'
        content += ''.join(k + '=' + json.dumps(v.replace('\\', '/'), ensure_ascii=False) + '\n' for k, v in overrides.items())
        env_file.write_text(content, encoding='utf-8')
        env_file.chmod(0o600)
        (data / 'imports.json').write_text(json.dumps(imports, ensure_ascii=False, indent=2), encoding='utf-8')
    if not (data / 'panel.token').exists():
        (data / 'panel.token').write_text(secrets.token_hex(32) + '\n', encoding='utf-8')
        (data / 'panel.token').chmod(0o600)
    return env_file


def runtime_env(data: Path, env_file: Path, ports: dict[str, int]) -> dict[str, str]:
    from dotenv import dotenv_values
    env = {**os.environ, **{k: v for k, v in dotenv_values(env_file).items() if v is not None}}
    env.update({'ANYAN_PANEL_MODE': 'local', 'ANYAN_DATA_DIR': str(data), 'ANYAN_ENV_FILE': str(env_file),
                'PANEL_TOKEN_FILE': str(data / 'panel.token'), 'ANYAN_LOCAL_CONTROL_FILE': str(data / 'local-control.json'),
                'ANYAN_CORE_PORT': str(ports['anyan-core']), 'ANYAN_GATEWAY_PORT': str(ports['anyan-gateway']),
                'BANK_API_BASE': f'http://127.0.0.1:{ports["anyan-core"]}/api/v1',
                'PYTHONIOENCODING': 'utf-8'})
    return env


class LocalStack:
    def __init__(self, data: Path, root: Path, ports: dict[str, int], env: dict[str, str]):
        self.data, self.root, self.ports, self.env = data, root, ports, env
        self.children: dict[str, subprocess.Popen] = {}
        self.stop_files: dict[str, Path] = {}
        self.started: dict[str, str] = {}
        self.lock = threading.RLock()

    def status(self) -> dict:
        return {'root': str(self.root), 'data_dir': str(self.data), 'services': {
            unit: {'unit': unit, 'active': 'active' if unit in self.children and self.children[unit].poll() is None else 'inactive',
                   'pid': self.children[unit].pid if unit in self.children else None, 'port': self.ports[unit],
                   'since': self.started.get(unit, ''), 'sub': 'running', 'memory_mb': None, 'restarts': 0}
            for unit in UNITS}}

    def start(self, unit: str) -> None:
        with self.lock:
            if unit in self.children and self.children[unit].poll() is None:
                return
            if not port_free(self.ports[unit]):
                raise RuntimeError('Port occupied; unmanaged service was not changed: ' + str(self.ports[unit]))
            marker = self.data / 'control' / (unit + '-' + secrets.token_hex(8) + '.stop')
            self.stop_files[unit] = marker
            child_env = {**runtime_env(self.data, Path(self.env['ANYAN_ENV_FILE']), self.ports),
                         'ANYAN_LOCAL_SHUTDOWN_FILE': str(marker), 'ANYAN_LOCAL_SERVICE': unit}
            with (self.data / 'logs' / (unit + '.log')).open('ab') as log:
                self.children[unit] = subprocess.Popen([sys.executable, str(self.root / 'scripts/serve_local_panel.py'),
                    '_service', '--unit', unit, '--port', str(self.ports[unit])], cwd=self.root, env=child_env,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, **child_options())
            self.started[unit] = datetime.now(timezone.utc).isoformat()
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                if self.children[unit].poll() is not None:
                    raise RuntimeError(unit + ' exited; see its local service log')
                try:
                    url = f'http://127.0.0.1:{self.ports[unit]}'
                    if unit == 'anyan-panel':
                        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url + '/', timeout=1) as response:
                            if response.status == 200:
                                return
                    elif http_json(url + '/healthz', timeout=1).get('status') == 'ok':
                        return
                except (OSError, ValueError):
                    pass
                time.sleep(0.2)
            raise RuntimeError(unit + ' startup timed out')

    def stop(self, unit: str) -> None:
        with self.lock:
            child = self.children.get(unit)
            if child is None or child.poll() is not None:
                return
            self.stop_files[unit].touch()
            child.wait(timeout=30)
            self.stop_files[unit].unlink(missing_ok=True)


def serve(data: Path, root: Path, ports: dict[str, int], control_port: int) -> None:
    for port in [*ports.values(), control_port]:
        if not port_free(port):
            raise RuntimeError('Port occupied; existing processes were not changed: ' + str(port))
    env_file = prepare_settings(data, root, ports)
    env = runtime_env(data, env_file, ports)
    stack = LocalStack(data, root, ports, env)
    stopped = threading.Event()
    token = secrets.token_hex(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if not hmac.compare_digest(self.headers.get('X-Local-Control', ''), token):
                self.send_error(403)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 8192:
                    raise ValueError('Invalid request size')
                body = json.loads(self.rfile.read(size))
                operation, units = body['operation'], body.get('units', [])
                if not isinstance(units, list) or any(unit not in UNITS for unit in units):
                    raise ValueError('Unknown local service')
                with stack.lock:
                    if operation == 'status':
                        result = stack.status()
                    elif operation == 'shutdown':
                        stopped.set()
                        result = {'ok': True}
                    elif operation in {'start', 'stop', 'restart'}:
                        for unit in units:
                            if operation in {'stop', 'restart'}:
                                stack.stop(unit)
                            if operation in {'start', 'restart'}:
                                stack.start(unit)
                        result = {'ok': True}
                    else:
                        raise ValueError('Unknown control operation')
                payload = json.dumps(result).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception:
                self.send_error(503, 'Local service operation failed; inspect local logs')
    control = ThreadingHTTPServer(('127.0.0.1', control_port), Handler)
    config = data / 'local-control.json'
    config.write_text(json.dumps({'url': f'http://127.0.0.1:{control_port}', 'token': token}), encoding='utf-8')
    config.chmod(0o600)
    threading.Thread(target=control.serve_forever, daemon=True).start()
    try:
        bootstrap = "import main; from bank_core.admin_accounts import initialize_baselines; from bank_core.persistence import LEDGER_COLLECTIONS; from scheduler import TASK_STORE; assert not main.ledger_store_status()['writes_frozen']; initialize_baselines({**LEDGER_COLLECTIONS, 'TASK_STORE': TASK_STORE.export_records()}); main.persist_ledger(); assert not main.ledger_store_status()['last_write_failed']"
        # A second launcher cannot start services while the initial ledger is
        # being imported, and status reports readiness only after health checks.
        with stack.lock:
            subprocess.run([sys.executable, '-c', bootstrap], cwd=root, env={**env, 'DEEPSEEK_API_KEY': '', 'DASHSCOPE_API_KEY': '', 'BANK_FEISHU_ENABLED': '0'}, check=True)
            for unit in UNITS:
                stack.start(unit)
        while not stopped.wait(1):
            pass
    finally:
        for unit in reversed(UNITS):
            stack.stop(unit)
        control.shutdown()
        control.server_close()


def run_service(unit: str, port: int) -> None:
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(UNITS[unit], host='127.0.0.1', port=port, workers=1, timeout_graceful_shutdown=20))
    marker = Path(os.environ['ANYAN_LOCAL_SHUTDOWN_FILE'])
    def watch():
        while not marker.exists():
            time.sleep(0.2)
        server.should_exit = True
    threading.Thread(target=watch, daemon=True).start()
    server.run()


def main() -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Start local banking backend and account management panel')
    parser.add_argument('command', choices=['start', 'stop', 'status', '_serve', '_service'])
    parser.add_argument('--data-dir', type=Path, default=default_data_dir())
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--core-port', type=int, default=8000)
    parser.add_argument('--gateway-port', type=int, default=8100)
    parser.add_argument('--panel-port', type=int, default=9100)
    parser.add_argument('--control-port', type=int, default=19111)
    parser.add_argument('--unit', choices=list(UNITS))
    parser.add_argument('--port', type=int)
    args = parser.parse_args()
    data = args.data_dir.resolve()
    ports = dict(zip(UNITS, [args.core_port, args.gateway_port, args.panel_port]))
    if len(set([*ports.values(), args.control_port])) != 4 or any(not 1024 <= p <= 65535 for p in [*ports.values(), args.control_port]):
        parser.error('Use four distinct unprivileged local ports')
    if args.command == '_service':
        if args.unit is None or args.port is None:
            parser.error('Missing service identity')
        run_service(args.unit, args.port)
        return 0
    if args.command == '_serve':
        serve(data, ROOT, ports, args.control_port)
        return 0
    config = data / 'local-control.json'
    try:
        status = control_request(config, 'status')
    except (OSError, ValueError):
        status = None
    if args.command == 'status':
        print(json.dumps(status or {'running': False}, ensure_ascii=False))
        return 0
    if args.command == 'stop':
        if status:
            control_request(config, 'shutdown')
            owned_ports = [row['port'] for row in status['services'].values()]
            owned_ports.append(urlsplit(json.loads(config.read_text(encoding='utf-8'))['url']).port)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if all(port_free(port) for port in owned_ports):
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError('本机服务尚未完成停止，请查看 supervisor.log')
        print('本机服务已停止。')
        return 0
    if status:
        if Path(status['root']).resolve() != ROOT or Path(status['data_dir']).resolve() != data:
            raise RuntimeError('Local controller belongs to another checkout')
        for unit in UNITS:
            if status['services'][unit]['active'] != 'active':
                control_request(config, 'start', (unit,))
        status = control_request(config, 'status')
    if not status:
        for port in [*ports.values(), args.control_port]:
            if not port_free(port):
                raise RuntimeError('端口已被其他进程占用，请先结束原本机服务后再启用面板：' + str(port))
        data.mkdir(parents=True, exist_ok=True)
        (data / 'logs').mkdir(exist_ok=True)
        with (data / 'logs/supervisor.log').open('ab') as log:
            subprocess.Popen([sys.executable, str(ROOT / 'scripts/serve_local_panel.py'), '_serve', '--data-dir', str(data),
                '--core-port', str(args.core_port), '--gateway-port', str(args.gateway_port), '--panel-port', str(args.panel_port),
                '--control-port', str(args.control_port)], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **child_options())
        deadline = time.monotonic() + 65
        while time.monotonic() < deadline:
            try:
                status = control_request(config, 'status')
                if all(row['active'] == 'active' for row in status['services'].values()):
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.3)
        else:
            raise RuntimeError('本机服务启动失败，请查看 ' + str(data / 'logs/supervisor.log'))
    if Path(status['root']).resolve() != ROOT or Path(status['data_dir']).resolve() != data:
        raise RuntimeError('Local controller belongs to another checkout')
    url = f'http://127.0.0.1:{status["services"]["anyan-panel"]["port"]}'
    print('本地后台：' + url)
    print('访问令牌文件：' + str(data / 'panel.token'))
    print('本地数据目录：' + str(data))
    if not args.no_browser:
        webbrowser.open(url)
    return 0
