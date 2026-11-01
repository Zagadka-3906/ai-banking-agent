"""Short-lived offline ledger worker; invoked by the loopback panel via stdin."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys


def run(command: dict) -> dict:
    # Load credentials before importing Core; never return them or put passwords
    # on the command line. A panel read uses the signed snapshot but never saves.
    from dotenv import dotenv_values
    env_file = os.environ.get('ANYAN_ENV_FILE', '/etc/anyan/anyan.env')
    values = dotenv_values(env_file)
    os.environ.update({k: v for k, v in values.items() if v is not None})
    data = Path(os.environ.get('ANYAN_DATA_DIR', '/var/lib/anyan'))
    ledger = data / 'ledger.json'
    os.environ.update({'BANK_LEDGER_STORE_PATH': str(ledger), 'BANK_DEVICE_STORE_PATH': '',
        'BANK_AUDIT_LOG_DIR': 'off', 'BANK_AUDIT_LOG_PATH': '', 'BANK_CONVERSATION_DB': 'memory',
        'BANK_APPROVAL_STORE_PATH': 'memory', 'BANK_FEISHU_ENABLED': '0', 'BANK_TASK_SCHEDULER_SECONDS': '0',
        'DEEPSEEK_API_KEY': '', 'DASHSCOPE_API_KEY': ''})
    from bank_core.admin_accounts import (AccountEditError, account_snapshot, change_account, initialize_baselines,
                                         new_account_template, require)
    require(os.environ.get('BANK_DEMO_MODE') == '1', '账户编辑只在演示模式下启用')
    import main
    from bank_core import persistence
    from scheduler import TASK_STORE

    require(ledger.is_file() and main.ledger_store_status()['file_loaded'] and not main.ledger_store_status()['writes_frozen'],
            '账本未配置或未通过完整性校验，禁止编辑')
    revision = hashlib.sha256(ledger.read_bytes()).hexdigest()
    state = {name: deepcopy(persistence.LEDGER_COLLECTIONS[name]) for name in persistence.LEDGER_STATE_NAMES}
    state['TASK_STORE'] = TASK_STORE.export_records()
    action = command.get('action')
    uid = command.get('user_id')
    baselines = state['ACCOUNT_INITIAL_STATES']
    if action == 'list':
        return {'revision': revision, 'accounts': [
            {'user_id': owner, **{k: row.get(k) for k in ('name', 'phone', 'balance', 'locked_amount', 'status')},
             'baseline_at': baselines.get(owner, {}).get('saved_at'), 'shadow': row.get('source') == 'CONTACT_BOOK'}
            for owner, row in state['ACCOUNTS'].items()], 'template': new_account_template(),
            'products': [{k: row.get(k) for k in ('product_id', 'name', 'risk_level')} for row in state['WEALTH_PRODUCTS']]}
    if action == 'get':
        return {'revision': revision, 'data': account_snapshot(state, uid), 'baseline_at': baselines.get(uid, {}).get('saved_at')}
    require(action in {'create', 'save', 'reset', 'baseline', 'initialize'}, '未知操作')
    require(command.get('revision') == revision, '账户数据已变化，请刷新后再保存')
    if action == 'initialize':
        count = initialize_baselines(state)
    else:
        try:
            state = change_account(state, uid, action, command.get('data'), command.get('password'))
        except AccountEditError:
            raise
        count = 1
    for name in persistence.LEDGER_STATE_NAMES:
        container = persistence.LEDGER_COLLECTIONS[name]
        container.clear()
        if isinstance(container, dict):
            container.update(state[name])
        else:
            container.extend(state[name])
    TASK_STORE.restore(TASK_STORE.decode_records(state['TASK_STORE']))
    persistence.persist_ledger()
    require(not main.ledger_store_status()['last_write_failed'], '账本保存失败')
    return {'ok': True, 'changed': count, 'user_id': uid, 'revision': hashlib.sha256(ledger.read_bytes()).hexdigest()}


def main() -> None:
    try:
        command = json.loads(sys.stdin.read(2_000_001))
        if not isinstance(command, dict):
            raise ValueError('请求必须为对象')
        result = run(command)
    except Exception as exc:
        from bank_core.admin_accounts import AccountEditError
        result = {'ok': False, 'error': str(exc) if isinstance(exc, AccountEditError) else '账户配置无效或保存失败，请检查输入及服务日志'}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
