"""Body limits apply to the wire bytes, not only a caller's length header."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import agent_api
import main
from http_controls import cache_bounded_body
from security import SecurityError


@pytest.fixture(params=[(main, "/api/v1/auth/login"), (agent_api, "/api/v1/agent/messages")])
def endpoint(request, monkeypatch):
    module, path = request.param
    monkeypatch.setattr(module, "_MAX_REQUEST_BYTES", 64)
    monkeypatch.setenv("BANK_RATE_LIMIT_PER_MINUTE", "0")
    main.reset_state()
    yield TestClient(module.app), path
    main.reset_state()


@pytest.mark.parametrize("length", [None, "0", "1", "65", "9" * 5000])
def test_oversize_body_is_rejected_even_without_an_honest_length(endpoint, length):
    client, path = endpoint
    headers = {} if length is None else {"Content-Length": length}
    # An iterator causes HTTPX to use chunked encoding when no length is given.
    response = client.post(path, content=iter([b"x" * 32, b"y" * 33]), headers=headers)
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "REQUEST_TOO_LARGE"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("length", ["-1", "invalid", ""])
def test_invalid_content_length_has_a_controlled_response(endpoint, length):
    client, path = endpoint
    response = client.post(path, content=b"{}", headers={"Content-Length": length})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_CONTENT_LENGTH"


def test_exact_limit_preserves_body_for_endpoint_validation(endpoint):
    client, path = endpoint
    response = client.post(path, content=iter([b'{"extra":', b'"value"}' + b" " * 47]), headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "missing"


def test_stream_read_stops_as_soon_as_limit_is_exceeded():
    chunks = iter([b"a" * 32, b"b" * 33, b"must never be read"])
    received = []

    async def receive():
        chunk = next(chunks)
        received.append(chunk)
        return {"type": "http.request", "body": chunk, "more_body": True}

    request = Request({"type": "http", "headers": []}, receive)
    with pytest.raises(SecurityError) as failure:
        asyncio.run(cache_bounded_body(request, 64))
    assert failure.value.code == "REQUEST_TOO_LARGE"
    assert len(received) == 2


def test_interrupted_upload_is_a_client_error(endpoint):
    client, path = endpoint
    messages = iter([
        {"type": "http.request", "body": b'{"text":', "more_body": True},
        {"type": "http.disconnect"},
    ])
    sent = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1", "method": "POST", "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "server": ("localhost", 80), "client": ("127.0.0.1", 1234),
    }
    asyncio.run(client.app(scope, receive, send))
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 400
    assert b"REQUEST_BODY_INCOMPLETE" in sent[1]["body"]
