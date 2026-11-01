"""Contact-book domain rules: public views, link verification, consent and AA eligibility."""

from __future__ import annotations

import uuid
import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from bank_core.ledger import (
    ACCOUNTS,
    CONTACT_RELATIONSHIP_LABELS,
    CONTACTS,
    CONTACT_MAX_PER_USER,
    CONTACT_PHONE_RE,
    LINK_UNVERIFIED,
    LINK_VERIFIED,
    USER_CONTACTS,
    TRANSACTIONS,
    demo_contact_rows,
    transfer_contact_id,
)
from bank_core.models import (
    StrictModel,
)
from security import (
    STATE_LOCK,
)


class ContactAddRequest(StrictModel):
    name: str
    phone: str
    relationship: str
    # A birthday is optional display/reminder data, not an authorization input;
    # it is validated as a plain calendar date and never accepted in the future.
    birthday: Optional[str] = None


def contact_public_view(row: dict[str, Any], *, caller_id: Optional[str] = None) -> dict[str, Any]:
    """Client-safe projection of one contact-book row (no account internals).

    ``account_name`` is the *payee account holder's* real name, so it is only
    disclosed once that account has actually consented to being linked (see
    ``_link_verification``).  Otherwise the caller sees the name they typed:
    anyone can type someone else's phone number, and echoing the holder's real
    name back would turn the contact book into a name-lookup oracle over the
    account database.

    Consent is recomputed on every read rather than replayed from the stored
    row, because it can be granted *after* the contact was created: the owner
    registering this caller's number later must take effect immediately.
    """

    account_id = str(row.get("account_id") or "")
    verification = _contact_link_status(row, caller_id=caller_id)
    link_verified = verification == LINK_VERIFIED
    linked_name = ACCOUNTS.get(account_id, {}).get("name") if account_id and link_verified else None
    return {
        "contact_id": row.get("contact_id"),
        "name": row.get("name"),
        "phone": row.get("phone"),
        "relationship": row.get("relationship"),
        "relationship_label": CONTACT_RELATIONSHIP_LABELS.get(str(row.get("relationship")), str(row.get("relationship"))),
        "birthday": row.get("birthday"),
        "account_id": account_id or None,
        "linked_existing": bool(row.get("linked_existing")),
        "account_name": linked_name,
        "link_verification": verification,
        "link_verified": link_verified,
        "created_at": row.get("created_at"),
    }


def _link_verification(account_id: str, phone: str, name: str, *, caller_id: Optional[str] = None) -> str:
    """Record the link consent state for a freshly added contact.

    Kept for the persisted row; readers should call ``_contact_link_status`` so
    a later grant is not missed.
    """

    del name
    return _contact_consent(account_id, phone, caller_id=caller_id)


def _contact_consent(account_id: str, phone: str, *, caller_id: Optional[str]) -> str:
    """Whether a phone-to-account link carries the account owner's consent.

    A phone number alone is *not* proof of anything: it is a string the caller
    typed.  Linking it is fine for a payee the caller is paying (their own
    money, their own choice), but it must not let them target that account
    with an AA request or infer the holder's real name.

    Consent exists when the two parties have each registered the other: the
    account holder's own book contains this phone, or the caller is paying with
    the number registered on their own account.
    """

    caller = ACCOUNTS.get(str(caller_id or ""))
    if caller and str(caller.get("phone") or "") == phone:
        return LINK_VERIFIED
    if caller_id and str(caller_id) == account_id:
        return LINK_VERIFIED
    for row in USER_CONTACTS.get(account_id, []):
        if row.get('source') in {'TRANSFER_HISTORY', 'DEMO_SEED'}:
            continue
        if str(row.get("phone") or "") == phone:
            return LINK_VERIFIED
    # The caller's own number for this account may have been registered by the
    # holder only after the contact was first added, so treat "the book the
    # contact points at now lists the caller's number" as consent too.
    if caller:
        caller_phone = str(caller.get("phone") or "")
        for row in USER_CONTACTS.get(account_id, []):
            if row.get('source') in {'TRANSFER_HISTORY', 'DEMO_SEED'}:
                continue
            if caller_phone and str(row.get("phone") or "") == caller_phone:
                return LINK_VERIFIED
    return LINK_UNVERIFIED


def _contact_link_status(row: Mapping[str, Any], *, caller_id: Optional[str] = None) -> str:
    """Live consent state for a stored contact-book row."""

    account_id = str(row.get("account_id") or "")
    if not account_id:
        return LINK_UNVERIFIED
    owner_id = str(row.get("owner_id") or caller_id or "")
    return _contact_consent(account_id, str(row.get("phone") or ""), caller_id=owner_id or None)


def aa_request_eligible(user_id: str, name: str) -> bool:
    """Whether ``name`` resolves to a verified recipient of an AA request."""

    with STATE_LOCK:
        for row in USER_CONTACTS.get(user_id, []):
            if name in {str(row.get("name") or ""), str(row.get("phone") or "")}:
                if str(row.get("account_id") or "") not in ACCOUNTS:
                    return False
                if row.get('source') in {'TRANSFER_HISTORY', 'DEMO_SEED'} and CONTACTS.get(name) == row.get('account_id'):
                    return True  # Existing built-in permission, not consent from an automatic row.
                return _contact_consent(str(row.get("account_id") or ""), str(row.get("phone") or ""), caller_id=user_id) == LINK_VERIFIED
    return name in CONTACTS


