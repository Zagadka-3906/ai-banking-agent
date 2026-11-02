"""In-memory demo ledger: accounts, contacts, transfers, locks and other domain collections."""

from __future__ import annotations

import os
import re
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from notification_outbox import OUTBOX
from scheduler import TASK_STORE
from security import (
    STATE_LOCK,
)

# ---------------------------------------------------------------------------
# In-memory demo ledger (public names retained for backwards compatibility)
# ---------------------------------------------------------------------------
# Every demo account shares one password.  The argon2id digest is baked in
# rather than computed at import: hashing costs ~33 ms per call, and a fixed
# digest keeps start-up (and therefore every test run) deterministic.  A real
# deployment provisions credentials through an enrolment flow, never a source
# constant.  The plaintext (`Bank@2026`) is documented in .env.example.
DEMO_PASSWORD_HASH = "$argon2id$v=19$m=65536,t=3,p=4$01eEUN2Ii59Smsmuoc9mhg$O+kWnd/qcEgxrcodomCtjb5PgtkprfAfvHOfDqFEyi8"

ACCOUNTS: dict[str, dict[str, Any]] = {
    "user_zhang": {"card_no": "622202001", "name": "张三", "phone": "13800138000", "balance": 15000.0, "locked_amount": 0.0, "status": "ACTIVE", "credential_version": 1, "password_hash": DEMO_PASSWORD_HASH},
    "user_li": {"card_no": "622202002", "name": "李四", "phone": "13900139000", "balance": 2000.0, "locked_amount": 0.0, "status": "ACTIVE", "credential_version": 1, "password_hash": DEMO_PASSWORD_HASH},
    "user_wife": {"card_no": "622202003", "name": "王美", "phone": "13700137000", "balance": 5000.0, "locked_amount": 0.0, "status": "ACTIVE", "credential_version": 1, "password_hash": DEMO_PASSWORD_HASH},
}

CONTACTS: dict[str, str] = {
    "张三": "user_zhang",
    "李四": "user_li",
    "爱人": "user_wife",
    "老婆": "user_wife",
    "王美": "user_wife",
    "王美（爱人）": "user_wife",
    "13900139000": "user_li",
    "13700137000": "user_wife",
}

# Per-user contact book: manual entries and actual transfer counterparties
# (name, phone, relationship, birthday). A matching existing account is
# *linked* to it; anything else gets a zero-balance shadow payee account so a
# transfer to the new contact settles end to end in the mock.  Shadow accounts
# carry an unusable password digest, so enrollment can never become a login.
USER_CONTACTS: dict[str, list[dict[str, Any]]] = {}
# Panel-only reset baselines share the ledger's signed atomic commit. Never
# expose these password-bearing records through customer or panel read APIs.
ACCOUNT_INITIAL_STATES: dict[str, dict[str, Any]] = {}

# 关系词表：家人细分为爱人/兄弟/儿子/女儿/父母。录入接受中文标签或规范码，
# 存储统一为规范码；同一张表也用于“按称呼转账”时的称呼归一。
CONTACT_RELATIONSHIPS = {
    "爱人": "SPOUSE", "老婆": "SPOUSE", "媳妇": "SPOUSE", "妻子": "SPOUSE", "夫人": "SPOUSE", "丈夫": "SPOUSE", "老公": "SPOUSE",
    "兄弟": "SIBLING", "兄妹": "SIBLING", "姐妹": "SIBLING", "哥哥": "SIBLING", "弟弟": "SIBLING", "姐姐": "SIBLING", "妹妹": "SIBLING",
    "儿子": "SON",
    "女儿": "DAUGHTER",
    "父亲": "PARENT", "爸爸": "PARENT", "母亲": "PARENT", "妈妈": "PARENT", "父母": "PARENT",
    "家人": "FAMILY", "亲人": "FAMILY",
    "朋友": "FRIEND", "好友": "FRIEND",
    "同事": "COLLEAGUE",
    "其他": "OTHER",
}
CONTACT_RELATIONSHIP_LABELS = {"SPOUSE": "爱人", "SIBLING": "兄弟", "SON": "儿子", "DAUGHTER": "女儿", "PARENT": "父母", "FAMILY": "家人", "FRIEND": "朋友", "COLLEAGUE": "同事", "OTHER": "其他"}

