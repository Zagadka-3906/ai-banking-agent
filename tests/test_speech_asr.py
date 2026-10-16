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
        native = {"output": {"text": "给李四转三百元"}}
        compatible = {"choices": [{"message": {"content": "给李四转三百元"}}]}
        return SimpleNamespace(status_code=200, json=lambda: native if "multimodal-generation" in url else compatible)

    monkeypatch.setattr(speech_asr, "_post", fake_post)
    return client, calls


def _sent_audio(data_url: str) -> bytes:
    assert data_url.startswith("data:audio/wav;base64,")
    return base64.b64decode(data_url.split(",", 1)[1])


def test_qwen_audio_uses_native_api_with_contacts_as_hotwords(provider):
    """Request shape verified against Model Studio on 2026-10-16 (qwen-audio-3.0-asr-flash)."""
    client, calls = provider
    response = client.post("/api/v1/agent/speech/transcribe", content=wav(), headers={**_headers(), **CONSENT})
    assert response.status_code == 200, response.text
    assert response.json()["text"] == "给李四转三百元" and response.json()["seconds"] == 1.5
    call = calls[0]
    assert call["url"].endswith("/api/v1/services/aigc/multimodal-generation/generation")
    assert call["headers"]["X-DashScope-SSE"] == "disable"
    body = call["json"]
    assert body["model"] == "qwen-audio-3.0-asr-flash"
    params = body["parameters"]
    assert params["format"] == "wav" and params["sample_rate"] == "16000" and params["language_hints"] == ["zh"]
    assert params["vocabulary"]["李四"] == 5 and "转账" in params["vocabulary"]
    assert _sent_audio(body["input"]["messages"][0]["content"][0]["input_audio"]["data"]) == wav()


def test_qwen3_uses_compatible_api_and_keeps_spoken_numbers(provider, monkeypatch):
    """Request shape verified against Model Studio on 2026-10-16 (qwen3-asr-flash)."""
    client, calls = provider
    monkeypatch.setenv("BANK_ASR_MODEL", "qwen3-asr-flash")
    response = client.post("/api/v1/agent/speech/transcribe", content=wav(), headers={**_headers(), **CONSENT})
    assert response.status_code == 200, response.text
    body = calls[0]["json"]
    assert calls[0]["url"].endswith("/compatible-mode/v1/chat/completions")
    # ITN would turn "一百五" into "150" and skip the parser's ambiguity question.
    assert body["asr_options"] == {"language": "zh", "enable_itn": False}
    system, user = body["messages"]
    assert system["role"] == "system" and isinstance(system["content"], list)  # a plain string is rejected
    assert "李四" in system["content"][0]["text"] and "转账" in system["content"][0]["text"]
    audio = user["content"][0]["input_audio"]
    assert audio["format"] == "wav" and _sent_audio(audio["data"]) == wav()
    status = client.get("/api/v1/agent/speech/status", headers=_headers()).json()
    assert status["keeps_spoken_numbers"] is True
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
    assert status["keeps_spoken_numbers"] is False  # the default qwen-audio model always writes digits


def test_provider_failure_never_leaks_its_body_or_the_key(provider, monkeypatch):
    client, _ = provider
    monkeypatch.setattr(speech_asr, "_post", lambda *a, **k: SimpleNamespace(status_code=401, text="invalid key test-provider-key", json=lambda: {}))
    response = client.post("/api/v1/agent/speech/transcribe", content=wav(), headers={**_headers(), **CONSENT})
    assert response.status_code == 502 and response.json()["detail"]["code"] == "ASR_FAILED"
    assert "test-provider-key" not in response.text


def test_empty_or_control_character_transcripts(provider, monkeypatch):
    client, _ = provider
    monkeypatch.setattr(speech_asr, "_post", lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: {"output": {"text": "\x00 \x07"}}))
    response = client.post("/api/v1/agent/speech/transcribe", content=wav(), headers={**_headers(), **CONSENT})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "ASR_EMPTY"


def test_contact_annotations_are_dropped_from_hotwords():
    # The demo's contact list shows "王美（爱人）"; live ASR heard "王梅" until the
    # hotword was the spoken name itself.
    rows = [{"display_name": "王美（爱人）"}, {"name": "李四 (同事)"}, {"display_name": "张三"}, {"display_name": "（）"}]
    assert speech_asr.contact_names(rows) == ["王美", "李四", "张三"]


def test_hint_context_is_bounded_and_deduplicated():
    context = speech_asr.context_text(["李四", "李四", " ", "x" * 30] + [f"名{i}" for i in range(80)])
    assert context.count("李四") == 1 and "x" * 30 not in context
    assert context.count("名") <= 50
