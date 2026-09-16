"""多模态票据助手的严格校验与可复现 Mock 抽取。

这里只保存脱敏元数据；上传的原始媒体在校验和抽取完成后立即丢弃。
"""

from __future__ import annotations

import base64
import hashlib
import re
import uuid
from typing import Any

MAX_ARTIFACT_BYTES = 256 * 1024
ALLOWED_MEDIA_TYPES = {"image/png", "image/jpeg", "application/pdf", "audio/wav", "audio/mpeg"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

# Tiny deterministic fixtures shared by the Android UI tests.
FIXTURES: dict[str, dict[str, Any]] = {
    "431ced6916a2a21a156e38701afe55bbd7f88969fbbfc56d7fe099d47f265460": {
        "category": "差旅-住宿", "amount": 680.0, "currency": "CNY", "merchant": "Mock 京都酒店",
        "invoice_number": "MOCK-INV-001", "confidence": 0.99,
    },
    "d54bf4c173cf64a7880b8cda4c9a0b3ff8b166ce8f70545399d8f59af2effbcd": {
        "category": "餐饮", "amount": 128.0, "currency": "CNY", "merchant": "Mock 餐厅",
        "invoice_number": "MOCK-INV-002", "confidence": 0.98,
    },
    "337ca484f7d44bde1e15fcc7eb62d870cedc77360db0c0a5241e0158afd74f9b": {
        "category": "交通", "amount": 45.0, "currency": "CNY", "merchant": "Mock 机场快线",
        "invoice_number": None, "confidence": 0.97,
    },
}

def _magic_matches(media_type: str, data: bytes) -> bool:
    return (
        (media_type == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n"))
        or (media_type == "image/jpeg" and data.startswith(b"\xff\xd8\xff"))
        or (media_type == "application/pdf" and data.startswith(b"%PDF-"))
        or (media_type == "audio/wav" and len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE")
        or (media_type == "audio/mpeg" and (data.startswith(b"ID3") or data[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}))
    )

def validate_upload(media_type: str, content_base64: str, sha256: str) -> tuple[bytes, str]:
    if media_type not in ALLOWED_MEDIA_TYPES:
        raise ValueError("UNSUPPORTED_MEDIA_TYPE")
    if not content_base64:
        raise ValueError("EMPTY_CONTENT")
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        raise ValueError("INVALID_SHA256")
    try:
        data = base64.b64decode(content_base64, validate=True)
    except (ValueError, TypeError):
        raise ValueError("INVALID_BASE64") from None
    if not data:
        raise ValueError("EMPTY_CONTENT")
    if len(data) > MAX_ARTIFACT_BYTES:
        raise ValueError("ARTIFACT_TOO_LARGE")
    if not _magic_matches(media_type, data):
        raise ValueError("MIME_MAGIC_MISMATCH")
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256:
        raise ValueError("SHA256_MISMATCH")
    return data, digest

def extract_mock(digest: str) -> dict[str, Any]:
    fact = FIXTURES.get(digest)
    if fact is None:
        return {"recognition_mode": "MOCK_RULES", "provenance": "UNVERIFIED_UPLOAD", "confidence": 0.0,
                "status": "NEEDS_REVIEW", "reimbursement_draft": {"status": "NEEDS_REVIEW"},
                "funds_debited": False}
    result = {"recognition_mode": "MOCK_RULES", "provenance": "MOCK_FIXTURE", **fact,
              "status": "DRAFT", "funds_debited": False}
    result["reimbursement_draft"] = {"status": "DRAFT", "category": fact["category"],
                                     "amount": fact["amount"], "currency": fact["currency"],
                                     "merchant": fact["merchant"]}
    return result

def new_artifact_id() -> str:
    return f"art_{uuid.uuid4().hex}"
