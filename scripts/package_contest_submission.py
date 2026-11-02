"""Package five contest categories, excluding credentials and runtime data.

This material package does not certify stable-release acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
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
        if (forbidden.intersection(p.parts) or (leaf.startswith(".env") and not leaf.endswith(".example"))
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


def package(folder: Path, ffmpeg: str | None = None) -> dict:
    folder = folder.resolve()
    if folder != ROOT / "submission":
        raise ValueError("Delivery destination must be ignored project submission/")
    ensure_source_clean(ROOT)
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
            shutil.copy2(ROOT / "docs" / name, folder / category / name)
    shutil.copytree(ROOT / "docs/assets", folder / CATEGORIES[1] / "assets", dirs_exist_ok=True)
    (folder / CATEGORIES[0] / "作品名称.txt").write_text("安言：安全可控的银行 AI 智能体\n", encoding="utf-8")
    (folder / CATEGORIES[0] / "报名简介.txt").write_text(intro + "\n", encoding="utf-8")
    info = folder / CATEGORIES[3]
    (info / "GitHub仓库地址.txt").write_text(REPOSITORY + "\n", encoding="utf-8")
    (info / "README.md").write_text(
        "# 作品源码与运行\n\n"
        "仓库：" + REPOSITORY + "\n\n"
        "解压 source.zip 后，先读根目录 README.md，再按 deploy/README.md 部署。"
        "测试用例、验证记录和安全自评报告在 docs/ 下，本目录放的是便于浏览的副本。\n\n"
        "副本里的相对链接以解压后的源码目录为准，不指向本交付目录。"
        "源码包不含真实配置、私钥、会话、设备登记、账本、日志和构建缓存。\n",
        encoding="utf-8")
    shutil.copy2(ROOT / "deploy/README.md", info / "部署说明.md")
    source = info / "source.zip"
    subprocess.run(["git", "archive", "--format=zip", "--prefix=ai-banking-agent/", "--output", str(source), "HEAD"], cwd=ROOT, check=True)
    with zipfile.ZipFile(source) as archive:
        assert_public_source(archive.namelist())
        if archive.testzip():
            raise ValueError("Invalid source archive")
    descriptions = [f"名称与 {len(intro)} 字简介，自行上传报名系统。", "系统架构图、核心算法、安全设计。", "10 页可编辑 PPTX、9 分 10 秒讲稿、真实安卓演示 MP4。", "源码、README、GitHub 地址、部署说明、测试与公网 APK。", "权限分级与 R1–R22 已知风险。"]
    (folder / "交付清单.md").write_text("# 安言参赛交付\n\n" + "\n".join(f"{i}. [{c[3:]}]({c}/)：{d}" for i, (c, d) in enumerate(zip(CATEGORIES, descriptions), 1)) + f"\n\n源码提交：`{commit}`。视频 {duration:.2f} 秒，真实安卓、隔离规则模式、中文字幕、无音轨，红区展示后取消。\n\n不含本机账户、会话、设备、私钥、令牌、日志和缓存。源码解压后查看完整相对链接。APK 连接公网地址，绑定项目 CA。\n", encoding="utf-8")
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.name not in {"安言参赛交付.zip", "manifest.json"})
    expected = {f"{category}/{name}" for category, names in mapping.items() for name in names}
    expected.update({f"{CATEGORIES[0]}/{name}" for name in ("作品名称.txt", "报名简介.txt")})
    expected.update({f"{CATEGORIES[1]}/assets/{name}" for name in ("系统架构图.svg", "系统架构图.mmd")})
    expected.update({f"{CATEGORIES[2]}/{name}" for name in ("安言答辩.pptx", "安言演示视频.mp4", "安言演示字幕.srt", "媒体说明.json")})
    expected.update({f"{CATEGORIES[3]}/{name}" for name in ("README.md", "GitHub仓库地址.txt", "部署说明.md", "source.zip", "安装包/安言-公网演示.apk", "安装包/项目CA公开证书.pem")})
    expected.add("交付清单.md")
    assert_delivery_files({p.relative_to(folder).as_posix() for p in files}, expected)
    manifest = {"commit": commit, "repository": REPOSITORY, "introduction_characters": len(intro), "slides": count,
                "speaking_seconds": timing["speaking_seconds"], "video_seconds": duration, "video_mode": "actual_android_offline_rules",
                "files": {p.relative_to(folder).as_posix(): {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files},
                "excluded": ["credentials", "runtime_data", "private_archives", "logs", "build_caches"]}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    bundle = folder / "安言参赛交付.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for p in [*files, folder / "manifest.json"]:
            archive.write(p, p.relative_to(folder).as_posix())
    with zipfile.ZipFile(bundle) as archive:
        if archive.testzip():
            raise ValueError("Invalid delivery archive")
    return {"commit": commit, "archive": str(bundle), "bytes": bundle.stat().st_size, "files": len(files), "video_seconds": duration}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery-dir", type=Path, default=ROOT / "submission")
    parser.add_argument("--ffmpeg", help="Path to installed FFmpeg")
    args = parser.parse_args()
    print(json.dumps(package(args.delivery_dir, args.ffmpeg), ensure_ascii=False))


if __name__ == "__main__":
    main()
