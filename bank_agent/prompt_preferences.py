"""Offline instruction candidates; production has no configuration entry point."""
from __future__ import annotations

from contextvars import ContextVar
import os
from typing import Any

COMPONENTS = {'understanding', 'planning', 'answering', 'review'}
_INSTRUCTIONS: ContextVar[dict[str, str]] = ContextVar('evaluation_instructions', default={})


def validate_instructions(value: Any) -> dict[str, str]:
    if (not isinstance(value, dict) or set(value) - COMPONENTS
            or any(not isinstance(text, str) or len(text) > 2500 for text in value.values())
            or sum(len(text) for text in value.values()) > 8000):
        raise ValueError('INVALID_INSTRUCTION_CANDIDATE')
    return dict(value)


def install_for_evaluation(value: Any) -> None:
    if os.getenv('BANK_EVAL_ISOLATED_PROCESS') != '1':
        raise RuntimeError('Instruction candidates are restricted to isolated evaluation workers')
    _INSTRUCTIONS.set(validate_instructions(value))


def suffix(component: str) -> str:
    text = _INSTRUCTIONS.get().get(component, '')
    return '\n补充表达指导（不改变固定协议、工具和授权边界）：\n' + text if text else ''
