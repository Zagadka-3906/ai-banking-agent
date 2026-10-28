"""Field-level projections of validated reads; no account state is imported here."""
from __future__ import annotations

from typing import Any, Mapping
from decimal import Decimal
import re

from bank_agent.verification import (finite_number, valid_balance_payload, valid_card_collection,
                                     valid_holding_collection, valid_subscription_collection, valid_text_field,
                                     valid_wealth_collection)

LABELS = {
    'total_balance': '总余额', 'available_balance': '可用余额', 'locked_amount': '锁定金额',
    'amount': '金额', 'principal': '持仓本金', 'estimated_value': '演示市值', 'min_amount': '起购金额',
    'daily_limit': '日额度', 'status': '登记状态', 'name': '名称', 'merchant': '商户', 'time': '时间',
    'category': '分类', 'card_id': '卡片编号', 'product_id': '产品编号', 'risk_level': '产品风险等级',
    'liquidity': '流动性', 'annualized_yield': '演示年化收益率', 'created_at': '登记时间',
    'transaction_id': '关联流水', 'run_at': '计划执行时间', 'task_type': '任务类型',
    'risk_profile': '本人测评等级', 'risk_type': '投资者类型', 'assessed_at': '测评时间', 'matching_levels': '匹配风险等级',
}
MONEY_FIELDS = {'total_balance', 'available_balance', 'locked_amount', 'amount', 'principal', 'estimated_value', 'min_amount', 'daily_limit'}
ROW_FIELDS = {
    'cards': ['status', 'daily_limit', 'channels'],
    'wealth_products': ['name', 'risk_level', 'min_amount', 'liquidity', 'annualized_yield'],
    'wealth_holdings': ['product_id', 'principal', 'estimated_value'],
    'tasks': ['status', 'task_type', 'run_at', 'amount'],
    'subscriptions': ['amount', 'status', 'next_charge'],
    'transactions': ['merchant', 'amount', 'time', 'category', 'card_id'],
    'disputes': ['status', 'transaction_id', 'created_at'],
}
SELECTORS = ('merchant', 'name', 'product_id', 'card_id', 'task_id', 'case_id', 'contact_id', 'tx_id', 'subscription_id', 'holding_id', 'label', 'last4')
TASK_STATUSES = {'PENDING', 'WAITING_USER', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'HUMAN_TAKEOVER'}
RESOURCE_NAMES = {'cards': '卡片', 'wealth_products': '理财产品', 'wealth_holdings': '理财持仓',
                  'tasks': '待办任务', 'subscriptions': '订阅', 'transactions': '流水', 'disputes': '争议工单'}


def record_names(row: Mapping[str, Any], resource: str) -> list[str]:
    names = [row[key] for key in SELECTORS if isinstance(row.get(key), str)]
    # The Core encodes transfer counterparties in this fixed merchant format.
    # Exact canonical aliases let a recipient name select the real receipt.
    if resource == 'transactions' and row.get('category') == '转账支出':
        match = re.fullmatch(r'转账给 (\S.{0,79})', str(row.get('merchant') or ''))
        if match:
            names.append(match.group(1))
    return names


def selector_matches_record(selector: str, row: Mapping[str, Any], resource: str) -> bool:
    """Resolve a display label against one current row, never strip a wrong ID."""
    from unicodedata import normalize
    from bank_agent.extraction import PRODUCT_ALIASES

    names = record_names(row, resource)
    if resource in {'wealth_products', 'wealth_holdings'}:
        names += [alias for alias, code in PRODUCT_ALIASES.items() if row.get('product_id') == code]
    known = {normalize('NFKC', name).strip().casefold() for name in names}
    value = normalize('NFKC', selector).strip().casefold()
    if value in known:
        return True
    pair = re.fullmatch(r'([^()]+?)\s*\(([^()]+)\)', value)
    return bool(pair and all(part.strip() in known for part in pair.groups()))


def transaction_scope_document(context: Mapping[str, Any], subjects: list[str]) -> dict[str, Any]:
    from bank_agent.rendering import bill_scope_label
    return {'id': 'read.transactions.scope', 'source': 'core://transactions',
            'text': '本次流水读取范围：' + bill_scope_label(context) + '。', 'fields': [],
            'subject': subjects[0] if len(subjects) == 1 else None, 'entity_names': subjects}


def document_matches_subject(row: Mapping[str, Any], selector: str) -> bool:
    return (row.get('scope') == 'resource_policy' or row.get('subject') == selector
            or selector in row.get('entity_names', []))


