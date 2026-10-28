"""Public model defaults and explicit provider options; never stores credentials."""
from __future__ import annotations

import os

DEFAULT_MODEL = 'deepseek-flash'
DEFAULT_THINKING = 'disabled'


def thinking_options(value: str | None = None) -> dict:
    mode = (os.getenv('BANK_MODEL_THINKING', DEFAULT_THINKING) if value is None else value).strip().lower()
    if mode not in {'', 'enabled', 'disabled'}:
        raise ValueError('INVALID_MODEL_THINKING')
    return {'thinking': {'type': mode}} if mode else {}
