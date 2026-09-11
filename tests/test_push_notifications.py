"""Push notification dispatch tests.

These cover the *server half* of notification delivery: which device a
notification may be sent to, what the audit trail says when nothing was sent,
that a due task notifies its owner, and the FCM HTTP v1 flow itself.

The FCM tests run against a local mock server, so the JWT assertion, the token
exchange, the per-device send and the refresh-on-401 path are all genuinely
exercised.  The one thing they cannot prove is that Google accepts the request;
that needs a real service account.
"""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pytest

pytest.importorskip("cryptography")
pytest.importorskip("fastapi")

from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa  # noqa: E402
from cryptography.hazmat.primitives.serialization import (  # noqa: E402
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import notifications  # noqa: E402
import security  # noqa: E402


def new_device_key():
    return ec.generate_private_key(ec.SECP256R1())


def session_headers(client: TestClient, user_id: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/session", json={"user_id": user_id})
    assert response.status_code == 200, response.text
    return {"X-User-Id": user_id, "X-Session-Token": response.json()["session_token"]}


def enroll(client: TestClient, headers: dict[str, str]) -> str:
    der = new_device_key().public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    response = client.post(
        "/api/v1/devices/register",
        json={"public_key": base64.b64encode(der).decode("ascii"), "label": "push-test", "platform": "android"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["device_id"]


def attach(client: TestClient, headers: dict[str, str], device_id: str, token: str = "fcm-token-1"):
    return client.post(f"/api/v1/devices/{device_id}/push-token", json={"token": token}, headers=headers)


@pytest.fixture()
def client():
    main.reset_state()
    yield TestClient(main.app)
    main.reset_state()


# ---------------------------------------------------------------------------
# token registration
# ---------------------------------------------------------------------------


def test_registering_a_token_requires_a_session(client):
    """A token attached to a device decides where that user's notifications go."""

    headers = session_headers(client, "user_zhang")
    device_id = enroll(client, headers)

    response = client.post(f"/api/v1/devices/{device_id}/push-token", json={"token": "t"}, headers={"X-User-Id": "user_zhang"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "SESSION_REQUIRED"


def test_a_token_can_only_be_attached_to_your_own_device(client):
    zhang = session_headers(client, "user_zhang")
    li = session_headers(client, "user_li")
    device_id = enroll(client, zhang)

    stolen = attach(client, li, device_id)
    # Indistinguishable from "no such device": no enumeration oracle.
    assert stolen.status_code == 404
    assert stolen.json()["detail"]["code"] == "DEVICE_NOT_FOUND"

    assert attach(client, zhang, device_id).status_code == 200


def test_an_empty_token_is_refused(client):
    headers = session_headers(client, "user_zhang")
    device_id = enroll(client, headers)
    response = attach(client, headers, device_id, token="   ")
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_PUSH_TOKEN"


def test_listing_reports_push_support_without_leaking_the_token(client):
    headers = session_headers(client, "user_zhang")
    device_id = enroll(client, headers)
    assert attach(client, headers, device_id, token="fcm-secret-token").status_code == 200

    listing = client.get("/api/v1/devices", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["data"][0]["push_enabled"] is True
    # A delivery token reaches the device; a listing has no business handing it out.
    assert "fcm-secret-token" not in json.dumps(listing.json())


def test_the_audit_records_a_fingerprint_not_the_token(client):
    headers = session_headers(client, "user_zhang")
    device_id = enroll(client, headers)
    attach(client, headers, device_id, token="fcm-secret-token")

    registered = [event for event in security.AUDIT_LOGS if event["action"] == "PUSH_TOKEN_REGISTERED"]
    assert registered, "token registration must be auditable"
    assert "fcm-secret-token" not in json.dumps(registered)
    assert registered[-1]["metadata"]["token_fingerprint"]


def test_revoking_a_device_stops_notifying_it(client):
    headers = session_headers(client, "user_zhang")
    device_id = enroll(client, headers)
    attach(client, headers, device_id)
    assert len(security.push_tokens_for("user_zhang")) == 1

    client.post(f"/api/v1/devices/{device_id}/revoke", headers=headers)
    assert security.push_tokens_for("user_zhang") == []


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


def test_dispatch_without_a_registered_device_audits_a_skip(client):
    result = notifications.dispatch_to_user("user_zhang", title="t", body="b")
    assert result.delivered == 0
    assert result.detail == "no registered push token"

    dispatched = [event for event in security.AUDIT_LOGS if event["action"] == "PUSH_DISPATCHED"]
    assert dispatched[-1]["outcome"] == "NO_DEVICE"


def test_dispatch_targets_only_the_owners_devices(client, monkeypatch):
    """Whatever the transport is, it must never be handed another user's token."""

    sent: list[list[str]] = []

    class RecordingTransport:
        name = "recording"

        def send(self, tokens, *, title, body, data):
            sent.append(list(tokens))
            return notifications.PushResult(delivered=len(tokens), failed=0, detail="ok", provider=self.name)

    monkeypatch.setattr(notifications, "resolve_transport", lambda: RecordingTransport())

    zhang = session_headers(client, "user_zhang")
    li = session_headers(client, "user_li")
    attach(client, zhang, enroll(client, zhang), token="zhang-token")
    attach(client, li, enroll(client, li), token="li-token")

    result = notifications.dispatch_to_user("user_zhang", title="t", body="b")
    assert sent == [["zhang-token"]], sent
    assert result.delivered == 1
    assert result.provider == "recording"


def test_an_unconfigured_deployment_says_so_instead_of_pretending(client, monkeypatch):
    monkeypatch.delenv("BANK_FCM_CREDENTIALS", raising=False)
    headers = session_headers(client, "user_zhang")
    attach(client, headers, enroll(client, headers), token="fcm-token-1")

    result = notifications.dispatch_to_user("user_zhang", title="t", body="b")
    assert result.delivered == 0
    assert "no push provider" in result.detail

    dispatched = [event for event in security.AUDIT_LOGS if event["action"] == "PUSH_DISPATCHED"]
    assert dispatched[-1]["outcome"] == "NOT_SENT"
    assert dispatched[-1]["metadata"]["devices"] == 1
    assert "no push provider" in dispatched[-1]["metadata"]["detail"]


def test_a_configured_but_missing_credential_says_so(client, monkeypatch):
    """A credential path that points nowhere must not look like delivery.

    This replaced an earlier assertion that the FCM transport was simply not
    implemented; now that it is, the interesting property is that a *broken*
    configuration still degrades with a specific reason rather than failing
    silently or crashing the scheduler.
    """

    monkeypatch.setenv("BANK_FCM_CREDENTIALS", "/nonexistent/service-account.json")
    transport = notifications.resolve_transport()
    assert transport.name == "none"
    assert "not found" in transport.reason


# ---------------------------------------------------------------------------
# integration with the scheduler
# ---------------------------------------------------------------------------


def test_a_due_task_notifies_the_user_who_must_confirm_it(client):
    headers = session_headers(client, "user_zhang")
    attach(client, headers, enroll(client, headers), token="fcm-token-1")

    task = main.TASK_STORE.create(
        "user_zhang",
        "SCHEDULED_TRANSFER",
        {"schedule_id": "SCH-TEST"},
        run_at="2000-01-01T00:00:00+00:00",
    )

    due = client.post("/api/v1/tasks/run-due", headers=headers)
    assert due.status_code == 200, due.text
    assert any(row["task_id"] == task.task_id and row["status"] == "WAITING_USER" for row in due.json()["data"])

    dispatched = [event for event in security.AUDIT_LOGS if event["action"] == "PUSH_DISPATCHED"]
    assert dispatched, "a task waiting for the user must produce a dispatch attempt"
    assert dispatched[-1]["metadata"]["devices"] == 1
    assert dispatched[-1]["metadata"]["data"]["task_id"] == task.task_id
    # The message must not imply the money already moved.
    assert "确认" in dispatched[-1]["metadata"]["title"]


def test_run_due_uses_one_correlation_id(client):
    """One scheduler run is one auditable chain, not a scatter of ids."""

    headers = {**session_headers(client, "user_zhang"), "X-Request-Id": "REQ-run-due-1"}
    attach(client, headers, enroll(client, headers), token="fcm-token-1")
    main.TASK_STORE.create("user_zhang", "SCHEDULED_TRANSFER", {"schedule_id": "SCH-1"}, run_at="2000-01-01T00:00:00+00:00")

    assert client.post("/api/v1/tasks/run-due", headers=headers).status_code == 200

    correlated = [event["action"] for event in security.AUDIT_LOGS if event.get("request_id") == "REQ-run-due-1"]
    assert "TASK_RUN" in correlated, correlated
    # The dispatch belongs to the same run, which only holds because the id is
    # bound once instead of being re-minted at each call site.
    assert "PUSH_DISPATCHED" in correlated, correlated


# ---------------------------------------------------------------------------
# FCM HTTP v1, against a local server
# ---------------------------------------------------------------------------


class _RecordingServer(ThreadingHTTPServer):
    """A mock Google endpoint that records what it was asked to do."""

    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _RecordingHandler)
        self.requests: list[tuple[str, dict, dict]] = []
        # path -> list of (status, payload) to return in order; the last entry
        # repeats once exhausted.
        self.responses: dict[str, list[tuple[int, dict]]] = {}

    @property
    def base_url(self) -> str:
        host, port = self.server_address[0], self.server_address[1]
        return f"http://{host}:{port}"

    def take_response(self, path: str) -> tuple[int, dict]:
        queue = self.responses.get(path)
        if not queue:
            return 200, {}
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def posted(self, path: str) -> list[dict]:
        return [body for seen, _headers, body in self.requests if seen == path]


class _RecordingHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")
        try:
            if "application/x-www-form-urlencoded" in content_type:
                # What the OAuth2 token endpoint actually receives.
                body = {key: values[0] for key, values in parse_qs(raw.decode("utf-8")).items()}
            else:
                body = json.loads(raw) if raw else {}
        except ValueError:
            body = {"_raw": raw.decode("utf-8", "replace")}
        server: _RecordingServer = self.server  # type: ignore[assignment]
        server.requests.append((self.path, dict(self.headers), body))
        status, payload = server.take_response(self.path)
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:  # keep the test output quiet
        return


@pytest.fixture()
def fcm_server():
    server = _RecordingServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _service_account(tmp_path, server: _RecordingServer, *, project_id: str = "bank-agent-demo"):
    """A locally generated stand-in for a downloaded service-account file."""

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode("ascii")
    path = tmp_path / "firebase-adminsdk.json"
    path.write_text(
        json.dumps(
            {
                "type": "service_account",
                "project_id": project_id,
                "client_email": "pusher@bank-agent-demo.iam.gserviceaccount.com",
                "private_key": pem,
                "token_uri": f"{server.base_url}/token",
            }
        ),
        encoding="utf-8",
    )
    return path, key


def test_a_service_account_is_exchanged_for_a_token_and_used_for_delivery(tmp_path, fcm_server):
    path, key = _service_account(tmp_path, fcm_server)
    fcm_server.responses["/token"] = [(200, {"access_token": "ya29.test", "expires_in": 3600})]

    transport = notifications.FcmTransport(credentials_path=str(path), base_url=fcm_server.base_url)
    result = transport.send(["device-a", "device-b"], title="预约转账待确认", body="需要您确认", data={"task_id": "TSK1"})

    assert (result.delivered, result.failed) == (2, 0), result
    assert result.provider == "fcm"

    # The token exchange carried a signed JWT assertion ...
    exchange = fcm_server.posted("/token")[0]
    assert exchange["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    assertion = exchange["assertion"]
    header_segment, claims_segment, signature_segment = assertion.split(".")
    header = json.loads(base64.urlsafe_b64decode(header_segment + "=="))
    claims = json.loads(base64.urlsafe_b64decode(claims_segment + "=="))
    assert header == {"alg": "RS256", "typ": "JWT"}
    assert claims["iss"].endswith("iam.gserviceaccount.com")
    assert claims["scope"] == notifications.FCM_SCOPE
    assert claims["exp"] > claims["iat"]

    # ... signed with the service account's private key, which is the part that
    # makes Google believe the caller.
    signature = base64.urlsafe_b64decode(signature_segment + "==")
    key.public_key().verify(
        signature,
        f"{header_segment}.{claims_segment}".encode("ascii"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )

    # Each device got its own message, bearing the exchange's access token.
    sends = fcm_server.posted("/v1/projects/bank-agent-demo/messages:send")
    assert len(sends) == 2, sends
    assert [item["message"]["token"] for item in sends] == ["device-a", "device-b"]
    assert all(item["message"]["data"]["task_id"] == "TSK1" for item in sends)
    authorization = [headers for seen, headers, _ in fcm_server.requests if seen.endswith("messages:send")][0]
    assert authorization["Authorization"] == "Bearer ya29.test"
    # Data-only messages: the app owns the wording, not the system renderer.
    assert "notification" not in sends[0]["message"]


def test_a_refused_device_is_counted_without_failing_the_others(tmp_path, fcm_server):
    path, _ = _service_account(tmp_path, fcm_server)
    fcm_server.responses["/token"] = [(200, {"access_token": "ya29.test", "expires_in": 3600})]
    fcm_server.responses["/v1/projects/bank-agent-demo/messages:send"] = [
        (200, {"name": "projects/bank-agent-demo/messages/1"}),
        (400, {"error": {"status": "INVALID_ARGUMENT", "message": "token is not registered"}}),
    ]

    transport = notifications.FcmTransport(credentials_path=str(path), base_url=fcm_server.base_url)
    result = transport.send(["good", "stale"], title="t", body="b", data={})

    assert (result.delivered, result.failed) == (1, 1), result
    assert "400" in result.detail and "not registered" in result.detail


def test_an_expired_access_token_is_refreshed_once(tmp_path, fcm_server):
    path, _ = _service_account(tmp_path, fcm_server)
    fcm_server.responses["/token"] = [
        (200, {"access_token": "stale", "expires_in": 3600}),
        (200, {"access_token": "fresh", "expires_in": 3600}),
    ]
    fcm_server.responses["/v1/projects/bank-agent-demo/messages:send"] = [
        (401, {"error": {"status": "UNAUTHENTICATED"}}),
        (200, {"name": "projects/bank-agent-demo/messages/2"}),
    ]

    transport = notifications.FcmTransport(credentials_path=str(path), base_url=fcm_server.base_url)
    result = transport.send(["device-a"], title="t", body="b", data={})

    assert (result.delivered, result.failed) == (1, 0), result
    assert len(fcm_server.posted("/token")) == 2, "the expired token should be re-exchanged exactly once"


def test_the_access_token_is_cached_across_sends(tmp_path, fcm_server):
    path, _ = _service_account(tmp_path, fcm_server)
    fcm_server.responses["/token"] = [(200, {"access_token": "cached", "expires_in": 3600})]

    transport = notifications.FcmTransport(credentials_path=str(path), base_url=fcm_server.base_url)
    transport.send(["a"], title="t", body="b", data={})
    transport.send(["b"], title="t", body="b", data={})

    # A second exchange per notification would be pure latency against Google.
    assert len(fcm_server.posted("/token")) == 1


def test_a_malformed_credential_degrades_honestly(tmp_path, monkeypatch):
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("BANK_FCM_CREDENTIALS", str(broken))

    transport = notifications.resolve_transport()
    assert transport.name == "none"
    assert "unusable" in transport.reason


def test_a_credential_without_a_private_key_is_rejected(tmp_path, monkeypatch):
    incomplete = tmp_path / "sa.json"
    incomplete.write_text(json.dumps({"project_id": "p", "client_email": "a@b", "token_uri": "https://x"}), encoding="utf-8")
    monkeypatch.setenv("BANK_FCM_CREDENTIALS", str(incomplete))

    transport = notifications.resolve_transport()
    assert transport.name == "none"
    assert "unusable" in transport.reason


def test_a_usable_credential_selects_the_fcm_transport(tmp_path, fcm_server, monkeypatch):
    path, _ = _service_account(tmp_path, fcm_server)
    monkeypatch.setenv("BANK_FCM_CREDENTIALS", str(path))

    transport = notifications.resolve_transport()
    assert transport.name == "fcm"
    assert transport.project_id == "bank-agent-demo"


# ---------------------------------------------------------------------------
# the reported push capability
#
# A device holding a valid FCM token and a server that cannot send are two
# different states, and both are invisible from the outside unless the health
# report distinguishes them.
# ---------------------------------------------------------------------------


def test_push_health_says_the_server_cannot_send_without_a_credential(client, monkeypatch):
    monkeypatch.delenv("BANK_FCM_CREDENTIALS", raising=False)

    report = security.push_health()

    assert report["configured"] is False
    assert report["provider"] == "none"
    assert "BANK_FCM_CREDENTIALS" in report["reason"]


def test_push_health_counts_devices_holding_a_token(client, monkeypatch):
    monkeypatch.delenv("BANK_FCM_CREDENTIALS", raising=False)
    headers = session_headers(client, "user_zhang")
    device_id = enroll(client, headers)

    assert security.push_health()["devices_with_tokens"] == 0

    attach(client, headers, device_id, token="fcm-token-1")
    assert security.push_health()["devices_with_tokens"] == 1


def test_push_health_reports_ready_once_a_credential_is_usable(tmp_path, fcm_server, monkeypatch):
    path, _ = _service_account(tmp_path, fcm_server)
    monkeypatch.setenv("BANK_FCM_CREDENTIALS", str(path))

    report = security.push_health()

    assert report["configured"] is True
    assert report["provider"] == "fcm"
    assert report["reason"] == "ready to send"


def test_push_health_does_not_leak_the_credential_path(tmp_path, monkeypatch):
    """The health endpoint is unauthenticated, so it must not echo a path."""

    secret_path = tmp_path / "private-location" / "firebase-adminsdk.json"
    monkeypatch.setenv("BANK_FCM_CREDENTIALS", str(secret_path))

    report = security.push_health()

    assert report["configured"] is False
    assert str(secret_path) not in report["reason"]
    assert "<BANK_FCM_CREDENTIALS>" in report["reason"]


def test_the_health_endpoint_exposes_the_push_capability(client, monkeypatch):
    monkeypatch.delenv("BANK_FCM_CREDENTIALS", raising=False)

    body = client.get("/api/v1/security/health").json()

    assert body["push"]["configured"] is False
    assert body["push"]["devices_with_tokens"] == 0


# ---------------------------------------------------------------------------
# tokens the provider says are dead
#
# Every reinstall mints a new registration token and orphans the old one.  Left
# alone the registry only grows: each notification fans out to addresses nobody
# owns any more, `failed` counts climb for reasons that have nothing to do with
# the current phone, and a real delivery problem hides inside that noise.
#
# The narrowness matters as much as the pruning: only the provider naming a
# *token* as unusable may unsubscribe a device.  Throttling, an outage, or our
# own credential expiring must leave it alone.
# ---------------------------------------------------------------------------

SEND_PATH = "/v1/projects/bank-agent-demo/messages:send"


def _ready_to_send(client, tmp_path, fcm_server, monkeypatch, *, token: str = "fcm-token-1"):
    """A configured dispatcher with one enrolled device holding one token.

    ``resolve_transport`` is pointed at the mock deliberately.  The transport it
    builds by default targets the real ``fcm.googleapis.com``, so a test that
    only sets the credential file would quietly exercise Google instead of the
    code under test - and get a perfectly convincing 401 for its fake access
    token, which is exactly how a "transient failure" assertion passes without
    testing anything.
    """

    path, _ = _service_account(tmp_path, fcm_server)
    monkeypatch.setenv("BANK_FCM_CREDENTIALS", str(path))
    monkeypatch.setattr(
        notifications,
        "resolve_transport",
        lambda: notifications.FcmTransport(credentials_path=str(path), base_url=fcm_server.base_url),
    )
    fcm_server.responses["/token"] = [(200, {"access_token": "ya29.test", "expires_in": 3600})]
    headers = session_headers(client, "user_zhang")
    device_id = enroll(client, headers)
    attach(client, headers, device_id, token=token)
    return device_id


def test_a_token_the_provider_calls_unregistered_is_dropped(client, tmp_path, fcm_server, monkeypatch):
    device_id = _ready_to_send(client, tmp_path, fcm_server, monkeypatch)
    fcm_server.responses[SEND_PATH] = [
        (404, {"error": {"status": "NOT_FOUND", "details": [{"errorCode": "UNREGISTERED"}]}}),
    ]

    result = notifications.dispatch_to_user("user_zhang", title="t", body="b")

    assert result.failed == 1
    assert result.dead_tokens == ("fcm-token-1",), result.detail
    assert security.push_tokens_for("user_zhang") == [], "the dead address must not be retried"
    assert "push_token" not in security.DEVICES[device_id], "the device itself is kept"
    dropped = [event for event in security.AUDIT_LOGS if event["action"] == "PUSH_TOKEN_UNREGISTERED"]
    assert dropped and dropped[-1]["outcome"] == "DROPPED"
    assert dropped[-1]["metadata"]["devices"] == [device_id]
    # The trail records the fingerprint, never the delivery address.
    assert "fcm-token-1" not in json.dumps(dropped[-1]["metadata"])
    dispatched = [event for event in security.AUDIT_LOGS if event["action"] == "PUSH_DISPATCHED"]
    assert dispatched[-1]["metadata"]["pruned_devices"] == [device_id]


def test_the_prune_is_persisted_not_just_in_memory(client, tmp_path, fcm_server, monkeypatch):
    """A restart must not resurrect an address the provider already rejected."""

    store = tmp_path / "devices.json"
    monkeypatch.setenv("BANK_DEVICE_STORE_PATH", str(store))
    _ready_to_send(client, tmp_path, fcm_server, monkeypatch)
    fcm_server.responses[SEND_PATH] = [(404, {"error": {"details": [{"errorCode": "UNREGISTERED"}]}})]

    notifications.dispatch_to_user("user_zhang", title="t", body="b")

    assert "fcm-token-1" not in store.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("status", "payload"),
    [
        (429, {"error": {"status": "RESOURCE_EXHAUSTED"}}),
        (500, {"error": {"status": "INTERNAL"}}),
        (503, {"error": {"status": "UNAVAILABLE"}}),
        (401, {"error": {"status": "UNAUTHENTICATED"}}),
    ],
)
def test_a_transient_refusal_never_unsubscribes_the_device(client, tmp_path, fcm_server, monkeypatch, status, payload):
    """Throttled, broken or mis-authenticated must not look like "gone"."""

    _ready_to_send(client, tmp_path, fcm_server, monkeypatch)
    fcm_server.responses[SEND_PATH] = [(status, payload)]

    result = notifications.dispatch_to_user("user_zhang", title="t", body="b")

    assert result.failed == 1
    assert result.dead_tokens == ()
    assert [row["token"] for row in security.push_tokens_for("user_zhang")] == ["fcm-token-1"]
    assert not [event for event in security.AUDIT_LOGS if event["action"] == "PUSH_TOKEN_UNREGISTERED"]


def test_a_token_minted_for_another_project_is_dropped(client, tmp_path, fcm_server, monkeypatch):
    _ready_to_send(client, tmp_path, fcm_server, monkeypatch)
    fcm_server.responses[SEND_PATH] = [
        (403, {"error": {"status": "PERMISSION_DENIED", "details": [{"errorCode": "SENDER_ID_MISMATCH"}]}}),
    ]

    result = notifications.dispatch_to_user("user_zhang", title="t", body="b")

    assert result.dead_tokens == ("fcm-token-1",), result.detail
    assert security.push_tokens_for("user_zhang") == []


def test_a_payload_we_got_wrong_does_not_prune_the_device(client, tmp_path, fcm_server, monkeypatch):
    """A 400 is also what FCM says when *our* message is malformed.

    Deleting the user's delivery address would repair the wrong thing: the token
    is fine, the request was not.  A token that really is dead keeps answering
    404 until it is pruned, so waiting costs nothing.
    """

    _ready_to_send(client, tmp_path, fcm_server, monkeypatch)
    fcm_server.responses[SEND_PATH] = [(400, {"error": {"status": "INVALID_ARGUMENT", "message": "bad data field"}})]

    result = notifications.dispatch_to_user("user_zhang", title="t", body="b")

    assert result.dead_tokens == (), result.detail
    assert security.push_tokens_for("user_zhang") != []


def test_a_permission_error_without_a_token_verdict_is_not_pruned(client, tmp_path, fcm_server, monkeypatch):
    """Only the provider naming the token counts; a bare 403 is our problem."""

    _ready_to_send(client, tmp_path, fcm_server, monkeypatch)
    fcm_server.responses[SEND_PATH] = [(403, {"error": {"status": "PERMISSION_DENIED", "message": "caller lacks permission"}})]

    result = notifications.dispatch_to_user("user_zhang", title="t", body="b")

    assert result.dead_tokens == (), result.detail
    assert security.push_tokens_for("user_zhang") != []


def test_a_verbose_provider_error_still_prunes(client, tmp_path, fcm_server, monkeypatch):
    """The text describing a dead token must not be able to abort its removal.

    FCM bodies are long and untrusted, and ``sanitize_text`` *raises* on
    over-length input.  An earlier version passed the raw body straight through,
    so the exception escaped and the reason string silently prevented the very
    cleanup it was reporting - the token stayed and kept failing forever.
    """

    _ready_to_send(client, tmp_path, fcm_server, monkeypatch)
    fcm_server.responses[SEND_PATH] = [
        (404, {"error": {"status": "NOT_FOUND", "message": "y" * 4000, "details": [{"errorCode": "UNREGISTERED"}]}}),
    ]

    result = notifications.dispatch_to_user("user_zhang", title="t", body="b")

    assert result.dead_tokens == ("fcm-token-1",), result.detail
    assert security.push_tokens_for("user_zhang") == []
    dropped = [event for event in security.AUDIT_LOGS if event["action"] == "PUSH_TOKEN_UNREGISTERED"]
    assert dropped[-1]["outcome"] == "DROPPED"
    assert 0 < len(dropped[-1]["metadata"]["reason"]) <= 120


def test_dropping_an_unknown_token_is_recorded_rather_than_guessed(client):
    outcome = security.detach_push_token("nobody-has-this", reason="test")

    assert outcome["dropped"] == []
    events = [event for event in security.AUDIT_LOGS if event["action"] == "PUSH_TOKEN_UNREGISTERED"]
    assert events and events[-1]["outcome"] == "NO_MATCH"


def test_pruning_one_device_leaves_the_others_alone(client, tmp_path, fcm_server, monkeypatch):
    """Two phones, one dead address: only the dead one loses its token."""

    path, _ = _service_account(tmp_path, fcm_server)
    monkeypatch.setenv("BANK_FCM_CREDENTIALS", str(path))
    fcm_server.responses["/token"] = [(200, {"access_token": "ya29.test", "expires_in": 3600})]
    headers = session_headers(client, "user_zhang")
    first = enroll(client, headers)
    attach(client, headers, first, token="fcm-token-1")
    second = enroll(client, headers)
    attach(client, headers, second, token="fcm-token-2")
    # The mock answers per path, so drive one token through the transport
    # directly rather than fanning out and having both refused.
    fcm_server.responses[SEND_PATH] = [(404, {"error": {"details": [{"errorCode": "UNREGISTERED"}]}})]

    # Pointed at the mock explicitly: the default transport targets the real
    # fcm.googleapis.com, where a fake access token earns a convincing 401.
    transport = notifications.FcmTransport(credentials_path=str(path), base_url=fcm_server.base_url)
    result = transport.send(["fcm-token-1"], title="t", body="b", data={})
    for token in result.dead_tokens:  # what dispatch_to_user does with the answer
        security.detach_push_token(token, reason=result.detail)

    assert result.dead_tokens == ("fcm-token-1",), result.detail
    assert "push_token" not in security.DEVICES[first]
    assert security.DEVICES[second]["push_token"] == "fcm-token-2", "the live phone must keep its address"
    assert [row["device_id"] for row in security.push_tokens_for("user_zhang")] == [second]
