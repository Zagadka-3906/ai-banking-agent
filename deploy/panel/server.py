"""安言运维台: a server back office for one 安言 deployment.

It listens on 127.0.0.1 only and is reached through an SSH tunnel; an access
token (``/etc/anyan/panel.token``) guards it on top of that. Everything it shows
is read from the machine - systemd, the journal, the Nginx access log, the
audit files and the ledger snapshot - and secrets are reported only as
configured or not. The few actions (restart a service, reset the demo data)
are written to their own log; a reset moves the old state into a backup folder
instead of deleting it.

Run: uvicorn server:app --app-dir deploy/panel --host 127.0.0.1 --port 9100
"""

from __future__ import annotations

import hmac
from functools import wraps
import json
import os
import re
import secrets
import socket
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from fastapi import Cookie, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse

DATA_DIR = Path(os.getenv("ANYAN_DATA_DIR", "/var/lib/anyan"))
ENV_FILE = Path(os.getenv("ANYAN_ENV_FILE", "/etc/anyan/anyan.env"))
TOKEN_FILE = Path(os.getenv("PANEL_TOKEN_FILE", "/etc/anyan/panel.token"))
ACCESS_LOG = Path(os.getenv("NGINX_ACCESS_LOG", "/var/log/nginx/access.log"))
ACTION_LOG = DATA_DIR / "panel-actions.jsonl"
STATIC = Path(__file__).with_name("index.html")
LOCAL_MODE = os.getenv('ANYAN_PANEL_MODE') == 'local'
LOCAL_CONTROL_FILE = Path(os.getenv('ANYAN_LOCAL_CONTROL_FILE', str(DATA_DIR / 'local-control.json')))
CORE_PORT = int(os.getenv('ANYAN_CORE_PORT', '8000'))
GATEWAY_PORT = int(os.getenv('ANYAN_GATEWAY_PORT', '8100'))

SERVICES = ("anyan-core", "anyan-gateway") if LOCAL_MODE else ("anyan-core", "anyan-gateway", "nginx")
SESSION_SECONDS = 12 * 3600
CSRF_HEADER = "anyan-panel"
# Settings shown as configured / not configured, never by value.
SECRET_KEYS = ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "BANK_AUTH_SECRET", "BANK_ADMIN_AUDIT_TOKEN", "FEISHU_APP_SECRET")
VISIBLE_KEYS = ("BANK_LLM_MODEL", "BANK_MODEL_FAST_PATH", "BANK_MFA_DYNAMIC_OTP", "BANK_REQUIRE_SESSION", "BANK_DEMO_MODE",
                "BANK_RATE_LIMIT_PER_MINUTE", "BANK_AGENT_RATE_LIMIT_PER_MINUTE", "BANK_ASR_ENABLED", "BANK_FEISHU_ENABLED")
# State the gateway and core rebuild from their seed when it is missing.
RESETTABLE = ("ledger.json", "devices.json", "conversations.sqlite3", "conversations.sqlite3-wal",
              "conversations.sqlite3-shm", "conversations.sqlite3.approvals.json")

app = FastAPI(title="安言运维台", docs_url=None, redoc_url=None, openapi_url=None)
_sessions: dict[str, float] = {}
_account_lock = threading.Lock()


# --- pure helpers (unit-tested) ----------------------------------------------

def env_overview(text: str) -> dict[str, Any]:
    values = {}
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return {
        "secrets": {key: bool(values.get(key)) for key in SECRET_KEYS},
        "settings": {key: values.get(key, "") for key in VISIBLE_KEYS},
    }


_LOG_LINE = re.compile(r'^(\S+) \S+ \S+ \[([^\]]+)\] "(\S+) (\S+)[^"]*" (\d{3}) (\d+|-)')


def parse_access_line(line: str) -> Optional[dict[str, Any]]:
    match = _LOG_LINE.match(line)
    if not match:
        return None
    ip, stamp, method, path, status, size = match.groups()
    try:
        at = datetime.strptime(stamp, "%d/%b/%Y:%H:%M:%S %z")
    except ValueError:
        return None
    return {"ip": ip, "at": at, "method": method, "path": path.split("?", 1)[0], "status": int(status),
            "bytes": 0 if size == "-" else int(size)}


