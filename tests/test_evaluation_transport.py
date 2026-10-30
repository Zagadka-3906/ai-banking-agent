"""Verify the actual SDK request contract, without a network provider."""
import json
from types import SimpleNamespace

import httpx
import pytest
from langchain_openai import ChatOpenAI
from openai import BadRequestError, APIStatusError, APITimeoutError, APIConnectionError

from scripts.dialogue_eval.models import json_output_model, SimulatedUser, ask, EvaluationJSONError, valid_transport_diagnostics
from scripts.dialogue_eval.costs import CostLedger, MeteredModel
from workflow_runtime import invoke_json_model
from model_config import DEFAULT_MODEL, thinking_options
from scripts.scenario_backend import ObservedScenarioModel


@pytest.mark.parametrize('accepted', [True, False])
def test_evaluation_requests_structured_json_without_silent_fallback(accepted):
    requests = []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload['response_format'] == {'type': 'json_object'}
        if not accepted:
            return httpx.Response(400, json={'error': {'message': 'unsupported format', 'type': 'invalid_request_error'}})
        return httpx.Response(200, json={'id': 'test', 'object': 'chat.completion', 'created': 1, 'model': 'test',
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': '{"text":"查余额"}'}, 'finish_reason': 'stop'}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = json_output_model(ChatOpenAI(model='test', api_key='test-only-not-a-real-key',
            base_url='http://model.invalid/v1', http_client=client, max_retries=0))
        user = SimulatedUser(model, 1, 'v3')
        if accepted:
            assert user.next({'goal': '查余额'}, '') == {'text': '查余额'}
        else:
            with pytest.raises(BadRequestError):
                user.next({'goal': '查余额'}, '')
    assert len(requests) == 1


