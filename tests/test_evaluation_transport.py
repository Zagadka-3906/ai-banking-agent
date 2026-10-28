"""Verify the actual SDK request contract, without a network provider."""
import json

import httpx
import pytest
from langchain_openai import ChatOpenAI
from openai import BadRequestError

from scripts.dialogue_eval.models import json_output_model, SimulatedUser
from scripts.dialogue_eval.costs import CostLedger, MeteredModel
from workflow_runtime import invoke_json_model
from model_config import DEFAULT_MODEL, thinking_options


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