def _area(path: str) -> str:
    parts = path.strip("/").split("/")
    if parts[0] == "gateway" and "messages" in parts:
        return "对话"
    if parts[0] == "gateway" and "suggestions" in parts:
        return "猜你想问"
    if parts[0] == "gateway" and "approvals" in parts:
        return "审批"
    if parts[0] == "gateway" and "speech" in parts:
        return "语音"
    if parts[0] == "core" and "auth" in parts:
        return "登录"
    if parts[0] in ("core", "gateway"):
        return "其他接口" if parts[-1] != "healthz" else "健康检查"
    return "其他"


def access_summary(lines: Iterable[str], now: datetime, hours: int = 24) -> dict[str, Any]:
    since = now - timedelta(hours=hours)
    rows = [row for row in map(parse_access_line, lines) if row and row["at"] >= since]
    statuses = Counter(f"{row['status'] // 100}xx" for row in rows)
    hourly = Counter(row["at"].astimezone(now.tzinfo).strftime("%H:00") for row in rows)
    return {
        "hours": hours,
        "requests": len(rows),
        "visitors": len({row["ip"] for row in rows}),
        "bytes_out": sum(row["bytes"] for row in rows),
        "status": dict(sorted(statuses.items())),
        "rate_limited": sum(1 for row in rows if row["status"] == 429),
        "server_errors": sum(1 for row in rows if row["status"] >= 500),
        "areas": dict(Counter(_area(row["path"]) for row in rows).most_common()),
        "top_visitors": Counter(row["ip"] for row in rows).most_common(5),
        "hourly": [{"hour": (now - timedelta(hours=offset)).astimezone(now.tzinfo).strftime("%H:00"),
                    "requests": hourly.get((now - timedelta(hours=offset)).astimezone(now.tzinfo).strftime("%H:00"), 0)}
                   for offset in range(min(hours, 24) - 1, -1, -1)],
    }


def _event_time(event: dict[str, Any]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(event.get("timestamp")))
    except ValueError:
        return None


def audit_summary(events: list[dict[str, Any]], now: datetime, hours: int = 24) -> dict[str, Any]:
    since = now - timedelta(hours=hours)
    recent = [event for event in events if (_event_time(event) or since) > since]

    def count(action: str, outcome: Optional[str] = None) -> int:
        return sum(1 for e in recent if e.get("action") == action and (outcome is None or e.get("outcome") == outcome))

    model_calls = sum(
        1 for e in recent
        if (e.get("action") == "INTENT_PARSE" and ((e.get("metadata") or {}).get("parse_meta") or {}).get("source") == "model")
        or (e.get("action") == "AGENT_SUGGEST" and (e.get("metadata") or {}).get("source") == "model")
        or e.get("action") == "SPEECH_TRANSCRIBE"
    )
    return {
        "hours": hours,
        "logins": count("LOGIN", "SUCCESS"),
        "login_failures": sum(1 for e in recent if e.get("action") == "LOGIN" and e.get("outcome") != "SUCCESS"),
        "active_users": len({e.get("user_id") for e in recent if e.get("action") == "LOGIN" and e.get("outcome") == "SUCCESS"}),
        "turns": count("AGENT_RESPONSE"),
        "model_calls": model_calls,
        "money_moves": sum(1 for e in recent if e.get("action") in {"TRANSFER_EXECUTE", "OPERATION_EXECUTE", "TASK_EXECUTE"}
                           and e.get("outcome") == "SUCCESS"),
        "blocked": sum(1 for e in recent if e.get("decision") == "BLOCK"),
        "injections": count("PROMPT_INJECTION_BLOCKED"),
    }


