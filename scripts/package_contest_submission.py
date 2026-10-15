"""Package the committed source and reviewed media; exclude all private run data."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery-dir", type=Path, default=ROOT / "build/contest-upgrade/delivery")
    args = parser.parse_args()
    folder = args.delivery_dir.resolve()
    if not folder.is_relative_to(ROOT / "build"):
        raise ValueError("Deliveries must stay under ignored build/")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT):
        raise ValueError("Commit and verify source before packaging")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    media = [folder / "安言答辩.pptx", folder / "安言演示视频.mp4"]
    if not all(file.is_file() and file.stat().st_size for file in media):
        raise ValueError("Reviewed deck and actual demo video are required")
    source = folder / "source.zip"
    subprocess.run(["git", "archive", "--format=zip", "--prefix=ai-banking-agent/", "--output", str(source), "HEAD"], cwd=ROOT, check=True)
    files = [source, *media]
    manifest = {"commit": commit, "files": {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in files},
                "excluded": ["credentials", "logs", "raw_observations", "build_caches", "apk"],
                "pending": ["Feishu platform policy 21001", "human usability exports", "real-device speech recognition"]}
    archive = folder / "安言参赛交付.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for file in files:
            bundle.write(file, file.name)
        bundle.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        bundle.writestr("交付说明.md", f"# 安言参赛交付\n\n源码提交：`{commit}`\n\n仓库：https://github.com/Zagadka-3906/ai-banking-agent\n\n"
                        "source.zip 含 README、部署说明、作品简介、技术文档、安全自评与测试。PPT 可编辑，视频为真实模拟器操作、规则模式、中文字幕、无音轨。\n\n"
                        "飞书本轮被平台策略 21001 拒绝；真实人员记录未回收；端侧语音需支持的真机继续验收。详情见源码中的 docs/参赛完善与验收-2026-10-15.md。\n")
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None or len(bundle.namelist()) != 5:
            raise ValueError("Submission archive validation failed")
    print(json.dumps({"commit": commit, "archive": str(archive), "bytes": archive.stat().st_size}))


if __name__ == "__main__":
    main()
