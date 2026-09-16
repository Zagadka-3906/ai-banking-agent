import hashlib
import hmac
import json
import time

import pytest

from fastapi.testclient import TestClient

import agent_api
import im_webhook


@pytest.fixture(autouse=True)
def im_config(monkeypatch):
    monkeypatch.setenv("BANK_IM_PROVIDER_SECRETS", "demo=demo-im-secret,other=other-secret")
    im_webhook.reset_state()


def _signed(payload, event_id="e1", provider="demo"):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    stamp = str(int(time.time()))
    secret = {"demo": b"demo-im-secret", "other": b"other-secret"}[provider]
    sig = hmac.new(secret, f"{stamp}.{event_id}.".encode() + raw, hashlib.sha256).hexdigest()
    return raw, {"X-IM-Provider": provider, "X-IM-Timestamp": stamp, "X-IM-Event-Id": event_id, "X-IM-Signature": sig}


def test_signed_bind_and_message_ignores_body_identity(monkeypatch):
    im_webhook.reset_state()
    monkeypatch.setattr(agent_api, "principal_for", lambda **_: "user_zhang")
    monkeypatch.setattr(im_webhook.SERVICE, "start_turn", lambda **kw: {"code": 200, "status": "COMPLETED", "request_id": kw["request_id"], "reply": "余额", "result": {}})
    client = TestClient(agent_api.app)
    code = client.post("/api/v1/im/bindings", headers={"X-Session-Token": "session-secret"}).json()["binding_code"]
    body, headers = _signed({"event_type": "bind", "external_subject": "alice", "binding_code": code, "user_id": "attacker"})
    assert client.post("/api/v1/im/webhook", content=body, headers=headers).status_code == 200
    body, headers = _signed({"event_type": "message", "external_subject": "alice", "text": "查余额", "user_id": "attacker"}, "e2")
    result = client.post("/api/v1/im/webhook", content=body, headers=headers)
    assert result.status_code == 200 and result.json()["status"] == "COMPLETED"


def test_signature_timestamp_replay_and_code_reuse_rejected(monkeypatch):
    im_webhook.reset_state()
    monkeypatch.setattr(agent_api, "principal_for", lambda **_: "u")
    client = TestClient(agent_api.app)
    code = client.post("/api/v1/im/bindings", headers={"X-Session-Token": "s"}).json()["binding_code"]
    body, headers = _signed({"event_type": "bind", "external_subject": "x", "binding_code": code})
    assert client.post("/api/v1/im/webhook", content=body, headers={**headers, "X-IM-Signature": "bad"}).status_code == 401
    assert client.post("/api/v1/im/webhook", content=body, headers=headers).status_code == 200
    body2, headers2 = _signed({"event_type": "bind", "external_subject": "x", "binding_code": code}, "e2")
    assert client.post("/api/v1/im/webhook", content=body2, headers=headers2).status_code == 403
    stale_body, stale_headers = _signed({"event_type": "message", "external_subject": "x", "text": "x"}, "e3")
    stale_headers["X-IM-Timestamp"] = "1"
    assert client.post("/api/v1/im/webhook", content=stale_body, headers=stale_headers).status_code == 401


def test_provider_config_required(monkeypatch):
    monkeypatch.delenv("BANK_IM_PROVIDER_SECRETS")
    client = TestClient(agent_api.app)
    body, headers = _signed({"event_type": "message", "external_subject": "x", "text": "查余额"})
    assert client.post("/api/v1/im/webhook", content=body, headers=headers).status_code == 403


def test_unbound_cross_provider_and_unbind_rejected(monkeypatch):
    monkeypatch.setattr(agent_api, "principal_for", lambda **_: "u")
    client = TestClient(agent_api.app)
    code = client.post("/api/v1/im/bindings", headers={"X-Session-Token": "s"}).json()["binding_code"]
    body, headers = _signed({"event_type": "bind", "external_subject": "x", "binding_code": code})
    assert client.post("/api/v1/im/webhook", content=body, headers=headers).status_code == 200
    msg, mh = _signed({"event_type": "message", "external_subject": "x", "text": "查余额"}, "m1", "other")
    assert client.post("/api/v1/im/webhook", content=msg, headers=mh).status_code == 403
    assert client.delete("/api/v1/im/bindings/demo/x", headers={"X-Session-Token": "s"}).status_code == 200
    msg, mh = _signed({"event_type": "message", "external_subject": "x", "text": "查余额"}, "m2")
    assert client.post("/api/v1/im/webhook", content=msg, headers=mh).status_code == 403


