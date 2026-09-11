"""Channel gateway tests: the contract a native client (the Android app) sees.

These run entirely in-process: ``agent._http`` and the gateway's core client are
both redirected at a ``TestClient`` wrapping the real core app, so the tests
exercise the genuine authorization state machine (precheck -> confirm -> MFA ->
settle) without needing a network listener.

The properties under test are the ones a *channel* introduces risk to:

* the client is never handed the signed nonce or the MFA proof, only an opaque
  ``approval_id`` bound to the authenticated principal;
* a conversation cannot silently abandon a pending approval, and a replayed
  message cannot produce a second precheck;
* one user intent still produces ONE audit chain even though the channel splits
  it across several HTTP requests.
"""

import json
import os

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import agent  # noqa: E402
import conversation  # noqa: E402
import main  # noqa: E402
import security  # noqa: E402
from bank_tools import BankAPIError  # noqa: E402

os.environ.setdefault("BANK_DEMO_MODE", "1")

# Every session credential the graph handed to the core during a test.  The
# fixture clears it; ``fake_http`` fills it.
FORWARDED_SESSIONS: list = []


class _CoreClient:
    """``BankClient`` stand-in that talks to an in-process core ``TestClient``.

    It mirrors the production signature (including the ``request_id`` keyword)
    so a regression that drops the correlation id fails here rather than only in
    a live deployment.
    """

    def __init__(self, core: TestClient, principal: str, session_token: str | None = None):
        self.core = core
        self.principal = principal
        self.session_token = session_token

    def _headers(self, *, auth_token=None, request_id=None):
        headers = {"X-User-Id": self.principal}
        if self.session_token:
            headers["X-Session-Token"] = self.session_token
        if auth_token:
            headers["X-Auth-Token"] = auth_token
        if request_id:
            headers["X-Request-Id"] = request_id
        return headers

    @staticmethod
    def _raise(response):
        payload = None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        detail = payload.get("detail") if isinstance(payload, dict) else None
        message = detail.get("message") if isinstance(detail, dict) else str(detail or response.text)
        raise BankAPIError(message, status_code=response.status_code, payload=payload)

    def confirm(self, auth_token, *, request_id=None):
        response = self.core.post(
            "/api/v1/risk/confirm",
            json={},
            headers=self._headers(auth_token=auth_token, request_id=request_id),
        )
        if response.status_code >= 400:
            self._raise(response)
        return response.json()

    def request_face_challenge(self, auth_token, *, request_id=None):
        response = self.core.post(
            "/api/v1/risk/mfa/face/challenge",
            json={"auth_token": auth_token},
            headers=self._headers(auth_token=auth_token, request_id=request_id),
        )
        if response.status_code >= 400:
            self._raise(response)
        return response.json()

    def verify_mfa(self, auth_token, *, otp, face_attestation=None, face_verified=None, request_id=None):
        body = {"otp": otp}
        if face_attestation:
            body["face_attestation"] = face_attestation
        response = self.core.post(
            "/api/v1/risk/mfa/verify",
            json=body,
            headers=self._headers(auth_token=auth_token, request_id=request_id),
        )
        if response.status_code >= 400:
            self._raise(response)
        return response.json()


@pytest.fixture()
def gateway(monkeypatch):
    """A gateway whose graph and core client are both wired to ``main.app``."""

    import agent_api

    main.reset_state()
    # Keep the test off the filesystem: an explicit in-memory checkpointer, and
    # a rebuilt graph so no checkpoint leaks between tests.
    monkeypatch.setenv("BANK_CONVERSATION_DB", "memory")
    monkeypatch.setattr(conversation, "_GRAPH", None)
    monkeypatch.setattr(conversation, "_GRAPH_BACKEND", "uninitialised")
    conversation.SERVICE.reset()
    FORWARDED_SESSIONS.clear()

    core = TestClient(main.app)

    def fake_http(method, path, *, state, json_body=None, headers=None):
        # Mirror the real ``agent._http`` contract: both the request id from
        # graph state and the caller's session credential from the channel
        # context are propagated.  Replacing the transport must not silently
        # drop either of them, or the thing under test stops being tested.
        merged = {"X-Request-Id": state.get("request_id") or "test-request"}
        session_token = agent._CHANNEL_SESSION.get()
        if session_token:
            merged["X-Session-Token"] = session_token
        merged.update(headers or {})
        FORWARDED_SESSIONS.append(session_token)
        return core.request(method, "/api/v1/" + path.lstrip("/"), json=json_body, headers=merged)

    monkeypatch.setattr(agent, "_http", fake_http)
    monkeypatch.setattr(
        conversation.SERVICE,
        "_client_factory",
        lambda principal, session_token=None: _CoreClient(core, principal, session_token),
    )

    def fake_session_lookup(session_token: str, request_id):
        """Stand-in for the gateway's call to the core, same contract."""

        response = core.get("/api/v1/auth/session", headers={"X-Session-Token": session_token})
        if response.status_code >= 400:
            raise BankAPIError("session rejected", status_code=response.status_code, payload=response.json())
        return response.json()

    monkeypatch.setattr(agent_api, "_core_session_subject", fake_session_lookup)

    yield TestClient(agent_api.app), core

    conversation.SERVICE.reset()
    main.reset_state()


