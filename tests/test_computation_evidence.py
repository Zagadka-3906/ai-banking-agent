"""Arithmetic is based on verified fields and keeps its exact operand lineage."""
import pytest

from bank_agent.computations import calculation_evidence, expand_calculation_citations
from bank_agent.evidence import project_records
from bank_agent.evidence_session import EvidenceSession
from bank_agent.goal_answering import _fallback, _validate_coverage
from bank_planning.requests import normalize_goals, request_goals
from test_agent_gateway import gateway  # noqa: F401
from test_agent_gateway import _send
from test_general_dialogue_loop import model, goal
from test_inquiry_boundaries import financial_state


def calculation(operation='difference',subjects=None):
    field='records' if operation=='count' else 'min_amount'
    return {'act':'QUERY','resource':'wealth_products','fields':[field],'question':'核对计算结果',
            'subjects':subjects if subjects is not None else ['零星宝','恒泉稳利'],
            'calculation':{'operation':operation,'field':field}}


def state(goals):
    return {'user_id':'user_zhang','user_input':'零星宝与恒泉稳利的起购金额及差额','parse_meta':{
        'goals':normalize_goals(goals,'零星宝与恒泉稳利的起购金额及差额',{})}}


@pytest.mark.parametrize('spec',[
    {'operation':'eval','field':'min_amount'}, {'operation':'sum','field':'annualized_yield'},
    {'operation':'difference','field':'min_amount','python':'danger()'},
])
def test_calculation_spec_is_not_a_code_or_arbitrary_field_tool(spec):
    with pytest.raises(ValueError):
        state([{**calculation(),'calculation':spec}])


def test_difference_requires_two_source_bound_selectors():
    with pytest.raises(ValueError,match='OPERANDS'):
        state([calculation(subjects=['零星宝'])])
    normalized=normalize_goals([calculation()], '无产品对象的查询',{})
    assert normalized[0]['act']=='UNCLEAR'


def test_separate_reads_produce_calculation_with_exact_current_input_ids(gateway):  # noqa: F811
    request=state([calculation()])
    session=EvidenceSession(request)
    session.read({'tool':'wealth_products','subjects':['零星宝'],'fields':['min_amount']})
    session.read({'tool':'wealth_products','subjects':['恒泉稳利'],'fields':['min_amount']})
    facts=session.facts()
    computed=next(row for row in facts if row.get('goal_id')=='q1')
    assert computed['values']=={'min_amount':'999.0'}
    assert len(computed['input_evidence_ids'])==2
    known={row['id']:row for row in facts}
    assert all(known[key]['source']=='core://wealth_products' for key in computed['input_evidence_ids'])
    assert len({known[key]['entity_id'] for key in computed['input_evidence_ids']})==2
    coverage=[{'goal_id':'q1','status':'ANSWERED','evidence_ids':[computed['id']],'reason':'计算结果'}]
    with pytest.raises(ValueError,match='CALCULATION'):
        _validate_coverage(coverage,request_goals(request),facts)
    plan=expand_calculation_citations({'evidence_ids':[computed['id']],'coverage':coverage},facts)
    assert _validate_coverage(plan['coverage'],request_goals(request),facts)
    text,coverage=_fallback(request_goals(request),session)
    assert '999.00' in text and coverage[0]['status']=='ANSWERED'


def test_raw_values_alone_do_not_mark_a_calculation_goal_answered(gateway):  # noqa: F811
    request=state([calculation()])
    session=EvidenceSession(request)
    session.read({'tool':'wealth_products','subjects':['零星宝'],'fields':['min_amount']})
    text,coverage=_fallback(request_goals(request),session)
    assert coverage[0]['status']=='NEEDS_DATA' and '计算依据' in text


def test_arithmetic_does_not_read_amounts_from_question_or_reuse_derived_values(gateway):  # noqa: F811
    request=state([calculation()])
    session=EvidenceSession(request)
    session.read({'tool':'wealth_products','subjects':['零星宝','恒泉稳利'],'fields':['min_amount']})
    facts=session.facts()
    inputs=[row for row in facts if not row.get('derived')]
    assert calculation_evidence(request_goals(request),[{**row,'values':{}} for row in inputs])==[]
    assert calculation_evidence(request_goals(request),[row for row in facts if row.get('derived')])==[]


@pytest.mark.parametrize('operation,expected',[('sum','1001.0'),('count','2')])
def test_aggregate_checks_collection_completeness_and_uses_decimal(gateway,operation,expected):  # noqa: F811
    request=state([calculation(operation,[])])
    session=EvidenceSession(request)
    session.read({'tool':'wealth_products','fields':['min_amount']})
    facts=[row for row in session.facts() if not row.get('derived')]
    # The actual shelf can contain more products; the expected value comes
    # from its typed records rather than hardcoding a two-product assumption.
    from decimal import Decimal
    operand=[row for row in facts if row.get('entity_id')]
    expected=str(sum((Decimal(row['values']['min_amount']) for row in operand),Decimal(0))) if operation=='sum' else str(len(operand))
    calculated=calculation_evidence(request_goals(request),facts)
    field='records' if operation=='count' else 'min_amount'
    assert calculated[0]['values'][field]==expected
    incomplete=[{**row,'complete':False} if 'collection_size' in row else row for row in facts]
    if operation=='sum':
        assert calculation_evidence(request_goals(request),incomplete)==[]
    else:
        assert calculation_evidence(request_goals(request),incomplete)[0]['values'][field]==expected