# 转账称呼比录入更宽：用户嘴里说出来的亲属叫法都要能归到关系类别。
CONTACT_RELATION_SYNONYMS: dict[str, str] = dict(CONTACT_RELATIONSHIPS)
CONTACT_RELATION_SYNONYMS.update({
    "太太": "SPOUSE", "先生": "SPOUSE", "内人": "SPOUSE",
    "兄": "SIBLING", "弟": "SIBLING", "姐": "SIBLING", "妹": "SIBLING",
    "闺女": "DAUGHTER",
    "爸": "PARENT", "妈": "PARENT",
    "家里": "FAMILY",
})
CONTACT_PHONE_RE = re.compile(r"^\d{5,20}$")
CONTACT_BIRTHDAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Same reasoning as CARD_MAX_PER_USER: the book is part of the persisted ledger
# and the snapshot is rewritten in full on every mutation, so an uncapped add is
# unbounded growth with quadratic disk work - and each new non-matching phone
# also provisions an account record.
CONTACT_MAX_PER_USER = max(1, int(os.getenv("BANK_MAX_CONTACTS_PER_USER", "200")))
# Whether a contact-book entry that resolves to a *real* account carries that
# account owner's consent.  A typed phone number proves nothing on its own, so
# a provisional link is good enough to pay *to* (the caller's own money) but
# never proof of account ownership; see ``aa_request_eligible``.
LINK_VERIFIED = "OWNER_CONSENTED"
LINK_UNVERIFIED = "PROVISIONAL"

DAILY_TRANSFERS: dict[str, float] = {"user_zhang": 0.0}
DAILY_TRANSFER_DATE: dict[str, str] = {}
# Individual lock records make rollback/unlock auditable and prevent a caller
# from releasing another user's reserved funds.
LOCKS: dict[str, dict[str, Any]] = {}
SUBSCRIPTIONS: dict[str, list[dict[str, Any]]] = {
    "user_zhang": [
        {"subscription_id": "SUB-APPLE", "merchant": "Apple 订阅", "amount": 15.0, "next_charge": (datetime.now(timezone(timedelta(hours=8))).date() + timedelta(days=1)).isoformat(), "status": "ACTIVE"},
        {"subscription_id": "SUB-VIDEO", "merchant": "视频会员", "amount": 25.0, "next_charge": (datetime.now(timezone(timedelta(hours=8))).date() + timedelta(days=3)).isoformat(), "status": "ACTIVE"},
    ]
}
SCHEDULED_TRANSFERS: list[dict[str, Any]] = []
SPLIT_REQUESTS: list[dict[str, Any]] = []
MOCK_ORDERS: list[dict[str, Any]] = []
TRANSACTIONS: list[dict[str, Any]] = [
    {"tx_id": "TX1000", "account_id": "user_zhang", "merchant": "咖啡店", "category": "餐饮", "amount": 28.0, "time": f"{datetime.now().strftime('%Y-%m-%d')} 09:15:00"},
    {"tx_id": "TX1001", "account_id": "user_zhang", "merchant": "美团外卖", "category": "餐饮", "amount": 45.0, "time": "2026-08-01 12:30:00"},
    {"tx_id": "TX1002", "account_id": "user_zhang", "merchant": "Apple 订阅", "category": "订阅代扣", "amount": 15.0, "time": "2026-08-03 08:00:00"},
    {"tx_id": "TX1003", "account_id": "user_zhang", "merchant": "海底捞火锅", "category": "餐饮", "amount": 320.0, "time": "2026-08-05 19:20:00"},
    {"tx_id": "TX1004", "account_id": "user_zhang", "merchant": "境外未知商户", "category": "可疑交易", "amount": 999.0, "time": "2026-08-07 03:15:00", "card_id": "CARD-001", "country": "US"},
    *[{"tx_id": f"TX-CLOUD-{offset}", "account_id": "user_zhang", "merchant": "云端存储", "category": "订阅代扣", "amount": 19.9, "time": f"{(datetime.now().date() - timedelta(days=offset)).isoformat()} 09:00:00"} for offset in (60, 30, 0)],
]
DISPUTE_CASES: list[dict[str, Any]] = []