def _headers(principal: str = "user_zhang"):
    return {"X-User-Id": principal}


def _send(client, text, *, conversation_id, message_id=None, principal="user_zhang"):
    body = {"text": text, "conversation_id": conversation_id}
    if message_id:
        body["message_id"] = message_id
    response = client.post("/api/v1/agent/messages", json=body, headers=_headers(principal))
    assert response.status_code == 200, response.text
    return response.json()


# -- green ----------------------------------------------------------------


def test_green_turn_completes_without_any_credential(gateway):
    client, _ = gateway
    envelope = _send(client, "查一下余额", conversation_id="c-green")
    assert envelope["status"] == "COMPLETED"
    assert envelope["approval"] is None
    assert "¥" in envelope["reply"] or "余额" in envelope["reply"]


def test_gateway_health_reports_the_checkpointer_backend(gateway):
    client, _ = gateway
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["conversation"]["checkpointer"] == "memory"
    assert body["conversation"]["pending_approvals"] == 0


# -- yellow ---------------------------------------------------------------


def test_yellow_turn_returns_an_opaque_handle_and_never_the_nonce(gateway):
    client, _ = gateway
    envelope = _send(client, "给李四转 300 元", conversation_id="c-yellow")
    assert envelope["status"] == "APPROVAL_REQUIRED"
    assert envelope["risk_level"] == "YELLOW"
    approval = envelope["approval"]
    assert approval["approval_id"].startswith("APR")
    assert approval["mfa_required"] is False
    # The whole point of the handle: the signed nonce stays server-side.
    assert "auth_token" not in str(envelope)


def test_yellow_approval_settles_the_transfer(gateway):
    client, core = gateway
    envelope = _send(client, "给李四转 300 元", conversation_id="c-yellow-settle")
    approval_id = envelope["approval"]["approval_id"]

    resolved = client.post(f"/api/v1/agent/approvals/{approval_id}", json={"decision": "approve"}, headers=_headers())
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["status"] == "COMPLETED"

    balance = core.get("/api/v1/accounts/user_zhang/balance", headers=_headers()).json()
    assert float(balance["available_balance"]) == 14700.0


def test_rejecting_an_approval_leaves_the_ledger_untouched(gateway):
    client, core = gateway
    envelope = _send(client, "给李四转 300 元", conversation_id="c-yellow-reject")
    approval_id = envelope["approval"]["approval_id"]

    resolved = client.post(f"/api/v1/agent/approvals/{approval_id}", json={"decision": "reject"}, headers=_headers())
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "COMPLETED"

    balance = core.get("/api/v1/accounts/user_zhang/balance", headers=_headers()).json()
    assert float(balance["available_balance"]) == 15000.0


# -- red ------------------------------------------------------------------