def ledger_overview(snapshot: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not snapshot:
        return {"persisted": False, "accounts": [], "counts": {}}
    state = snapshot.get("state") or {}
    accounts = [
        {"user_id": user_id, "name": record.get("name"), "balance": record.get("balance"),
         "locked": record.get("locked_amount"), "status": record.get("status")}
        for user_id, record in (state.get("ACCOUNTS") or {}).items()
    ]

    def size(name: str) -> int:
        value = state.get(name)
        if isinstance(value, dict):
            return sum(len(rows) if isinstance(rows, list) else 1 for rows in value.values())
        return len(value or [])

    return {"persisted": True, "accounts": accounts, "counts": {
        "交易流水": size("TRANSACTIONS"), "预约转账": size("SCHEDULED_TRANSFERS"), "AA 收款": size("SPLIT_REQUESTS"),
        "资金锁定": size("LOCKS"), "理财持仓": size("WEALTH_HOLDINGS"), "风险测评档案": size("WEALTH_RISK_PROFILES"),
        "卡片": size("CARDS"), "订阅": size("SUBSCRIPTIONS"),
    }}


def parse_systemctl_show(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


# --- machine access ----------------------------------------------------------

def _run(*args: str, timeout: float = 15) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _read_jsonl(path: Path, limit: int = 20000) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle.readlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def _http_json(url: str) -> Optional[dict[str, Any]]:
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


def _service(unit: str) -> dict[str, Any]:
    if LOCAL_MODE:
        from deploy.panel.local_runtime import control_request
        try:
            return control_request(LOCAL_CONTROL_FILE, 'status')['services'][unit]
        except (OSError, ValueError, KeyError):
            return {'unit': unit, 'active': 'unknown', 'sub': '', 'since': '', 'restarts': '', 'memory_mb': None}
    info = parse_systemctl_show(_run("systemctl", "show", unit, "--property=ActiveState,SubState,ActiveEnterTimestamp,MemoryCurrent,NRestarts"))
    memory = info.get("MemoryCurrent", "")
    return {"unit": unit, "active": info.get("ActiveState", "unknown"), "sub": info.get("SubState", ""),
            "since": info.get("ActiveEnterTimestamp", ""), "restarts": info.get("NRestarts", ""),
            "memory_mb": round(int(memory) / 1048576, 1) if memory.isdigit() else None}


def _system() -> dict[str, Any]:
    meminfo = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            meminfo[key] = int(value.split()[0]) * 1024
    except (OSError, ValueError):
        pass
    try:
        load = [float(x) for x in Path("/proc/loadavg").read_text().split()[:3]]
        uptime = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError):
        load, uptime = [], 0.0
    network = {}
    try:
        for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
            name, data = line.split(":", 1)
            fields = data.split()
            if name.strip() != "lo":
                network[name.strip()] = {"rx": int(fields[0]), "tx": int(fields[8])}
    except (OSError, ValueError, IndexError):
        pass
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', wintypes.DWORD), ('load', wintypes.DWORD)] + [
                (key, ctypes.c_ulonglong) for key in ('total', 'available', 'page_total', 'page_available', 'virtual_total', 'virtual_available', 'extended')]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            meminfo.update(MemTotal=status.total, MemAvailable=status.available)
        ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        uptime = ctypes.windll.kernel32.GetTickCount64() / 1000
    disk = shutil.disk_usage(DATA_DIR if DATA_DIR.is_dir() else Path.cwd())
    data_bytes = sum(f.stat().st_size for f in DATA_DIR.rglob("*") if f.is_file()) if DATA_DIR.is_dir() else 0
    return {
        "hostname": socket.gethostname(),
        "cpus": os.cpu_count(), "load": load, "uptime_hours": round(uptime / 3600, 1),
        "memory": {"total": meminfo.get("MemTotal"), "available": meminfo.get("MemAvailable"),
                   "swap_total": meminfo.get("SwapTotal"), "swap_free": meminfo.get("SwapFree")},
        "disk": {"total": disk.total, "used": disk.used}, "data_bytes": data_bytes, "network": network,
    }


def _record_action(action: str, result: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with ACTION_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "action": action, "result": result},
                                ensure_ascii=False) + "\n")


# --- authentication ----------------------------------------------------------

def _expected_token() -> str:
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _require(session: Optional[str]) -> None:
    expires = _sessions.get(session or "")
    if not expires or expires < time.time():
        _sessions.pop(session or "", None)
        raise HTTPException(401, "请先登录")


def _require_write(session: Optional[str], requested_by: Optional[str]) -> None:
    _require(session)
    # A cross-site form cannot set this header; the cookie is also SameSite=Strict.
    if requested_by != CSRF_HEADER:
        raise HTTPException(403, "缺少操作确认头")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC, headers={"Cache-Control": "no-store"})


@app.get('/accounts.js')
def account_script() -> FileResponse:
    return FileResponse(STATIC.with_name('accounts.js'), media_type='application/javascript', headers={'Cache-Control': 'no-store'})