def _document(resource: str, index: int, text: str, fields: list[str], subject: str | None = None) -> dict[str, Any]:
    if not valid_text_field(text, max_length=400):
        raise ValueError('EVIDENCE_FIELD_INVALID')
    return {'id': f'read.{resource}.{index}', 'text': text, 'source': 'core://' + resource,
            'fields': fields, 'subject': subject, 'required': False}


def project_records(resource: str, data: Mapping[str, Any], state: Mapping[str, Any], *,
                    fields: list[str] | None = None, subject: str | None = None,
                    subjects: list[str] | None = None) -> list[dict[str, Any]]:
    if data.get('code') != 200 or data.get('user_id', state.get('user_id')) != state.get('user_id'):
        raise ValueError('EVIDENCE_OWNER_OR_STATUS')
    selected = fields or ['records']
    if resource == 'balance':
        if not valid_balance_payload(data):
            raise ValueError('BALANCE_FIELDS_INVALID')
        keys = ['total_balance', 'available_balance', 'locked_amount'] if 'records' in selected else selected
        aliases = [state['user_id'], '我', '本人', '我的账户', '当前账户']
        if valid_text_field(data.get('user_name'), max_length=120):
            aliases.append(data['user_name'])
        return [{**_document(resource, index, f'{LABELS[key]}为¥{float(data[key]):,.2f}。', [key]), 'entity_names': aliases,
                 'values': {key: str(Decimal(str(data[key])))}} for index, key in enumerate(keys)]
    if resource == 'risk_profile':
        profile = data.get('data')
        if profile is None:
            return [_document(resource, 0, '本人尚无已完成的风险测评档案；不能据此匹配或申购产品。',
                              [*selected] if selected != ['records'] else ['records', 'risk_profile', 'risk_type', 'assessed_at', 'matching_levels'])]
        if (not isinstance(profile, dict) or profile.get('risk_profile') not in {'C1', 'C2', 'C3', 'C4', 'C5', 'CONSERVATIVE', 'BALANCED', 'AGGRESSIVE'}):
            raise ValueError('RISK_PROFILE_INVALID')
        keys = ['risk_profile', 'risk_type', 'assessed_at', 'matching_levels'] if 'records' in selected else selected
        parts = [f'{LABELS[key]}：{profile[key]}' for key in keys if key in profile and valid_text_field(profile[key], max_length=160)]
        return [_document(resource, 0, '；'.join(parts) + '。', [key for key in keys if key in profile])]
    rows = data.get('data')
    validators = {'cards': valid_card_collection, 'wealth_products': valid_wealth_collection,
                  'wealth_holdings': valid_holding_collection, 'subscriptions': valid_subscription_collection}
    if resource in validators and not validators[resource](data):
        raise ValueError('READ_COLLECTION_INVALID')
    if not isinstance(rows, list) or len(rows) > 5000 or any(not isinstance(row, dict) for row in rows):
        raise ValueError('READ_COLLECTION_INVALID')
    if resource == 'tasks' and any(row.get('owner_id') != state.get('user_id') or row.get('status') not in TASK_STATUSES
                                    or not all(valid_text_field(row.get(key), max_length=100) for key in ('task_id', 'task_type')) for row in rows):
        raise ValueError('TASK_OWNER_OR_STATUS_INVALID')
    selectors = subjects or ([subject] if subject else [])
    missing_selectors = []
    if selectors:
        missing_selectors = [name for name in selectors if not any(selector_matches_record(name, row, resource) for row in rows)]
        rows = [row for row in rows if any(selector_matches_record(name, row, resource) for name in selectors)]
        subject = selectors[0] if len(selectors) == 1 else None
    keys = [key for key in selected if key != 'records'] if any(key != 'records' for key in selected) else ROW_FIELDS[resource]
    if not rows:
        if missing_selectors:
            return [_document(resource, index, f'已读取本人当前{RESOURCE_NAMES[resource]}记录，未找到“{name}”的匹配记录。',
                              ['records', *keys], name) for index, name in enumerate(missing_selectors)]
        return [_document(resource, 0, f'已读取本人当前{RESOURCE_NAMES[resource]}记录，匹配共0条，未找到' + (f'“{subject}”的匹配记录。' if subject else '记录。'),
                          ['records', *keys], subject) | {'collection_size': 0, 'selection': list(selectors), 'complete': True}]
    documents: list[dict[str, Any]] = [{**_document(resource, 0, f'本次本人{RESOURCE_NAMES[resource]}匹配共{len(rows)}条。', ['records'], subject),
                                       'collection_size': len(rows), 'selection': list(selectors), 'complete': len(rows) <= 12}]
    for row in rows[:12]:
        identity = next((str(row[key]) for key in ('subscription_id', 'task_id', 'holding_id', 'case_id', 'tx_id', 'product_id', 'card_id') if row.get(key)), resource)
        record_id = identity
        if resource == 'subscriptions':
            identity = str(row['merchant']) + '（' + identity + '）'
        parts, available, numeric_values = [], [], {}
        for key in keys:
            value = row.get(key)
            if resource == 'tasks' and key == 'amount':
                value = (row.get('payload') or {}).get('amount')
            if key == 'next_charge':
                from bank_agent.query_focus import banking_today, charge_description
                parts.append(charge_description(row, banking_today()))
                available.append(key)
                continue
            if key == 'channels':
                channels = row.get('channels')
                if isinstance(channels, dict) and all(type(flag) is bool for flag in channels.values()):
                    parts.append('交易渠道：' + '、'.join(f'{dict(ONLINE="网上支付", OVERSEAS="境外交易").get(name, name)}={"开启" if flag else "关闭"}' for name, flag in channels.items()))
                    available.append(key)
                continue
            if value is None:
                continue
            if key in MONEY_FIELDS:
                if not finite_number(value):
                    raise ValueError('EVIDENCE_NUMBER_INVALID')
                label = '每期金额' if resource == 'subscriptions' and key == 'amount' else LABELS[key]
                parts.append(f'{label}¥{float(value):,.2f}')
                numeric_values[key] = str(Decimal(str(value)))
            elif key == 'annualized_yield':
                if not finite_number(value, nonnegative=True):
                    raise ValueError('EVIDENCE_NUMBER_INVALID')
                parts.append(f'演示年化收益率{float(value) * 100:.2f}%')
            elif not valid_text_field(str(value), max_length=160):
                raise ValueError('EVIDENCE_FIELD_INVALID')
            else:
                status = {'ACTIVE': '生效中', 'CANCELLED': '已取消', 'CANDIDATE': '待核实候选'}.get(str(value), str(value)) if resource == 'subscriptions' else str(value)
                parts.append(f'{LABELS.get(key, key)}：{status}')
            available.append(key)
        if parts:
            from bank_agent.extraction import PRODUCT_ALIASES
            entities = record_names(row, resource)
            entities += [alias for alias, product_id in PRODUCT_ALIASES.items() if row.get('product_id') == product_id]
            entities += [name for name in selectors if selector_matches_record(name, row, resource)]
            documents.append({**_document(resource, len(documents), identity + '：' + '；'.join(parts) + '。', ['records', *available],
                                          subject or row.get('merchant') or row.get('name') or row.get('product_id')),
                              'entity_names': entities, 'entity_id': record_id, 'values': numeric_values,
                              **({'card_last4': row['last4']} if resource == 'cards' and isinstance(row.get('last4'), str)
                                 and re.fullmatch(r'\d{4}', row['last4']) else {})})
    for name in missing_selectors:
        documents.append(_document(resource, len(documents), f'已读取本人当前{RESOURCE_NAMES[resource]}记录，未找到“{name}”的匹配记录。', ['records', *keys], name))
    if len(selectors) == 2 and len(rows) == 2 and not missing_selectors:
        # Derive comparisons from the same verified numeric fields, never from
        # numbers supplied by the model. The generator must cite this result.
        for key in keys:
            if key in MONEY_FIELDS and all(finite_number(row.get(key)) for row in rows):
                difference = abs(Decimal(str(rows[0][key])) - Decimal(str(rows[1][key])))
                names = [str(row.get('name') or row.get('product_id') or row.get('card_id') or resource) for row in rows]
                documents.append({**_document(resource, len(documents), f'{names[0]}与{names[1]}的{LABELS[key]}差额为¥{difference:,.2f}。', [key]),
                                  'entity_names': list(selectors), 'derived': True})
    if len(documents) == 1:
        return [_document(resource, 0, '已读取记录，但当前数据没有所问字段，不能推断缺失值。', ['records'], subject)]
    if len(rows) > 12:
        documents.append({**_document(resource, len(documents), f'匹配记录共{len(rows)}条，当前证据仅展示前12条，不能称为完整列表。', ['records'], subject), 'required': True})
    if resource == 'subscriptions':
        documents.append({**_document(resource, len(documents), '以上为本系统订阅登记状态与原计划日期，不能证明真实会员权益、扣款结果或商户重试。', ['records'], subject), 'required': True})
    if resource == 'wealth_holdings':
        documents.append({**_document(resource, len(documents), '市值为演示值，不代表实时行情或实际收益。', ['records'], subject), 'required': True})
    if resource == 'wealth_products':
        documents.append({**_document(resource, len(documents), '以上为演示产品信息，不是未来收益预测或收益保证。', ['records'], subject), 'required': True})
    return documents
