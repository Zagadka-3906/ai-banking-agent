"""Acceptance load contracts use real threads and isolated model executors."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from types import SimpleNamespace
import threading

import pytest

import main
import workflow_runtime as workflow
from test_agent_gateway import _send, gateway  # noqa: F401
from test_inquiry_boundaries import financial_state


@pytest.mark.parametrize('sessions', [1, 3, 10])
def test_simultaneous_authenticated_read_sessions_keep_subjects_and_do_not_write(gateway, sessions):  # noqa: F811
    client, _ = gateway
    barrier = threading.Barrier(sessions)
    subjects = ['user_zhang', 'user_li', 'user_wife']
    before = financial_state()
    def request(index):
        principal = subjects[index % len(subjects)]
        barrier.wait(timeout=5)
        response = _send(client, '查询账户余额', conversation_id=f'load-{sessions}-{index}', principal=principal)
        assert response['approval'] is None and response['business_status'] == 'SUCCEEDED'
        expected = format(main.ACCOUNTS[principal]['balance'], ',.2f')
        assert expected in response['reply']
        assert response['intent'] == 'BALANCE_QUERY'
        return response['request_id']
    with ThreadPoolExecutor(max_workers=sessions) as pool:
        request_ids = list(pool.map(request, range(sessions)))
    assert len(set(request_ids)) == sessions
    assert financial_state() == before


@pytest.mark.parametrize('sessions', [1, 3, 10])
def test_model_load_keeps_two_running_eight_queued_and_rejects_overload(monkeypatch, sessions):
    channel = ContextVar('load_channel', default='missing')
    release = threading.Event()
    entered = threading.Event()
    admitted = threading.Event()
    completed = threading.Event()
    lock = threading.Lock()
    counts = {'admitted': 0, 'released': 0, 'active': 0, 'peak': 0}
    calls = []
    class ObservedSlots:
        def __init__(self):
            self.slots = threading.BoundedSemaphore(10)
        def acquire(self, **kwargs):
            obtained = self.slots.acquire(**kwargs)
            if obtained:
                with lock:
                    counts['admitted'] += 1
                    if counts['admitted'] == sessions:
                        admitted.set()
            return obtained
        def release(self):
            self.slots.release()
            with lock:
                counts['released'] += 1
                if counts['released'] == sessions:
                    completed.set()
    def invoke(_):
        with lock:
            counts['active'] += 1
            counts['peak'] = max(counts['peak'], counts['active'])
            calls.append(channel.get())
            if counts['active'] == min(2, sessions):
                entered.set()
        try:
            assert release.wait(5)
            return channel.get()
        finally:
            with lock:
                counts['active'] -= 1
    def request(index):
        channel.set(str(index))
        return workflow.invoke_model(SimpleNamespace(invoke=invoke), [])
    monkeypatch.setenv('BANK_MODEL_TIMEOUT_SECONDS', '5')
    monkeypatch.setattr(workflow, '_model_slots', ObservedSlots())
    with ThreadPoolExecutor(max_workers=2) as sdk:
        monkeypatch.setattr(workflow, '_model_pool', sdk)
        with ThreadPoolExecutor(max_workers=sessions) as clients:
            pending = [clients.submit(request, index) for index in range(sessions)]
            try:
                assert admitted.wait(5) and entered.wait(5)
                assert len(calls) == min(2, sessions)
                if sessions == 10:
                    with pytest.raises(TimeoutError, match='MODEL_CAPACITY_EXHAUSTED'):
                        request('overload')
            finally:
                release.set()
            assert [future.result(timeout=6) for future in pending] == [str(index) for index in range(sessions)]
        assert completed.wait(5)
    assert counts['peak'] == min(2, sessions)
    assert counts['admitted'] == counts['released'] == sessions
    assert sorted(calls) == sorted(str(index) for index in range(sessions))
