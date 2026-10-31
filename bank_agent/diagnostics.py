"""Opt-in evaluation diagnostics; production never writes these sinks."""
from __future__ import annotations

import json
import os
from typing import Any, Mapping


def capture_rejection(state: Mapping[str, Any] | None, code: str, attempt: int, answer: Any, **extra: Any) -> None:
    """Append a bounded rejection record when BANK_ANSWER_REJECTION_CAPTURE is set.

    The audit trail must never carry rejected model text and the trial record
    only exposes codes, so the evaluation harness opts into this separate sink
    to keep the exact wording available for diagnosis.  Failures to write are
    silently ignored: the capture is diagnostics, never a scoring input.
    """
    sink = os.getenv('BANK_ANSWER_REJECTION_CAPTURE')
    if not sink:
        return
    record = {'request_id': (state or {}).get('request_id'), 'code': code, 'attempt': attempt,
              'answer': str(answer or '')[:1200], **extra}
    try:
        with open(sink, 'a', encoding='utf8') as log:
            log.write(json.dumps(record, ensure_ascii=False) + '\n')
    except OSError:
        pass
