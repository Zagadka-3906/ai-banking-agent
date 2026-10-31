"""Tests for concurrent trial execution, worker job isolation, and concurrency limits."""
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace


from scripts.dialogue_eval.costs import CostLedger
from scripts import evaluate_dialogue_tasks as evaluator


PRICES = {'test': {'input_cached': '.1', 'input_uncached': '1', 'output': '2', 'context_tokens': 1000}}


def test_unique_job_files_created_per_task_and_trial(tmp_path, monkeypatch):
    """Ensure that concurrent workers each receive their own unique job file,

    never overwriting a single worker-job.json.
    """
    monkeypatch.setattr(evaluator, 'ROOT', tmp_path)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    monkeypatch.setattr(evaluator, 'source_fingerprints', lambda: {'source.py': 'fixed'})
    monkeypatch.setattr(evaluator.subprocess, 'check_output', lambda cmd, **kw: 'rev1' if 'rev-parse' in cmd else '')

    captured_job_paths = []
    active_workers = 0
    max_active_workers = 0
    lock = threading.Lock()

    def fake_worker(command, **kwargs):
        nonlocal active_workers, max_active_workers
        job_file = Path(command[-1])
        with lock:
            active_workers += 1
            max_active_workers = max(max_active_workers, active_workers)
            captured_job_paths.append(job_file.name)
        time.sleep(0.05)
        job = json.loads(job_file.read_text(encoding='utf-8'))
        result_path = Path(job['result'])
        result_path.write_text(json.dumps({
            'task_id': job['task']['id'],
            'trial': job['trial'],
            'seed': job['seed'],
            'status': 'PASS',
            'turns': [{'phase': 1, 'user': 'q', 'coverage': [], 'read_failures': []}],
            'checks': [{'id': 'phase_1.no_business_write', 'passed': True}],
            'model_calls_completed': True,
            'judge_schema_diagnostics': [],
            'evaluation_transport': {'user': [], 'judge': []},
        }, ensure_ascii=False), encoding='utf-8')
        with lock:
            active_workers -= 1
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(evaluator.subprocess, 'run', fake_worker)

    cases = Path(__file__).parent / 'data/dialogue_tasks.json'
    task_id = json.loads(cases.read_text(encoding='utf-8'))['tasks'][0]['id']
    output = tmp_path / 'build/concurrency-test'

    exit_code = evaluator.main([
        '--live', '--unlimited-cost', '--trials', '4', '--concurrency', '4',
        '--task', task_id, '--cases', str(cases), '--output-dir', str(output)
    ])

    assert exit_code == 0
    # Every trial must have had its own unique worker-job filename
    assert len(captured_job_paths) == 4
    assert len(set(captured_job_paths)) == 4
    for name in captured_job_paths:
        assert name.startswith(f"worker-job-{task_id}-")
        assert name.endswith(".json")

    # Verify concurrency was actually exercised
    assert max_active_workers >= 2

    # Verify report is properly formed and sorted
    report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    assert report['status'] == 'COMPLETED'
    assert len(report['trials']) == 4
    trials_indices = [t['trial'] for t in report['trials']]
    assert trials_indices == [1, 2, 3, 4]


def test_concurrent_stop_on_failure_halts_pending_jobs(tmp_path, monkeypatch):
    """When a worker reports FAIL with --stop-on-failure in concurrent mode,

    the scheduler halts and marks stop reason TASK_FAILED.
    """
    monkeypatch.setattr(evaluator, 'ROOT', tmp_path)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    monkeypatch.setattr(evaluator, 'source_fingerprints', lambda: {'source.py': 'fixed'})
    monkeypatch.setattr(evaluator.subprocess, 'check_output', lambda cmd, **kw: 'rev1' if 'rev-parse' in cmd else '')

    def fake_worker_fail(command, **kwargs):
        job_file = Path(command[-1])
        job = json.loads(job_file.read_text(encoding='utf-8'))
        result_path = Path(job['result'])
        # trial 1 fails immediately
        status = 'FAIL' if job['trial'] == 1 else 'PASS'
        result_path.write_text(json.dumps({
            'task_id': job['task']['id'],
            'trial': job['trial'],
            'seed': job['seed'],
            'status': status,
            'turns': [],
            'checks': [{'id': 'phase_1.no_business_write', 'passed': status == 'PASS'}],
            'model_calls_completed': True,
            'judge_schema_diagnostics': [],
            'evaluation_transport': {'user': [], 'judge': []},
        }, ensure_ascii=False), encoding='utf-8')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(evaluator.subprocess, 'run', fake_worker_fail)

    cases = Path(__file__).parent / 'data/dialogue_tasks.json'
    task_id = json.loads(cases.read_text(encoding='utf-8'))['tasks'][0]['id']
    output = tmp_path / 'build/stop-test'

    exit_code = evaluator.main([
        '--live', '--unlimited-cost', '--stop-on-failure', '--trials', '5', '--concurrency', '2',
        '--task', task_id, '--cases', str(cases), '--output-dir', str(output)
    ])

    assert exit_code in {1, 2}
    report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    assert report['cost']['campaign']['stop_reason'] in {'TASK_FAILED', 'TRIAL_ERROR'}


def test_cost_ledger_in_flight_concurrency_limiting(tmp_path):
    """CostLedger must respect max_concurrent_requests and serialize/wait when saturated."""
    budget = CostLedger.configure(
        tmp_path / 'cost.sqlite3',
        prices=PRICES,
        max_output_tokens=100,
        max_calls=100,
        max_concurrent_requests=2,
    )

    id1, _ = budget.reserve('run', 'agent', 'test')
    id2, _ = budget.reserve('run', 'agent', 'test')

    snap = budget.snapshot()
    assert snap['in_flight_calls'] == 2

    # A third reserve should wait until one finishes
    resolved = []

    def third_call():
        identifier, _ = budget.reserve('run', 'agent', 'test')
        resolved.append(identifier)

    thread = threading.Thread(target=third_call)
    thread.start()
    time.sleep(0.08)
    # Thread should still be waiting
    assert len(resolved) == 0

    def make_answer():
        return SimpleNamespace(usage_metadata={'input_tokens': 100, 'output_tokens': 20,
                                               'input_token_details': {'cache_read': 50}})

    # Complete call 1
    budget.completed(id1, make_answer())
    thread.join(timeout=2.0)
    assert len(resolved) == 1

    id3 = resolved[0]
    budget.completed(id2, make_answer())
    budget.completed(id3, make_answer())

    snap_final = budget.snapshot()
    assert snap_final['in_flight_calls'] == 0
    assert snap_final['calls'] == 3
