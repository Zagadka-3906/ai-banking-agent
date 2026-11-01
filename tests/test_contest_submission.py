"""Verify delivery boundaries, private data exclusions and source provenance."""
from pathlib import Path
import subprocess

import pytest

from scripts.package_contest_submission import assert_delivery_files, assert_public_source, ensure_source_clean, introduction, validate_media


@pytest.mark.parametrize("length", [0, 201])
def test_intro_rejects_outside_limit(length):
    with pytest.raises(ValueError):
        introduction("## 报名简介\n\n" + "字" * length + "\n")


def test_intro_boundary():
    assert len(introduction("## 报名简介\n\n" + "字" * 200 + "\n")) == 200


@pytest.mark.parametrize("name", [".env", "nested/.env.production", "agent_devices.json", "sessions.sqlite3", "build/log.json", "id_ed25519", "android/local.properties"])
def test_source_rejects_private_entries(name):
    with pytest.raises(ValueError):
        assert_public_source(["ai-banking-agent/" + name])


def test_source_allows_examples_and_ci():
    assert_public_source(["ai-banking-agent/.env.example", "ai-banking-agent/.env.eval.example", "ai-banking-agent/.github/workflows/ci.yml", "ai-banking-agent/tests/test_core_api.py"])
    assert_delivery_files({"source.zip", "demo.apk"}, {"source.zip", "demo.apk"})
    with pytest.raises(ValueError):
        assert_delivery_files({"source.zip", ".env"}, {"source.zip"})
    with pytest.raises(ValueError):
        assert_delivery_files(set(), {"source.zip"})


@pytest.mark.parametrize("values", [(11, 550, 80), (10, 601, 80), (10, 550, 301), (10, 0, 80), (10, 550, 0)])
def test_media_limits(values):
    with pytest.raises(ValueError):
        validate_media(*values)


def test_media_limit_boundaries():
    validate_media(10, 600, 300)


def test_untracked_preserved_but_tracked_changes_rejected(tmp_path: Path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init", "--quiet")
    git("config", "user.name", "Submission test")
    git("config", "user.email", "test@example.invalid")
    tracked = tmp_path / "README.md"
    tracked.write_text("original", encoding="utf-8")
    git("add", "README.md")
    git("commit", "--quiet", "-m", "fixture")
    personal = tmp_path / "personal.txt"
    personal.write_text("keep", encoding="utf-8")
    ensure_source_clean(tmp_path)
    tracked.write_text("modified", encoding="utf-8")
    with pytest.raises(ValueError):
        ensure_source_clean(tmp_path)
    assert personal.read_text(encoding="utf-8") == "keep"
