"""Bounded inference speculation cannot change semantics, deadlines or admission."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from types import SimpleNamespace

import pytest

import workflow_runtime as workflow


@pytest.fixture
def isolated_pool(monkeypatch):
    deadline = time.monotonic()+2
    while workflow._model_inflight and time.monotonic() < deadline:
        time.sleep(.005)
    assert workflow._model_inflight == 0
    pool = ThreadPoolExecutor(max_workers=2)
    monkeypatch.setattr(workflow, '_model_pool', pool)
    monkeypatch.setattr(workflow, '_model_slots', threading.BoundedSemaphore(10))
    monkeypatch.setattr(workflow, '_UNDERSTANDING_HEDGE_DELAY_SECONDS', .03)
    monkeypatch.setenv('BANK_MODEL_TIMEOUT_SECONDS', '.3')
    events = []
    monkeypatch.setattr(workflow, 'audit_event', lambda action, **kwargs: events.append((action, kwargs)))
    try:
        yield events
    finally:
        pool.shutdown(wait=True)
        assert workflow._model_inflight == 0


def understand(model):
    with workflow.understanding_tail_hedge():
        return workflow.invoke_json_model(model, ['PRIVATE-INPUT'])


def wait_count(expected):
    deadline = time.monotonic()+.5
    while workflow._model_inflight != expected and time.monotonic() < deadline:
        time.sleep(.002)
    assert workflow._model_inflight == expected


def test_fast_response_is_one_request_and_timing_never_contains_input(isolated_pool):
    calls = []
    model = SimpleNamespace(invoke=lambda messages: calls.append(messages) or SimpleNamespace(content='{}'))
    assert understand(model).content == '{}' and len(calls) == 1
    assert all(action != 'MODEL_UNDERSTANDING_HEDGE' for action, _ in isolated_pool)
    assert 'PRIVATE-INPUT' not in str(isolated_pool)


def test_idle_secondary_can_win_but_the_late_primary_keeps_its_slot(isolated_pool):
    release, calls = threading.Event(), []
    def invoke(messages):
        calls.append(messages)
        if len(calls) == 1:
            release.wait(2)
            return SimpleNamespace(content='{"intent":"TRANSFER","amount":999}')
        return SimpleNamespace(content='{"intent":"BALANCE_QUERY"}')
    try:
        result = understand(SimpleNamespace(invoke=invoke))
        assert result.content == '{"intent":"BALANCE_QUERY"}' and len(calls) == 2
        assert calls[0] == calls[1]
        wait_count(1)
        assert workflow.invoke_model(SimpleNamespace(invoke=lambda messages: 'separate'), []) == 'separate'
        wait_count(1)
        assert workflow.wait_for_model_calls(.01) is False
    finally:
        release.set()


def test_malformed_first_response_is_not_replaced_by_a_later_better_answer(isolated_pool):
    release, calls = threading.Event(), []
    def invoke(messages):
        calls.append(messages)
        if len(calls) == 1:
            time.sleep(.06)
            return SimpleNamespace(content='INVALID_JSON')
        release.wait(2)
        return SimpleNamespace(content='{"valid":true}')
    try:
        assert understand(SimpleNamespace(invoke=invoke)).content == 'INVALID_JSON'
        assert len(calls) == 2
        wait_count(1)
    finally:
        release.set()


def test_shared_deadline_does_not_restart_for_the_secondary(isolated_pool, monkeypatch):
    release, calls = threading.Event(), []
    monkeypatch.setenv('BANK_MODEL_TIMEOUT_SECONDS', '.1')
    def invoke(messages):
        calls.append(messages)
        release.wait(2)
        return SimpleNamespace(content='{}')
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            understand(SimpleNamespace(invoke=invoke))
        assert time.monotonic()-started < .25 and len(calls) == 2 and workflow._model_inflight == 2
    finally:
        release.set()


def test_no_speculation_when_another_call_is_already_admitted(isolated_pool, monkeypatch):
    release, entered, calls = threading.Event(), threading.Event(), []
    monkeypatch.setenv('BANK_MODEL_TIMEOUT_SECONDS', '.12')
    def other(messages):
        entered.set()
        release.wait(2)
    def invoke(messages):
        calls.append(messages)
        release.wait(2)
    with ThreadPoolExecutor(max_workers=1) as caller:
        existing = caller.submit(workflow.invoke_model, SimpleNamespace(invoke=other), [])
        assert entered.wait(1)
        try:
            with pytest.raises(TimeoutError):
                understand(SimpleNamespace(invoke=invoke))
            assert len(calls) == 1 and workflow._model_inflight == 2
            assert all(action != 'MODEL_UNDERSTANDING_HEDGE' for action, _ in isolated_pool)
        finally:
            release.set()
            try:
                existing.result(timeout=1)
            except TimeoutError:
                pass


@pytest.mark.parametrize('exception', [RuntimeError('transport failed'), TimeoutError('SDK timed out')])
def test_a_completed_exception_is_not_a_slow_pending_call(isolated_pool, exception):
    calls = []
    def invoke(messages):
        calls.append(messages)
        raise exception
    with pytest.raises(type(exception)):
        understand(SimpleNamespace(invoke=invoke))
    assert len(calls) == 1 and workflow.wait_for_model_calls(.5)


def test_plain_generation_does_not_inherit_understanding_speculation(isolated_pool):
    calls = []
    def invoke(messages):
        calls.append(messages)
        time.sleep(.06)
        return SimpleNamespace(content='{}')
    assert workflow.invoke_json_model(SimpleNamespace(invoke=invoke), []).content == '{}'
    assert len(calls) == 1
