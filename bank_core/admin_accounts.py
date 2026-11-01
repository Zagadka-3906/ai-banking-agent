"""Offline account scenario editing for the authenticated, loopback ops panel.

No HTTP route or live-state import: the panel worker runs with banking services
stopped, validates a copy, then commits once through the signed ledger writer.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import math
import re
import uuid
from typing import Any, cast

from bank_security.passwords import hash_password

ACCOUNT_FIELDS = {'name', 'phone', 'card_no', 'balance', 'locked_amount', 'status'}
MAPS = {'cards': 'CARDS', 'contacts': 'USER_CONTACTS', 'subscriptions': 'SUBSCRIPTIONS',
        'holdings': 'WEALTH_HOLDINGS', 'risk_profile': 'WEALTH_RISK_PROFILES',
        'daily_spent': 'DAILY_TRANSFERS', 'daily_spent_date': 'DAILY_TRANSFER_DATE', 'risk_attempts': 'RISK_ATTEMPTS'}
LISTS = {'transactions': ('TRANSACTIONS', 'account_id'), 'disputes': ('DISPUTE_CASES', 'owner_id'),
         'schedules': ('SCHEDULED_TRANSFERS', 'user_id'), 'splits': ('SPLIT_REQUESTS', 'user_id'),
         'orders': ('MOCK_ORDERS', 'user_id'), 'tasks': ('TASK_STORE', 'owner_id'), 'outbox': ('OUTBOX', 'owner_id')}
DICT_ROWS = {'locks': ('LOCKS', 'user_id')}
DEFAULTS: dict[str, Any] = {'cards': [], 'contacts': [], 'subscriptions': [], 'holdings': [],
                          'risk_profile': None, 'daily_spent': 0.0, 'daily_spent_date': None, 'risk_attempts': []}
IDS = {'cards': 'card_id', 'contacts': 'contact_id', 'subscriptions': 'subscription_id', 'holdings': 'holding_id',
       'transactions': 'tx_id', 'disputes': 'case_id', 'schedules': 'schedule_id', 'splits': 'split_id',
       'orders': 'mock_order_id', 'tasks': 'task_id', 'outbox': 'outbox_id'}


class AccountEditError(ValueError):
    pass


def require(condition: Any, message: str) -> None:
    if not condition:
        raise AccountEditError(message)


def money(value: Any, field: str, maximum: float = 1e12) -> None:
    require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum, field + '必须为有效的非负金额')


def text(value: Any, field: str, maximum: int = 120) -> None:
    require(isinstance(value, str) and 0 < len(value.strip()) <= maximum
            and not re.search(r'[\x00-\x1f]', value), field + '不能为空或包含控制字符')


def identifier(value: Any) -> None:
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value), '账户标识仅允许字母、数字、短横线和下划线，最长64位')


def account_snapshot(state: dict, uid: str, *, private: bool = False) -> dict:
    require(uid in state['ACCOUNTS'], '账户不存在')
    account = deepcopy(state['ACCOUNTS'][uid])
    view: dict[str, Any] = {'user_id': uid, 'account': account if private else {k: account.get(k) for k in ACCOUNT_FIELDS}}
    for key, collection in MAPS.items():
        view[key] = deepcopy(state.get(collection, {}).get(uid, DEFAULTS[key]))
    for key, (collection, owner) in LISTS.items():
        view[key] = deepcopy([row for row in state.get(collection, []) if row.get(owner) == uid])
    for key, (collection, owner) in DICT_ROWS.items():
        view[key] = deepcopy({k: row for k, row in state.get(collection, {}).items() if row.get(owner) == uid})
    card_ids = {row['card_id'] for row in view['cards']}
    for key, collection in [('card_spent', 'CARD_DAILY_SPENT'), ('card_spent_date', 'CARD_DAILY_SPENT_DATE')]:
        view[key] = {k: v for k, v in state.get(collection, {}).items() if k in card_ids}
    return view


def initialize_baselines(state: dict) -> int:
    baselines = state.setdefault('ACCOUNT_INITIAL_STATES', {})
    added = 0
    for uid in state['ACCOUNTS']:
        if uid not in baselines:
            baselines[uid] = {'saved_at': datetime.now(timezone.utc).isoformat(), 'data': account_snapshot(state, uid, private=True)}
            added += 1
    return added


def validate_view(state: dict, uid: str, view: dict) -> None:
    from bank_core.cards import CARD_MAX_DAILY_LIMIT, CARD_MAX_PER_USER
    from bank_core.ledger import CONTACT_MAX_PER_USER
    from scheduler import TaskStore

    expected = {'user_id', 'account', *MAPS, *LISTS, *DICT_ROWS, 'card_spent', 'card_spent_date'}
    require(set(view) == expected and view['user_id'] == uid, '字段不完整或账户标识被改动，请重新加载')
    account = view['account']
    require(isinstance(account, dict) and set(account) == ACCOUNT_FIELDS, '账户资料包含不支持的字段')
    for field in ('name', 'phone', 'card_no'):
        text(account[field], field)
    require(re.fullmatch(r'\d{5,20}', account['phone']), '手机号必须是5至20位数字')
    shadow_placeholder = account['card_no'] == '----' and state['ACCOUNTS'].get(uid, {}).get('source') == 'CONTACT_BOOK'
    require(shadow_placeholder or re.fullmatch(r'\d{5,24}', account['card_no']), '账号必须是5至24位数字')
    for other, record in state['ACCOUNTS'].items():
        if other != uid:
            require(record.get('phone') != account['phone'] and (shadow_placeholder or record.get('card_no') != account['card_no']), '手机号或账号已被其他账户使用')
    require(account['status'] in {'ACTIVE', 'DISABLED', 'FROZEN', 'CLOSED'}, '账户状态无效')
    for field in ('balance', 'locked_amount'):
        money(account[field], field)
    require(account['locked_amount'] <= account['balance'], '锁定金额不能超过余额')
    for key in (*IDS,):
        rows = view[key]
        require(isinstance(rows, list) and len(rows) <= 5000 and all(isinstance(r, dict) for r in rows), key + '必须为记录数组，最多5000条')
        ids = [r.get(IDS[key]) for r in rows]
        require(all(isinstance(x, str) and 0 < len(x) <= 128 for x in ids) and len(ids) == len(set(ids)), key + '编号缺失或重复')
        collection = MAPS.get(key) or (LISTS[key][0] if key in LISTS else None)
        if key in MAPS:
            others = [r for owner, data in state.get(collection, {}).items() if owner != uid for r in data]
        else:
            others = [r for r in state.get(collection, []) if r.get(LISTS[key][1]) != uid]
        # Transaction IDs deliberately identify both sides of one transfer.
        if key != 'transactions':
            require(not (set(ids) & {r.get(IDS[key]) for r in others}), key + '编号与其他账户冲突')
        for row in rows:
            for field in ('amount', 'principal', 'estimated_value', 'daily_limit', 'per_person', 'initiator_share'):
                if field in row:
                    money(row[field], key + '.' + field)
            for field in ('time', 'created_at', 'updated_at', 'purchased_at', 'scheduled_at', 'run_at', 'next_charge', 'birthday'):
                if row.get(field) is not None:
                    require(isinstance(row[field], str), key + '.' + field + '日期格式错误')
                    try:
                        datetime.fromisoformat(row[field].replace('Z', '+00:00'))
                    except ValueError as exc:
                        raise AccountEditError(key + '.' + field + '日期格式错误') from exc
    require(len(view['cards']) <= CARD_MAX_PER_USER and len(view['contacts']) <= CONTACT_MAX_PER_USER, '卡片或联系人数超过系统容量')
    cards = {row['card_id'] for row in view['cards']}
    for row in view['cards']:
        require(row.get('status') in {'ACTIVE', 'FROZEN', 'LOST', 'CLOSED'}, '卡片状态无效')
        require(isinstance(row.get('last4'), str) and re.fullmatch(r'\d{4}', row['last4']), '卡片尾号应为4位数字')
        money(row.get('daily_limit'), '卡片日额度', CARD_MAX_DAILY_LIMIT)
        require(isinstance(row.get('channels', {}), dict) and set(row.get('channels', {})) <= {'ONLINE', 'OVERSEAS'}
                and all(type(v) is bool for v in row.get('channels', {}).values()), '渠道开关必须为布尔值')
        if row.get('pan_ref'):
            allowed = [*state.get('CARDS', {}).get(uid, []),
                       *state.get('ACCOUNT_INITIAL_STATES', {}).get(uid, {}).get('data', {}).get('cards', [])]
            require(any(c.get('card_id') == row['card_id'] and c.get('pan_ref') == row['pan_ref'] for c in allowed),
                    '不能填写或挪用其他账户的卡片凭据')
    for row in view['contacts']:
        text(row.get('name'), '联系人姓名')
        require(isinstance(row.get('phone'), str) and re.fullmatch(r'\d{5,20}', row['phone']), '联系人手机号无效')
        require(row.get('account_id') in (None, '', *state['ACCOUNTS']), '联系人关联账户不存在')
        require(row.get('owner_id', uid) == uid, '联系人不能归属其他账户')
    for key, (_, owner) in LISTS.items():
        for row in view[key]:
            require(row.get(owner) == uid, key + '存在不属于本账户的记录')
            if row.get('card_id'):
                require(row['card_id'] in cards, key + '关联卡片不存在')
    transactions = {row['tx_id'] for row in view['transactions']}
    for row in view['disputes']:
        require(row.get('transaction_id') in transactions, '工单关联流水不存在')
    for row in view['tasks']:
        require(row.get('task_type') in {'SCHEDULED_TRANSFER', 'ORDER_DRAFT', 'REMINDER', 'FUND_RESERVATION', 'SPLIT_BILL', 'RENEWAL_REMINDER'}, '待办类型无效')
        require(isinstance(row.get('payload'), dict), '待办内容无效')
        require(row['payload'].get('from_user', uid) == uid and row['payload'].get('user_id', uid) == uid,
                '待办内容不能指定其他付款账户')
    for row in view['transactions']:
        require(isinstance(row.get('time'), str), '流水时间不能为空')
        money(row.get('amount'), '流水金额')
    for row in view['subscriptions']:
        text(row.get('merchant'), '订阅名称')
        money(row.get('amount'), '订阅金额')
        require(row.get('status') in {'ACTIVE', 'CANCELLED', 'CANDIDATE'}, '订阅状态无效')
        require(isinstance(row.get('next_charge'), str), '订阅计划日期不能为空')
    product_ids = {p['product_id'] for p in state['WEALTH_PRODUCTS']}
    for row in view['holdings']:
        require(row.get('product_id') in product_ids, '持仓关联产品不存在')
        money(row.get('principal'), '持仓本金')
        money(row.get('estimated_value'), '持仓估值')
    profile = view['risk_profile']
    require(profile is None or isinstance(profile, dict) and profile.get('risk_profile') in {'C1', 'C2', 'C3', 'C4', 'C5', 'CONSERVATIVE', 'BALANCED', 'AGGRESSIVE'}, '测评档案等级无效')
    if profile is not None and profile.get('assessed_at') is not None:
        require(isinstance(profile['assessed_at'], str), '测评时间格式错误')
        try:
            datetime.fromisoformat(profile['assessed_at'].replace('Z', '+00:00'))
        except ValueError as exc:
            raise AccountEditError('测评时间格式错误') from exc
    money(view['daily_spent'], '当日累计转出')
    require(isinstance(view['risk_attempts'], list) and len(view['risk_attempts']) <= 100, '测评次数记录无效')
    for value in view['risk_attempts']:
        money(value, '测评时间戳')
    for key in ('card_spent', 'card_spent_date'):
        require(isinstance(view[key], dict) and set(view[key]) <= cards, '卡片累计支付包含其他卡片')
    for value in view['card_spent'].values():
        money(value, '卡片累计支付')
    for value in [view['daily_spent_date'], *view['card_spent_date'].values()]:
        if value is not None:
            try:
                require(isinstance(value, str), '累计日期无效')
                date.fromisoformat(value)
            except ValueError as exc:
                raise AccountEditError('累计日期格式应为 YYYY-MM-DD') from exc
    require(isinstance(view['locks'], dict), '锁资记录必须为对象')
    for key, row in view['locks'].items():
        require(isinstance(row, dict) and row.get('user_id') == uid and row.get('lock_id') == key, '锁资记录归属或编号错误')
        require(key not in state.get('LOCKS', {}) or state['LOCKS'][key].get('user_id') == uid, '锁资编号与其他账户冲突')
        money(row.get('amount'), '锁资金额')
    require(round(sum(row['amount'] for row in view['locks'].values() if row.get('status') == 'LOCKED'), 2) == round(account['locked_amount'], 2), '锁资明细合计必须等于账户锁定金额')
    merged_tasks = [row for row in state.get('TASK_STORE', []) if row['owner_id'] != uid] + view['tasks']
    try:
        TaskStore.decode_records(merged_tasks)
    except (ValueError, TypeError) as exc:
        raise AccountEditError('待办格式、依赖关系或归属无效') from exc
    require(all(row.get('status') != 'RUNNING' for row in view['tasks']), '不能写入正在执行的任务状态')


def apply_view(state: dict, uid: str, view: dict, password: str | None = None, *, private: bool = False) -> None:
    old_account = state['ACCOUNTS'].get(uid, {})
    if private:
        account = deepcopy(view['account'])
        view = deepcopy(view)
        view['account'] = {k: account.get(k) for k in ACCOUNT_FIELDS}
    else:
        account = {**old_account, **view['account']}
    validate_view(state, uid, view)
    if password:
        account['password_hash'] = hash_password(password)
        if account.get('source') == 'CONTACT_BOOK':
            require(account['card_no'] != '----', '将收款账户启用登录前，请先设置账户号码')
            account['source'] = 'PANEL'
    require(bool(account.get('password_hash')), '创建账户需要设置登录密码')
    account['credential_version'] = max(int(old_account.get('credential_version', 0)), int(account.get('credential_version', 0))) + 1
    account['demo_contacts_seeded'] = account['transfer_contacts_backfilled'] = True
    state['ACCOUNTS'][uid] = account
    # Preserve unrelated aliases and refuse ambiguous identity aliases.
    aliases = state['CONTACTS']
    new_aliases = (account['name'], account['phone']) if account.get('source') != 'CONTACT_BOOK' else ()
    for alias in new_aliases:
        require(alias not in aliases or aliases[alias] == uid, '姓名或手机号与现有收款人别名冲突')
    for alias in (old_account.get('name'), old_account.get('phone')):
        if alias and aliases.get(alias) == uid:
            aliases.pop(alias)
    for alias in new_aliases:
        aliases[alias] = uid
    old_cards = {row['card_id'] for row in state.get('CARDS', {}).get(uid, [])}
    for key, collection in MAPS.items():
        value = deepcopy(view[key])
        if value is None:
            state[collection].pop(uid, None)
        else:
            state[collection][uid] = value
    for key, (collection, owner) in LISTS.items():
        state[collection] = [row for row in state[collection] if row.get(owner) != uid] + deepcopy(view[key])
    for key, (collection, owner) in DICT_ROWS.items():
        state[collection] = {k: r for k, r in state[collection].items() if r.get(owner) != uid} | deepcopy(view[key])
    for key, collection in [('card_spent', 'CARD_DAILY_SPENT'), ('card_spent_date', 'CARD_DAILY_SPENT_DATE')]:
        state[collection] = {k: v for k, v in state[collection].items() if k not in old_cards} | deepcopy(view[key])


def change_account(original: dict, uid: str, action: str, view: dict | None = None, password: str | None = None) -> dict:
    """Return a validated copy. Passwords/baselines are never returned to UI."""
    identifier(uid)
    state = deepcopy(original)
    require(action in {'create', 'save', 'reset', 'baseline'}, '未知账户操作')
    require((uid not in state['ACCOUNTS']) if action == 'create' else uid in state['ACCOUNTS'], '账户已存在或不存在')
    if action != 'create':
        initialize_baselines(state)
    if action in {'create', 'save'}:
        require(isinstance(view, dict), '缺少账户配置')
        apply_view(state, uid, cast(dict, view), password)
    elif action == 'reset':
        baseline = deepcopy(state['ACCOUNT_INITIAL_STATES'][uid]['data'])
        apply_view(state, uid, baseline, private=True)
    if action in {'baseline', 'create'}:
        state.setdefault('ACCOUNT_INITIAL_STATES', {})[uid] = {'saved_at': datetime.now(timezone.utc).isoformat(),
                                                             'data': account_snapshot(state, uid, private=True)}
    from bank_core.persistence import _ledger_state_valid
    require(_ledger_state_valid(state), '配置未通过完整账本校验')
    return state


def new_account_template() -> dict:
    suffix = uuid.uuid4().hex[:10].upper()
    return {'user_id': '', 'account': {'name': '', 'phone': '', 'card_no': '', 'balance': 15000.0, 'locked_amount': 0.0, 'status': 'ACTIVE'},
            **deepcopy(DEFAULTS), **{key: [] for key in LISTS}, 'locks': {}, 'card_spent': {}, 'card_spent_date': {}} | {
                'cards': [{'card_id': 'CARD-' + suffix, 'number_masked': '**** **** **** 0000', 'last4': '0000',
                           'type': '借记卡', 'status': 'ACTIVE', 'daily_limit': 5000.0, 'label': '演示借记卡',
                           'channels': {'ONLINE': True, 'OVERSEAS': False}}]}
