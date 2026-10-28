"""A rejected gate or dirty candidate cannot create a publishable archive."""
from types import SimpleNamespace

import pytest

from scripts import package_stable_candidate as packaging


def test_blocked_gate_cannot_generate_source_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(packaging, 'ROOT', tmp_path)
    monkeypatch.setattr(packaging.subprocess, 'check_output', lambda *args, **kwargs: b'')
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1, stdout=b'blocked', stderr=b'')
    monkeypatch.setattr(packaging.subprocess, 'run', run)
    target = tmp_path / 'build/candidate'
    assert packaging.package(tmp_path / 'manifest.json', target) == 1
    assert len(calls) == 1 and not (target / 'source.tar.gz').exists()


def test_dirty_source_is_rejected_before_running_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(packaging, 'ROOT', tmp_path)
    monkeypatch.setattr(packaging.subprocess, 'check_output', lambda *args, **kwargs: b'bank_agent/parsing.py')
    with pytest.raises(ValueError, match='Commit tracked'):
        packaging.package(tmp_path / 'manifest.json', tmp_path / 'build/candidate')
    assert not (tmp_path / 'build/candidate').exists()


def test_candidate_cannot_be_packaged_outside_ignored_build(tmp_path, monkeypatch):
    monkeypatch.setattr(packaging, 'ROOT', tmp_path)
    with pytest.raises(ValueError, match='under build'):
        packaging.package(tmp_path / 'manifest.json', tmp_path / 'published')
