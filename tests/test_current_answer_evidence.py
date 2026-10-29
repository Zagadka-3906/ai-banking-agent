"""Fresh answers use current evidence and preserve each selected entity's scope."""
import pytest

from bank_agent.consultation import read_evidence
from bank_agent.evidence import project_records, transaction_scope_document
from bank_agent.evidence_session import EvidenceSession
from bank_agent.goal_answering import _matching, _validate_coverage
from bank_planning.requests import normalize_goals
from test_agent_gateway import _send, gateway  # noqa: F401
from test_general_dialogue_loop import goal, model
from test_inquiry_boundaries import financial_state


@pytest.mark.parametrize('money', ['¥19.90', '￥999.00', '¥1,500.50', '¥-19.90'])
def test_currency_prefixed_amount_is_not_a_calendar_range(money):
    from intent_language import requested_date_range
    assert requested_date_range(f'帮我查这笔金额{money}的交易线索') is None
    assert requested_date_range(f'查2026-10-01到2026-10-28金额{money}的交易') == {'start': '2026-10-01', 'end': '2026-10-28'}


@pytest.mark.parametrize('text', [
    '我看到有一笔云端存储19.90的订阅代扣，我不太记得开过这个，能帮我查一下这笔可疑交易的线索吗？',
    '这笔其他商户12.50的消费查一下', '那笔10.25的扣款能查线索吗',
    '查这笔数额为19.90的交易', '一笔商户99.99的付款是什么',
])
def test_bare_decimal_in_a_monetary_reference_does_not_invent_calendar_dates(text):
    from intent_language import requested_date_range
    assert requested_date_range(text) is None
    dated = '查2026-10-01到2026-10-28，' + text
    assert requested_date_range(dated) == {'start': '2026-10-01', 'end': '2026-10-28'}


@pytest.mark.parametrize('text,expected', [
    ('查10.25的交易', {'start': '2026-10-25', 'end': '2026-10-25'}),
    ('查一笔在10.25的扣款', {'start': '2026-10-25', 'end': '2026-10-25'}),
    ('查一笔日期为10.25的扣款', {'start': '2026-10-25', 'end': '2026-10-25'}),
    ('一笔10.25那天的消费', {'start': '2026-10-25', 'end': '2026-10-25'}),
    ('查一笔在2.30的扣款', {'error': 'DATE_RANGE_INVALID'}),
    ('查一笔日期为19.90的扣款', {'error': 'DATE_RANGE_INVALID'}),
])
def test_amount_reference_does_not_hide_explicit_dotted_dates_or_invalid_dates(text, expected):
    from datetime import datetime
    from intent_language import requested_date_range
    assert requested_date_range(text, now=datetime(2026, 10, 29)) == expected


def test_non_transaction_goals_do_not_inherit_a_product_duration_calendar_error():
    from bank_agent.parsing import local_parse
    from bank_agent.understanding import enforce_inquiry
    text = '确认零星宝·活钱管家1元起购和恒泉稳利·60天持有1000元起购是否准确'
    parsed = local_parse(text)
    goals = normalize_goals([goal('wealth_products', ['min_amount'], '确认两种产品最低起购金额')], text, {})
    parsed.update(intent='WEALTH_QUERY', parse_meta={**parsed['parse_meta'], 'source': 'model', 'dialogue_act': 'QUERY', 'goals': goals})
    assert parsed['slots']['date_range_ambiguous']
    result = enforce_inquiry(parsed, text)
    assert not result['slots'].get('date_range_ambiguous') and result['date_scope'] == 'ALL' and result['date_range'] is None
    assert not any(reason.startswith('DATE_RANGE_') for reason in result['parse_meta']['reason_codes'])


@pytest.mark.parametrize('selector', ['尾号3082', '末四位：3082', '后四位3082的卡', '尾号 3082 的银行卡'])
def test_card_tail_selector_is_exact_and_resource_bound(selector):
    from bank_agent.evidence import selector_matches_record
    row = {'card_id': 'CARD-001', 'last4': '3082'}
    assert selector_matches_record(selector, row, 'cards')
    assert not selector_matches_record(selector, {**row, 'last4': '3083'}, 'cards')
    assert not selector_matches_record(selector, row, 'transactions')