def test_empty_complete_collection_can_be_counted_without_inventing_records():
    request=state([calculation('count',[])])
    facts=project_records('wealth_products',{'code':200,'data':[]},request,fields=['min_amount'])
    computed=calculation_evidence(request_goals(request),facts)
    assert computed[0]['values']['records']=='0'
    plan=expand_calculation_citations({'evidence_ids':[computed[0]['id']],'coverage':[
        {'goal_id':'q1','status':'ANSWERED','evidence_ids':[computed[0]['id']],'reason':'完整空集合计数'}]},[*facts,*computed])
    assert _validate_coverage(plan['coverage'],request_goals(request),[*facts,*computed])


def test_gateway_validates_computation_lineage_before_answering(gateway,monkeypatch):  # noqa: F811
    client,_=gateway
    model(monkeypatch,[goal('wealth_products',['min_amount'],'零星宝起购金额',subject='零星宝'),
                       goal('wealth_products',['min_amount'],'恒泉稳利起购金额',subject='恒泉稳利'),calculation()])
    before=financial_state()
    reply=_send(client,'零星宝与恒泉稳利起购金额各是多少，差额是多少',conversation_id='explicit-calculation')
    assert reply['business_status']=='SUCCEEDED' and '999.00' in reply['reply'] and reply['approval'] is None
    assert financial_state()==before


def test_modified_computation_result_is_rejected_by_final_responder(gateway):  # noqa: F811
    from bank_agent.nodes.responder import _respond
    request=state([calculation()])
    session=EvidenceSession(request)
    session.read({'tool':'wealth_products','fields':['min_amount'],'subjects':['零星宝','恒泉稳利']})
    result=session.result()
    for row in result['data']['documents']:
        if row.get('goal_id')=='q1':
            row['text']='差额为¥9999.00。'
    result['data']['facts']=[row['text'] for row in result['data']['documents']]
    rejected=_respond({**request,'intent':'BANKING_HELP','execution_result':result})
    assert rejected['business_status']=='FAILED' and rejected['execution_result'] is None


def test_contact_count_uses_complete_server_count_without_exposing_contact_details(gateway):  # noqa: F811
    request={'user_id':'user_zhang','user_input':'我的联系人共有多少位','parse_meta':{'goals':normalize_goals([
        {'act':'QUERY','resource':'contacts','fields':['records'],'question':'联系人数量','calculation':{'operation':'count','field':'records'}}],
        '我的联系人共有多少位',{})}}
    session=EvidenceSession(request)
    assert session.read({'tool':'contacts','fields':['records']})
    results=[row for row in session.facts() if row.get('goal_id')=='q1']
    import main
    assert len(results)==1 and int(results[0]['values']['records'])==len(main.USER_CONTACTS.get('user_zhang',[]))


def test_synthesized_aggregate_requires_question_and_complete_collection(gateway):  # noqa: F811
    from decimal import Decimal
    request={'user_id':'user_zhang','user_input':'消费合计为什么不等于当前可用余额？','parse_meta':{
        'goals':normalize_goals([goal('general',[],'消费合计为什么不等于当前可用余额',act='EXPLAIN')],
                                '消费合计为什么不等于当前可用余额',{})}}
    session=EvidenceSession(request)
    assert session.synthesize_aggregate_calculation() is False
    session.read({'tool':'transactions','fields':['amount']})
    assert session.synthesize_aggregate_calculation() is True
    document=session.synthesized[0]
    assert document['id']=='read.transactions.calculation.q1' and document['source']=='calculation://sum'
    computed=Decimal(document['values']['amount'])
    operands=[row for row in session.facts() if row['id'] in document['input_evidence_ids'] and 'values' in row]
    assert operands and sum((Decimal(row['values']['amount']) for row in operands),Decimal(0))==computed
    assert all(row['id'] in {row['id'] for row in session.facts()} for row in session.synthesized)
    assert session.synthesize_aggregate_calculation() is False


def test_synthesized_aggregate_ignores_questions_without_a_total(gateway):  # noqa: F811
    request={'user_id':'user_zhang','user_input':'帮我看下本月流水的类别','parse_meta':{
        'goals':normalize_goals([goal('transactions',['records'],'本月流水类别')],'本月流水类别',{})}}
    session=EvidenceSession(request)
    session.read({'tool':'transactions','fields':['records','amount']})
    assert session.synthesize_aggregate_calculation() is False


def test_amount_less_complete_collection_requests_a_read_then_synthesizes(gateway):  # noqa: F811
    """The 20261031 batch: the phase-2 read projected no amount column."""
    request = {'user_id': 'user_zhang', 'user_input': '消费合计为什么不等于当前可用余额？', 'parse_meta': {
        'goals': normalize_goals([goal('general', [], '消费合计为什么不等于当前可用余额', act='EXPLAIN')],
                                 '消费合计为什么不等于当前可用余额', {})}}
    session = EvidenceSession(request)
    session.read({'tool': 'transactions', 'fields': ['category']})
    operands = [row for row in session.facts()
                if row.get('source') == 'core://transactions' and row.get('entity_id')]
    assert operands and all('amount' not in row.get('values', {}) for row in operands)
    assert session.aggregate_collection_needs_amount_read() is True
    session.read({'tool': 'transactions', 'fields': ['records', 'amount']})
    assert session.synthesize_aggregate_calculation() is True
    document = session.synthesized[0]
    from decimal import Decimal
    computed = Decimal(document['values']['amount'])
    inputs = [row for row in session.facts() if row['id'] in document['input_evidence_ids'] and 'values' in row]
    assert inputs and sum((Decimal(row['values']['amount']) for row in inputs), Decimal(0)) == computed