def test_binding_code_is_hashed_and_single_use(monkeypatch):
    monkeypatch.setattr(agent_api, "principal_for", lambda **_: "u")
    client = TestClient(agent_api.app)
    code = client.post("/api/v1/im/bindings", headers={"X-Session-Token": "s"}).json()["binding_code"]
    assert all(code not in str(v) for v in im_webhook._CODES.values())
    body, headers = _signed({"event_type": "bind", "external_subject": "x", "binding_code": code})
    assert client.post("/api/v1/im/webhook", content=body, headers=headers).status_code == 200
    assert not im_webhook._CODES


def test_approval_projection_and_confirmation_text_never_resolves(monkeypatch):
    monkeypatch.setattr(agent_api, "principal_for", lambda **_: "u")
    called = []
    monkeypatch.setattr(im_webhook.SERVICE, "resolve_approval", lambda **kw: called.append(kw))
    monkeypatch.setattr(im_webhook.SERVICE, "start_turn", lambda **kw: {"code": 200, "status": "APPROVAL_REQUIRED", "reply": "confirm", "request_id": kw["request_id"], "approval": {"approval_id": "a", "summary": "ok", "auth_token": "secret", "mfa_proof": "proof", "otp": "123456"}})
    client = TestClient(agent_api.app)
    code = client.post("/api/v1/im/bindings", headers={"X-Session-Token": "s"}).json()["binding_code"]
    body, headers = _signed({"event_type": "bind", "external_subject": "x", "binding_code": code})
    client.post("/api/v1/im/webhook", content=body, headers=headers)
    for text_value, eid in (("确认", "q1"), ("验证码 123456", "q2")):
        body, headers = _signed({"event_type": "message", "external_subject": "x", "text": text_value}, eid)
        result = client.post("/api/v1/im/webhook", content=body, headers=headers)
        assert result.status_code == 200
        assert "auth_token" not in str(result.json()) and "mfa_proof" not in str(result.json()) and "otp" not in str(result.json())
    assert not called


def test_each_message_revalidates_session_and_revocation_blocks_next(monkeypatch):
    calls = {"valid": True, "turns": 0}
    from fastapi import HTTPException
    def resolve(**kwargs):
        if not calls["valid"]:
            raise HTTPException(401, detail={"code": "INVALID_SESSION", "message": "expired"})
        return "u"
    monkeypatch.setattr(agent_api, "principal_for", resolve)
    monkeypatch.setattr(im_webhook.SERVICE, "start_turn", lambda **kw: calls.__setitem__("turns", calls["turns"] + 1) or {"code": 200, "status": "COMPLETED", "request_id": kw["request_id"], "reply": "ok"})
    client = TestClient(agent_api.app)
    code = client.post("/api/v1/im/bindings", headers={"X-Session-Token": "session-token"}).json()["binding_code"]
    body, headers = _signed({"event_type": "bind", "external_subject": "revoked", "binding_code": code})
    assert client.post("/api/v1/im/webhook", content=body, headers=headers).status_code == 200
    body, headers = _signed({"event_type": "message", "external_subject": "revoked", "text": "查余额"}, "rev-1")
    assert client.post("/api/v1/im/webhook", content=body, headers=headers).status_code == 200
    calls["valid"] = False
    body, headers = _signed({"event_type": "message", "external_subject": "revoked", "text": "查余额"}, "rev-2")
    assert client.post("/api/v1/im/webhook", content=body, headers=headers).status_code == 401
    assert calls["turns"] == 1


def test_im_responses_recursively_exclude_credentials_and_provider_secret(monkeypatch):
    monkeypatch.setattr(agent_api, "principal_for", lambda **_: "u")
    token, secret, otp = "session-token", "demo-im-secret", "123456"
    monkeypatch.setattr(im_webhook.SERVICE, "start_turn", lambda **kw: {"code": 200, "status": "APPROVAL_REQUIRED", "request_id": kw["request_id"], "reply": "ok", "approval": {"approval_id": "a", "summary": "ok", "auth_token": token, "nonce": secret, "mfa_proof": otp, "otp": otp}})
    client = TestClient(agent_api.app)
    code = client.post("/api/v1/im/bindings", headers={"X-Session-Token": token}).json()["binding_code"]
    body, headers = _signed({"event_type": "bind", "external_subject": "scan", "binding_code": code})
    assert client.post("/api/v1/im/webhook", content=body, headers=headers).status_code == 200
    body, headers = _signed({"event_type": "message", "external_subject": "scan", "text": "转账"}, "scan-1")
    payload = client.post("/api/v1/im/webhook", content=body, headers=headers).json()
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)
    all_text = " ".join(strings(payload))
    assert all(value not in all_text for value in (token, secret, code, "auth_token", "nonce", "mfa_proof", otp))
