"""The server back office (deploy/panel): read-only views, token login, guarded actions."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "t" * 64
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone(timedelta(hours=8)))


@pytest.fixture
def panel(tmp_path, monkeypatch):
    (tmp_path / "audit").mkdir()
    (tmp_path / "panel.token").write_text(TOKEN + "\n", encoding="utf-8")
    (tmp_path / "anyan.env").write_text(
        "DEEPSEEK_API_KEY=sk-secret-value\nDASHSCOPE_API_KEY=\nBANK_AUTH_SECRET=abc\nBANK_MFA_DYNAMIC_OTP=1\n", encoding="utf-8")
    monkeypatch.setenv("ANYAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANYAN_ENV_FILE", str(tmp_path / "anyan.env"))
    monkeypatch.setenv("PANEL_TOKEN_FILE", str(tmp_path / "panel.token"))
    monkeypatch.setenv("NGINX_ACCESS_LOG", str(tmp_path / "access.log"))
    spec = importlib.util.spec_from_file_location("ops_panel_under_test", ROOT / "deploy/panel/server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda args, **kw: calls.append(list(args)) or type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})())
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    return module, TestClient(module.app), tmp_path, calls


def _login(client):
    assert client.post("/api/login", json={"token": TOKEN}).status_code == 200


def test_secrets_are_reported_only_as_configured():
    spec = importlib.util.spec_from_file_location("ops_panel_helpers", ROOT / "deploy/panel/server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    view = module.env_overview("DEEPSEEK_API_KEY=sk-123\nDASHSCOPE_API_KEY=\nBANK_MFA_DYNAMIC_OTP=1\n# BANK_AUTH_SECRET=x\n")
    assert view["secrets"]["DEEPSEEK_API_KEY"] is True and view["secrets"]["DASHSCOPE_API_KEY"] is False
    assert view["secrets"]["BANK_AUTH_SECRET"] is False
    assert "sk-123" not in json.dumps(view)
    assert view["settings"]["BANK_MFA_DYNAMIC_OTP"] == "1"


def test_access_log_summary_counts_the_last_day(panel):
    module, *_ = panel
    lines = [
        '1.2.3.4 - - [18/Oct/2026:11:30:00 +0800] "POST /gateway/api/v1/agent/messages/stream HTTP/1.1" 200 5120 "-" "okhttp"',
        '1.2.3.4 - - [18/Oct/2026:11:31:00 +0800] "GET /gateway/api/v1/agent/conversations/abc/suggestions HTTP/1.1" 429 162 "-" "okhttp"',
        '5.6.7.8 - - [18/Oct/2026:10:00:00 +0800] "POST /core/api/v1/auth/login HTTP/1.1" 200 300 "-" "okhttp"',
        '5.6.7.8 - - [16/Oct/2026:10:00:00 +0800] "GET /core/healthz HTTP/1.1" 200 60 "-" "curl"',
        "garbage line",
    ]
    summary = module.access_summary(lines, NOW)
    assert summary["requests"] == 3 and summary["visitors"] == 2
    assert summary["rate_limited"] == 1 and summary["bytes_out"] == 5120 + 162 + 300
    assert summary["areas"] == {"对话": 1, "猜你想问": 1, "登录": 1}
    assert len(summary["hourly"]) == 24
    assert {row["hour"]: row["requests"] for row in summary["hourly"]}["11:00"] == 2


def test_audit_summary_counts_logins_turns_and_model_calls(panel):
    module, *_ = panel
    at = (NOW - timedelta(hours=1)).isoformat()
    old = (NOW - timedelta(days=3)).isoformat()
    events = [
        {"timestamp": at, "action": "LOGIN", "outcome": "SUCCESS", "user_id": "user_zhang"},
        {"timestamp": at, "action": "LOGIN", "outcome": "FAILURE", "user_id": "user_li"},
        {"timestamp": old, "action": "LOGIN", "outcome": "SUCCESS", "user_id": "user_li"},
        {"timestamp": at, "action": "AGENT_RESPONSE", "outcome": "SUCCEEDED"},
        {"timestamp": at, "action": "INTENT_PARSE", "metadata": {"parse_meta": {"source": "model"}}},
        {"timestamp": at, "action": "INTENT_PARSE", "metadata": {"parse_meta": {"source": "rules"}}},
        {"timestamp": at, "action": "AGENT_SUGGEST", "metadata": {"source": "model"}},
        {"timestamp": at, "action": "PROMPT_INJECTION_BLOCKED", "decision": "BLOCK"},
        {"timestamp": at, "action": "TRANSFER_EXECUTE", "outcome": "SUCCESS"},
    ]
    summary = module.audit_summary(events, NOW)
    assert (summary["logins"], summary["login_failures"], summary["active_users"]) == (1, 1, 1)
    assert (summary["turns"], summary["model_calls"], summary["money_moves"]) == (1, 2, 1)
    assert summary["blocked"] == 1 and summary["injections"] == 1


def test_everything_needs_the_token(panel):
    _, client, *_ = panel
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/business").status_code == 401
    assert client.post("/api/login", json={"token": "wrong"}).status_code == 401
    assert client.get("/").status_code == 200  # the login page itself


def test_actions_need_the_panel_header(panel):
    _, client, _, calls = panel
    _login(client)
    assert client.post("/api/restart/anyan-core").status_code == 403
    assert client.post("/api/restart/sshd", headers={"X-Requested-By": "anyan-panel"}).status_code == 400
    assert not calls
    ok = client.post("/api/restart/anyan-core", headers={"X-Requested-By": "anyan-panel"})
    assert ok.status_code == 200 and calls == [["systemctl", "restart", "anyan-core"]]
    assert client.get("/api/actions").json()["actions"][0]["action"] == "重启 anyan-core"


def test_business_view_reads_the_ledger_snapshot(panel):
    _, client, data, _ = panel
    _login(client)
    assert client.get("/api/business").json()["persisted"] is False
    (data / "ledger.json").write_text(json.dumps({"version": 1, "state": {
        "ACCOUNTS": {"user_zhang": {"name": "张三", "balance": 14700.0, "locked_amount": 0.0, "status": "ACTIVE", "password_hash": "x"}},
        "TRANSACTIONS": [{}, {}], "WEALTH_HOLDINGS": {"user_zhang": [{}]},
    }}), encoding="utf-8")
    view = client.get("/api/business").json()
    assert view["accounts"] == [{"user_id": "user_zhang", "name": "张三", "balance": 14700.0, "locked": 0.0, "status": "ACTIVE"}]
    assert view["counts"]["交易流水"] == 2 and view["counts"]["理财持仓"] == 1
    assert "password_hash" not in json.dumps(view)


def test_reset_moves_state_to_a_backup_and_keeps_the_audit(panel):
    _, client, data, calls = panel
    _login(client)
    for name in ("ledger.json", "devices.json", "conversations.sqlite3"):
        (data / name).write_text("old", encoding="utf-8")
    (data / "audit/core-audit.jsonl").write_text("{}\n", encoding="utf-8")
    result = client.post("/api/reset-demo", headers={"X-Requested-By": "anyan-panel"}).json()
    backup = Path(result["backup"])
    assert sorted(result["moved"]) == ["conversations.sqlite3", "devices.json", "ledger.json"]
    assert all((backup / name).read_text(encoding="utf-8") == "old" for name in result["moved"])
    assert not (data / "ledger.json").exists() and (data / "audit/core-audit.jsonl").exists()
    assert calls[0] == ["systemctl", "stop", "anyan-gateway", "anyan-core"]
    assert calls[-1] == ["systemctl", "start", "anyan-gateway"]


def test_overview_never_contains_secret_values(panel):
    module, client, *_ = panel
    module._run = lambda *args, **kw: ""
    module._http_json = lambda url: None
    _login(client)
    body = client.get("/api/overview").text
    assert "sk-secret-value" not in body and "abc" not in json.loads(body)["config"]["settings"].values()


def test_https_panel_prefix_keeps_login_cookie_scoped_secure_and_revokes_it(panel):
    from fastapi import FastAPI
    module, _, _, calls = panel
    proxy = FastAPI()
    proxy.mount('/panel', module.app)
    with TestClient(proxy, base_url='https://panel.example', headers={'X-Forwarded-Prefix': '/panel'}) as client:
        assert client.get('/panel/api/actions').status_code == 401
        assert client.post('/panel/api/restart/anyan-core', headers={'X-Requested-By': 'anyan-panel'}).status_code == 401
        assert client.get('/panel/').status_code == 200
        assert client.get('/panel/accounts.js').status_code == 200
        logged_in = client.post('/panel/api/login', json={'token': TOKEN})
        assert logged_in.status_code == 200
        cookie = logged_in.headers['set-cookie']
        assert 'Path=/panel/' in cookie and 'Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=strict' in cookie
        assert client.get('/panel/api/actions').status_code == 200
        assert client.post('/panel/api/restart/anyan-core').status_code == 403
        assert not calls  # No business/service changes in an authentication test.
        logged_out = client.post('/panel/api/logout')
        assert 'Path=/panel/' in logged_out.headers['set-cookie'] and 'Max-Age=0' in logged_out.headers['set-cookie']
        assert client.get('/panel/api/actions').status_code == 401


def test_plain_http_tunnel_still_uses_root_cookie(panel):
    _, client, *_ = panel
    response = client.post('/api/login', json={'token': TOKEN})
    assert response.status_code == 200
    assert 'Path=/' in response.headers['set-cookie'] and '; Secure' not in response.headers['set-cookie']
    assert client.get('/api/actions').status_code == 200
    client.post('/api/logout')
    assert client.get('/api/actions').status_code == 401
