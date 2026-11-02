"""Literal card references; ownership and unique matches must come from Core."""
from __future__ import annotations

import re

CARD_ID_PATTERN = r'(?i:(?<![A-Za-z0-9])card\s*[-_－]?\s*([0-9a-z]{2,})(?![A-Za-z0-9]))'
CARD_TAIL_PATTERN = r'(?:尾号|末四位|后四位)\s*(?:为|是)?\s*([0-9]{4})(?![0-9])'
_SHORT_ID = r'(?<![A-Za-z0-9])(?:[0-9a-fA-F]{8}|[0-9]{3,8})(?![A-Za-z0-9])'
_SELECTION = rf'(?:{CARD_ID_PATTERN}|{CARD_TAIL_PATTERN}|{_SHORT_ID})'


def card_selection_answer(text: str) -> bool:
    """A bare answer to a card question, never a new query or transfer."""
    return re.fullmatch(
        rf'\s*(?:就)?(?:用|使用|选|选择|卡片编号(?:是|为)?|卡号(?:是|为)?)?\s*'
        rf'{_SELECTION}(?:\s*(?:或|或者|和|、)\s*{_SELECTION})*'
        r'\s*(?:的)?(?:卡片|卡)?(?:吧|就好|就行)?[。！!，,]?\s*', text,
    ) is not None


def card_references(text: str, *, allow_short: bool = False) -> list[tuple[str, str]]:
    # A card number in the transfer memo is data, not a payment-card choice.
    text = re.split(r'备注|附言', text, maxsplit=1)[0]
    refs = [('id', match.group(1).upper()) for match in re.finditer(CARD_ID_PATTERN, text)]
    refs += [('tail', match.group(1)) for match in re.finditer(CARD_TAIL_PATTERN, text)]
    if not refs and allow_short and card_selection_answer(text):
        refs = [('id', match.group().upper()) for match in re.finditer(_SHORT_ID, text)]
    return list(dict.fromkeys(refs))
