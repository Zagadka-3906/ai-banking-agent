"""Simulator goal drift is rejected before the bank sees the wrong request."""
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
from langchain_openai import ChatOpenAI
import pytest

from scripts.dialogue_eval.costs import CostLedger, MeteredModel
from scripts.dialogue_eval.engine import run_trial, valid_user_goal_record
from scripts.dialogue_eval.models import ConversationJudge, json_output_model
from scripts.dialogue_eval.schema import load_dataset
from test_dialogue_task_evaluation import Environment, verdict


def cancellation_task():
    item = load_dataset(Path('tests/data/dialogue_repair_regression_v3.json'))[0].tasks[0]
    return item.model_copy(update={'phases': [item.phases[-1]]})


def check(*, faithful=True, expected='WITHDRAW', actual='WITHDRAW'):
    return dict(faithful=faithful, expected_act=expected, actual_act=actual, reason='当前目标与实际提问的行为核对')


@pytest.mark.parametrize('faithful', [True, False])
def test_opposite_request_never_reaches_agent_even_if_fidelity_boolean_is_wrong(faithful):
    item, env = cancellation_task(), Environment()
    judge = SimpleNamespace(verify_user_goal=lambda *args: check(faithful=faithful, actual='ACTION'),
                            assess=lambda *args: pytest.fail('No post-hoc verdict can repair simulator drift'))
    text = '我还是想申购零星宝，麻烦先帮我办理。'
    row = run_trial(item, env, SimpleNamespace(next=lambda *args: {'text': text}), judge,
                    seed=1, trial=1, rubric_version='v3', verify_user_goal=True)
    assert row['status'] == 'ERROR' and row['error_type'] == 'SimulatorDrift'
    assert row['simulator_drift']['user'] == text and row['simulator_drift']['phase'] == 1
    assert env.steps == 0 and row['turns'] == [] and len(row['simulator_goal_checks']) == 1


def test_faithful_user_preflight_runs_before_agent_and_cannot_hide_a_real_agent_failure():
    item, env, order = cancellation_task(), Environment(safe=False), []
    original = env.step
    def step(*args):
        order.append('agent')
        return original(*args)
    env.step = step
    def verify(*args):
        order.append('goal_check')
        return check()
    judge = SimpleNamespace(verify_user_goal=verify, assess=lambda partial, *args: verdict(partial))
    row = run_trial(item, env, SimpleNamespace(next=lambda view, utterance: {'text': utterance}), judge,
                    seed=1, trial=1, verify_user_goal=True)
    assert order == ['goal_check', 'agent'] and row['status'] == 'FAIL' and env.steps == 1
    turn = row['turns'][0]
    assert valid_user_goal_record(turn['user_goal_check'], item.phases[0].goal, turn['user'])


@pytest.mark.parametrize('bad', [{}, {**check(), 'faithful': 'true'}, {**check(), 'actual_act': 'INVENTED'},
                                 {**check(), 'extra': 'private data'}])
def test_malformed_preflight_is_an_error_without_agent_execution_or_retry(bad):
    item, env, calls = cancellation_task(), Environment(), []
    def verify(*args):
        calls.append(args)
        return bad
    row = run_trial(item, env, SimpleNamespace(next=lambda view, utterance: {'text': utterance}),
                    SimpleNamespace(verify_user_goal=verify), seed=1, trial=1, verify_user_goal=True)
    assert row['status'] == 'ERROR' and env.steps == 0 and len(calls) == 1
    assert row['simulator_goal_checks'] == [{'phase': 1, 'user': item.phases[0].utterance, 'check': None}]


@pytest.mark.parametrize('action,act', [('inquiry', 'INQUIRE'), ('clarify', 'ACTION'), ('proposal', 'ACTION')])
def test_every_simulated_phase_checks_its_goal_and_preserves_valid_request(action, act):
    item, env = cancellation_task(), Environment()
    item.phases[0] = item.phases[0].model_copy(update={'action': action})
    judge = SimpleNamespace(verify_user_goal=lambda *args: check(expected=act, actual=act),
                            assess=lambda partial, *args: verdict(partial))
    row = run_trial(item, env, SimpleNamespace(next=lambda view, utterance: {'text': utterance}), judge,
                    seed=1, trial=1, verify_user_goal=True)
    assert env.steps == 1 and len(row['simulator_goal_checks']) == 1
    assert valid_user_goal_record(row['turns'][0]['user_goal_check'], item.phases[0].goal, item.phases[0].utterance)


def test_preflight_timeout_retains_the_user_message_and_never_calls_bank():
    item, env = cancellation_task(), Environment()
    def verify(*args):
        raise TimeoutError('test verifier timeout')
    row = run_trial(item, env, SimpleNamespace(next=lambda view, utterance: {'text': utterance}),
                    SimpleNamespace(verify_user_goal=verify), seed=1, trial=1, verify_user_goal=True)
    assert row['status'] == 'ERROR' and row['error_type'] == 'TimeoutError' and env.steps == 0
    assert row['simulator_goal_checks'] == [{'phase': 1, 'user': item.phases[0].utterance, 'check': None}]


def test_verifier_only_sees_public_goal_facts_and_previous_user_messages():
    captured = []
    judge = ConversationJudge(SimpleNamespace(invoke=lambda messages: captured.extend(messages)
        or SimpleNamespace(content=json.dumps(check()))), 'v3')
    item = cancellation_task()
    view = item.user_view(item.phases[0], [{'role': 'user', 'text': '之前申购'},
                                         {'role': 'assistant', 'text': 'PRIVATE_ASSISTANT_REPLY'}])
    result = judge.verify_user_goal(view, '先不申购了')
    payload = json.loads(captured[-1].content)
    assert result['faithful'] and set(payload) == {'current_goal', 'known_facts', 'previous_user_messages', 'user_text'}
    assert payload['previous_user_messages'] == ['之前申购']
    assert 'PRIVATE_ASSISTANT_REPLY' not in captured[-1].content and item.phases[0].criteria not in captured[-1].content
    assert 'PRIVATE_FIXED_UTTERANCE' not in captured[-1].content


def test_preflight_is_json_metered_once_and_has_no_score_retry(tmp_path):
    requests = []
    ledger = CostLedger.configure(tmp_path/'meter.sqlite3', max_calls=2)
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body['response_format'] == {'type': 'json_object'}
        return httpx.Response(200, json={'id': 'test', 'object': 'chat.completion', 'created': 1, 'model': 'test',
            'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30},
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': json.dumps(check(faithful=False, actual='ACTION'))},
                         'finish_reason': 'stop'}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sdk = json_output_model(ChatOpenAI(model='test', api_key='test-only', base_url='http://model.invalid/v1',
                                          http_client=client, max_retries=0))
        judge = ConversationJudge(MeteredModel(sdk, ledger, 'preflight', 'judge', 'test'), 'v3')
        item = cancellation_task()
        result = judge.verify_user_goal(item.user_view(item.phases[0], []), '我还是想申购零星宝')
    assert result['faithful'] is False and len(requests) == ledger.snapshot()['calls'] == 1
    assert ledger.snapshot()['roles']['judge']['input_tokens'] == 20
    assert judge.requests == 1 and judge.transport_failures == []
