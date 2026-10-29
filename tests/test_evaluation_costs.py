"""Verify admission and real SDK usage without contacting a paid provider."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest
from langchain_openai import ChatOpenAI

from scripts.dialogue_eval.costs import (CostLedger, EvaluationStopped, MeteredModel, evaluation_values,
    nested_cost_arguments, token_usage)
from scripts import evaluate_dialogue_tasks as evaluator


PRICES = {'test': {'input_cached': '.1', 'input_uncached': '1', 'output': '2', 'context_tokens': 1000}}


def ledger(tmp_path, **limits):
    return CostLedger.configure(tmp_path/'cost.sqlite3', prices=PRICES, max_output_tokens=100, **limits)


def answer(incoming=100, outgoing=20, cached=50):
    return SimpleNamespace(usage_metadata={'input_tokens': incoming, 'output_tokens': outgoing,
                                          'input_token_details': {'cache_read': cached}})


def test_cache_usage_and_reasoning_are_not_double_counted(tmp_path):
    budget = ledger(tmp_path)
    response = answer()
    response.usage_metadata['output_token_details'] = {'reasoning': 15}
    model = MeteredModel(SimpleNamespace(invoke=lambda *args, **kwargs: response), budget, 'run', 'agent', 'test')
    model.invoke([])
    data = budget.snapshot()['roles']['agent']
    assert data['input_tokens'] == 100 and data['output_tokens'] == 20
    assert data['cache_hit_tokens'] == data['cache_miss_tokens'] == 50
    assert Decimal(data['estimated_or_reserved_cny']) == Decimal('.000095')


@pytest.mark.parametrize('accepted', [True, False])
def test_real_sdk_request_is_capped_and_402_stops_every_role(tmp_path, accepted):
    requests = []
    budget = ledger(tmp_path)
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload['max_tokens'] == 100
        assert 'max_completion_tokens' not in payload
        if not accepted:
            return httpx.Response(402, json={'error': {'message': 'SECRET_KEY must not be saved', 'type': 'insufficient_balance_error'}})
        return httpx.Response(200, json={'id': 'test', 'object': 'chat.completion', 'created': 1, 'model': 'test',
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120,
                      'prompt_cache_hit_tokens': 50, 'prompt_cache_miss_tokens': 50},
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop'}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sdk = ChatOpenAI(model='test', api_key='test-only', base_url='http://model.invalid/v1', http_client=client, max_retries=0)
        model = MeteredModel(sdk, budget, 'run', 'user', 'test')
        if accepted:
            model.invoke('test', max_tokens=9000)
            assert budget.snapshot()['roles']['user']['cache_hit_tokens'] == 50
        else:
            with pytest.raises(Exception):
                model.invoke('test')
            with pytest.raises(EvaluationStopped, match='INSUFFICIENT_BALANCE'):
                MeteredModel(sdk, budget, 'run', 'judge', 'test').invoke('never sent')
            snapshot = budget.snapshot()
            assert snapshot['calls'] == 1 and snapshot['estimated_or_reserved_cny'] == '0'
            assert 'SECRET_KEY' not in json.dumps(snapshot)
    assert len(requests) == 1


def test_parallel_threads_cannot_each_spend_the_same_remaining_budget(tmp_path):
    budget = ledger(tmp_path, max_cost_cny='.003')
    def reserve(index):
        try:
            return budget.reserve('run', 'agent', 'test')[0]
        except EvaluationStopped:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        reservations = list(pool.map(reserve, range(8)))
    assert sum(value is not None for value in reservations) == 2
    assert budget.snapshot()['calls'] == 2
    assert Decimal(budget.snapshot()['estimated_or_reserved_cny']) == Decimal('.0024')


def test_independent_processes_share_atomic_call_admission(tmp_path):
    budget = ledger(tmp_path, max_calls=1)
    source = ('from pathlib import Path; import sys; '
              'from scripts.dialogue_eval.costs import CostLedger; '
              'CostLedger(Path(sys.argv[1])).reserve("process", "agent", "test")')
    processes = [subprocess.Popen([sys.executable, '-c', source, str(budget.path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for _ in range(3)]
    results = [process.communicate(timeout=30) for process in processes]
    assert len(results) == 3
    assert sum(process.returncode == 0 for process in processes) == 1
    assert budget.snapshot()['calls'] == 1


def test_timeout_keeps_its_possible_charge_and_repeated_errors_stop(tmp_path):
    budget = ledger(tmp_path)
    for _ in range(3):
        identifier, _ = budget.reserve('run', 'agent', 'test')
        budget.failed(identifier, TimeoutError('PRIVATE_REQUEST'))
    data = budget.snapshot()
    assert data['stop_reason'] == 'CONSECUTIVE_PROVIDER_ERRORS'
    assert Decimal(data['estimated_or_reserved_cny']) == Decimal('.0036')
    with pytest.raises(EvaluationStopped):
        budget.reserve('run', 'judge', 'test')
    assert 'PRIVATE_REQUEST' not in json.dumps(data)


def test_missing_usage_never_becomes_a_free_success(tmp_path):
    budget = ledger(tmp_path, max_cost_cny='.01')
    identifier, _ = budget.reserve('run', 'agent', 'test')
    budget.completed(identifier, SimpleNamespace())
    data = budget.snapshot()
    assert data['stop_reason'] == 'MISSING_TOKEN_USAGE'
    assert Decimal(data['estimated_or_reserved_cny']) == Decimal('.0012')


@pytest.mark.parametrize('usage', [
    {'input_tokens': True, 'output_tokens': 1}, {'input_tokens': -1, 'output_tokens': 1},
    {'input_tokens': 1, 'output_tokens': 2, 'input_token_details': {'cache_read': 3}},
])
def test_invalid_token_metadata_cannot_reduce_reserved_cost(usage):
    assert token_usage(SimpleNamespace(usage_metadata=usage)) is None


def test_unknown_model_has_no_fake_zero_cost_and_cannot_use_currency_cap(tmp_path):
    with pytest.raises(ValueError, match='EXPLICIT_PRICES'):
        CostLedger.configure(tmp_path/'reject.sqlite3', max_cost_cny=2, models=['legacy-alias'])
    budget = ledger(tmp_path)
    identifier, _ = budget.reserve('run', 'agent', 'legacy-alias')
    budget.completed(identifier, answer())
    assert budget.snapshot()['estimated_or_reserved_cny'] is None
    assert budget.snapshot()['roles']['agent']['input_tokens'] == 100


def test_existing_campaign_cannot_silently_change_or_reset_limits(tmp_path):
    budget = ledger(tmp_path, max_calls=1)
    budget.reserve('run', 'user', 'test')
    with pytest.raises(ValueError, match='POLICY_MISMATCH'):
        CostLedger.configure(budget.path, max_calls=2, prices=PRICES, max_output_tokens=100)
    assert budget.snapshot()['calls'] == 1


def test_evaluation_overrides_do_not_modify_parent_or_reveal_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'production-fixture')
    env = tmp_path/'eval.env'
    env.write_text('BANK_EVAL_AGENT_API_KEY=separate-fixture\nBANK_EVAL_AGENT_MODEL=test\n', encoding='utf-8')
    values = evaluation_values(tmp_path, env)
    import os
    assert values['DEEPSEEK_API_KEY'] == 'separate-fixture' and values['BANK_LLM_MODEL'] == 'test'
    assert os.environ['DEEPSEEK_API_KEY'] == 'production-fixture'


def test_cli_stops_after_first_402_and_preserves_all_unrun_trials(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluator, 'ROOT', tmp_path)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    monkeypatch.setattr(evaluator, 'source_fingerprints', lambda: {'source.py': 'fixed'})
    monkeypatch.setattr(evaluator.subprocess, 'check_output', lambda command, **kwargs: 'fixture' if 'rev-parse' in command else '')
    workers = []
    def worker(command, **kwargs):
        job = json.loads(Path(command[-1]).read_text(encoding='utf-8'))
        workers.append(job)
        budget = CostLedger(Path(job['budget_ledger']))
        identifier, _ = budget.reserve(job['cost_run'], 'user', 'test')
        error = RuntimeError('SECRET_PROVIDER_BODY')
        error.status_code = 402
        budget.failed(identifier, error)
        Path(job['result']).write_text(json.dumps({'task_id': job['task']['id'], 'trial': job['trial'], 'seed': job['seed'],
            'status': 'ERROR', 'error_type': 'EvaluationStopped', 'turns': [], 'checks': []}), encoding='utf-8')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(evaluator.subprocess, 'run', worker)
    cases = Path(__file__).parent/'data/dialogue_tasks.json'
    task_id = json.loads(cases.read_text(encoding='utf-8'))['tasks'][0]['id']
    campaign = tmp_path/'build/campaign.sqlite3'
    for index in range(2):
        output = tmp_path/f'build/run{index}'
        assert evaluator.main(['--live', '--unlimited-cost', '--trials', '5', '--task', task_id, '--cases', str(cases),
                               '--output-dir', str(output), '--budget-ledger', str(campaign)]) == 2
        report = json.loads((output/'report.json').read_text(encoding='utf-8'))
        assert report['status'] == 'STOPPED' and not report['summary']['complete']
        assert report['summary']['not_run'] == (4 if index == 0 else 5)
        assert report['cost']['campaign']['calls'] == 1
        assert 'SECRET_PROVIDER_BODY' not in json.dumps(report)
    assert len(workers) == 1


def test_nested_optimization_reuses_the_campaign_budget(tmp_path):
    budget = ledger(tmp_path)
    args = SimpleNamespace(max_model_calls=2000, max_output_tokens=100, max_cost_cny=Decimal('2'), price_file=None, eval_env_file=None)
    arguments = nested_cost_arguments(args, budget)
    assert arguments[:2] == ['--budget-ledger', str(budget.path)]
    assert '--max-cost-cny' in arguments and '--unlimited-cost' not in arguments


@pytest.mark.parametrize('status,reason', [('FAIL', 'TASK_FAILED'), ('ERROR', 'TRIAL_ERROR')])
def test_stop_on_failure_stops_shared_campaign_after_first_failure_or_simulator_error(tmp_path, monkeypatch, status, reason):
    monkeypatch.setattr(evaluator, 'ROOT', tmp_path)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    monkeypatch.setattr(evaluator, 'source_fingerprints', lambda: {'source.py': 'fixed'})
    monkeypatch.setattr(evaluator.subprocess, 'check_output', lambda command, **kwargs: 'fixture' if 'rev-parse' in command else '')
    workers = []
    def worker(command, **kwargs):
        job = json.loads(Path(command[-1]).read_text(encoding='utf-8'))
        workers.append(job)
        budget = CostLedger(Path(job['budget_ledger']))
        identifier, _ = budget.reserve(job['cost_run'], 'user', 'deepseek-flash')
        budget.completed(identifier, answer())
        Path(job['result']).write_text(json.dumps({'task_id': job['task']['id'], 'trial': job['trial'], 'seed': job['seed'],
            'status': status, 'error_type': 'SimulatorDrift' if status == 'ERROR' else None, 'turns': [], 'checks': []}), encoding='utf-8')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(evaluator.subprocess, 'run', worker)
    cases = Path(__file__).parent/'data/dialogue_tasks.json'
    task_id = json.loads(cases.read_text(encoding='utf-8'))['tasks'][0]['id']
    campaign = tmp_path/'build/campaign.sqlite3'
    for index in range(2):
        output = tmp_path/f'build/run{index}'
        assert evaluator.main(['--live', '--unlimited-cost', '--stop-on-failure', '--trials', '5', '--task', task_id,
                               '--cases', str(cases), '--output-dir', str(output), '--budget-ledger', str(campaign)]) == 2
        report = json.loads((output/'report.json').read_text(encoding='utf-8'))
        assert report['status'] == 'STOPPED' and report['stop_reason'] == reason
        assert report['summary']['not_run'] == (4 if index == 0 else 5)
        assert report['cost']['campaign']['calls'] == 1
        assert len(report['trials']) == (1 if index == 0 else 0)
    assert len(workers) == 1


def test_optimization_reflection_is_counted_in_the_same_campaign(tmp_path, monkeypatch):
    from langchain_openai import ChatOpenAI
    from scripts.optimize_dialogue_prompts import BankingAdapter
    budget = ledger(tmp_path)
    requests = []
    def invoke(self, messages, config=None, **kwargs):
        requests.append(kwargs)
        return SimpleNamespace(content='{"answering":"逐项回答"}', usage_metadata=answer().usage_metadata)
    monkeypatch.setattr(ChatOpenAI, 'invoke', invoke)
    adapter = BankingAdapter(tmp_path/'cases.json', tmp_path, 1, ledger=budget, cost_arguments=[],
                             values={'DEEPSEEK_API_KEY': 'test-only', 'BANK_LLM_MODEL': 'test'})
    assert adapter.propose_new_texts({}, {}, ['answering']) == {'answering': '逐项回答'}
    assert budget.snapshot()['roles']['reflection']['calls'] == 1
    assert requests[0]['extra_body']['max_tokens'] == 100


def test_live_evaluation_requires_a_deliberate_budget_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluator, 'ROOT', tmp_path)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    cases = Path(__file__).parent/'data/dialogue_tasks.json'
    output = tmp_path/'build/not-started'
    with pytest.raises(SystemExit):
        evaluator.main(['--live', '--cases', str(cases), '--output-dir', str(output)])
    assert not output.exists()