def resolve_contact_account(name: str, phone: str, *, caller_id: Optional[str] = None) -> tuple[str, bool, str]:
    """Return the payee account a contact points at, provisioning one if needed.

    A phone that already belongs to a demo account links to it and records
    whether the owner consented (see ``_link_verification``).  Anything else
    gets a zero-balance shadow payee account so the transfer settles end to
    end; its password digest is deliberately unusable, so a contact can never
    be turned into a login.
    """

    with STATE_LOCK:
        linked = next(
            (uid for uid, account in ACCOUNTS.items() if str(account.get("phone") or "") == phone and str(account.get("status")) == "ACTIVE"),
            None,
        )
        if linked:
            return linked, True, _link_verification(linked, phone, name, caller_id=caller_id)
        account_id = f"ext{uuid.uuid4().hex[:12]}"
        ACCOUNTS[account_id] = {
            "card_no": "----",
            "name": name,
            "phone": phone,
            "balance": 0.0,
            "locked_amount": 0.0,
            "status": "ACTIVE",
            "credential_version": 1,
            # Unusable digest: argon2 verification fails on it, so the shadow
            # payee cannot be logged into; a bare ``""`` would trip the ledger
            # store's structural validation instead.
            "password_hash": "!contact-book-no-login",
            "source": "CONTACT_BOOK",
            "transfer_contacts_backfilled": True,
            "demo_contacts_seeded": True,
        }
        return account_id, False, LINK_UNVERIFIED


def _save_transfer_contact(owner_id: str, counterparty_id: str) -> bool:
    """Caller holds STATE_LOCK and the transfer's ledger transaction."""
    account = ACCOUNTS.get(counterparty_id)
    if owner_id == counterparty_id or owner_id not in ACCOUNTS or not account:
        return False
    name, phone = account.get('name'), account.get('phone')
    if (not isinstance(name, str) or not name.strip() or len(name) > 120
            or not isinstance(phone, str) or not CONTACT_PHONE_RE.fullmatch(phone)):
        return False
    rows = USER_CONTACTS.get(owner_id, [])
    if (any(row.get('account_id') == counterparty_id or row.get('phone') == phone for row in rows)
            or len(rows) >= CONTACT_MAX_PER_USER):
        return False
    USER_CONTACTS.setdefault(owner_id, []).append({
        'contact_id': transfer_contact_id(owner_id, counterparty_id),
        'owner_id': owner_id, 'name': name, 'phone': phone, 'relationship': 'OTHER', 'birthday': None,
        'account_id': counterparty_id, 'linked_existing': account.get('source') != 'CONTACT_BOOK',
        'link_verification': LINK_UNVERIFIED, 'source': 'TRANSFER_HISTORY',
        'created_at': datetime.now(timezone.utc).isoformat(),
    })
    return True


def backfill_transfer_contacts(*, user_ids: tuple[str, ...] | None = None) -> int:
    """Migrate trusted statement counterparties once; never turn GET into a write.

    Old rows have only a rendered party name. Resolve those only when the
    canonical account name is unique; current rows carry an explicit ID.
    The per-owner marker is persisted with deletions, so a restart cannot
    recreate a contact the user removed. A later new transfer can save it again.
    """
    with STATE_LOCK:
        owners = {uid for uid in (user_ids if user_ids is not None else tuple(ACCOUNTS))
                  if uid in ACCOUNTS and ACCOUNTS[uid].get('transfer_contacts_backfilled') is not True}
        names: dict[str, list[str]] = {}
        for uid, account in ACCOUNTS.items():
            if isinstance(account.get('name'), str):
                names.setdefault(account['name'], []).append(uid)
        added = 0
        for tx in TRANSACTIONS:
            owner = tx.get('account_id')
            amount = tx.get('amount')
            if (owner not in owners or tx.get('category') not in {'转账支出', '转账收入', 'AA 收款'}
                    or not isinstance(amount, (int, float)) or isinstance(amount, bool) or not math.isfinite(amount) or amount <= 0
                    or tx.get('status', 'SUCCESS') not in {'SUCCESS', 'SUCCEEDED', 'COMPLETED', 'SETTLED'}):
                continue
            counterparty = tx.get('counterparty_id')
            if counterparty is None:
                merchant = str(tx.get('merchant') or '')
                pattern = (r'转账给 (.+)' if tx['category'] == '转账支出' else
                           r'AA 收款 - (.+)' if tx['category'] == 'AA 收款' else r'来自 (.+) 的转账')
                match = re.fullmatch(pattern, merchant)
                candidates = names.get(match[1], []) if match else []
                counterparty = candidates[0] if len(candidates) == 1 else None
            if isinstance(counterparty, str):
                added += int(_save_transfer_contact(owner, counterparty))
        for owner in owners:
            ACCOUNTS[owner]['transfer_contacts_backfilled'] = True
        return added


def seed_demo_contacts() -> int:
    """Upgrade older books once, preserving edits and later explicit deletions."""
    added = 0
    with STATE_LOCK:
        for owner, account in ACCOUNTS.items():
            if account.get('source') == 'CONTACT_BOOK' or account.get('demo_contacts_seeded') is True:
                continue
            for row in demo_contact_rows(owner):
                rows = USER_CONTACTS.get(owner, [])
                if (len(rows) >= CONTACT_MAX_PER_USER or any(
                    old.get('account_id') == row['account_id'] or old.get('phone') == row['phone'] for old in rows
                )):
                    continue
                USER_CONTACTS.setdefault(owner, []).append(row)
                added += 1
            account['demo_contacts_seeded'] = True
    return added


def remember_transfer_parties(sender_id: str, receiver_id: str) -> None:
    """Save both parties inside the same atomic boundary as actual settlement."""
    with STATE_LOCK:
        _save_transfer_contact(sender_id, receiver_id)
        _save_transfer_contact(receiver_id, sender_id)
