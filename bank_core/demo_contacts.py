"""Narrow migration of the original demo's name/relationship mix-up."""
from __future__ import annotations

from typing import Any


def normalize_demo_contact_identity(state: dict[str, Any]) -> int:
    """Only correct built-in identity fields; retain user edits and deleted rows."""
    changed = 0

    def account(row: dict[str, Any]) -> None:
        nonlocal changed
        if row.get('name') == '王美（爱人）' and row.get('phone') == '13700137000':
            row['name'] = '王美'
            changed += 1

    def contacts(owner: str, rows: list[dict[str, Any]]) -> None:
        nonlocal changed
        for row in rows:
            if (row.get('account_id') == 'user_wife' and row.get('phone') == '13700137000'
                    and row.get('name') == '王美（爱人）' and row.get('source') in {'DEMO_SEED', 'TRANSFER_HISTORY'}):
                row['name'] = '王美'
                if owner == 'user_zhang' and row.get('relationship') == 'OTHER':
                    row['relationship'] = 'SPOUSE'
                changed += 1

    account(state.get('ACCOUNTS', {}).get('user_wife', {}))
    for owner, rows in state.get('USER_CONTACTS', {}).items():
        contacts(owner, rows)
    # Reset must not restore the same incorrect demo identity. Monetary baseline
    # fields, saved_at and all non-demo/user-edited contact fields remain intact.
    for owner, baseline in state.get('ACCOUNT_INITIAL_STATES', {}).items():
        data = baseline.get('data', {})
        if owner == 'user_wife':
            account(data.get('account', {}))
        contacts(owner, data.get('contacts', []))
    return changed
