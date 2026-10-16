"""Cloud speech recognition for the Gateway (Alibaba Model Studio Qwen ASR).

Android prefers its on-device recognizer; this path exists for devices without
one and runs only after the user consented to cloud recognition. The audio is
relayed by the Gateway, so the API key never ships in the app.

Boundaries kept here:
* The transcript is returned for the user to review in the input box. It is
  never sent as a message or treated as an approval by this module.
* Spoken numbers: the parser asks about elliptical amounts such as "一百五".
  ``qwen3-asr-flash`` (OpenAI-compatible) keeps them as spoken because
  ``enable_itn`` stays off. ``qwen-audio-3.*`` (DashScope native) always writes
  digits ("150") and has no switch; the user still reviews the transcript in
  the input box and confirms the amount on the approval card.
* Audit records metadata only (duration, size, outcome), never audio or text.
"""

from __future__ import annotations

import base64
import os
import re
import struct
from typing import Any, Iterable, Optional

import requests

from security import SecurityError

DEFAULT_MODEL = "qwen-audio-3.0-asr-flash"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
# Qwen-Audio-3.x / Fun-ASR-Flash only answer on DashScope's native endpoint.
DEFAULT_NATIVE_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
NATIVE_MODEL_PREFIXES = ("qwen-audio-", "fun-asr-flash")
SAMPLE_RATE = 16_000
MAX_SECONDS = 20
MAX_TRANSCRIPT_CHARS = 500
# Banking vocabulary given to the recogniser as context, alongside the user's
# own contact names, so payees and products are not heard as homophones.
BUSINESS_TERMS = ("转账", "收款人", "余额", "账单", "理财", "申购", "赎回", "风险测评", "挂失", "解挂", "冻结",
                  "解冻", "额度", "虚拟卡", "订阅", "代扣", "续费", "AA", "预约", "锁定", "活期")