@pytest.mark.parametrize('accepted', [True, False])
def test_agent_json_wire_format_preserves_metering_and_never_retries_plain_text(tmp_path, accepted):
    requests = []
    ledger = CostLedger.configure(tmp_path/'cost.sqlite3', max_calls=3)
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload['response_format'] == {'type': 'json_object'} and payload['max_tokens'] == 4096
        assert payload['thinking'] == {'type': 'disabled'}
        assert 'JSON object' in payload['messages'][0]['content']
        if not accepted:
            return httpx.Response(400, json={'error': {'message': 'unsupported format', 'type': 'invalid_request_error'}})
        return httpx.Response(200, json={'id': 'test', 'object': 'chat.completion', 'created': 1, 'model': 'test',
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': '{"answer":"查询完成"}'}, 'finish_reason': 'stop'}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sdk = ChatOpenAI(model='test', api_key='test-only', base_url='http://model.invalid/v1', http_client=client,
                        max_retries=0, extra_body=thinking_options('disabled'))
        stages = {}
        model = MeteredModel(sdk, ledger, 'agent-run', 'agent', 'test', stage_usage=stages)
        if accepted:
            assert json.loads(invoke_json_model(model, [('user', 'Return JSON')]).content)['answer'] == '查询完成'
            assert ledger.snapshot()['roles']['agent']['input_tokens'] == 10
        else:
            with pytest.raises(BadRequestError):
                invoke_json_model(model, [('user', 'Return JSON')])
    assert len(requests) == ledger.snapshot()['calls'] == 1
    assert stages['other']['calls'] == 1 and 'content' not in str(stages)


def test_flash_is_default_and_invalid_thinking_mode_cannot_silently_fall_back(monkeypatch):
    assert DEFAULT_MODEL == 'deepseek-flash'
    monkeypatch.delenv('BANK_MODEL_THINKING', raising=False)
    assert thinking_options() == {'thinking': {'type': 'disabled'}}
    assert thinking_options('') == {} and thinking_options('enabled') == {'thinking': {'type': 'enabled'}}
    with pytest.raises(ValueError, match='INVALID_MODEL_THINKING'):
        thinking_options('unknown-mode')


@pytest.mark.parametrize('accepted', [True, False])
def test_scenario_observer_preserves_json_binding_and_costs_without_private_logs(tmp_path, accepted):
    requests, records = [], []
    ledger = CostLedger.configure(tmp_path/'cost.sqlite3', max_calls=3)
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload['response_format'] == {'type': 'json_object'}
        assert payload['thinking'] == {'type': 'disabled'} and payload['max_tokens'] == 4096
        if not accepted:
            return httpx.Response(400, json={'error': {'message': 'PRIVATE provider response', 'type': 'invalid_request_error'}})
        return httpx.Response(200, json={'id': 'test', 'object': 'chat.completion', 'created': 1, 'model': 'test',
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': '{"answer":"查询完成"}'}, 'finish_reason': 'stop'}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sdk = ChatOpenAI(model='test', api_key='test-only', base_url='http://model.invalid/v1', http_client=client,
                        max_retries=0, extra_body=thinking_options('disabled'))
        observed = ObservedScenarioModel(MeteredModel(sdk, ledger, 'scenario', 'agent', 'test'), records)
        if accepted:
            assert json.loads(invoke_json_model(observed, [('user', 'Return JSON')]).content)['answer'] == '查询完成'
            assert ledger.snapshot()['roles']['agent']['input_tokens'] == 10
        else:
            with pytest.raises(BadRequestError):
                invoke_json_model(observed, [('user', 'Return JSON')])
    assert len(requests) == len(records) == ledger.snapshot()['calls'] == 1
    assert records[0]['success'] is accepted
    assert set(records[0]) == ({'success', 'elapsed_ms'} if accepted else {'success', 'elapsed_ms', 'error_type'})
    assert 'PRIVATE' not in str(records) and 'content' not in str(records)


def test_format_repair_stays_structured_preserves_task_and_meters_both_attempts(tmp_path):
    requests = []
    ledger = CostLedger.configure(tmp_path/'cost.sqlite3', max_calls=3)
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload['response_format'] == {'type': 'json_object'}
        assert payload['max_tokens'] == 4096 and payload['thinking'] == {'type': 'disabled'}
        content = '只查本人余额，先不办理。' if len(requests) == 1 else '{"text":"只查本人余额，先不办理。"}'
        return httpx.Response(200, json={'id': 'test', 'object': 'chat.completion', 'created': 1, 'model': 'test',
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sdk = json_output_model(ChatOpenAI(model='test', api_key='test-only', base_url='http://model.invalid/v1',
            http_client=client, max_retries=0, extra_body=thinking_options('disabled')))
        user = SimulatedUser(MeteredModel(sdk, ledger, 'format-run', 'user', 'test'), 1, 'v3')
        assert user.next({'current_goal': '只查本人余额，不办理'}, '') == {'text': '只查本人余额，先不办理。'}
    assert len(requests) == ledger.snapshot()['calls'] == 2
    assert requests[1]['messages'][:2] == requests[0]['messages']
    assert 'format_retry' in requests[1]['messages'][-1]['content']
    assert user.transport_failures == [{'request': 1, 'attempt': 1, 'error_code': 'MALFORMED_JSON',
                                        'response_chars': len('只查本人余额，先不办理。'), 'retried': True}]


@pytest.mark.parametrize('content', ['private malformed output', '[]', 'null'])
def test_unrepaired_format_errors_remain_errors_without_exposing_output(content):
    calls, diagnostics = [], []
    def invoke(messages):
        calls.append(messages)
        return SimpleNamespace(content=content)
    with pytest.raises(EvaluationJSONError, match='^EVALUATION_JSON_OBJECT_REQUIRED$'):
        ask(SimpleNamespace(invoke=invoke), 'Return JSON', {'task': 'original'}, diagnostics=diagnostics)
    assert len(calls) == len(diagnostics) == 2
    assert diagnostics[-1]['retried'] is False
    assert content not in str(diagnostics) and content not in calls[-1][-1].content


@pytest.mark.parametrize('value', [{'passed': False}, {'text': '偏离原目标的办理请求'}, {'missing': 'schema'}])
def test_valid_json_scores_or_schema_or_goal_errors_are_never_format_retried(value):
    calls, diagnostics = [], []
    def invoke(messages):
        calls.append(messages)
        return SimpleNamespace(content=json.dumps(value))
    assert ask(SimpleNamespace(invoke=invoke), 'Return JSON', {}, diagnostics=diagnostics) == value
    assert len(calls) == 1 and not diagnostics


@pytest.mark.parametrize('first', [{'utterance': '查余额'}, {'text': '查余额', 'extra': 1}, {'text': ''}])
def test_simulated_user_schema_mismatch_uses_the_single_format_retry(first):
    calls = []
    def invoke(messages):
        calls.append(messages)
        return SimpleNamespace(content=json.dumps(first if len(calls) == 1 else {'text': '查余额'}, ensure_ascii=False))
    user = SimulatedUser(SimpleNamespace(invoke=invoke), 1, 'v3')
    assert user.next({'current_goal': '查余额'}, '') == {'text': '查余额'}
    assert len(calls) == 2 and 'format_retry' in calls[1][-1].content
    assert user.transport_failures == [{'request': 1, 'attempt': 1, 'error_code': 'SCHEMA_INVALID',
                                        'response_chars': len(json.dumps(first, ensure_ascii=False)), 'retried': True}]
    assert valid_transport_diagnostics(user.transport_failures, successful=True)


def test_simulated_user_repeated_schema_mismatch_remains_an_evaluation_error():
    calls = []
    def invoke(messages):
        calls.append(messages)
        return SimpleNamespace(content='{"utterance":"查余额"}')
    user = SimulatedUser(SimpleNamespace(invoke=invoke), 1, 'v3')
    with pytest.raises(EvaluationJSONError):
        user.next({'current_goal': '查余额'}, '')
    assert len(calls) == 2 and [row['retried'] for row in user.transport_failures] == [True, False]


@pytest.mark.parametrize('sequence,error_type,code', [
    (['timeout', 200], None, 'API_TIMEOUT'),
    (['connection', 200], None, 'API_CONNECTION'),
    ([408, 200], None, 'API_RETRYABLE_STATUS'),
    ([429, 200], None, 'API_RETRYABLE_STATUS'),
    ([503, 200], None, 'API_RETRYABLE_STATUS'),
    (['timeout', 'timeout'], APITimeoutError, 'API_TIMEOUT'),
    (['connection', 'connection'], APIConnectionError, 'API_CONNECTION'),
    ([503, 503], APIStatusError, 'API_RETRYABLE_STATUS'),
    ([400], APIStatusError, None),
    ([401], APIStatusError, None),
    (['quota'], APIStatusError, None),
    (['malformed', 503, 200], None, 'MALFORMED_JSON'),
])
def test_evaluator_retries_only_bounded_transport_errors_with_identical_payload_and_full_costs(tmp_path, sequence, error_type, code):
    requests, diagnostics = [], []
    ledger = CostLedger.configure(tmp_path/'cost.sqlite3', max_calls=4)
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload['response_format'] == {'type': 'json_object'}
        assert payload['thinking'] == {'type': 'disabled'}
        outcome = sequence[len(requests)-1]
        if outcome == 'timeout':
            raise httpx.ReadTimeout('PRIVATE timeout body', request=request)
        if outcome == 'connection':
            raise httpx.ConnectError('PRIVATE connection body', request=request)
        if outcome == 'quota' or isinstance(outcome, int) and outcome != 200:
            return httpx.Response(429 if outcome == 'quota' else outcome,
                json={'error': {'message': 'PRIVATE error body', 'type': 'api_error',
                                'code': 'insufficient_quota' if outcome == 'quota' else 'temporary'}})
        return httpx.Response(200, json={'id': 'test', 'object': 'chat.completion', 'created': 1, 'model': 'test',
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'broken format' if outcome == 'malformed' else '{"faithful":false}'}, 'finish_reason': 'stop'}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sdk = json_output_model(ChatOpenAI(model='test', api_key='test-only', base_url='http://model.invalid/v1',
            http_client=client, max_retries=0, extra_body=thinking_options('disabled')))
        model = MeteredModel(sdk, ledger, 'transport', 'judge', 'test')
        if error_type is not None:
            with pytest.raises(error_type):
                ask(model, 'Return JSON', {'goal': 'original action'}, diagnostics=diagnostics)
        else:
            assert ask(model, 'Return JSON', {'goal': 'original action'}, diagnostics=diagnostics) == {'faithful': False}
    assert len(requests) == ledger.snapshot()['calls'] == len(sequence)
    assert valid_transport_diagnostics(diagnostics, successful=error_type is None)
    assert 'PRIVATE' not in str(diagnostics)
    if code is not None:
        assert diagnostics[0]['error_code'] == code and diagnostics[0]['retried'] is True
    else:
        assert diagnostics == []
    if sequence[0] == 'malformed':
        assert requests[1] == requests[2] and requests[1]['messages'][:2] == requests[0]['messages']
    elif len(requests) > 1:
        assert requests[0] == requests[1]
    if error_type is not None and code is not None:
        assert diagnostics[-1]['retried'] is False
    assert ledger.snapshot()['roles']['judge']['usage_unknown_calls'] == sum(value not in (200, 'malformed') for value in sequence)