DEMO_ACCOUNT_IDS = ('user_zhang', 'user_li', 'user_wife')


def transfer_contact_id(owner_id: str, counterparty_id: str) -> str:
    return 'CTT' + uuid.uuid5(uuid.NAMESPACE_URL, f'bank-transfer-contact:{owner_id}:{counterparty_id}').hex[:20].upper()


def demo_contact_rows(owner_id: str) -> list[dict[str, Any]]:
    return [{
        'contact_id': transfer_contact_id(owner_id, uid), 'owner_id': owner_id,
        'name': ACCOUNTS[uid]['name'], 'phone': ACCOUNTS[uid]['phone'],
        'relationship': 'SPOUSE' if {owner_id, uid} == {'user_zhang', 'user_wife'} else 'OTHER', 'birthday': None, 'account_id': uid,
        'linked_existing': True, 'link_verification': LINK_UNVERIFIED, 'source': 'DEMO_SEED',
        'created_at': datetime.now(timezone.utc).isoformat(),
    } for uid in DEMO_ACCOUNT_IDS if uid != owner_id and uid in ACCOUNTS]


for _owner_id in DEMO_ACCOUNT_IDS:
    USER_CONTACTS[_owner_id] = demo_contact_rows(_owner_id)[:CONTACT_MAX_PER_USER]
    ACCOUNTS[_owner_id]['demo_contacts_seeded'] = True
    ACCOUNTS[_owner_id]['transfer_contacts_backfilled'] = True

# Ledger collections by name, for the transaction snapshot below and the
# durable store. Every module that owns ledger state registers its containers
# here at import; ``main`` imports them all before serving a request.
LEDGER_COLLECTIONS: dict[str, Any] = {}


def register_collections(**containers: Any) -> None:
    LEDGER_COLLECTIONS.update(containers)


register_collections(
    ACCOUNT_INITIAL_STATES=ACCOUNT_INITIAL_STATES,
    ACCOUNTS=ACCOUNTS, CONTACTS=CONTACTS, USER_CONTACTS=USER_CONTACTS,
    DAILY_TRANSFERS=DAILY_TRANSFERS, DAILY_TRANSFER_DATE=DAILY_TRANSFER_DATE, LOCKS=LOCKS,
    SUBSCRIPTIONS=SUBSCRIPTIONS, SCHEDULED_TRANSFERS=SCHEDULED_TRANSFERS, SPLIT_REQUESTS=SPLIT_REQUESTS,
    MOCK_ORDERS=MOCK_ORDERS, DISPUTE_CASES=DISPUTE_CASES, TRANSACTIONS=TRANSACTIONS, OUTBOX=OUTBOX,
)


@contextmanager
def ledger_transaction():
    """Best-effort atomicity for the in-memory demo ledger.

    Production deployments must replace this shim with a database transaction
    and an outbox.  Keeping the snapshot boundary here nevertheless prevents
    a task-store or validation failure from leaving half-applied balance,
    lock, schedule, holding, or subscription mutations in the competition
    sandbox.
    """

    names = (
        "ACCOUNTS",
        "USER_CONTACTS",
        "DAILY_TRANSFERS",
        "DAILY_TRANSFER_DATE",
        "CARD_DAILY_SPENT",
        "CARD_DAILY_SPENT_DATE",
        "LOCKS",
        "WEALTH_HOLDINGS",
        "CARDS",
        "SUBSCRIPTIONS",
        "SCHEDULED_TRANSFERS",
        "SPLIT_REQUESTS",
        "MOCK_ORDERS",
        "DISPUTE_CASES",
        "TRANSACTIONS",
        "OUTBOX",
        "WEALTH_RISK_PROFILES",
    )
    with STATE_LOCK:
        snapshots = {name: deepcopy(LEDGER_COLLECTIONS[name]) for name in names}
        task_snapshot = TASK_STORE.snapshot()
        try:
            yield
        except Exception:
            for name, snapshot in snapshots.items():
                current = LEDGER_COLLECTIONS[name]
                if isinstance(current, dict):
                    current.clear()
                    current.update(deepcopy(snapshot))
                else:
                    current[:] = deepcopy(snapshot)
            TASK_STORE.restore(task_snapshot)
            raise
