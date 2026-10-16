"""Cloud speech recognition relay: consent, validation, hotwords, no leaks."""

from __future__ import annotations

import base64
import json
import struct
from types import SimpleNamespace

import pytest

import security
import speech_asr
from bank_tools import BankClient
from test_agent_gateway import _headers, gateway  # noqa: F401

CONSENT = {"X-Speech-Consent": "cloud-asr", "Content-Type": "audio/wav"}


def wav(seconds: float = 1.5, *, rate: int = 16_000, channels: int = 1) -> bytes:
    frames = int(seconds * rate) * channels
    data = b"\x00\x01" * frames
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * channels * 2, channels * 2, 16)
    return (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
            + b"data" + struct.pack("<I", len(data)) + data)


@pytest.fixture
def provider(gateway, monkeypatch):  # noqa: F811
    client, core = gateway
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-provider-key")
    monkeypatch.setenv("BANK_ASR_MODEL", "qwen-audio-3.0-asr-flash")

    def core_read(bank, method, path, *, headers=None, read_only=False, **_):
        assert method == "GET" and read_only is True
        return core.get("/api/v1/" + path, headers={"X-User-Id": bank.user_id, "X-Session-Token": bank.session_token})

    monkeypatch.setattr(BankClient, "_request", core_read)
    calls = []

    def fake_post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json})
        return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": {"content": "给李四转三百元"}}]})

    monkeypatch.setattr(speech_asr, "_post", fake_post)
    return client, calls


def test_transcript_is_returned_for_review_with_contacts_as_context(provider):
    client, calls = provider
    response = client.post("/api/v1/agent/speech/transcribe", content=wav(), headers={**_headers(), **CONSENT})
    assert response.status_code == 200, response.text
    assert response.json()["text"] == "给李四转三百元" and response.json()["seconds"] == 1.5
    body = calls[0]["json"]
    assert calls[0]["url"].endswith("/chat/completions")
    assert body["model"] == "qwen-audio-3.0-asr-flash"
    # ITN would turn "一百五" into "150" and skip the parser's ambiguity question.
    assert body["asr_options"] == {"language": "zh", "enable_itn": False}
    context = body["messages"][0]["content"]
    assert body["messages"][0]["role"] == "system" and "李四" in context and "转账" in context
    audio = body["messages"][1]["content"][0]["input_audio"]["data"]
    assert audio.startswith("data:audio/wav;base64,") and base64.b64decode(audio.split(",", 1)[1]) == wav()
    # Nothing was sent as a message, and the audit keeps metadata only.
    event = next(row for row in security.get_audit_logs(limit=200) if row["action"] == "SPEECH_TRANSCRIBE")
    assert "李四" not in json.dumps(event, ensure_ascii=False) and event["metadata"]["seconds"] == 1.5


def test_consent_header_is_required_before_any_upload(provider):
    client, calls = provider
    response = client.post("/api/v1/agent/speech/transcribe", content=wav(), headers={**_headers(), "Content-Type": "audio/wav"})
    assert response.status_code == 428 and response.json()["detail"]["code"] == "SPEECH_CONSENT_REQUIRED"
    assert calls == []


def test_session_is_required(provider):
    client, calls = provider
    response = client.post("/api/v1/agent/speech/transcribe", content=wav(), headers=CONSENT)
    assert response.status_code == 401
    assert calls == []


@pytest.mark.parametrize(("audio", "reason"), [
    (b"not audio at all" * 10, "仅支持 WAV"),
    (wav(rate=8_000), "16 kHz"),
    (wav(channels=2), "16 kHz"),
    (wav(seconds=0.1), "过短"),
    (wav(seconds=21), "20 秒"),
], ids=["not-wav", "8khz", "stereo", "too-short", "too-long"])
def test_unsupported_audio_is_rejected_before_the_provider(provider, audio, reason):
    client, calls = provider
    response = client.post("/api/v1/agent/speech/transcribe", content=audio, headers={**_headers(), **CONSENT})
    assert response.status_code == 400 and reason in response.json()["detail"]["message"]
    assert calls == []


def test_disabled_without_key_and_status_reports_it(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    assert client.get("/api/v1/agent/speech/status", headers=_headers()).json()["cloud_asr"] is False
    response = client.post("/api/v1/agent/speech/transcribe", content=wav(), headers={**_headers(), **CONSENT})
    assert response.status_code == 503 and response.json()["detail"]["code"] == "ASR_DISABLED"
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    status = client.get("/api/v1/agent/speech/status", headers=_headers()).json()
    assert status["cloud_asr"] is True and status["format"] == "audio/wav" and status["max_seconds"] == 20


def test_provider_failure_never_leaks_its_body_or_the_key(provider, monkeypatch):
    client, _ = provider
    monkeypatch.setattr(speech_asr, "_post", lambda *a, **k: SimpleNamespace(status_code=401, text="invalid key test-provider-key", json=lambda: {}))
    response = client.post("/api/v1/agent/speech/transcribe", content=wav(), headers={**_headers(), **CONSENT})
    assert response.status_code == 502 and response.json()["detail"]["code"] == "ASR_FAILED"
    assert "test-provider-key" not in response.text


def test_empty_or_control_character_transcripts(provider, monkeypatch):
    client, _ = provider
    monkeypatch.setattr(speech_asr, "_post", lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": {"content": "\x00 \x07"}}]}))
    response = client.post("/api/v1/agent/speech/transcribe", content=wav(), headers={**_headers(), **CONSENT})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "ASR_EMPTY"


def test_hint_context_is_bounded_and_deduplicated():
    context = speech_asr.context_text(["李四", "李四", " ", "x" * 30] + [f"名{i}" for i in range(80)])
    assert context.count("李四") == 1 and "x" * 30 not in context
    assert context.count("名") <= 50