@app.get('/api/panel-info')
def panel_info() -> dict[str, Any]:
    return {'mode': 'local' if LOCAL_MODE else 'server', 'services': list(SERVICES),
            'token_file': str(TOKEN_FILE) if LOCAL_MODE else '/etc/anyan/panel.token'}


@app.post("/api/login")
async def login(request: Request, response: Response) -> dict[str, Any]:
    body = await request.json()
    expected = _expected_token()
    if not expected or not hmac.compare_digest(expected, str(body.get("token", "")).strip()):
        time.sleep(1)  # slows guessing; the token is 32 random bytes anyway
        raise HTTPException(401, "访问令牌不正确")
    session = secrets.token_urlsafe(32)
    _sessions[session] = time.time() + SESSION_SECONDS
    response.set_cookie("anyan_panel", session, max_age=SESSION_SECONDS, httponly=True, samesite="strict")
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response, anyan_panel: Optional[str] = Cookie(None)) -> dict[str, Any]:
    _sessions.pop(anyan_panel or "", None)
    response.delete_cookie("anyan_panel")
    return {"ok": True}


# --- read endpoints ----------------------------------------------------------

@app.get("/api/overview")
def overview(anyan_panel: Optional[str] = Cookie(None)) -> dict[str, Any]:
    _require(anyan_panel)
    now = datetime.now().astimezone()
    try:
        lines = ACCESS_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    events = _read_jsonl(DATA_DIR / "audit/core-audit.jsonl") + _read_jsonl(DATA_DIR / "audit/gateway-audit.jsonl")
    try:
        env = env_overview(ENV_FILE.read_text(encoding="utf-8"))
    except OSError:
        env = {"secrets": {}, "settings": {}}
    return {
        "now": now.isoformat(),
        'mode': 'local' if LOCAL_MODE else 'server',
        "services": [_service(unit) for unit in SERVICES],
        "health": {"core": _http_json(f"http://127.0.0.1:{CORE_PORT}/healthz"), "gateway": _http_json(f"http://127.0.0.1:{GATEWAY_PORT}/healthz"),
                   "security": _http_json(f"http://127.0.0.1:{CORE_PORT}/api/v1/security/health")},
        "system": _system(),
        "traffic": {**access_summary(lines, now), 'available': not LOCAL_MODE},
        "activity": audit_summary(events, now),
        "config": env,
    }


@app.get("/api/business")
def business(anyan_panel: Optional[str] = Cookie(None)) -> dict[str, Any]:
    _require(anyan_panel)
    path = DATA_DIR / "ledger.json"
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except ValueError:
        snapshot = None
    view = ledger_overview(snapshot)
    view["updated_at"] = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat() if path.is_file() else None
    return view


@app.get("/api/audit")
def audit(limit: int = 100, only_issues: bool = False, anyan_panel: Optional[str] = Cookie(None)) -> dict[str, Any]:
    _require(anyan_panel)
    events = []
    for service in ("core", "gateway"):
        for event in _read_jsonl(DATA_DIR / f"audit/{service}-audit.jsonl", limit=2000):
            event["service"] = service
            events.append(event)
    if only_issues:
        events = [e for e in events if e.get("decision") == "BLOCK" or e.get("outcome") in {"FAILURE", "FAILED", "ERROR"}]
    events.sort(key=lambda e: str(e.get("timestamp")), reverse=True)
    keep = ("timestamp", "service", "action", "user_id", "decision", "outcome", "reason", "risk_level", "request_id")
    return {"events": [{key: e.get(key) for key in keep} for e in events[:max(1, min(limit, 500))]]}


@app.get("/api/logs")
def logs(unit: str = "anyan-gateway", lines: int = 150, issues: bool = False,
         anyan_panel: Optional[str] = Cookie(None)) -> dict[str, Any]:
    _require(anyan_panel)
    if unit not in SERVICES:
        raise HTTPException(400, "未知服务")
    if LOCAL_MODE:
        path = DATA_DIR / 'logs' / (unit + '.log')
        rows = path.read_text(encoding='utf-8', errors='replace').splitlines() if path.is_file() else []
        if issues:
            rows = [row for row in rows if re.search(r'warning|error|exception|failed', row, re.I)]
        return {'unit': unit, 'text': '\n'.join(rows[-max(10, min(lines, 1000)):])}
    args = ["journalctl", "-u", unit, "-n", str(max(10, min(lines, 1000))), "--no-pager", "-o", "short-iso"]
    if issues:
        args += ["-p", "warning"]
    return {"unit": unit, "text": _run(*args)}


