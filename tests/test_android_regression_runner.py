import pytest
from types import SimpleNamespace

from scripts import run_android_regression as runner
from scripts.run_android_regression import verify_results


def test_device_verifier_rejects_skips_and_duplicate_names(tmp_path):
    result = tmp_path / "TEST-suite.xml"
    result.write_text('<testsuite><testcase classname="A" name="x"><skipped/></testcase></testsuite>')
    with pytest.raises(ValueError):
        verify_results(tmp_path, 1)
    result.write_text('<testsuite><testcase classname="A" name="x"/><testcase classname="A" name="x"/></testsuite>')
    with pytest.raises(ValueError):
        verify_results(tmp_path, 2)
    result.write_text('<testsuite><testcase classname="A" name="x"/></testsuite>')
    assert verify_results(tmp_path, 1)["tests"] == 1
    result.write_text('<testsuite tests="1" errors="1"><testcase classname="A" name="x"/></testsuite>')
    with pytest.raises(ValueError):
        verify_results(tmp_path, 1)


@pytest.mark.parametrize('outcome', ['success', 'failed', 'timeout', 'compile_failure'])
def test_device_runner_preserves_failed_results_and_never_reuses_old_success(tmp_path, monkeypatch, outcome):
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    source = tmp_path/'android/app/build/outputs/androidTest-results/connected/scenario'
    source.mkdir(parents=True)
    old = '<testsuite tests="1"><testcase classname="A" name="old"/></testsuite>'
    (source/'TEST-old.xml').write_text(old)
    output = tmp_path/'build/new-run'
    output.mkdir(parents=True)
    def invoke(*args, **kwargs):
        assert not source.exists()
        if outcome != 'compile_failure':
            source.mkdir()
            (source/'TEST-current.xml').write_text('<testsuite tests="1" failures="1"><testcase classname="A" name="current"><failure/></testcase></testsuite>')
            (source/'test-result.pb').write_bytes(b'current device observations')
        if outcome == 'timeout':
            raise runner.subprocess.TimeoutExpired(['private-command', 'private-token'], 1800)
        return SimpleNamespace(returncode=0 if outcome == 'success' else 1)
    monkeypatch.setattr(runner.subprocess, 'run', invoke)
    if outcome == 'success':
        runner.run_device_gradle(['private-command'], env={}, source=source, output=output)
    else:
        with pytest.raises(RuntimeError) as error:
            runner.run_device_gradle(['private-command'], env={}, source=source, output=output)
        assert 'private-token' not in str(error.value) and 'private-command' not in str(error.value)
    assert (output/'prior-device-results/TEST-old.xml').read_text() == old
    assert not (output/'device-results/TEST-old.xml').exists()
    if outcome == 'compile_failure':
        assert not (output/'device-results').exists()
    else:
        assert (output/'device-results/test-result.pb').read_bytes() == b'current device observations'
    with pytest.raises(ValueError):
        verify_results(output/'device-results', 1)


@pytest.mark.parametrize('invalid', ['source', 'outside_output', 'existing_evidence'])
def test_device_runner_rejects_unsafe_move_or_evidence_reuse_before_running(tmp_path, monkeypatch, invalid):
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    source = tmp_path/'android/app/build/outputs/androidTest-results/connected/scenario'
    if invalid == 'source':
        source = tmp_path/'user-files'
    source.mkdir(parents=True)
    marker = source/'original.txt'
    marker.write_text('preserve')
    output = tmp_path/('outside' if invalid == 'outside_output' else 'build/new-run')
    output.mkdir(parents=True)
    if invalid == 'existing_evidence':
        (output/'device-results').mkdir()
    def unexpected(*args, **kwargs):
        pytest.fail('Invalid paths must be refused before invoking Gradle')
    monkeypatch.setattr(runner.subprocess, 'run', unexpected)
    with pytest.raises(ValueError):
        runner.run_device_gradle([], env={}, source=source, output=output)
    assert marker.read_text() == 'preserve'
