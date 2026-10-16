"""Cloud speech recognition for the Gateway (Alibaba Model Studio Qwen ASR).

Android prefers its on-device recognizer; this path exists for devices without
one and runs only after the user consented to cloud recognition. The audio is
relayed by the Gateway, so the API key never ships in the app.

Boundaries kept here:
* The transcript is returned for the user to review in the input box. It is
  never sent as a message or treated as an approval by this module.
* ``enable_itn`` stays off: it would turn "一百五" into "150" and silently
  remove the ambiguity the intent parser asks the user to resolve.
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
SAMPLE_RATE = 16_000
MAX_SECONDS = 20
MAX_TRANSCRIPT_CHARS = 500
# Banking vocabulary given to the recogniser as context, alongside the user's
# own contact names, so payees and products are not heard as homophones.
BUSINESS_TERMS = ("转账", "收款人", "余额", "账单", "理财", "申购", "赎回", "风险测评", "挂失", "解挂", "冻结",
                  "解冻", "额度", "虚拟卡", "订阅", "代扣", "续费", "AA", "预约", "锁定", "活期")


def enabled() -> bool:
    return bool(os.getenv("DASHSCOPE_API_KEY", "").strip()) and os.getenv("BANK_ASR_ENABLED", "1") != "0"


def status() -> dict[str, Any]:
    return {"cloud_asr": enabled(), "format": "audio/wav", "sample_rate": SAMPLE_RATE, "max_seconds": MAX_SECONDS,
            "model": os.getenv("BANK_ASR_MODEL", DEFAULT_MODEL) if enabled() else None}


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


def context_text(names: Iterable[str]) -> str:
    """System context for the recogniser: business words and the caller's payee names."""

    unique = list(dict.fromkeys(name.strip() for name in names if isinstance(name, str) and 0 < len(name.strip()) <= 20))[:50]
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
    body = {
        "model": os.getenv("BANK_ASR_MODEL", DEFAULT_MODEL),
        "messages": [
            {"role": "system", "content": context_text(names)},
            {"role": "user", "content": [{"type": "input_audio", "input_audio": {
                "data": "data:audio/wav;base64," + base64.b64encode(audio).decode("ascii")}}]},
        ],
        "stream": False,
        "asr_options": {"language": "zh", "enable_itn": False},
    }
    base_url = os.getenv("BANK_ASR_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    try:
        timeout = max(3.0, min(float(os.getenv("BANK_ASR_TIMEOUT_SECONDS", "15")), 60.0))
    except ValueError:
        timeout = 15.0
    try:
        response = _post(f"{base_url}/chat/completions", timeout=timeout, json=body,
                         headers={"Authorization": f"Bearer {os.getenv('DASHSCOPE_API_KEY', '').strip()}"})
    except requests.RequestException as exc:
        raise SecurityError("语音识别服务暂时不可用，请稍后重试或输入文字", 502, "ASR_UNAVAILABLE") from exc
    if response.status_code >= 400:
        # The provider body may echo request details; only the status is surfaced.
        raise SecurityError("语音识别失败，请重试或输入文字", 502, "ASR_FAILED")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise SecurityError("语音识别结果无效，请重试", 502, "ASR_INVALID_RESPONSE") from exc
    if isinstance(content, list):  # tolerate the content-parts form
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(content or "")).strip()
    if not text:
        raise SecurityError("没有识别到语音内容，请靠近麦克风再说一遍", 422, "ASR_EMPTY")
    return text[:MAX_TRANSCRIPT_CHARS]


def contact_names(rows: Optional[Iterable[Any]]) -> list[str]:
    names = []
    for row in rows or []:
        if isinstance(row, dict):
            names.extend(str(row.get(key)) for key in ("display_name", "name", "alias") if row.get(key))
    return names