@app.get("/api/actions")
def actions(anyan_panel: Optional[str] = Cookie(None)) -> dict[str, Any]:
    _require(anyan_panel)
    return {"actions": list(reversed(_read_jsonl(ACTION_LOG, limit=50))), 'services': list(SERVICES), 'local': LOCAL_MODE}


# --- write endpoints ---------------------------------------------------------

def _account_worker(command: dict[str, Any]) -> dict[str, Any]:
    """Separate process: no Core globals, credentials or models in panel memory."""
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, 'ANYAN_DATA_DIR': str(DATA_DIR), 'ANYAN_ENV_FILE': str(ENV_FILE), 'PYTHONIOENCODING': 'utf-8'}
    try:
        worker = subprocess.run([sys.executable, '-m', 'deploy.panel.account_worker'], cwd=root, env=env,
            input=json.dumps(command, ensure_ascii=False, allow_nan=False), capture_output=True, text=True, encoding='utf-8', timeout=45)
        result = json.loads(worker.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(503, '账户管理暂时不可用，原始错误未公开，请检查服务日志') from exc
    if worker.returncode or not isinstance(result, dict):
        raise HTTPException(503, '账户管理进程未正常完成')
    if result.get('ok') is False:
        raise HTTPException(409, str(result.get('error') or '操作未完成'))
    return result


@app.get('/api/accounts')
def account_list(anyan_panel: Optional[str] = Cookie(None)) -> dict[str, Any]:
    _require(anyan_panel)
    with _account_lock:
        return _account_worker({'action': 'list'})


@app.get('/api/accounts/{user_id}')
def account_detail(user_id: str, anyan_panel: Optional[str] = Cookie(None)) -> dict[str, Any]:
    _require(anyan_panel)
    with _account_lock:
        return _account_worker({'action': 'get', 'user_id': user_id})


def _checked_service(command: str, *units: str) -> None:
    if LOCAL_MODE:
        from deploy.panel.local_runtime import control_request
        try:
            control_request(LOCAL_CONTROL_FILE, command, units)
            return
        except (OSError, ValueError) as exc:
            raise HTTPException(503, '本机服务控制未完成，请检查本地运行日志') from exc
    result = subprocess.run(['systemctl', command, *units], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise HTTPException(503, '服务' + ('停止' if command == 'stop' else '启动') + '失败，请检查运维日志')


@app.post('/api/accounts/{user_id}/{action}')
async def account_change(user_id: str, action: str, request: Request, anyan_panel: Optional[str] = Cookie(None),
                         x_requested_by: Optional[str] = Header(None)) -> dict[str, Any]:
    _require_write(anyan_panel, x_requested_by)
    if action not in {'create', 'save', 'reset', 'baseline', 'initialize'}:
        raise HTTPException(400, '未知账户操作')
    raw = await request.body()
    if len(raw) > 2_000_000:
        raise HTTPException(413, '账户配置过大，请减少记录')
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(400, '配置不是有效 JSON') from exc
    if not isinstance(body, dict) or set(body) - {'revision', 'data', 'password'} or not isinstance(body.get('revision'), str):
        raise HTTPException(400, '请求字段无效，请刷新后操作')
    # Acquire without blocking the async event loop. Account writes run in a
    # normal thread; the same mutex excludes reads, global reset and restarts.
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(_change_account_stopped, user_id, action, body)


def _change_account_stopped(user_id: str, action: str, body: dict[str, Any]) -> dict[str, Any]:
    with _account_lock:
        # Cheap validation precedes downtime; Core checks again after it stops.
        import hashlib
        ledger = DATA_DIR / 'ledger.json'
        if not ledger.is_file() or hashlib.sha256(ledger.read_bytes()).hexdigest() != body['revision']:
            raise HTTPException(409, '账户数据已变化，请刷新后再保存')
        backup = DATA_DIR / 'backup' / ('account-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + secrets.token_hex(4))
        stopped = written = False
        original_stat = ledger.stat()
        try:
            stopped = True
            _checked_service('stop', 'anyan-gateway', 'anyan-core')
            backup.mkdir(parents=True, mode=0o700)
            for name in RESETTABLE:
                if (DATA_DIR / name).is_file():
                    shutil.copy2(DATA_DIR / name, backup / name)
            result = _account_worker({**body, 'action': action, 'user_id': user_id})
            written = True
            # Worker runs as root; retain the Core's existing owner/mode.
            if hasattr(os, 'chown'):
                os.chown(ledger, original_stat.st_uid, original_stat.st_gid)
            os.chmod(ledger, original_stat.st_mode & 0o777)
        except Exception:
            if stopped and (backup / 'ledger.json').is_file():
                shutil.copy2(backup / 'ledger.json', ledger)
                if hasattr(os, 'chown'):
                    os.chown(ledger, original_stat.st_uid, original_stat.st_gid)
            _record_action('账户管理 ' + action, '失败；未完成，保留备份 ' + str(backup))
            raise
        finally:
            if stopped:
                try:
                    _checked_service('start', 'anyan-core')
                finally:
                    _checked_service('start', 'anyan-gateway')
        if written:
            for port in (CORE_PORT, GATEWAY_PORT):
                for attempt in range(30):
                    if _http_json(f'http://127.0.0.1:{port}/healthz'):
                        break
                    time.sleep(1)
                else:
                    _record_action('账户管理 ' + action, '已保存但服务健康检查未通过；备份 ' + str(backup))
                    raise HTTPException(503, '数据已保存，但服务尚未恢复，请查看服务状态，勿重复提交')
        labels = {'create': '新增账户', 'save': '保存账户配置', 'reset': '恢复账户初始状态',
                  'baseline': '更新账户初始状态', 'initialize': '保存现有账户初始状态'}
        _record_action(labels[action] + ' ' + user_id, '成功；备份 ' + str(backup))
        return {**result, 'ok': written, 'backup': str(backup), 'result': labels[action] + '完成，请在手机重新登录'}


def _serialize_operations(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        with _account_lock:
            return function(*args, **kwargs)
    return guarded

@app.post("/api/restart/{unit}")
@_serialize_operations
def restart(unit: str, anyan_panel: Optional[str] = Cookie(None),
            x_requested_by: Optional[str] = Header(None)) -> dict[str, Any]:
    _require_write(anyan_panel, x_requested_by)
    if unit not in SERVICES:
        raise HTTPException(400, "未知服务")
    if LOCAL_MODE:
        _checked_service('restart', unit)
        _record_action('重启 ' + unit, '成功（本机托管服务）')
        return {'ok': True, 'result': '成功'}
    if unit == "nginx":
        test = subprocess.run(["nginx", "-t"], capture_output=True, text=True, check=False)
        if test.returncode:
            _record_action(f"重启 {unit}", "已取消：Nginx 配置检查未通过")
            raise HTTPException(409, "Nginx 配置检查未通过，未重启")
    result = subprocess.run(["systemctl", "restart", unit], capture_output=True, text=True, timeout=60, check=False)
    outcome = "成功" if result.returncode == 0 else f"失败：{result.stderr.strip()[:200]}"
    _record_action(f"重启 {unit}", outcome)
    return {"ok": result.returncode == 0, "result": outcome}


@app.post("/api/reset-demo")
@_serialize_operations
def reset_demo(anyan_panel: Optional[str] = Cookie(None), x_requested_by: Optional[str] = Header(None)) -> dict[str, Any]:
    """Back to the seeded demo: balances, devices and conversations. Audit files stay."""

    _require_write(anyan_panel, x_requested_by)
    backup = DATA_DIR / "backup" / datetime.now().strftime("%Y%m%d-%H%M%S")
    _checked_service('stop', 'anyan-gateway', 'anyan-core')
    moved = []
    try:
        backup.mkdir(parents=True, exist_ok=True)
        for name in RESETTABLE:
            source = DATA_DIR / name
            if source.exists():
                shutil.move(str(source), str(backup / name))
                moved.append(name)
    finally:
        _checked_service('start', 'anyan-core')
        time.sleep(2)
        _checked_service('start', 'anyan-gateway')
    outcome = f"已恢复演示数据，旧数据移到 {backup}（{len(moved)} 个文件）"
    _record_action("恢复演示数据", outcome)
    return {"ok": True, "result": outcome, "backup": str(backup), "moved": moved}
