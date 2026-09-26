from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import Mock

from fastapi.testclient import TestClient

import agent_api
import feige_relay


SECRET = "test-feige-relay-secret-with-more-than-32-bytes"


def _post(client: TestClient, event: dict, event_id: str, *, valid: bool = True):
    raw = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode()
    stamp = str(int(time.time()))
    signature = hmac.new(SECRET.encode(), f"{stamp}.{event_id}.".encode() + raw, hashlib.sha256).hexdigest()
    return client.post(
        "/api/v1/im/feige/relay", content=raw,
        headers={"Content-Type": "application/json", "X-Feige-Timestamp": stamp,
                 "X-Feige-Event-Id": event_id,
                 "X-Feige-Signature": signature if valid else "0" * 64},
    )


def test_relay_requires_signature_binding_and_rejects_replay(monkeypatch):
    feige_relay.reset_state()
    monkeypatch.setenv("BANK_FEIGE_RELAY_SECRET", SECRET)
    monkeypatch.setattr(agent_api, "principal_for", lambda **_: "user_zhang")
    read = Mock()
    read.return_value.json.return_value = {"code": 200, "available_balance": 500.0, "total_balance": 600.0, "locked_amount": 100.0}
    monkeypatch.setattr(feige_relay.BankClient, "_request", read)
    with TestClient(agent_api.app) as client:
        no_sig = _post(client, {"kind": "message", "subject": "im-user-1", "text": "查余额"}, "evt-1", valid=False)
        assert no_sig.status_code == 401
        unbound = _post(client, {"kind": "message", "subject": "im-user-1", "text": "查余额"}, "evt-2")
        assert unbound.status_code == 403
        issued = client.post("/api/v1/im/feige/binding-code", headers={"X-Session-Token": "valid-session"})
        assert issued.status_code == 200, issued.text
        bound = _post(client, {"kind": "bind", "subject": "im-user-1", "binding_code": issued.json()["binding_code"]}, "evt-3")
        assert bound.status_code == 200
        result = _post(client, {"kind": "message", "subject": "im-user-1", "text": "查余额"}, "evt-4")
        assert result.status_code == 200 and "¥500.00" in result.json()["reply"]
        assert read.call_args.args == ("GET", "accounts/user_zhang/balance")
        replay = _post(client, {"kind": "message", "subject": "im-user-1", "text": "查余额"}, "evt-4")
        assert replay.status_code == 409
        assert read.call_count == 1
        mixed = _post(client, {"kind": "message", "subject": "im-user-1", "text": "查余额并给李四转账300元"}, "evt-7")
        assert mixed.status_code == 200
        assert all(call.args[0] == "GET" for call in read.call_args_list)
    feige_relay.reset_state()


def test_relay_never_approves_a_mutation(monkeypatch):
    feige_relay.reset_state()
    monkeypatch.setenv("BANK_FEIGE_RELAY_SECRET", SECRET)
    monkeypatch.setattr(agent_api, "principal_for", lambda **_: "user_zhang")
    read = Mock()
    monkeypatch.setattr(feige_relay.BankClient, "_request", read)
    with TestClient(agent_api.app) as client:
        code = client.post("/api/v1/im/feige/binding-code", headers={"X-Session-Token": "valid-session"}).json()["binding_code"]
        assert _post(client, {"kind": "bind", "subject": "im-user-1", "binding_code": code}, "evt-5").status_code == 200
        result = _post(client, {"kind": "message", "subject": "im-user-1", "text": "转账 300 元"}, "evt-6")
        assert result.status_code == 200 and result.json()["status"] == "APP_HANDOFF"
        assert "approval_id" not in result.json()
        read.assert_not_called()
    feige_relay.reset_state()