def test_red_approval_requires_both_factors(gateway):
    client, _ = gateway
    envelope = _send(client, "给李四转 2000 元", conversation_id="c-red-factors")
    assert envelope["risk_level"] == "RED"
    assert envelope["approval"]["mfa_required"] is True

    response = client.post(
        f"/api/v1/agent/approvals/{envelope['approval']['approval_id']}",
        json={"decision": "approve"},
        headers=_headers(),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "MFA_FACTORS_REQUIRED"


def test_red_approval_settles_with_otp_and_device_attestation(gateway):
    client, core = gateway
    envelope = _send(client, "给李四转 2000 元", conversation_id="c-red-settle")
    approval_id = envelope["approval"]["approval_id"]

    challenge = client.post(f"/api/v1/agent/approvals/{approval_id}/face-challenge", json={}, headers=_headers())
    assert challenge.status_code == 200, challenge.text
    challenge_body = challenge.json()
    attestation = security.sign_face_attestation(
        challenge_body["challenge_id"],
        user_id="user_zhang",
        token_jti=challenge_body["auth_token_jti"],
    )

    resolved = client.post(
        f"/api/v1/agent/approvals/{approval_id}",
        json={"decision": "approve", "otp": "888888", "face_attestation": attestation},
        headers=_headers(),
    )
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["status"] == "COMPLETED"
    # The device collects the OTP and signs the challenge; it never receives the
    # proof that authorizes settlement.
    assert "mfa_proof" not in str(body)

    balance = core.get("/api/v1/accounts/user_zhang/balance", headers=_headers()).json()
    assert float(balance["available_balance"]) == 13000.0


def test_face_challenge_is_not_issued_for_a_yellow_approval(gateway):
    client, _ = gateway
    envelope = _send(client, "给李四转 300 元", conversation_id="c-yellow-challenge")
    response = client.post(
        f"/api/v1/agent/approvals/{envelope['approval']['approval_id']}/face-challenge",
        json={},
        headers=_headers(),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "MFA_NOT_REQUIRED"


# -- conversation integrity ----------------------------------------------


def test_message_while_pending_does_not_start_a_new_operation(gateway):
    client, core = gateway
    first = _send(client, "给李四转 300 元", conversation_id="c-pending")
    approval_id = first["approval"]["approval_id"]

    second = _send(client, "再给王美转 500 元", conversation_id="c-pending")
    assert second["status"] == "APPROVAL_REQUIRED"
    assert second["approval"]["approval_id"] == approval_id

    # Approving must settle the ORIGINAL request, not the ignored second one.
    resolved = client.post(f"/api/v1/agent/approvals/{approval_id}", json={"decision": "approve"}, headers=_headers())
    assert resolved.status_code == 200, resolved.text
    balance = core.get("/api/v1/accounts/user_zhang/balance", headers=_headers()).json()
    assert float(balance["available_balance"]) == 14700.0


def test_retried_message_id_is_deduped(gateway):
    client, _ = gateway
    first = _send(client, "给李四转 300 元", conversation_id="c-dedupe", message_id="msg-1")
    second = _send(client, "给李四转 300 元", conversation_id="c-dedupe", message_id="msg-1")

    # A redelivered channel message must replay the answer, not open a second
    # precheck (which would leave two live nonces and two approval cards).
    assert second["approval"]["approval_id"] == first["approval"]["approval_id"]
    assert second["request_id"] == first["request_id"]


def test_resolved_approval_cannot_be_replayed(gateway):
    client, _ = gateway
    envelope = _send(client, "给李四转 300 元", conversation_id="c-replay")
    approval_id = envelope["approval"]["approval_id"]

    assert client.post(f"/api/v1/agent/approvals/{approval_id}", json={"decision": "approve"}, headers=_headers()).status_code == 200
    replay = client.post(f"/api/v1/agent/approvals/{approval_id}", json={"decision": "approve"}, headers=_headers())
    assert replay.status_code >= 400
    assert replay.json()["detail"]["code"] in {"APPROVAL_ALREADY_RESOLVED", "APPROVAL_NOT_FOUND"}


def test_approval_handle_is_bound_to_the_authenticated_principal(gateway):
    client, _ = gateway
    # ``user_li`` must name someone other than himself, or no approval is made.
    foreign = _send(client, "给王美转 150 元", conversation_id="c-foreign", principal="user_li")
    approval_id = foreign["approval"]["approval_id"]

    stolen = client.post(f"/api/v1/agent/approvals/{approval_id}", json={"decision": "approve"}, headers=_headers("user_zhang"))
    assert stolen.status_code == 403
    assert stolen.json()["detail"]["code"] == "APPROVAL_PRINCIPAL_MISMATCH"


def test_conversation_id_cannot_address_another_principals_thread(gateway):
    client, _ = gateway
    # The principal is part of the derived thread id, so reusing a conversation
    # id under a different identity resolves to a different thread.
    own = conversation.conversation_thread_id("user_zhang", "shared-name")
    other = conversation.conversation_thread_id("user_li", "shared-name")
    assert own != other

    with pytest.raises(security.SecurityError):
        conversation.conversation_thread_id("user_zhang", "../../etc/passwd")


def test_missing_identity_is_refused(gateway, monkeypatch):
    client, _ = gateway
    import agent_api

    monkeypatch.setattr(agent_api, "_demo_identity_allowed", lambda: False)
    response = client.post("/api/v1/agent/messages", json={"text": "查余额", "conversation_id": "c-anon"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"


# -- session credentials are resolved by the core -------------------------


def _session_token(core: TestClient, user_id: str) -> str:
    response = core.post("/api/v1/auth/session", json={"user_id": user_id})
    assert response.status_code == 200, response.text
    return response.json()["session_token"]


def test_a_session_credential_alone_authenticates_the_caller(gateway):
    """The gateway must not keep its own session registry: the core is the only
    authority that can say whether a credential it minted is still good."""

    client, core = gateway
    token = _session_token(core, "user_zhang")
    response = client.post(
        "/api/v1/agent/messages",
        json={"text": "查一下余额", "conversation_id": "c-session"},
        headers={"X-Session-Token": token},  # deliberately no X-User-Id
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "COMPLETED"


def test_the_session_subject_beats_a_self_asserted_identity(gateway):
    client, core = gateway
    token = _session_token(core, "user_zhang")
    envelope = client.post(
        "/api/v1/agent/messages",
        json={"text": "给王美转 150 元", "conversation_id": "c-session-identity"},
        headers={"X-Session-Token": token, "X-User-Id": "user_li"},
    )
    assert envelope.status_code == 200, envelope.text
    approval_id = envelope.json()["approval"]["approval_id"]

    # The approval belongs to the session's subject, so claiming to be user_li
    # in a header cannot be used to decide it.
    claimed = client.post(
        f"/api/v1/agent/approvals/{approval_id}",
        json={"decision": "approve"},
        headers=_headers("user_li"),
    )
    assert claimed.status_code == 403
    assert claimed.json()["detail"]["code"] == "APPROVAL_PRINCIPAL_MISMATCH"


def test_an_unknown_session_is_refused(gateway):
    client, _ = gateway
    response = client.post(
        "/api/v1/agent/messages",
        json={"text": "查余额", "conversation_id": "c-bad-session"},
        headers={"X-Session-Token": "not-a-real-session"},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "INVALID_SESSION"


def test_a_core_outage_is_not_reported_as_an_expired_login(gateway, monkeypatch):
    """Sending a user through a pointless re-login during an outage is its own bug."""

    client, _ = gateway
    import agent_api

    def unreachable(session_token, request_id):
        raise BankAPIError("无法连接核心银行服务", status_code=None, payload=None)

    monkeypatch.setattr(agent_api, "_core_session_subject", unreachable)
    response = client.post(
        "/api/v1/agent/messages",
        json={"text": "查余额", "conversation_id": "c-outage"},
        headers={"X-Session-Token": "whatever"},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "CORE_UNAVAILABLE"


def test_a_turn_presents_the_callers_session_to_the_core(gateway):
    """The graph must act *as the signed-in user*, not as a channel asserting one.

    Once the core requires a session for writes, a turn that only set
    ``X-User-Id`` would be refused - so the credential has to travel with every
    call the turn makes.
    """

    client, core = gateway
    token = _session_token(core, "user_zhang")

    response = client.post(
        "/api/v1/agent/messages",
        json={"text": "查一下余额", "conversation_id": "c-forwarding"},
        headers={"X-Session-Token": token},
    )
    assert response.status_code == 200, response.text

    assert FORWARDED_SESSIONS, "the turn made no core call at all"
    assert all(value == token for value in FORWARDED_SESSIONS), FORWARDED_SESSIONS


# -- audit continuity -----------------------------------------------------


def test_yellow_chain_links_confirmation_to_the_same_request_id(gateway):
    client, core = gateway
    envelope = _send(client, "给李四转 300 元", conversation_id="c-audit-yellow")
    approval_id = envelope["approval"]["approval_id"]
    resolved = client.post(f"/api/v1/agent/approvals/{approval_id}", json={"decision": "approve"}, headers=_headers())
    assert resolved.status_code == 200, resolved.text
    request_id = resolved.json()["request_id"]
    assert request_id

    trace = core.get(f"/api/v1/security/audit/trace/{request_id}", headers=_headers())
    assert trace.status_code == 200, trace.text
    actions = [item["action"] for item in trace.json().get("events", [])]
    # The approval arrived on a second HTTP request; the chain must still be one.
    assert "RISK_PRECHECK" in actions
    assert "USER_CONFIRMATION" in actions
    assert "TRANSFER_EXECUTE" in actions


def test_red_chain_covers_challenge_and_mfa(gateway):
    client, core = gateway
    envelope = _send(client, "给李四转 2000 元", conversation_id="c-audit-red")
    approval_id = envelope["approval"]["approval_id"]
    challenge = client.post(f"/api/v1/agent/approvals/{approval_id}/face-challenge", json={}, headers=_headers()).json()
    attestation = security.sign_face_attestation(
        challenge["challenge_id"], user_id="user_zhang", token_jti=challenge["auth_token_jti"]
    )
    resolved = client.post(
        f"/api/v1/agent/approvals/{approval_id}",
        json={"decision": "approve", "otp": "888888", "face_attestation": attestation},
        headers=_headers(),
    )
    assert resolved.status_code == 200, resolved.text
    request_id = resolved.json()["request_id"]

    actions = [item["action"] for item in core.get(f"/api/v1/security/audit/trace/{request_id}", headers=_headers()).json()["events"]]
    assert "RISK_PRECHECK" in actions
    assert "FACE_CHALLENGE_ISSUED" in actions
    assert "MFA_VERIFY" in actions
    assert "TRANSFER_EXECUTE" in actions
    # A successful MFA already sets the server-side confirmation bit, so the red
    # path deliberately emits no separate USER_CONFIRMATION event.
    assert "USER_CONFIRMATION" not in actions


# -- streamed turns -------------------------------------------------------


def _parse_sse(body: str) -> list:
    """Decode an event-stream body into ``[(event, payload), ...]``."""

    frames: list = []
    event: str | None = None
    data: list[str] = []
    for line in body.splitlines():
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data.append(line[len("data:") :].strip())
        elif not line.strip() and event is not None:
            frames.append((event, json.loads("".join(data)) if data else None))
            event, data = None, []
    if event is not None:
        frames.append((event, json.loads("".join(data)) if data else None))
    return frames


def _stream(client, text: str, *, conversation_id: str, message_id: str | None = None, headers: dict | None = None):
    body = {"text": text, "conversation_id": conversation_id}
    if message_id:
        body["message_id"] = message_id
    response = client.post("/api/v1/agent/messages/stream", json=body, headers=headers or _headers())
    return response


def test_stream_reports_stages_then_one_result(gateway):
    client, core = gateway
    response = _stream(client, "查一下余额", conversation_id="c-stream-green")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")

    frames = _parse_sse(response.text)
    events = [name for name, _ in frames]
    stages = [payload["stage"] for name, payload in frames if name == "stage"]

    # Real node progress, in graph order - not a decorative animation.
    assert stages == ["intent_parser", "risk_checker", "executor", "responder"], stages
    # Exactly one terminal frame, and it comes last.
    assert events[-1] == "result", events
    assert events.count("result") + events.count("approval") + events.count("error") == 1, events
    assert frames[-1][1]["status"] == "COMPLETED"


def test_stream_emits_an_approval_frame_when_the_turn_pauses(gateway):
    client, _ = gateway
    frames = _parse_sse(_stream(client, "给李四转 300 元", conversation_id="c-stream-yellow").text)
    events = [name for name, _ in frames]
    assert events[-1] == "approval", events

    envelope = frames[-1][1]
    assert envelope["risk_level"] == "YELLOW"
    assert envelope["approval"]["approval_id"].startswith("APR")
    # The credential still never reaches the client, streamed or not.
    assert "auth_token" not in json.dumps(envelope, ensure_ascii=False)


def test_stream_replays_a_cached_message(gateway):
    client, _ = gateway
    first = _parse_sse(_stream(client, "查余额", conversation_id="c-stream-dedupe", message_id="m-1").text)
    second = _parse_sse(_stream(client, "查余额", conversation_id="c-stream-dedupe", message_id="m-1").text)

    # A redelivered message replays the answer instead of running a second turn.
    assert [name for name, _ in second] == ["result"]
    assert second[0][1]["request_id"] == first[-1][1]["request_id"]


def test_stream_authenticates_before_opening_the_stream(gateway):
    """An auth failure must be an HTTP status, not an event a client can ignore."""

    client, _ = gateway
    response = _stream(client, "查余额", conversation_id="c-stream-anon", headers={"X-Session-Token": "not-a-session"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "INVALID_SESSION"
