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
import json
import os
import re
import secrets
import shutil
import subprocess
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

SERVICES = ("anyan-core", "anyan-gateway", "nginx")
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
        with urllib.request.urlopen(url, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


def _service(unit: str) -> dict[str, Any]:
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
    disk = shutil.disk_usage("/")
    data_bytes = sum(f.stat().st_size for f in DATA_DIR.rglob("*") if f.is_file()) if DATA_DIR.is_dir() else 0
    return {
        "hostname": os.uname().nodename if hasattr(os, "uname") else "",
        "cpus": os.cpu_count(), "load": load, "uptime_hours": round(uptime / 3600, 1),
        "memory": {"total": meminfo.get("MemTotal"), "available": meminfo.get("MemAvailable"),
                   "swap_total": meminfo.get("SwapTotal"), "swap_free": meminfo.get("SwapFree")},
        "disk": {"total": disk.total, "used": disk.used}, "data_bytes": data_bytes, "network": network,
    }


def _record_action(action: str, result: str) -> None:
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
        "services": [_service(unit) for unit in SERVICES],
        "health": {"core": _http_json("http://127.0.0.1:8000/healthz"), "gateway": _http_json("http://127.0.0.1:8100/healthz"),
                   "security": _http_json("http://127.0.0.1:8000/api/v1/security/health")},
        "system": _system(),
        "traffic": access_summary(lines, now),
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
    args = ["journalctl", "-u", unit, "-n", str(max(10, min(lines, 1000))), "--no-pager", "-o", "short-iso"]
    if issues:
        args += ["-p", "warning"]
    return {"unit": unit, "text": _run(*args)}


@app.get("/api/actions")
def actions(anyan_panel: Optional[str] = Cookie(None)) -> dict[str, Any]:
    _require(anyan_panel)
    return {"actions": list(reversed(_read_jsonl(ACTION_LOG, limit=50)))}


# --- write endpoints ---------------------------------------------------------

@app.post("/api/restart/{unit}")
def restart(unit: str, anyan_panel: Optional[str] = Cookie(None),
            x_requested_by: Optional[str] = Header(None)) -> dict[str, Any]:
    _require_write(anyan_panel, x_requested_by)
    if unit not in SERVICES:
        raise HTTPException(400, "未知服务")
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
def reset_demo(anyan_panel: Optional[str] = Cookie(None), x_requested_by: Optional[str] = Header(None)) -> dict[str, Any]:
    """Back to the seeded demo: balances, devices and conversations. Audit files stay."""

    _require_write(anyan_panel, x_requested_by)
    backup = DATA_DIR / "backup" / datetime.now().strftime("%Y%m%d-%H%M%S")
    subprocess.run(["systemctl", "stop", "anyan-gateway", "anyan-core"], check=False, timeout=60)
    moved = []
    try:
        backup.mkdir(parents=True, exist_ok=True)
        for name in RESETTABLE:
            source = DATA_DIR / name
            if source.exists():
                shutil.move(str(source), str(backup / name))
                moved.append(name)
    finally:
        subprocess.run(["systemctl", "start", "anyan-core"], check=False, timeout=60)
        time.sleep(2)
        subprocess.run(["systemctl", "start", "anyan-gateway"], check=False, timeout=60)
    outcome = f"已恢复演示数据，旧数据移到 {backup}（{len(moved)} 个文件）"
    _record_action("恢复演示数据", outcome)
    return {"ok": True, "result": outcome, "backup": str(backup), "moved": moved}
