"""Compare local parsing with real model parsing; never execute bank actions.

Outputs contain only synthetic case IDs and extracted allow-listed fields.
Missing credentials and model errors are visible, never counted as model wins.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def score_case(case, parsed, clarify):
    issues = []
    for field, expected in case["expect"].items():
        actual = clarify if field == "clarify" else parsed.get(field)
        if actual != expected:
            issues.append({"field": field, "expected": expected, "actual": actual})
    return issues


def summarize(rows):
    completed = [row for row in rows if row["status"] != "NOT_RUN"]
    checked = Counter(field for row in completed for field in row.get("checked_fields", []))
    errors = Counter(field for row in completed for field in (row.get("checked_fields", []) if row["status"] == "MODEL_ERROR" or any(i["field"] == "runtime" for i in row["issues"]) else {i["field"] for i in row["issues"]}))
    return {"cases": len(rows), "evaluated": len(completed), "passed": sum(row["status"] == "PASS" for row in completed),
            "not_run": len(rows) - len(completed), "p50_ms": percentile([r["elapsed_ms"] for r in completed], .5),
            "p95_ms": percentile([r["elapsed_ms"] for r in completed], .95),
            "field_errors": dict(Counter(issue["field"] for row in completed for issue in row["issues"])),
            "field_metrics": {field: {"checked": count, "correct": count - errors[field], "accuracy": round((count - errors[field]) / count, 4)} for field, count in checked.items()},
            "model_calls": sum(row.get("model_calls", 0) for row in rows),
            "model_returns": sum(row.get("model_returns", 0) for row in rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "tests/data/intent_benchmark.json")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--live", action="store_true", help="Explicitly allow the configured model to receive the synthetic dataset")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # Never touch developer device/ledger/checkpoint stores.
    os.environ.update({"BANK_DEVICE_STORE_PATH": "", "BANK_LEDGER_STORE_PATH": "", "BANK_APPROVAL_STORE_PATH": "memory",
                       "BANK_AUDIT_LOG_PATH": "", "BANK_AUDIT_LOG_DIR": "off", "BANK_CONVERSATION_DB": "memory", "BANK_FEISHU_ENABLED": "0"})
    if not args.live:
        os.environ["DEEPSEEK_API_KEY"] = ""
    sys.path.insert(0, str(ROOT))
    import agent
    from bank_agent import runtime
    from planner import ALL_INTENTS, needs_clarification, validate_intent_payload
    from security import InjectionError, sanitize_text

    raw = args.cases.read_bytes()
    cases = json.loads(raw)
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Dataset must have unique nonempty case IDs")
    if any(case["expect"].get("intent", "UNKNOWN") not in ALL_INTENTS for case in cases):
        raise ValueError("Dataset uses an intent outside the application allowlist")
    results = {}
    original_model = runtime.llm
    for mode in ("rules", "model"):
        rows = []
        os.environ["BANK_MODEL_FAST_PATH"] = "0"
        for case in cases:
            row = {"id": case["id"], "status": "NOT_RUN", "issues": [], "elapsed_ms": 0, "model_calls": 0, "model_returns": 0,
                   "checked_fields": [*case["expect"], *(["blocked"] if case.get("blocked") else [])]}
            rows.append(row)
            if mode == "model" and (not args.live or original_model is None):
                continue

            class ObservedModel:
                def __init__(self, observation):
                    self.observation = observation

                def invoke(self, messages):
                    self.observation["model_calls"] += 1
                    response = original_model.invoke(messages)
                    self.observation["model_returns"] += 1
                    return response

            runtime.llm = ObservedModel(row) if mode == "model" else None
            started = time.perf_counter()
            try:
                text = sanitize_text(case["input"], field="评测输入", max_length=2000)
                parsed = validate_intent_payload(agent._model_parse(text) if mode == "model" else agent._local_parse(text))
                row["issues"] = score_case(case, parsed, needs_clarification(parsed))
                row["parsed"] = {key: parsed.get(key) for key in ("intent", "amount", "target", "date_scope")}
                row["parse_source"] = (parsed.get("parse_meta") or {}).get("source")
            except InjectionError:
                row["issues"] = [] if case.get("blocked") else [{"field": "blocked", "expected": False, "actual": True}]
            except Exception as error:
                row["issues"] = [{"field": "runtime", "actual": type(error).__name__}]
            else:
                if case.get("blocked"):
                    row["issues"].append({"field": "blocked", "expected": True, "actual": False})
            row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
            row["status"] = "FAIL" if row["issues"] else "PASS"
            # A swallowed model outage must not look like a successful model experiment.
            if mode == "model" and row["model_calls"] and not row["model_returns"]:
                row["status"] = "MODEL_ERROR"
            print(f"{mode} {case['id']}: {row['status']}", flush=True)
        results[mode] = {"summary": summarize(rows), "cases": rows}
    report = {"dataset_sha256": hashlib.sha256(raw).hexdigest(), "model": os.getenv("BANK_LLM_MODEL", "deepseek-chat"),
              "scope": "parser_only_no_bank_execution_no_task_completion_claim", "results": results}
    (args.output_dir / "benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 意图理解对比评测", "", "仅评测解析与澄清，不执行账务，不将结果称为端到端完成率。", "",
             "| 模式 | 实测 | 通过 | 未运行 | P50 ms | P95 ms | 模型调用/返回 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for mode, result in results.items():
        s = result["summary"]
        lines.append(f"| {mode} | {s['evaluated']} | {s['passed']} | {s['not_run']} | {s['p50_ms']} | {s['p95_ms']} | {s['model_calls']}/{s['model_returns']} |")
    lines += ["", "数据集 SHA256：`" + report["dataset_sha256"] + "`", "", "逐项差异见 benchmark.json。模型失败、未运行与规则回退须分别解释。"]
    (args.output_dir / "benchmark.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
