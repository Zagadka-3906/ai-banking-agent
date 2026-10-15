"""Summarize recorded business checks separately from instrumentation success."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from benchmark_intents import percentile


def summarize(folder):
    report = json.loads((folder / "evaluation.json").read_text(encoding="utf-8"))
    observations = json.loads((folder / "observations.json").read_text(encoding="utf-8"))
    calls, latencies = [], []
    for case in observations:
        # Recorder snapshots are cumulative within each reset case.
        calls.extend(max((turn.get("model_calls") or [] for turn in case["turns"]), key=len, default=[]))
        latencies.extend(turn["server"]["elapsed_ms"] for turn in case["turns"]
                         if isinstance(turn.get("server"), dict) and isinstance(turn["server"].get("elapsed_ms"), (int, float)))
    checks = [check for case in report["cases"] for turn in case["turns"] for check in turn["checks"]
              if check["name"] == "unchanged" and check.get("expected") is True]
    sources = Counter((((turn.get("server") or {}).get("parsed") or {}).get("parse_meta") or {}).get("source", "blocked_or_unparsed")
                      for case in observations for turn in case["turns"] if turn.get("input"))
    return {"cases": report["summary"]["cases"], "turns": report["summary"]["turns"],
            "cases_total": report["summary"]["cases_total"], "turns_total": report["summary"]["turns_total"],
            "model_calls": len(calls), "model_returns": sum(call.get("success") is True for call in calls),
            "p50_ms": percentile(latencies, .5), "p95_ms": percentile(latencies, .95),
            "parse_sources": dict(sources),
            "unchanged_checks": len(checks), "unexpected_state_changes": sum(not check["passed"] for check in checks),
            "passed": report["passed"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules-dir", type=Path, required=True)
    parser.add_argument("--live-dir", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    results = {"rules": summarize(args.rules_dir), "live_hybrid": summarize(args.live_dir)}
    report = {"dataset_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(), "results": results,
              "scope": "fixed_synthetic_end_to_end_regression_not_human_study_or_blind_generalization"}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 多轮端到端对比", "", "用例通过包含正确的澄清、待审批、取消或拒绝，不能全部称为已办成资金业务。", "",
             "| 模式 | 场景通过 | 对话轮通过 | 模型调用/返回 | P95 ms | 应保持状态/异常改变 |", "| --- | --- | --- | --- | --- | --- |"]
    for mode, result in results.items():
        lines.append(f"| {mode} | {result['cases']['PASS']}/{result['cases_total']} | {result['turns']['PASS']}/{result['turns_total']} | "
                     f"{result['model_calls']}/{result['model_returns']} | {result['p95_ms']} | {result['unchanged_checks']}/{result['unexpected_state_changes']} |")
    lines += ["", "未通过、观测错误与未录制均保留在各轮 evaluation.json；不以 JUnit 单项成功替代业务核对。"]
    (args.output_dir / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
