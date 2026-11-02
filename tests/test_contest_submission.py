"""Verify delivery boundaries, private data exclusions and source provenance."""
from pathlib import Path
import json
import subprocess

import pytest

from scripts.package_contest_submission import assert_delivery_files, assert_public_source, ensure_source_clean, introduction
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
    """Export real committed source without depending on presentation or video files."""
    monkeypatch.setattr(packaging, "ROOT", tmp_path)
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path).decode("utf-8").strip()
    git("init", "--quiet")
    git("config", "user.name", "Submission test")
    git("config", "user.email", "test@example.invalid")
    docs = tmp_path / "docs"
    docs.mkdir()
    names = ["作品简介", "技术文档", "核心算法说明", "安全设计文档", "答辩提纲", "演示视频", "测试用例", "验证记录", "整理说明", "安全自评报告", "评审指南"]
    for name in names:
        text = "# 文档\n\n```text\n用户 → 网关 → 核心 → 账本\n```\n" if f"{name}.md" in packaging.TECHNICAL_DOCUMENTS else "# 文档\n\n[规则](技术文档.md#架构)\n"
        (docs / f"{name}.md").write_text(text, encoding="utf-8")
    (docs / "作品简介.md").write_text("## 报名简介\n\n虚构账户演示。\n", encoding="utf-8")
    (docs / "assets").mkdir()
    for suffix in ("svg", "mmd"):
        (docs / f"assets/系统架构图.{suffix}").write_text("fixture", encoding="utf-8")
    architecture = docs / "技术文档.md"
    architecture.write_text(architecture.read_text(encoding="utf-8") + "\n![架构](assets/系统架构图.svg)\n\n```mermaid\nsequenceDiagram\n    用户->>核心: 确认\n```\n", encoding="utf-8")
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
    packaging.package(delivery)
    manifest = json.loads((delivery / "manifest.json").read_text(encoding="utf-8"))
    assert "apk" not in manifest
    assert not any(key in manifest for key in ("slides", "video_seconds", "speaking_seconds"))
    install = delivery / packaging.SOURCE_CATEGORY / "安装包"
    install.mkdir(parents=True)
    (install / "安言-公网演示.apk").write_bytes(b"apk fixture")
    (install / "项目CA公开证书.pem").write_bytes(b"public CA fixture")
    result = packaging.package(delivery, apk_source_commit=apk_commit)
    manifest = json.loads((delivery / "manifest.json").read_text(encoding="utf-8"))
    assert result["directory"] == str(delivery)
    assert manifest["delivery_format"] == "directory"
    assert manifest["commit"] != manifest["apk"]["source_commit"] == apk_commit
    assert set(manifest["deferred"]) == packaging.DEFERRED_CATEGORIES
    source = install.parent / "source"
    assert (source / "docs/验证记录.md").read_text(encoding="utf-8") == "updated documentation"
    assert not (source / "personal.txt").exists() and personal.read_text(encoding="utf-8") == "keep"
    assert not list(delivery.rglob("*.zip"))
    assert all((delivery / name).is_file() for name in manifest["files"])
    # PDF-authoring inputs stay independent of source/. Bundled SVG and inline
    # Mermaid must be retained for the eventual embedded PDF figures.
    isolated = tmp_path / "independent-uploads"
    isolated.mkdir()
    technical = delivery / packaging.CATEGORIES[0]
    assert {p.name for p in technical.iterdir()} == {*packaging.TECHNICAL_DOCUMENTS, "assets"}
    for name in packaging.TECHNICAL_DOCUMENTS:
        text = (technical / name).read_text(encoding="utf-8")
        (isolated / name).write_text(text, encoding="utf-8")
        assert text == (docs / name).read_text(encoding="utf-8")
        assert all(target in packaging.TECHNICAL_ASSETS for target in local_links(text))
        packaging.assert_standalone_technical_document(text)
    for relative in packaging.TECHNICAL_ASSETS:
        assert (technical / relative).read_bytes() == (docs / relative).read_bytes()
    assert "```mermaid" in (technical / "技术文档.md").read_text(encoding="utf-8")
    for path in delivery.rglob("*.md"):
        for target in local_links(path.read_text(encoding="utf-8")):
            assert (path.parent / target.split("#")[0]).exists(), (path, target)
    # Media may be absent, use any format, and change independently of this task.
    media = delivery / packaging.CATEGORIES[1] / "draft.custom"
    media.write_bytes(b"unchanged presentation draft")
    video = delivery / packaging.CATEGORIES[4] / "clip.custom"
    video.write_bytes(b"unchanged video draft")
    result = packaging.package(delivery)  # APK provenance is optional; never infer it from HEAD.
    manifest = json.loads((delivery / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["apk"]["source_commit"] is None
    assert media.read_bytes() == b"unchanged presentation draft"
    assert video.read_bytes() == b"unchanged video draft"
    assert not any(n.startswith(tuple(packaging.DEFERRED_CATEGORIES)) for n in manifest["files"])
    assert {p.name for p in delivery.iterdir() if p.is_dir()} == set(packaging.CATEGORIES)
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


def test_preparation_rejects_legacy_layout_without_mutating_user_files(tmp_path, monkeypatch):
    monkeypatch.setattr(packaging, "ROOT", tmp_path)
    monkeypatch.setattr(packaging, "ensure_source_clean", lambda root: None)
    old = tmp_path / "submission/04-作品信息"
    old.mkdir(parents=True)
    personal = old / "personal.txt"
    personal.write_bytes(b"keep")
    with pytest.raises(ValueError, match="Migrate legacy"):
        packaging.package(tmp_path / "submission")
    assert personal.read_bytes() == b"keep"


@pytest.mark.parametrize("dependency", [
    "[源码](../main.py)",
    "![架构](assets/architecture.svg)",
    "[架构][diagram]\n\n[diagram]: assets/architecture.svg",
    '<img src="architecture.svg">',
    '<image href="architecture.svg" />',
    '<object data="architecture.svg"></object>',
    "[验证](https://example.invalid/report)",
])
def test_standalone_technical_upload_rejects_reading_dependencies(dependency):
    with pytest.raises(ValueError, match="standalone"):
        packaging.assert_standalone_technical_document("# 独立材料\n\n" + dependency)
