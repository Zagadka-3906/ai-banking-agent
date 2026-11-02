"""Verify delivery boundaries, private data exclusions and source provenance."""
from pathlib import Path
import json
import subprocess
import zipfile

import pytest

from scripts.package_contest_submission import assert_delivery_files, assert_public_source, ensure_source_clean, introduction, validate_media
from scripts import package_contest_submission as packaging
from scripts.check_repository_docs import local_links


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


def test_directory_delivery_exports_committed_source_and_resolves_copied_links(tmp_path, monkeypatch):
    """Exercise the real git export with a different APK/media source version."""
    monkeypatch.setattr(packaging, "ROOT", tmp_path)
    monkeypatch.setattr(packaging, "video_duration", lambda *args: 47.0)
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path).decode("utf-8").strip()
    git("init", "--quiet")
    git("config", "user.name", "Submission test")
    git("config", "user.email", "test@example.invalid")
    docs = tmp_path / "docs"
    docs.mkdir()
    names = ["作品简介", "技术文档", "核心算法说明", "安全设计文档", "答辩提纲", "演示视频", "测试用例", "验证记录", "整理说明", "安全自评报告", "评审指南"]
    for name in names:
        (docs / f"{name}.md").write_text("# 文档\n\n[规则](技术文档.md#架构)\n", encoding="utf-8")
    (docs / "作品简介.md").write_text("## 报名简介\n\n虚构账户演示。\n", encoding="utf-8")
    (docs / "assets").mkdir()
    for suffix in ("svg", "mmd"):
        (docs / f"assets/系统架构图.{suffix}").write_text("fixture", encoding="utf-8")
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy/README.md").write_text("[技术](../docs/技术文档.md)", encoding="utf-8")
    git("add", "docs", "deploy")
    git("commit", "--quiet", "-m", "apk source")
    apk_commit = git("rev-parse", "HEAD")
    (docs / "验证记录.md").write_text("updated documentation", encoding="utf-8")
    git("add", "docs/验证记录.md")
    git("commit", "--quiet", "-m", "documentation update")
    # Private/untracked files must remain in place and never enter source/.
    personal = tmp_path / "personal.txt"
    personal.write_text("keep", encoding="utf-8")
    delivery = tmp_path / "submission"
    talk = delivery / packaging.CATEGORIES[2]
    talk.mkdir(parents=True)
    with zipfile.ZipFile(talk / "安言答辩.pptx", "w") as deck:
        deck.writestr("ppt/presentation.xml", "<presentation/>")
        for i in range(1, 11):
            deck.writestr(f"ppt/slides/slide{i}.xml", "<slide/>")
    for name in ("安言演示视频.mp4", "安言演示字幕.srt"):
        (talk / name).write_bytes(b"media fixture")
    (talk / "媒体说明.json").write_text(json.dumps({"speaking_seconds": 550, "business_version": apk_commit}), encoding="utf-8")
    install = delivery / packaging.CATEGORIES[3] / "安装包"
    install.mkdir(parents=True)
    (install / "安言-公网演示.apk").write_bytes(b"apk fixture")
    (install / "项目CA公开证书.pem").write_bytes(b"public CA fixture")
    result = packaging.package(delivery, apk_source_commit=apk_commit)
    manifest = json.loads((delivery / "manifest.json").read_text(encoding="utf-8"))
    assert result["directory"] == str(delivery)
    assert manifest["delivery_format"] == "directory"
    assert manifest["commit"] != manifest["apk"]["source_commit"] == apk_commit
    assert manifest["media_business_version"] == apk_commit
    source = install.parent / "source"
    assert (source / "docs/验证记录.md").read_text(encoding="utf-8") == "updated documentation"
    assert not (source / "personal.txt").exists() and personal.read_text(encoding="utf-8") == "keep"
    assert not list(delivery.rglob("*.zip"))
    assert all((delivery / name).is_file() for name in manifest["files"])
    for path in delivery.rglob("*.md"):
        for target in local_links(path.read_text(encoding="utf-8")):
            assert (path.parent / target.split("#")[0]).exists(), (path, target)
    # Rerunning cannot silently delete user files added to the exported tree.
    extra = source / "personal.txt"
    extra.write_text("keep too", encoding="utf-8")
    with pytest.raises(ValueError, match="Unexpected existing source"):
        packaging.package(delivery, apk_source_commit=apk_commit)
    assert extra.read_text(encoding="utf-8") == "keep too"


@pytest.mark.parametrize("target", ["../.env", "/tmp/private.key"])
def test_source_export_rejects_paths_outside_directory(target):
    with pytest.raises(ValueError):
        assert_public_source([target])
