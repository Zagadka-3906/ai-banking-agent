"""Regressions exercise wrong model decisions and bounded concurrent execution."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from types import SimpleNamespace

import pytest

from bank_agent import runtime
from bank_agent.dialogue_state import resume_task
from bank_agent.extraction import target_from_text
from test_agent_gateway import gateway, _send  # noqa: F401
from test_dialogue_commands import pending_state
from test_inquiry_boundaries import financial_state
from workflow_runtime import invoke_model


def test_three_concurrent_model_calls_queue_and_keep_context(monkeypatch):
    import workflow_runtime as workflow
    slots = threading.BoundedSemaphore(3)
    monkeypatch.setattr(workflow, '_model_slots', slots)
    monkeypatch.setenv('BANK_MODEL_TIMEOUT_SECONDS', '2')
    release = threading.Event()
    started = threading.Barrier(3)
    channel = ContextVar('test_model_channel', default='missing')
    calls = []
    lock = threading.Lock()
    def model(_):
        with lock:
            calls.append(channel.get())
            index = len(calls)
        if index <= 2:
            started.wait(timeout=2)
            assert release.wait(2)
        return channel.get()
    def call(value):
        channel.set(value)
        return invoke_model(SimpleNamespace(invoke=model), [])
    with ThreadPoolExecutor(3) as pool:
        futures = [pool.submit(call, str(index)) for index in range(3)]
        try:
            started.wait(timeout=2)
            assert len(calls) == 2
        finally:
            release.set()
        assert [future.result() for future in futures] == ['0', '1', '2']


def test_timeout_cancels_queued_work_without_running_it_later(monkeypatch):
    import workflow_runtime as workflow
    entered = threading.Event()
    release = threading.Event()
    calls = []
    monkeypatch.setenv('BANK_MODEL_TIMEOUT_SECONDS', '0.1')
    def slow(_):
        entered.set()
        release.wait(2)
    with ThreadPoolExecutor(1) as isolated:
        monkeypatch.setattr(workflow, '_model_pool', isolated)
        try:
            with pytest.raises(TimeoutError):
                invoke_model(SimpleNamespace(invoke=slow), [])
            assert entered.is_set()
            with pytest.raises(TimeoutError):
                invoke_model(SimpleNamespace(invoke=lambda _: calls.append('late')), [])
        finally:
            release.set()
    assert calls == []


def test_capacity_failure_is_not_misreported_as_unknown_business(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    def unavailable(*args, **kwargs):
        raise TimeoutError('MODEL_CAPACITY_EXHAUSTED')
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=unavailable))
    before = financial_state()
    reply = _send(client, '给李四转300元', conversation_id='capacity')
    assert reply['business_status'] == 'FAILED' and reply['approval'] is None
    assert '繁忙' in reply['reply'] and '原任务保留' in reply['reply']
    assert financial_state() == before


@pytest.mark.parametrize('text', ['给李四预约一笔转账', '向王美定时转账', '给刘宗屏预约明天转300元',
                                '给李四这笔转账的金额补充为300元', '给李四的转账金额补300元'])
def test_schedule_language_is_not_part_of_the_recipient(text):
    assert target_from_text(text) in {'李四', '王美', '刘宗屏'}


def test_explicit_resume_id_still_verifies_current_literal_amount_before_splitting():
    text = '回到刚才的转账，给李四转300元，先别确认。'
    parsed = {'intent': 'TRANSFER', 'parse_meta': {'dialogue_act': 'ACTION', 'resume_task_id': 'TRANSFER'}}
    restored = resume_task(pending_state(), parsed, text)
    assert restored and restored['_verified_slot_resume'] == 300
    assert restored['payload']['target'] == '李四'


@pytest.mark.parametrize('text', ['回到刚才的转账，给李四转300元，先别确认。', '那先接着原来的转账，给李四转300元，暂不确认。'])
def test_continuation_reference_is_not_an_extra_incomplete_operation(text):
    from intent_language import split_tasks
    from bank_agent.parsing import local_parse
    assert len(split_tasks(text)) == 1
    parsed = local_parse(text)
    assert parsed['target'] == '李四' and parsed['amount'] == 300


def test_withdrawal_without_a_frame_is_valid_dialogue_not_unsupported(gateway, monkeypatch):  # noqa: F811
    client, _ = gateway
    monkeypatch.setattr(runtime, 'llm', SimpleNamespace(invoke=lambda _: SimpleNamespace(content=json.dumps(
        {'intent': 'UNKNOWN', 'dialogue_act': 'WITHDRAW', 'scope': 'IN_SCOPE'}))))
    before = financial_state()
    reply = _send(client, '我不冻结了，放弃冻结卡片。', conversation_id='withdraw-act')
    assert reply['approval'] is None and '本轮不继续办理' in reply['reply']
    assert '未接入' not in reply['reply'] and financial_state() == before
