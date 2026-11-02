"""Prepare contest folders matching the submission form, without format conversion.

This material package does not certify stable-release acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import subprocess
import tarfile
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/Zagadka-3906/ai-banking-agent"
CATEGORIES = ("01-技术文档", "02-答辩材料", "03-源代码与部署材料", "04-安全自评报告", "05-演示视频")
SOURCE_CATEGORY = CATEGORIES[2]
DEFERRED_CATEGORIES = {CATEGORIES[1], CATEGORIES[4]}
TECHNICAL_DOCUMENTS = ("技术文档.md", "核心算法说明.md", "安全设计文档.md")


def introduction(text: str) -> str:
    match = re.search(r"## 报名简介[^\n]*\n\s*\n([^\n]+)", text)
    if not match or not 1 <= len(match[1].strip()) <= 200:
        raise ValueError("Introduction must contain 1–200 characters")
    return match[1].strip()


def ensure_source_clean(root: Path) -> None:
    # git archive excludes untracked personal files without deleting them.
    if subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=root, check=False).returncode:
        raise ValueError("Commit tracked changes before packaging")


def assert_public_source(names: list[str]) -> None:
    forbidden = {".git", ".venv", "build", "submission", "__pycache__", ".gradle", ".claude", "test-shots"}
    for name in names:
        p = PurePosixPath(name)
        leaf = p.name.lower()
        if (p.is_absolute() or ".." in p.parts or forbidden.intersection(p.parts) or (leaf.startswith(".env") and not leaf.endswith(".example"))
                or leaf.endswith((".sqlite", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".db", ".log", ".apk", ".key", ".p12", ".pfx", ".keystore", ".jks"))
                or leaf in {"agent_devices.json", "local.properties", "id_rsa", "id_ed25519", "panel.token"}):
            raise ValueError(f"Private or generated source entry: {name}")


def assert_delivery_files(actual: set[str], expected: set[str]) -> None:
    # Preserve unexpected local files, but never silently publish them.
    if actual != expected:
        raise ValueError(f"Delivery file set differs: extra={sorted(actual - expected)}, missing={sorted(expected - actual)}")


def assert_standalone_technical_document(text: str) -> None:
    """Reject navigation/media dependencies before exporting separate uploads."""
    visible = re.sub(r"(?ms)^(`{3,}|~{3,})[^\n]*\n.*?^\1\s*$", "", text)
    targets = re.findall(r"!?\[[^\]\n]*\]\(([^)]+)\)", visible)
    targets += re.findall(r"(?m)^\s{0,3}\[[^\]\n]+\]:\s*(\S+)", visible)
    if (any(not target.strip().startswith("#") for target in targets)
            or re.search(r"<(?:img|image|object|iframe|script|link)\b", visible, re.IGNORECASE)):
        raise ValueError("Technical documents must be standalone; linked files or external media are not allowed")


def relocate_markdown(text: str, original: str, destination: str) -> str:
    """Make copied documents link to the complete, exported source tree."""
    def replace(match: re.Match) -> str:
        target = match[2]
        url = urlsplit(target)
        if url.scheme or url.netloc or not url.path:
            return match[0]
        relative = posixpath.normpath(posixpath.join(posixpath.dirname(original), unquote(url.path)))
        if relative.startswith("../") or relative.startswith("/"):
            raise ValueError(f"Document link leaves repository: {target}")
        exported = f"{SOURCE_CATEGORY}/source/{relative}"
        relocated = posixpath.relpath(exported, posixpath.dirname(destination))
        if url.fragment:
            relocated += "#" + url.fragment
        return match[1] + relocated + match[3]
    return re.sub(r"(!?\[[^\]\n]*\]\()([^\s)]+)(\))", replace, text)


def export_source(source: Path) -> set[str]:
    data = subprocess.check_output(["git", "archive", "--format=tar", "HEAD"], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        members = archive.getmembers()
        assert_public_source([m.name for m in members])
        if any(not m.isfile() and not m.isdir() for m in members):
            raise ValueError("Source export must contain regular files and directories only")
        names = {m.name for m in members if m.isfile()}
        existing = {p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file()}
        if existing - names:
            raise ValueError("Unexpected existing source files; preserve and review them before preparing delivery")
        archive.extractall(source, filter="data")
    return names


def package(folder: Path, apk_source_commit: str | None = None) -> dict:
    folder = folder.resolve()
    if folder != ROOT / "submission":
        raise ValueError("Delivery destination must be ignored project submission/")
    ensure_source_clean(ROOT)
    legacy = {"01-作品简介", "02-技术文档", "03-答辩材料", "04-作品信息", "05-安全自评报告", "安言参赛交付.zip"}
    if any((folder / name).exists() for name in legacy):
        raise ValueError("Migrate legacy submission folders before preparing the current form layout")
    technical = {name: (ROOT / "docs" / name).read_text(encoding="utf-8") for name in TECHNICAL_DOCUMENTS}
    for text in technical.values():
        assert_standalone_technical_document(text)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    apk_commit = None
    if apk_source_commit:
        apk_commit = subprocess.check_output(["git", "rev-parse", "--verify", apk_source_commit + "^{commit}"], cwd=ROOT, text=True).strip()
    mapping = {
        CATEGORIES[0]: list(TECHNICAL_DOCUMENTS),
        SOURCE_CATEGORY: ["测试用例.md", "验证记录.md"],
        CATEGORIES[3]: ["安全自评报告.md"],
    }
    for category in CATEGORIES:
        (folder / category).mkdir(parents=True, exist_ok=True)
    for category, names in mapping.items():
        for name in names:
            text = (ROOT / "docs" / name).read_text(encoding="utf-8")
            exported = technical[name] if category == CATEGORIES[0] else relocate_markdown(text, f"docs/{name}", f"{category}/{name}")
            (folder / category / name).write_text(exported, encoding="utf-8")
    info = folder / SOURCE_CATEGORY
    (info / "GitHub仓库地址.txt").write_text(REPOSITORY + "\n", encoding="utf-8")
    review = (ROOT / "docs/评审指南.md").read_text(encoding="utf-8")
    (info / "README.md").write_text(relocate_markdown(review, "docs/评审指南.md", f"{SOURCE_CATEGORY}/README.md"), encoding="utf-8")
    deployment = (ROOT / "deploy/README.md").read_text(encoding="utf-8")
    (info / "部署说明.md").write_text(relocate_markdown(deployment, "deploy/README.md", f"{SOURCE_CATEGORY}/部署说明.md"), encoding="utf-8")
    for relative, original in (("补充材料/整理说明.md", "docs/整理说明.md"),
                               ("补充材料/作品简介/作品简介.md", "docs/作品简介.md")):
        if (info / relative).exists():
            (info / relative).write_text(relocate_markdown((ROOT / original).read_text(encoding="utf-8"), original,
                                                         f"{SOURCE_CATEGORY}/{relative}"), encoding="utf-8")
    source_names = export_source(info / "source")
    descriptions = ["系统架构图、技术说明、核心算法和安全设计。", "必交；本次暂不处理，已有文件原样保留。", "评审 README、部署说明、测试用例、仓库链接及展开的 source/ 源码。", "权限分级实现说明与已知风险清单。", "可选；本次暂不处理，已有文件原样保留。"]
    (folder / "交付清单.md").write_text(
        "# 安言提交材料\n\n按提交页面的五个栏目整理，当前不转换文件格式，不生成压缩包。\n\n"
        + "\n".join(f"{i}. [{c[3:]}]({c}/)：{d}" for i, (c, d) in enumerate(zip(CATEGORIES, descriptions), 1))
        + f"\n\n源码提交：`{commit}`。先阅读 [{SOURCE_CATEGORY}/README.md]({SOURCE_CATEGORY}/README.md)，运行入口在 source/ 内。"
        + "\n\n作品简介、安装包等已有补充资料收在第三栏，不另占提交栏目。PPT 和视频内容、格式、页数及时间均未验收，目录存在不代表这些材料已完成。"
        + "源码不含真实配置、私钥、会话、设备登记、账本、日志或缓存。\n", encoding="utf-8")
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p != folder / "manifest.json"
                   and p.relative_to(folder).parts[0] not in DEFERRED_CATEGORIES)
    expected = {f"{category}/{name}" for category, names in mapping.items() for name in names}
    expected.update(f"{SOURCE_CATEGORY}/{name}" for name in ("README.md", "GitHub仓库地址.txt", "部署说明.md"))
    expected.update(f"{SOURCE_CATEGORY}/source/{name}" for name in source_names)
    optional = ["安装包/安言-公网演示.apk", "安装包/项目CA公开证书.pem", "补充材料/整理说明.md",
                "补充材料/作品简介/作品简介.md", "补充材料/作品简介/作品名称.txt", "补充材料/作品简介/报名简介.txt"]
    expected.update(f"{SOURCE_CATEGORY}/{name}" for name in optional if (info / name).is_file())
    expected.add("交付清单.md")
    assert_delivery_files({p.relative_to(folder).as_posix() for p in files}, expected)
    manifest = {
        "commit": commit, "repository": REPOSITORY, "delivery_format": "directory", "layout_version": 2,
        "deferred": {category: {"managed": False, "validated": False} for category in sorted(DEFERRED_CATEGORIES)},
        "files": {p.relative_to(folder).as_posix(): {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files},
        "excluded": ["credentials", "runtime_data", "private_archives", "logs", "build_caches"],
    }
    apk = info / "安装包/安言-公网演示.apk"
    if apk.exists():
        manifest["apk"] = {"source_commit": apk_commit, "sha256": hashlib.sha256(apk.read_bytes()).hexdigest()}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"commit": commit, "directory": str(folder), "files": len(files), "deferred": sorted(DEFERRED_CATEGORIES)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery-dir", type=Path, default=ROOT / "submission")
    parser.add_argument("--apk-source-commit", help="Actual source commit used to build the existing APK")
    args = parser.parse_args()
    print(json.dumps(package(args.delivery_dir, args.apk_source_commit), ensure_ascii=False))


if __name__ == "__main__":
    main()
