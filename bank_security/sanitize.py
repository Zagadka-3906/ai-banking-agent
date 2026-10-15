"""Input cleaning, prompt-injection detection and audit redaction."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import deque
from typing import Any, Mapping, Optional

from bank_security.config import MAX_TEXT_LENGTH
from bank_security.errors import InjectionError


# Patterns are intentionally conservative and only target instructions that
# attempt to alter the agent's control plane.  Normal banking words such as
# “系统” or “验证码” alone are allowed.
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?", re.I),
    re.compile(r"忽略(?:之前|上面|此前).{0,12}(?:指令|规则|提示|要求)"),
    re.compile(r"(?:绕过|跳过|解除|取消).{0,12}(?:权限|风控|验证|安全)"),
    re.compile(r"(?:system|developer|assistant)\s*(?:prompt|message|指令)", re.I),
    re.compile(r"(?:reveal|show|print|泄露|输出).{0,20}(?:prompt|系统提示|密钥|token|凭证)", re.I),
    re.compile(r"(?:jailbreak|dan\s+mode|developer\s+mode|越狱|开发者模式)", re.I),
    re.compile(r"<\|\s*(?:system|assistant|tool|im_start|im_end)\s*\|>", re.I),
    # Delimiter/role tags used by the extraction prompt must remain data, not
    # a way for an utterance to close its sandbox and inject a new instruction.
    re.compile(r"</?\s*(?:user_utterance|system|developer|assistant|tool|instructions?)\b", re.I),
    re.compile(r"(?:tool[_ -]?call|function[_ -]?call|调用工具|执行代码|shell\s+command)", re.I),
    re.compile(r"(?:__import__|os\.system|subprocess|powershell|cmd\.exe|drop\s+table|union\s+select)", re.I),
]


def contains_prompt_injection(value: Any) -> bool:
    """Return whether *value* contains a control-plane injection attempt."""

    if value is None:
        return False
    try:
        text = unicodedata.normalize("NFKC", str(value))
        # Unicode format characters (for example ZERO WIDTH SPACE and
        # RIGHT-TO-LEFT OVERRIDE) are invisible in logs and can be inserted
        # between words to evade delimiter/role regexes.  They do not carry
        # useful meaning in a control instruction, so remove them before the
        # policy scan.  This keeps the original value untouched for ordinary
        # business storage while making the security decision canonical.
        text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    except Exception:
        return True
    if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in text):
        return True
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)


def sanitize_text(value: Any, *, field: str = "文本", max_length: int = MAX_TEXT_LENGTH) -> str:
    """Normalize and validate text received from a user/model.

    This function does not attempt to interpret natural language.  It only
    enforces a size/control-character boundary and rejects known attempts to
    manipulate the instruction hierarchy.
    """

    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    text = unicodedata.normalize("NFKC", value).strip()
    if not text:
        return ""
    if len(text) > max_length:
        raise InjectionError(f"{field}长度超过安全限制")
    if contains_prompt_injection(text):
        raise InjectionError(f"{field}包含疑似提示词注入或控制指令")
    return text


def redact(value: Any) -> Any:
    """Recursively redact credentials before putting data in an audit event."""

    sensitive = {
        "auth_token",
        "token",
        "x_auth_token",
        "mfa_token",
        "mfa_proof",
        "otp",
        "demo_code",
        "otp_digest",
        "sms_code",
        "secret",
        "password",
        "face_attestation",
        "authorization",
        "access_token",
        "api_key",
        "client_secret",
        "private_key",
        "cookie",
        "set-cookie",
    }
    if isinstance(value, Mapping):
        return {
            str(k): ("[REDACTED]" if str(k).lower() in sensitive else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple, deque)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        # Catch credentials passed under an unexpected metadata key as well as
        # the explicit key allow-list above.  Signed nonces/proofs are long
        # URL-safe strings containing a dot; API keys are typically long
        # high-entropy ASCII strings.  Short business identifiers and normal
        # Chinese descriptions are left untouched.
        compact = value.strip()
        credential_like = (
            len(compact) >= 32
            and bool(re.fullmatch(r"[A-Za-z0-9_\-.~+/=]+", compact))
            and ("." in compact or len(compact) >= 40)
        )
        if credential_like:
            return "[REDACTED]"
        if len(value) > 200:
            return value[:200] + "…"
    return value


def credential_fingerprint(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