def model() -> str:
    return os.getenv("BANK_ASR_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def uses_native_api(name: str) -> bool:
    return name.startswith(NATIVE_MODEL_PREFIXES)


def enabled() -> bool:
    return bool(os.getenv("DASHSCOPE_API_KEY", "").strip()) and os.getenv("BANK_ASR_ENABLED", "1") != "0"


def status() -> dict[str, Any]:
    return {"cloud_asr": enabled(), "format": "audio/wav", "sample_rate": SAMPLE_RATE, "max_seconds": MAX_SECONDS,
            "model": model() if enabled() else None,
            # Whether "一百五" reaches the parser as spoken (see module docstring).
            "keeps_spoken_numbers": not uses_native_api(model()) if enabled() else None}


def wav_seconds(audio: bytes) -> float:
    """Validate the one accepted format (16 kHz mono 16-bit PCM WAV) and return its length."""

    def invalid(message: str) -> SecurityError:
        return SecurityError(message, 400, "INVALID_AUDIO")

    if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise invalid("仅支持 WAV 音频")
    offset, fmt, data_size = 12, None, None
    while offset + 8 <= len(audio):
        chunk_id, size = audio[offset:offset + 4], struct.unpack("<I", audio[offset + 4:offset + 8])[0]
        body = offset + 8
        if chunk_id == b"fmt " and size >= 16:
            fmt = struct.unpack("<HHIIHH", audio[body:body + 16])
        elif chunk_id == b"data":
            data_size = min(size, len(audio) - body)
            break
        offset = body + size + (size & 1)
    if fmt is None or data_size is None:
        raise invalid("WAV 文件结构不完整")
    audio_format, channels, rate, _, _, bits = fmt
    if (audio_format, channels, rate, bits) != (1, 1, SAMPLE_RATE, 16):
        raise invalid("请使用 16 kHz 单声道 16 位 PCM 录音")
    seconds = data_size / (SAMPLE_RATE * 2)
    if seconds < 0.3:
        raise invalid("录音过短，请重新说一遍")
    if seconds > MAX_SECONDS + 0.5:
        raise invalid(f"单次录音不超过 {MAX_SECONDS} 秒")
    return round(seconds, 2)


def _unique_names(names: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(name.strip() for name in names if isinstance(name, str) and 0 < len(name.strip()) <= 20))[:50]


def vocabulary(names: Iterable[str]) -> dict[str, int]:
    """Inline hotwords for the native API: the caller's payees first, then business words."""

    return {word: 5 for word in [*_unique_names(names), *BUSINESS_TERMS][:60]}


def context_text(names: Iterable[str]) -> str:
    """Free-text context for the OpenAI-compatible API: business words and payee names."""

    unique = _unique_names(names)
    lines = ["银行业务对话，可能出现的词：" + "、".join(BUSINESS_TERMS) + "。"]
    if unique:
        lines.append("用户的联系人：" + "、".join(unique) + "。")
    return "\n".join(lines)


def _post(url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: float) -> requests.Response:
    """Transport seam; tests replace it instead of reaching the network."""

    return requests.post(url, headers=headers, json=json, timeout=timeout)


def transcribe(audio: bytes, *, names: Iterable[str] = ()) -> str:
    """Return the transcript of a validated WAV clip, or raise ``SecurityError``."""

    if not enabled():
        raise SecurityError("云端语音识别未启用，请使用文字输入", 503, "ASR_DISABLED")
    wav_seconds(audio)
    names = list(names)
    name = model()
    data_url = "data:audio/wav;base64," + base64.b64encode(audio).decode("ascii")
    headers = {"Authorization": f"Bearer {os.getenv('DASHSCOPE_API_KEY', '').strip()}"}
    if uses_native_api(name):
        url = os.getenv("BANK_ASR_NATIVE_URL", DEFAULT_NATIVE_URL)
        headers["X-DashScope-SSE"] = "disable"
        body: dict[str, Any] = {
            "model": name,
            "input": {"messages": [{"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": data_url}}]}]},
            "parameters": {"format": "wav", "sample_rate": str(SAMPLE_RATE), "language_hints": ["zh"],
                           "vocabulary": vocabulary(names)},
        }
    else:
        url = os.getenv("BANK_ASR_BASE_URL", DEFAULT_BASE_URL).rstrip("/") + "/chat/completions"
        body = {
            "model": name,
            "messages": [
                # This endpoint rejects a plain-string system message; context goes in as a text part.
                {"role": "system", "content": [{"text": context_text(names)}]},
                {"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": data_url, "format": "wav"}}]},
            ],
            "stream": False,
            "asr_options": {"language": "zh", "enable_itn": False},
        }
    try:
        timeout = max(3.0, min(float(os.getenv("BANK_ASR_TIMEOUT_SECONDS", "15")), 60.0))
    except ValueError:
        timeout = 15.0
    try:
        response = _post(url, timeout=timeout, json=body, headers=headers)
    except requests.RequestException as exc:
        raise SecurityError("语音识别服务暂时不可用，请稍后重试或输入文字", 502, "ASR_UNAVAILABLE") from exc
    if response.status_code >= 400:
        # The provider body may echo request details; only the status is surfaced.
        raise SecurityError("语音识别失败，请重试或输入文字", 502, "ASR_FAILED")
    try:
        payload = response.json()
        content = payload["output"]["text"] if uses_native_api(name) else payload["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise SecurityError("语音识别结果无效，请重试", 502, "ASR_INVALID_RESPONSE") from exc
    if isinstance(content, list):  # tolerate the content-parts form
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(content or "")).strip()
    if not text:
        raise SecurityError("没有识别到语音内容，请靠近麦克风再说一遍", 422, "ASR_EMPTY")
    return text[:MAX_TRANSCRIPT_CHARS]


def contact_names(rows: Optional[Iterable[Any]]) -> list[str]:
    """Spoken forms of the caller's contacts.

    Display names may carry an annotation such as "王美（爱人）"; the person says
    "王美", so the bracketed part is dropped before it becomes a hotword.
    """

    names = []
    for row in rows or []:
        if isinstance(row, dict):
            for key in ("display_name", "name", "alias"):
                if row.get(key):
                    names.append(re.sub(r"\s*[（(][^）)]*[）)]\s*", "", str(row[key])).strip())
    return [name for name in names if name]
