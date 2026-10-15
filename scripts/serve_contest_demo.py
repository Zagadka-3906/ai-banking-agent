"""Keep a dedicated, disposable scenario session available for human testing.

Use --reset only between trials, --stop after testing. No normal banking store
is read or reset. All controls and records stay under ignored build/.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_android_regression import wait_health


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", default="emulator-5580")
    parser.add_argument("--run-dir", type=Path, default=ROOT / "build/contest-upgrade/human-session")
    parser.add_argument("--duration-seconds", type=int, default=3600)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    folder = args.run_dir.resolve()
    if not folder.is_relative_to(ROOT / "build"):
        raise ValueError("Use a private build/ directory")
    folder.mkdir(parents=True, exist_ok=True)
    if args.stop:
        (folder / "stop").touch()
        return
    if args.reset:
        token = (folder / "control-token.txt").read_text().strip()
        for port in (18810, 18800):
            request = urllib.request.Request(f"http://127.0.0.1:{port}/__scenario/reset", data=b"{}",
                headers={"X-Scenario-Token": token, "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=10) as response:
                assert response.status == 200
        print("Isolated trial reset. Sign out and log in again before the next trial.")
        return
    if (folder / "stop").exists():
        raise ValueError("Use a new run directory after a stopped session")
    for port in (18800, 18810):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    sdk = Path(os.environ.get("ANDROID_HOME", os.environ.get("ANDROID_SDK_ROOT", "")))
    adb = str(sdk / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb"))
    device = [adb, "-s", args.serial]
    avd = subprocess.check_output([*device, "emu", "avd", "name"], text=True).splitlines()[0]
    if not avd.startswith(("bankagent-ci", "bankagent-account-isolation", "bankagent-sdk37-upgrade")):
        raise ValueError("A dedicated test AVD is required")
    token = secrets.token_hex(32)
    (folder / "control-token.txt").write_text(token)
    env = {**os.environ, "BANK_SCENARIO_CONTROL_TOKEN": token, "BANK_MFA_DYNAMIC_OTP": "0"}
    children, streams = [], []
    try:
        for service, port in (("core", 18800), ("gateway", 18810)):
            stream = (folder / f"{service}.log").open("w", encoding="utf-8")
            streams.append(stream)
            children.append(subprocess.Popen([sys.executable, "scripts/scenario_backend.py", service, "--run-dir", str(folder), "--mode", "offline"],
                cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT))
            wait_health(port, children)
            subprocess.run([*device, "reverse", f"tcp:{port}", f"tcp:{port}"], check=True, capture_output=True)
        subprocess.run([*device, "install", "-r", str(ROOT / "android/app/build/outputs/apk/scenario/app-scenario.apk")], check=True, capture_output=True)
        subprocess.run([*device, "shell", "am", "start", "-n", "com.aibanking.agent.scenariotest/com.aibanking.agent.MainActivity"], check=True, capture_output=True)
        (folder / "ready.json").write_text(json.dumps({"serial": args.serial, "mode": "offline_rules", "max_duration_seconds": args.duration_seconds}), encoding="utf-8")
        print("Human test session ready. Use the local usability page. Reset between trials; stop after testing.", flush=True)
        deadline = time.monotonic() + min(max(args.duration_seconds, 60), 7200)
        while time.monotonic() < deadline and not (folder / "stop").exists():
            if any(child.poll() is not None for child in children):
                raise RuntimeError("A demo backend exited")
            time.sleep(1)
    finally:
        for child in reversed(children):
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
        for stream in streams:
            stream.close()
        for port in (18800, 18810):
            subprocess.run([*device, "reverse", "--remove", f"tcp:{port}"], capture_output=True)


if __name__ == "__main__":
    main()