def test_answer_context_does_not_repeat_unread_historical_account_values(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    model(monkeypatch, [goal('balance', ['locked_amount'], '锁定金额')], intent='BALANCE_QUERY')
    _send(client, '查询锁定金额', conversation_id='fresh-answer')
    calls = model(monkeypatch, [goal('cards', ['channels'], '卡片渠道')])
    result = _send(client, '查询网上支付和境外交易渠道', conversation_id='fresh-answer')
    answers = [payload for system, payload in calls if '银行业务解答助手' in system]
    assert answers and '0.00' not in str(answers[0]['context'])
    assert all('locked_amount' not in row.get('fields', []) for row in answers[0]['facts'])
    assert result['approval'] is None and '网上支付' in result['reply']


@pytest.mark.parametrize('selectors', [['TX1004'], ['TX1004', 'TX1001']])
def test_transaction_analysis_is_computed_and_bound_per_selected_record(gateway, selectors):  # noqa: F811
    before = financial_state()
    state = {'user_id': 'user_zhang', 'user_input': '查询交易风险线索：' + '、'.join(selectors), 'date_scope': 'ALL'}
    documents = read_evidence(state, {'tool': 'transactions', 'fields': ['analysis', 'amount'], 'subjects': selectors})
    for selector in selectors:
        request = normalize_goals([goal('transactions', ['analysis'], '交易线索', subject=selector)], selector, {})[0]
        rows = [row for row in _matching(request, documents) if 'analysis' in row['fields']]
        assert rows and '共核验 1 笔' in '\n'.join(row['text'] for row in rows)
        assert all(other not in '\n'.join(row['text'] for row in rows) for other in {'TX1001', 'TX1004'} - {selector})
        assert ('999.00' if selector == 'TX1004' else '45.00') in '\n'.join(row['text'] for row in rows)
    assert financial_state() == before


def test_formatted_entity_label_requires_consistent_current_name_and_id():
    data = {'code': 200, 'data': [
        {'product_id': 'LX00118', 'name': '零星宝·活钱管家', 'risk_level': 'R1', 'min_amount': 1.0,
         'liquidity': '灵活', 'annualized_yield': 0.01},
        {'product_id': 'HQ30060', 'name': '恒泉稳利·60天持有', 'risk_level': 'R2', 'min_amount': 1000.0,
         'liquidity': '持有', 'annualized_yield': 0.02}]}
    selectors = ['零星宝·活钱管家(LX00118)', '恒泉稳利·60天持有（HQ30060）']
    rows = project_records('wealth_products', data, {'user_id': 'user_zhang'}, fields=['min_amount'], subjects=selectors)
    for selector in selectors:
        request = {'resource': 'wealth_products', 'subjects': [selector]}
        assert any(row.get('entity_id') for row in _matching(request, rows))
    mismatch = project_records('wealth_products', data, {'user_id': 'user_zhang'}, fields=['min_amount'],
                               subjects=['零星宝·活钱管家(HQ30060)'])
    assert not any(row.get('entity_id') for row in mismatch)


def test_registered_record_fields_cannot_be_declared_unsupported_to_hide_a_gap():
    request = normalize_goals([goal('cards', ['daily_limit'], '解释当前额度', act='EXPLAIN')
                               | {'requires_record': True}], '解释当前额度', {})
    with pytest.raises(ValueError, match='READ_CAPABILITY_MISCLASSIFIED'):
        _validate_coverage([{'goal_id': 'q1', 'status': 'UNSUPPORTED', 'evidence_ids': [], 'reason': '未提供'}], request, [])


def test_limit_policy_reads_current_maximum_even_without_a_proposed_amount(gateway):  # noqa: F811
    goals = normalize_goals([goal('general', [], '每日额度上限的规则', act='EXPLAIN') | {'topic': 'limits'}],
                            '每日额度上限的规则', {})
    session = EvidenceSession({'user_id': 'user_zhang', 'user_input': '每日额度上限的规则', 'parse_meta': {'goals': goals}})
    before = financial_state()
    session.bootstrap()
    assert any(row['id'] == 'context.limit_max' and row['source'] == 'core://assistant/context' for row in session.facts())
    assert financial_state() == before


def test_transaction_period_binds_after_semantic_goals_even_if_background_mentions_balance(monkeypatch):
    from bank_agent.parsing import model_parse
    from intent_language import requested_date_range
    text = '余额我看到了，麻烦再帮我查一下最近几笔交易明细。'
    model(monkeypatch, [goal('transactions', ['records'], '最近几笔交易明细')], intent='BILL_QUERY')
    parsed = model_parse(text)
    assert parsed['date_scope'] == 'CUSTOM' and parsed.get('date_range') == requested_date_range(text)


@pytest.mark.parametrize('fields', [['amount'], []])
def test_period_metadata_cannot_replace_transaction_record_evidence(fields):
    bounds = {'start': '2026-10-01', 'end': '2026-10-28'}
    scope = transaction_scope_document({'date_scope': 'CUSTOM', 'date_range': bounds}, ['TX1004'])
    assert all(value in scope['text'] for value in bounds.values())
    request = normalize_goals([goal('transactions', fields, '交易金额', subject='TX1004')], 'TX1004金额', {})
    with pytest.raises(ValueError, match='MISSING_GOAL_RECORD'):
        _validate_coverage([{'goal_id': 'q1', 'status': 'ANSWERED', 'evidence_ids': [scope['id']], 'reason': '查询期间'}],
                           request, [scope])


def test_a_resolved_question_reference_can_be_quoted_without_proving_a_record(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    def draft(payload):
        reference = next(row for row in payload['facts'] if 'requested_subject' in row.get('fields', []))
        ids = [reference['id'], 'guide.dispute.timing']
        return {'answer': '关于你所指的TX1004：当前演示没有真实审核队列，也未提供预计完成时间。',
                'evidence_ids': ids, 'unanswered': [], 'coverage': [
                    {'goal_id': 'q1', 'status': 'ANSWERED', 'evidence_ids': ids, 'reason': '说明产品时效边界'}]}
    model(monkeypatch, [goal('disputes', ['timing'], 'TX1004多久有结果', act='EXPLAIN', subject='TX1004')], draft=draft)
    before = financial_state()
    result = _send(client, 'TX1004多久有处理结果', conversation_id='question-reference')
    assert result['approval'] is None and result['result']['answer_presentation']['mode'] == 'GENERATED_REVIEWED'
    assert 'TX1004' in result['reply'] and financial_state() == before
    reference = {'id': 'request.subject.0', 'fields': ['requested_subject'], 'source': 'user://current-request', 'text': '本轮所指TX1004。'}
    query = normalize_goals([goal('transactions', ['amount'], '交易金额', subject='TX1004')], 'TX1004金额', {})
    with pytest.raises(ValueError, match='MISSING_GOAL_RECORD'):
        _validate_coverage([{'goal_id': 'q1', 'status': 'ANSWERED', 'evidence_ids': [reference['id']], 'reason': '对象引用'}], query, [reference])


def test_resource_policy_applies_to_the_named_case_but_cannot_prove_case_status(gateway):  # noqa: F811
    rows = read_evidence({'user_id': 'user_zhang', 'user_input': 'TX1004预计何时有结果'},
                         {'tool': 'disputes', 'fields': ['timing'], 'subjects': ['TX1004']})
    policies = [row for row in rows if row.get('scope') == 'resource_policy']
    assert policies
    ids = [row['id'] for row in policies]
    coverage = [{'goal_id': 'q1', 'status': 'ANSWERED', 'evidence_ids': ids, 'reason': '产品时效边界'}]
    query = normalize_goals([goal('disputes', ['timing'], '预计完成时间', subject='TX1004')], 'TX1004', {})
    assert _validate_coverage(coverage, query, policies)[0]['status'] == 'ANSWERED'
    status = normalize_goals([goal('disputes', ['status'], '工单状态', subject='TX1004')], 'TX1004', {})
    with pytest.raises(ValueError, match='MISSING_GOAL_RECORD'):
        _validate_coverage(coverage, status, policies)


def test_full_collection_read_does_not_erase_a_later_analysis_goal_scope(gateway):  # noqa: F811
    goals = normalize_goals([goal('transactions', ['analysis'], 'TX1004交易线索', subject='TX1004')], 'TX1004', {})
    session = EvidenceSession({'user_id': 'user_zhang', 'user_input': 'TX1004交易线索', 'date_scope': 'ALL',
                               'parse_meta': {'goals': goals}})
    assert session.read({'tool': 'transactions', 'fields': ['records']})
    assert session.read({'tool': 'transactions', 'fields': ['analysis'], 'subjects': ['TX1004']})
    rows = [row for row in _matching(goals[0], session.facts()) if 'analysis' in row['fields']]
    assert rows and '共核验 1 笔' in '\n'.join(row['text'] for row in rows)


def test_single_record_analysis_cannot_satisfy_a_later_collection_analysis(gateway):  # noqa: F811
    session = EvidenceSession({'user_id': 'user_zhang', 'user_input': '分析TX1004', 'date_scope': 'ALL'})
    assert session.read({'tool': 'transactions', 'fields': ['analysis'], 'subjects': ['TX1004']})
    global_goal = goal('transactions', ['analysis'], '全部流水分析')
    assert not any('analysis' in row['fields'] for row in _matching(global_goal, session.facts()))
    assert session.read({'tool': 'transactions', 'fields': ['analysis']})
    assert any('analysis' in row['fields'] and row.get('subject') is None for row in _matching(global_goal, session.facts()))


def test_shared_fetch_does_not_apply_legacy_single_record_reference_to_global_goal(gateway):  # noqa: F811
    import main
    text = '分析全部流水，同时分析这笔TX1004'
    goals = normalize_goals([goal('transactions', ['analysis'], '全部流水分析'),
                             goal('transactions', ['analysis'], 'TX1004线索', subject='TX1004')], text, {})
    state = {'user_id': 'user_zhang', 'user_input': text, 'memory_principal': 'user_zhang', 'date_scope': 'ALL',
             'parse_meta': {'goals': goals}, 'observations': {'BILL_QUERY': {'intent': 'BILL_QUERY', 'transaction_ids': ['TX1004']}}}
    documents = read_evidence(state, {'tool': 'transactions', 'fields': ['analysis']})
    expected = sum(row.get('account_id') == 'user_zhang' for row in main.TRANSACTIONS)
    global_text = '\n'.join(row['text'] for row in _matching(goals[0], documents))
    selected_text = '\n'.join(row['text'] for row in _matching(goals[1], documents))
    assert f'共核验 {expected} 笔' in global_text and '共核验 1 笔' in selected_text
