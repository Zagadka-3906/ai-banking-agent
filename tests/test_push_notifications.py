"""Push notification dispatch tests.

These cover the *server half* of notification delivery: which device a
notification may be sent to, what the audit trail says when nothing was sent,
and that a due task notifies its owner.  They deliberately do not pretend a
provider exists - the last test pins the honest behaviour of an unconfigured
deployment, because a transport that silently no-ops would make "the user was
told" unverifiable.
"""

import base64
import json

import pytest

pytest.importorskip("cryptography")
pytest.importorskip("fastapi")

from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402
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


def test_a_credential_alone_does_not_fake_a_transport(client, monkeypatch):
    """Setting the credential must not make the trail claim delivery.

    The FCM transport is the one piece still missing; this pins that a configured
    credential changes nothing until that code exists.
    """

    monkeypatch.setenv("BANK_FCM_CREDENTIALS", "/nonexistent/service-account.json")
    transport = notifications.resolve_transport()
    assert transport.name == "none"
    assert "not implemented" in transport.reason


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
