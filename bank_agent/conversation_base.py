"""Shared pieces of the conversation service: limits, ids, stage labels and its error type."""

from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from typing import Optional

from security import SecurityError, sanitize_text

try:  # The graph is optional so this module stays importable in minimal setups.
    from agent import build_graph, channel_session
except Exception:  # pragma: no cover - dependency guidance path
    build_graph = None  # type: ignore[assignment]

    @contextmanager
    def channel_session(token: Optional[str]):  # type: ignore[misc]
        """Fallback when the graph module is unavailable: present nothing."""

        yield

try:
    from langgraph.checkpoint.memory import MemorySaver
except Exception:  # pragma: no cover
    MemorySaver = None  # type: ignore[assignment]

try:
    from langgraph.types import Command
except Exception:  # pragma: no cover
    Command = None  # type: ignore[assignment]


logger = logging.getLogger("mock_banking.conversation")


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


# Approval lifetime.  It must stay *inside* the core's authorization nonce TTL
# (``BANK_TOKEN_TTL_SECONDS``, 120 s by default) because the handle points at a
# nonce that the core will refuse once expired.  A longer handle would let a
# user approve an operation the core has already invalidated, producing a
# confusing failure at settle time instead of an honest "please start over".
APPROVAL_TTL_SECONDS = _env_int("BANK_APPROVAL_TTL_SECONDS", 110, 30, 600)
APPROVAL_MAX_ATTEMPTS = _env_int("BANK_APPROVAL_MAX_ATTEMPTS", 5, 1, 20)
TURN_CACHE_TTL_SECONDS = _env_int("BANK_TURN_CACHE_TTL_SECONDS", 900, 60, 86_400)
TURN_CACHE_MAX_RECORDS = _env_int("BANK_TURN_CACHE_MAX_RECORDS", 2_000, 16, 200_000)

# Namespace for Android conversation thread ids in the checkpointer.
CHANNEL_NAMESPACE = "app"

_CONVERSATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# User-facing labels for the graph nodes, used by the streamed variant of a turn.
# Only nodes that actually run are reported, so the client shows real progress
# rather than a decorative animation.
_STAGE_LABELS: dict[str, str] = {
    "intent_parser": "正在解析您的指令",
    "risk_checker": "正在进行风控预检",
    "human_review": "等待您的确认",
    "executor": "正在执行",
    "responder": "正在整理结果",
}


class ConversationError(SecurityError):
    """A channel-level failure that maps directly onto an HTTP response."""


def conversation_thread_id(principal: str, conversation_id: str) -> str:
    """Derive the checkpointer thread id for one principal's conversation.

    The principal comes from the authenticated session, never from the request
    body, and it is part of the key - so supplying someone else's
    ``conversation_id`` cannot address their thread.
    """

    if not _CONVERSATION_ID_RE.match(conversation_id or ""):
        raise ConversationError("会话编号格式无效", 400, "INVALID_CONVERSATION_ID")
    safe_principal = sanitize_text(principal, field="用户标识", max_length=128)
    return f"{CHANNEL_NAMESPACE}:{safe_principal}:{conversation_id}"
