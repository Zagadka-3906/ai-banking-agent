"""Run the isolated banking regression against an already booted dedicated AVD.

No reset is sent to normal 8000/8100 services. Artifacts, random control token,
and process logs remain in the ignored build directory. Only own children stop.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def verify_results(folder: Path, minimum: int = 65):
    cases = []
    for file in folder.glob("TEST-*.xml"):
        root = ET.parse(file).getroot()
        recorded = list(root.iter("testcase"))
        if any(int(root.get(field, "0")) for field in ("failures", "errors", "skipped")) or int(root.get("tests", str(len(recorded)))) != len(recorded):
            raise ValueError("JUnit aggregate counts disagree with device results")
        cases.extend(recorded)
    names = {(case.get("classname"), case.get("name")) for case in cases}
    if len(cases) < minimum or len(names) != len(cases):
        raise ValueError("Missing or duplicated device test results")
    if any(case.find(tag) is not None for case in cases for tag in ("failure", "error", "skipped")):
        raise ValueError("Device test failure, error or skip")
    return {"tests": len(cases), "failures": 0, "errors": 0, "skipped": 0}


def wait_health(port, processes):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if any(p.poll() is not None for p in processes):
            raise RuntimeError("Isolated backend exited; inspect its private log")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(.5)
    raise TimeoutError("Isolated backend health timeout")


def verify_protobuf(folder: Path, expected: int):
    files = list(folder.rglob("test-result.pb"))
    if len(files) != 2:
        raise ValueError("Expected both device and aggregate protobuf results")
    cache = Path(os.getenv("GRADLE_USER_HOME", str(Path.home() / ".gradle"))) / "caches/modules-2/files-2.1"
    proto = next((cache / "com.android.tools.androidtest/test-result-proto/1.0.8").rglob("*.jar"))
    protobuf = next((cache / "com.google.protobuf/protobuf-java/3.25.5").rglob("*.jar"))
    java = str(Path(os.environ["JAVA_HOME"]) / "bin" / ("java.exe" if os.name == "nt" else "java"))
    subprocess.run([java, "-cp", os.pathsep.join([str(proto), str(protobuf)]), str(ROOT / "scripts/VerifyDeviceProto.java"),
                    str(expected), *[str(file) for file in files]], check=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--gradle-init", type=Path)
    parser.add_argument("--minimum-tests", type=int, default=70)
    parser.add_argument("--test-class", help="Run a specific class instead of the regression annotation")
    parser.add_argument("--variant", choices=("scenario", "debug"), default="scenario",
                        help="Debug validates demo OTP autofill; requires an explicit test class")
    parser.add_argument("--case-manifest", type=Path, help="Synthetic multi-turn cases for NaturalConversationUiTest")
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    args = parser.parse_args()
    if args.variant == "debug" and (not args.test_class or args.case_manifest):
        parser.error("Debug acceptance requires --test-class and cannot use scenario manifests")
    output = args.output_dir.resolve()
    if not output.is_relative_to(ROOT / "build"):
        raise ValueError("Artifacts must stay under repository build/")
    output.mkdir(parents=True, exist_ok=True)
    for port in (18800, 18810):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))  # fail instead of reusing someone else's backend
    sdk = os.getenv("ANDROID_HOME") or os.getenv("ANDROID_SDK_ROOT")
    adb = str(Path(sdk) / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")) if sdk else "adb"
    adb_args = [adb, "-s", args.serial]
    name = subprocess.check_output([*adb_args, "emu", "avd", "name"], text=True).splitlines()[0].strip()
    if not name.startswith(("bankagent-ci", "bankagent-account-isolation", "bankagent-sdk37-upgrade")):
        raise ValueError("Use a dedicated banking test AVD, never a personal device")
    pin = os.environ.get("BANK_TEST_DEVICE_PIN", "")
    if not pin.isdigit() or not 4 <= len(pin) <= 12:
        raise ValueError("BANK_TEST_DEVICE_PIN must hold the dedicated emulator PIN")
    # Cold boots with a device PIN stay behind keyguard. Instrumentation can
    # otherwise report missing Compose roots while every Activity is obscured.
    subprocess.run([*adb_args, "emu", "power", "ac", "on"], check=True, capture_output=True)
    subprocess.run([*adb_args, "shell", "svc", "power", "stayon", "true"], check=True, capture_output=True)
    subprocess.run([*adb_args, "shell", "input", "keyevent", "KEYCODE_WAKEUP"], check=True, capture_output=True)
    subprocess.run([*adb_args, "shell", "settings", "put", "global", "stay_on_while_plugged_in", "3"], check=True, capture_output=True)
    subprocess.run([*adb_args, "shell", "settings", "put", "system", "screen_off_timeout", "1800000"], check=True, capture_output=True)
    policy = subprocess.check_output([*adb_args, "shell", "dumpsys", "window", "policy"], text=True)
    if "showing=true" in policy or "inputRestricted=true" in policy:
        subprocess.run([*adb_args, "shell", "input", "keyevent", "KEYCODE_MENU"], check=True, capture_output=True)
        size = subprocess.check_output([*adb_args, "shell", "wm", "size"], text=True).split()[-1]
        width, height = (int(value) for value in size.split("x"))
        subprocess.run([*adb_args, "shell", "input", "swipe", str(width // 2), str(height * 3 // 4), str(width // 2), str(height // 4), "300"], check=True, capture_output=True)
        time.sleep(2)
        for digit in pin:
            subprocess.run([*adb_args, "shell", "input", "keyevent", str(7 + int(digit))], check=True, capture_output=True)
            time.sleep(.15)
        subprocess.run([*adb_args, "shell", "input", "keyevent", "KEYCODE_ENTER"], check=True, capture_output=True)
        time.sleep(2)
        policy = subprocess.check_output([*adb_args, "shell", "dumpsys", "window", "policy"], text=True)
        if "showing=true" in policy or "inputRestricted=true" in policy:
            raise RuntimeError("Dedicated emulator is still locked; unlock before testing")
    env = {**os.environ, "BANK_SCENARIO_CONTROL_TOKEN": secrets.token_hex(32), "BANK_MFA_DYNAMIC_OTP": "0"}
    children, streams = [], []
    try:
        for service, port in (("core", 18800), ("gateway", 18810)):
            stream = (output / f"{service}.log").open("w", encoding="utf-8")
            streams.append(stream)
            children.append(subprocess.Popen([sys.executable, "scripts/scenario_backend.py", service, "--run-dir", str(output), "--mode", args.mode],
                                             cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT))
            wait_health(port, children)
            subprocess.run([*adb_args, "reverse", f"tcp:{port}", f"tcp:{port}"], check=True, capture_output=True)
        env["ANDROID_SERIAL"] = args.serial
        command = [str(ROOT / "android" / ("gradlew.bat" if os.name == "nt" else "gradlew")), "--no-daemon", "--warning-mode=all"]
        if args.gradle_init:
            command += ["-I", str(args.gradle_init.resolve())]
        selector = f"-Pandroid.testInstrumentationRunnerArguments.class={args.test_class}" if args.test_class else "-Pandroid.testInstrumentationRunnerArguments.annotation=com.aibanking.agent.BankingRegression"
        command += (["-PbankScenarioTests=true"] if args.variant == "scenario" else [
            "-PbankScenarioTests=false",
            "-PbankGatewayUrl=http://127.0.0.1:18810/", "-PbankCoreUrl=http://127.0.0.1:18800/",
            "-PbankLocalNetwork=false",
        ])
        command += [selector,
                    f"-Pandroid.testInstrumentationRunnerArguments.controlToken={env['BANK_SCENARIO_CONTROL_TOKEN']}",
                    f"-Pandroid.testInstrumentationRunnerArguments.devicePin={pin}",
                    f":app:connected{args.variant.capitalize()}AndroidTest"]
        if args.case_manifest:
            if args.test_class != "com.aibanking.agent.NaturalConversationUiTest":
                raise ValueError("Case manifests require the natural conversation recorder")
            subprocess.run([*adb_args, "push", str(args.case_manifest.resolve()), "/data/local/tmp/anyan-acceptance-cases.json"], check=True, capture_output=True)
            command.append("-Pandroid.testInstrumentationRunnerArguments.caseFile=/data/local/tmp/anyan-acceptance-cases.json")
        with (output / "gradle.log").open("w", encoding="utf-8") as stream:
            try:
                result = subprocess.run(command, cwd=ROOT / "android", env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=1800)
            except subprocess.TimeoutExpired:
                raise RuntimeError("Device regression timed out; private command arguments are not printed") from None
            if result.returncode:
                raise RuntimeError("Device regression failed; inspect the private Gradle log")
        source = ROOT / "android/app/build/outputs/androidTest-results/connected" / args.variant
        summary = verify_results(source, args.minimum_tests)
        verify_protobuf(source, summary["tests"])
        with urllib.request.urlopen("http://127.0.0.1:18800/api/v1/security/health", timeout=10) as response:
            durability = json.load(response)["ledger_persistence"]
        if not durability["file_configured"] or durability["last_write_failed"] or durability["writes_frozen"]:
            raise RuntimeError("Isolated ledger persistence is not healthy")
        summary.update({"variant": args.variant, "mode": args.mode, "ledger_persistence": "healthy"})
        shutil.copytree(source, output / "device-results", dirs_exist_ok=True)
        if args.case_manifest:
            sys.path.insert(0, str(ROOT))
            from scripts.evaluate_agent_scenarios import evaluate_cases, render_markdown
            subprocess.run([*adb_args, "pull", "/data/local/tmp/anyan-natural-observations.json", str(output / "observations.json")], check=True, capture_output=True)
            report = evaluate_cases(json.loads(args.case_manifest.read_text(encoding="utf-8")), json.loads((output / "observations.json").read_text(encoding="utf-8")))
            (output / "evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            (output / "evaluation.md").write_text(render_markdown(report), encoding="utf-8")
            summary["business_passed"] = report["passed"]
        (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary), flush=True)
        if summary.get("business_passed") is False:
            raise RuntimeError("Business comparison contains mismatches; inspect evaluation.json, not just JUnit")
    finally:
        for child in reversed(children):
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10)
        for stream in streams:
            stream.close()
        for port in (18800, 18810):
            subprocess.run([*adb_args, "reverse", "--remove", f"tcp:{port}"], capture_output=True)


if __name__ == "__main__":
    main()
