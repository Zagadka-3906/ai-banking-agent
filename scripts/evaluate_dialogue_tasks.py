"""Isolated task simulations with an adaptive user, hard oracle and post-hoc judge.

This borrows evaluation concepts from tau2-bench; scores are NOT tau2 scores.
Dry-run has no side effects. Live mode requires explicit --live, model keys and
an ignored build/ output directory. Exit: 0 pass/plan, 1 task failure, 2 incomplete.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.dialogue_eval.engine import run_trial, summarize
from scripts.dialogue_eval.schema import Task, load_dataset


def source_fingerprints() -> dict[str, str]:
    files = [*ROOT.glob("*.py"), Path(__file__), ROOT/'scripts/optimize_dialogue_prompts.py']
    for directory in ("bank_agent", "bank_core", "bank_security", "bank_planning", "scripts/dialogue_eval"):
        files.extend((ROOT / directory).rglob("*.py"))
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(set(files))}


def isolated_settings(folder: Path) -> dict[str, str]:
    return {"PYTHON_DOTENV_DISABLED": "1", "BANK_EVAL_ISOLATED_PROCESS": "1",
            "BANK_AUTH_SECRET": secrets.token_hex(32), "BANK_TOKEN_SECRET": "",
            "BANK_REQUIRE_SESSION": "1", "BANK_REQUIRE_CLIENT_AUTH": "0", "BANK_DEMO_MODE": "1",
            "BANK_LOGIN_REQUIRE_OTP": "0", "BANK_MFA_DYNAMIC_OTP": "1", "BANK_MFA_OTP": "",
            "BANK_CONVERSATION_DB": str(folder / "conversations.sqlite3"), "BANK_APPROVAL_STORE_PATH": str(folder / "approvals.json"),
            "BANK_LEDGER_STORE_PATH": str(folder / "ledger.json"), "BANK_DEVICE_STORE_PATH": str(folder / "devices.json"),
            "BANK_AUDIT_LOG_DIR": str(folder / "audit"), "BANK_AUDIT_LOG_PATH": "", "BANK_ADMIN_AUDIT_TOKEN": "",
            "BANK_FEISHU_ENABLED": "0", "DASHSCOPE_API_KEY": "", "BANK_ASR_ENABLED": "0",
            "BANK_API_BASE": "http://evaluation.invalid/api/v1", "BANK_TASK_SCHEDULER_SECONDS": "0",
            "BANK_MODEL_FAST_PATH": "0", "BANK_ANSWER_PLANNING": "1", "BANK_ANSWER_MODE": "enforce",
            "BANK_AGENT_RATE_LIMIT_PER_MINUTE": "0", "BANK_RATE_LIMIT_PER_MINUTE": "0", "BANK_SESSION_TTL_SECONDS": "86400",
            "BANK_MODEL_TIMEOUT_SECONDS": "12", "BANK_MODEL_MAX_RETRIES": "0", "BANK_SANDBOX_ALLOW_THREAD_FALLBACK": "0"}


def worker(job_path: Path) -> int:
    if os.getenv("BANK_EVAL_ISOLATED_PROCESS") != "1" or any(name in sys.modules for name in ("main", "agent_api", "security")):
        raise RuntimeError("Worker requires a fresh isolated process")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    task = Task.model_validate(job["task"])
    from bank_agent.prompt_preferences import install_for_evaluation
    install_for_evaluation(job.get('instructions', {}))
    with tempfile.TemporaryDirectory(prefix="anyan-task-eval-") as temporary:
        os.environ.update(isolated_settings(Path(temporary)))
        from langchain_openai import ChatOpenAI
        from pydantic import SecretStr
        from scripts.dialogue_eval.bank_environment import BankEnvironment
        from scripts.dialogue_eval.models import ConversationJudge, ScriptedUser, SimulatedUser
        from bank_agent import runtime

        counters: dict[str, dict] = {}

        class ObservedModel:
            def __init__(self, model, role):
                self.model, self.role = model, role

            def invoke(self, messages, **kwargs):
                count = counters.setdefault(self.role, {"calls": 0, "returned": 0})
                count["calls"] += 1
                answer = self.model.invoke(messages, **kwargs)
                count["returned"] += 1
                return answer

        def evaluation_model(name, role, temperature):
            prefix = "BANK_EVAL_" + role.upper()
            return ObservedModel(ChatOpenAI(model=name, api_key=SecretStr(os.environ.get(prefix + "_API_KEY") or os.environ["DEEPSEEK_API_KEY"]),
                                           base_url=os.environ.get(prefix + "_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                                           temperature=temperature, timeout=20, max_retries=0), role)

        if runtime.llm is None:
            raise RuntimeError("Missing live agent model")
        runtime.llm = ObservedModel(runtime.llm, "agent")
        if runtime.answer_reviewer is not None:
            runtime.answer_reviewer = ObservedModel(runtime.answer_reviewer, "answer_review")
        user = (SimulatedUser(evaluation_model(job["models"]["user"], "user", 0.8), job["seed"])
                if job["user"] == "model" else ScriptedUser())
        judge = ConversationJudge(evaluation_model(job["models"]["judge"], "judge", 0))
        environment = BankEnvironment(task)
        try:
            result = run_trial(task, environment, user, judge, seed=job["seed"], trial=job["trial"])
            result["model_calls"] = counters
            Path(job["result"]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            environment.close()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "tests/data/dialogue_tasks.json")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261025)
    parser.add_argument("--task", action="append", default=[])
    parser.add_argument("--user", choices=["model", "scripted"], default="model")
    parser.add_argument('--prompt-bundle', type=Path, help='Offline-only JSON instruction candidate; never changes production')
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--_worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._worker:
        return worker(args._worker)
    if not 1 <= args.trials <= 10:
        parser.error("--trials must be between 1 and 10")
    dataset, digest = load_dataset(args.cases)
    from bank_agent.prompt_preferences import validate_instructions
    bundle_bytes = args.prompt_bundle.read_bytes() if args.prompt_bundle else b'{}'
    instructions = validate_instructions(json.loads(bundle_bytes))
    unknown = set(args.task) - {task.id for task in dataset.tasks}
    if unknown:
        parser.error("Unknown task IDs: " + ", ".join(sorted(unknown)))
    tasks = [task for task in dataset.tasks if not args.task or task.id in args.task]
    plan = {"status": "PLAN_ONLY", "dataset_sha256": digest, 'dataset_split': dataset.split,
            'prompt_bundle_sha256': hashlib.sha256(bundle_bytes).hexdigest(), "task_ids": [task.id for task in tasks], "user": args.user,
            "trials_per_task": args.trials, "planned_trials": len(tasks) * args.trials,
            "max_agent_turns": sum(len(task.phases) for task in tasks) * args.trials}
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    if not args.live or args.output_dir is None:
        parser.error("Use --dry-run, or explicitly --live --output-dir build/<new-run>")
    output = args.output_dir.resolve()
    if not output.is_relative_to(ROOT / "build") or output == ROOT / "build":
        parser.error("Evaluation transcripts must stay in an ignored build/<new-run> directory")
    if output.exists():
        parser.error("Use a new output directory to retain previous evidence")
    from dotenv import dotenv_values
    values = {**dotenv_values(ROOT / ".env"), **os.environ}
    if not values.get("DEEPSEEK_API_KEY"):
        parser.error("DEEPSEEK_API_KEY is required; missing credentials are not a successful simulation")
    models = {"agent": values.get("BANK_LLM_MODEL") or "deepseek-chat",
              "user": values.get("BANK_EVAL_USER_MODEL") or values.get("BANK_LLM_MODEL") or "deepseek-chat",
              "judge": values.get("BANK_EVAL_JUDGE_MODEL") or values.get("BANK_LLM_MODEL") or "deepseek-chat"}
    child_env = {**os.environ, "BANK_EVAL_ISOLATED_PROCESS": "1"}
    for key in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "BANK_LLM_MODEL", "BANK_REVIEW_MODEL"):
        if values.get(key) is not None:
            child_env[key] = str(values[key])
    for role in ("USER", "JUDGE"):
        for field in ("API_KEY", "BASE_URL"):
            key = f"BANK_EVAL_{role}_{field}"
            if values.get(key):
                child_env[key] = str(values[key])
    output.mkdir(parents=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT))
    rows: list[dict] = []
    metadata = {**plan, "status": "RUNNING", "revision": revision, "worktree_dirty": dirty, "models": models,
                "dataset_source": dataset.source, "started_utc": datetime.now(timezone.utc).isoformat(),
                "source_sha256": source_fingerprints()}

    def save():
        report = {**metadata, "summary": summarize(tasks, args.trials, rows), "trials": rows}
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    save()
    task_positions = {task.id: index for index, task in enumerate(dataset.tasks)}
    for task in tasks:
        for trial in range(1, args.trials + 1):
            trial_seed = args.seed + task_positions[task.id] * 100 + trial
            result_path = output / f"{task.id}-{trial}.json"
            job = {"task": task.model_dump(), "trial": trial, "seed": trial_seed, "result": str(result_path),
                   "models": models, "user": args.user, 'instructions': instructions}
            job_path = output / "worker-job.json"
            job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
            try:
                if source_fingerprints() != metadata["source_sha256"] or hashlib.sha256(args.cases.read_bytes()).hexdigest() != digest:
                    raise RuntimeError("Candidate sources or dataset changed during evaluation")
                if args.prompt_bundle and args.prompt_bundle.read_bytes() != bundle_bytes:
                    raise RuntimeError('Instruction candidate changed during evaluation')
                process = subprocess.run([sys.executable, "-m", "scripts.evaluate_dialogue_tasks", "--_worker", str(job_path)],
                                         cwd=ROOT, env=child_env, capture_output=True, timeout=600)
                if process.returncode != 0 or not result_path.exists():
                    # Retain private diagnostics locally; provider errors never enter the public summary.
                    (output / f"{task.id}-{trial}.worker.log").write_bytes(process.stdout + process.stderr)
                    raise RuntimeError("Evaluation worker failed")
                row = json.loads(result_path.read_text(encoding="utf-8"))
                if source_fingerprints() != metadata["source_sha256"]:
                    raise RuntimeError("Candidate sources changed during evaluation")
            except (subprocess.TimeoutExpired, OSError, ValueError, RuntimeError) as error:
                row = {"task_id": task.id, "trial": trial, "seed": trial_seed, "status": "ERROR", "error_type": type(error).__name__}
            rows.append(row)
            save()
            print(json.dumps({"task_id": task.id, "trial": trial, "status": row["status"],
                              "turns": len(row.get("turns", []))}), flush=True)
    (output / "worker-job.json").unlink(missing_ok=True)
    metadata["status"] = "COMPLETED"
    report = save()
    summary = report["summary"]
    lines = ["# 银行多轮任务评测", "", f"版本：{revision}；工作区有改动：{dirty}；用户模式：{args.user}",
             f"数据集 SHA256：{digest}", "", "| 任务 | 通过 / 计划 | pass^1 |", "| --- | --- | --- |"]
    for task in summary["tasks"]:
        value = task["pass_hat_k"]["1"]
        lines.append(f"| {task['id']} | {task['passed']} / {task['planned']} | {value if value is not None else '未完整评测'} |")
    lines.extend(["", "pass^k 是 k 次全部成功的组合估计；缺失/错误不计成功，错误单列。",
                  "本工具借鉴 tau2-bench 概念，并非官方银行 domain 或排行榜结果。"])
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 2 if not summary["complete"] else 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
