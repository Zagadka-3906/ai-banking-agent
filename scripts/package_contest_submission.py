"""Prepare five contest folders, excluding credentials and runtime data.

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
import shutil
import subprocess
import tarfile
from urllib.parse import unquote, urlsplit
import zipfile
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/Zagadka-3906/ai-banking-agent"
CATEGORIES = ("01-作品简介", "02-技术文档", "03-答辩材料", "04-作品信息", "05-安全自评报告")


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


def validate_media(slides: int, speaking_seconds: float, video_seconds: float) -> None:
    if slides != 10 or not 0 < speaking_seconds <= 600 or not 0 < video_seconds <= 300:
        raise ValueError("Require 10 slides, talk <=10 min, video <=5 min")


def assert_delivery_files(actual: set[str], expected: set[str]) -> None:
    # Preserve unexpected local files, but never silently publish them.
    if actual != expected:
        raise ValueError(f"Delivery file set differs: extra={sorted(actual - expected)}, missing={sorted(expected - actual)}")


def video_duration(path: Path, ffmpeg: str | None = None) -> float:
    executable = ffmpeg or shutil.which("ffmpeg")
    if not executable:
        try:
            import imageio_ffmpeg
            executable = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError as exc:
            raise ValueError("Supply --ffmpeg with an installed FFmpeg executable") from exc
    result = subprocess.run([executable, "-hide_banner", "-i", str(path)], capture_output=True, text=True, errors="replace")
    match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not match:
        raise ValueError("Unable to read video duration")
    return int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])


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
        exported = f"{CATEGORIES[3]}/source/{relative}"
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


def package(folder: Path, ffmpeg: str | None = None, apk_source_commit: str | None = None) -> dict:
    folder = folder.resolve()
    if folder != ROOT / "submission":
        raise ValueError("Delivery destination must be ignored project submission/")
    ensure_source_clean(ROOT)
    if (folder / "安言参赛交付.zip").exists() or (folder / CATEGORIES[3] / "source.zip").exists():
        raise ValueError("Review and remove legacy ZIP files before preparing directory delivery")
    if not apk_source_commit:
        raise ValueError("Supply the APK build source commit separately from the documentation/source version")
    apk_commit = subprocess.check_output(["git", "rev-parse", "--verify", apk_source_commit + "^{commit}"], cwd=ROOT, text=True).strip()
    intro = introduction((ROOT / "docs/作品简介.md").read_text(encoding="utf-8"))
    talk = folder / CATEGORIES[2]
    deck, video = talk / "安言答辩.pptx", talk / "安言演示视频.mp4"
    timing = json.loads((talk / "媒体说明.json").read_text(encoding="utf-8"))
    with zipfile.ZipFile(deck) as pptx:
        count = sum(bool(re.fullmatch(r"ppt/slides/slide\d+\.xml", n)) for n in pptx.namelist())
        if pptx.testzip():
            raise ValueError("Invalid PPTX archive")
        ElementTree.fromstring(pptx.read("ppt/presentation.xml"))
    duration = video_duration(video, ffmpeg)
    validate_media(count, timing["speaking_seconds"], duration)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    mapping = {
        CATEGORIES[0]: ["作品简介.md"],
        CATEGORIES[1]: ["技术文档.md", "核心算法说明.md", "安全设计文档.md"],
        CATEGORIES[2]: ["答辩提纲.md", "演示视频.md"],
        CATEGORIES[3]: ["测试用例.md", "验证记录.md", "整理说明.md"],
        CATEGORIES[4]: ["安全自评报告.md"],
    }
    for category, names in mapping.items():
        (folder / category).mkdir(parents=True, exist_ok=True)
        for name in names:
            text = (ROOT / "docs" / name).read_text(encoding="utf-8")
            (folder / category / name).write_text(relocate_markdown(text, f"docs/{name}", f"{category}/{name}"), encoding="utf-8")
    shutil.copytree(ROOT / "docs/assets", folder / CATEGORIES[1] / "assets", dirs_exist_ok=True)
    (folder / CATEGORIES[0] / "作品名称.txt").write_text("安言：安全可控的银行 AI 智能体\n", encoding="utf-8")
    (folder / CATEGORIES[0] / "报名简介.txt").write_text(intro + "\n", encoding="utf-8")
    info = folder / CATEGORIES[3]
    (info / "GitHub仓库地址.txt").write_text(REPOSITORY + "\n", encoding="utf-8")
    review = (ROOT / "docs/评审指南.md").read_text(encoding="utf-8")
    (info / "README.md").write_text(relocate_markdown(review, "docs/评审指南.md", f"{CATEGORIES[3]}/README.md"), encoding="utf-8")
    deployment = (ROOT / "deploy/README.md").read_text(encoding="utf-8")
    (info / "部署说明.md").write_text(relocate_markdown(deployment, "deploy/README.md", f"{CATEGORIES[3]}/部署说明.md"), encoding="utf-8")
    source_names = export_source(info / "source")
    descriptions = [f"名称与 {len(intro)} 字简介，自行上传报名系统。", "系统架构图、核心算法、安全设计。", "已有 PPTX、真实安卓演示 MP4 与更新后的文字说明；媒体版本分别记录。", "展开的源码目录、评审 README、GitHub 地址、部署说明、测试与公网 APK。", "权限分级与 R1–R22 已知风险。"]
    (folder / "交付清单.md").write_text("# 安言参赛交付\n\n" + "\n".join(f"{i}. [{c[3:]}]({c}/)：{d}" for i, (c, d) in enumerate(zip(CATEGORIES, descriptions), 1)) + f"\n\n源码提交：`{commit}`；APK 构建源码：`{apk_commit}`。视频 {duration:.2f} 秒，真实安卓、隔离规则模式、中文字幕、无音轨，红区展示后取消。\n\n本次只整理文件夹，不生成 ZIP。不含本机账户、会话、设备、私钥、令牌、日志和缓存。副本链接指向 source/ 内的完整文档。APK 连接公网地址，绑定项目 CA。\n", encoding="utf-8")
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p != folder / "manifest.json")
    expected = {f"{category}/{name}" for category, names in mapping.items() for name in names}
    expected.update({f"{CATEGORIES[0]}/{name}" for name in ("作品名称.txt", "报名简介.txt")})
    expected.update({f"{CATEGORIES[1]}/assets/{name}" for name in ("系统架构图.svg", "系统架构图.mmd")})
    expected.update({f"{CATEGORIES[2]}/{name}" for name in ("安言答辩.pptx", "安言演示视频.mp4", "安言演示字幕.srt", "媒体说明.json")})
    expected.update({f"{CATEGORIES[3]}/{name}" for name in ("README.md", "GitHub仓库地址.txt", "部署说明.md", "安装包/安言-公网演示.apk", "安装包/项目CA公开证书.pem")})
    expected.update(f"{CATEGORIES[3]}/source/{name}" for name in source_names)
    expected.add("交付清单.md")
    assert_delivery_files({p.relative_to(folder).as_posix() for p in files}, expected)
    apk = info / "安装包/安言-公网演示.apk"
    manifest = {"commit": commit, "repository": REPOSITORY, "delivery_format": "directory", "introduction_characters": len(intro), "slides": count,
                "apk": {"source_commit": apk_commit, "sha256": hashlib.sha256(apk.read_bytes()).hexdigest()},
                "media_business_version": timing.get("business_version"),
                "speaking_seconds": timing["speaking_seconds"], "video_seconds": duration, "video_mode": "actual_android_offline_rules",
                "files": {p.relative_to(folder).as_posix(): {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files},
                "excluded": ["credentials", "runtime_data", "private_archives", "logs", "build_caches"]}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"commit": commit, "directory": str(folder), "files": len(files), "video_seconds": duration}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery-dir", type=Path, default=ROOT / "submission")
    parser.add_argument("--ffmpeg", help="Path to installed FFmpeg")
    parser.add_argument("--apk-source-commit", required=True, help="Actual source commit used to build the existing APK")
    args = parser.parse_args()
    print(json.dumps(package(args.delivery_dir, args.ffmpeg, args.apk_source_commit), ensure_ascii=False))


if __name__ == "__main__":
    main()
