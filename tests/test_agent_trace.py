"""Focused contract tests for gateway-owned correlation and trace aggregation."""

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("langgraph")
pytest.importorskip("fastapi")

import agent_api  # noqa: E402
import conversation  # noqa: E402
import security  # noqa: E402


def test_gateway_owns_request_id_and_redacts_response_audit(monkeypatch):
    monkeypatch.setattr(agent_api, "principal_for", lambda **_: "user_zhang")
    def start(**kw):
        result = {"code": 200, "status": "COMPLETED", "request_id": kw["request_id"], "reply": "余额已查询", "intent": "BALANCE", "risk_level": "GREEN", "result": {}}
        conversation.SERVICE._audit_response("user_zhang", result, kw.get("client_request_id"))
        return result
    monkeypatch.setattr(agent_api.SERVICE, "start_turn", start)
    client = TestClient(agent_api.app)
    response = client.post(
        "/api/v1/agent/messages",
        json={"text": "查一下余额", "conversation_id": "trace-owned"},
        headers={"X-User-Id": "user_zhang", "X-Request-Id": "attacker-id"},
    )
    assert response.status_code == 200
    envelope = response.json()
    request_id = envelope["request_id"]
    assert request_id.startswith("gw_")
    assert request_id != "attacker-id"
    events = [e for e in security.get_audit_logs(user_id="user_zhang", limit=1000) if e.get("request_id") == request_id]
    response_events = [e for e in events if e["action"] == "AGENT_RESPONSE"]
    assert response_events
    metadata = response_events[-1]["metadata"]
    assert metadata["client_request_id"] == "attacker-id"
    assert "reply" not in metadata and "user_input" not in metadata
    assert all(secret not in str(metadata) for secret in ("otp", "mfa_proof", "nonce", "auth_token"))


def test_gateway_no_header_uses_one_authoritative_id(monkeypatch):
    monkeypatch.setattr(agent_api, "principal_for", lambda **_: "user_zhang")
    seen = []
    def start(**kw):
        seen.append(kw["request_id"])
        return {"code": 200, "status": "COMPLETED", "request_id": kw["request_id"], "reply": "完成", "intent": "BALANCE", "risk_level": "GREEN", "result": {}}
    monkeypatch.setattr(agent_api.SERVICE, "start_turn", start)
    response = TestClient(agent_api.app).post("/api/v1/agent/messages", json={"text": "查余额"}, headers={"X-User-Id": "user_zhang"})
    assert response.status_code == 200 and response.json()["request_id"] == seen[0] and seen[0].startswith("gw_")


def test_aggregate_trace_handles_wrapped_core_and_duplicate_events(monkeypatch):
    local = [
        {"event_id": "g1", "timestamp": "2026-01-01T00:00:00Z", "action": "INTENT_PARSE", "user_id": "u", "request_id": "gw_x", "integrity_hash": "gh1"},
        {"event_id": "g2", "timestamp": "2026-01-01T00:00:01Z", "action": "AGENT_RESPONSE", "user_id": "u", "request_id": "gw_x", "integrity_hash": "gh2"},
    ]
    core_events = [
        {"event_id": "c1", "timestamp": "2026-01-01T00:00:02Z", "action": "EXECUTION", "user_id": "u", "request_id": "gw_x", "integrity_hash": "ch1"},
        {"event_id": "c1", "timestamp": "2026-01-01T00:00:03Z", "action": "EXECUTION", "user_id": "u", "request_id": "gw_x", "integrity_hash": "ch2"},
    ]
    monkeypatch.setattr(conversation, "get_audit_logs", lambda **_: local)
    monkeypatch.setattr(conversation, "verify_audit_chain", lambda: {"valid": True})

    class Response:
        def json(self):
            return {"code": 200, "data": {"events": core_events}}

    class Client:
        def __init__(self, **kwargs):
            pass
        def _request(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(conversation, "BankClient", Client)
    trace = conversation.SERVICE.aggregate_trace(principal="u", request_id="gw_x")
    assert trace["complete"] is True
    assert trace["missing_sources"] == []
    assert [e["source"] for e in trace["events"]] == ["gateway", "gateway", "core", "core"]
    assert [e["event_id"] for e in trace["events"]].count("c1") == 2
    assert trace["chain_verification"]["gateway"]["valid"] is True
    assert trace["chain_verification"]["core"]["available"] is False


def test_aggregate_trace_is_partial_when_core_unavailable(monkeypatch):
    event = {"event_id": "g1", "timestamp": "2026-01-01T00:00:00Z", "action": "AGENT_RESPONSE", "user_id": "u", "request_id": "gw_y", "integrity_hash": "gh"}
    monkeypatch.setattr(conversation, "get_audit_logs", lambda **_: [event])
    class Client:
        def __init__(self, **kwargs): pass
        def _request(self, *args, **kwargs): raise RuntimeError("offline")
    monkeypatch.setattr(conversation, "BankClient", Client)
    trace = conversation.SERVICE.aggregate_trace(principal="u", request_id="gw_y")
    assert trace["complete"] is False
    assert trace["missing_sources"] == ["core"]
