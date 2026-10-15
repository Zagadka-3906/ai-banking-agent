"""Summarize paired human observations; never invent missing participants."""
from __future__ import annotations

import argparse
import glob
import json
import math
from pathlib import Path
import statistics


def evaluate(records):
    unique = {}
    for row in records:
        key = (row["participant"], row["task"], row["mode"])
        if key in unique or row["mode"] not in {"agent", "baseline"}:
            raise ValueError("Duplicate or invalid trial")
        if row.get("source") != "human_observed" or row.get("consent") is not True:
            raise ValueError("Only consented human observations are accepted")
        if (isinstance(row["seconds"], bool) or not isinstance(row["seconds"], (int, float)) or not math.isfinite(row["seconds"]) or row["seconds"] < 0
                or type(row["interactions"]) is not int or row["interactions"] < 0
                or row["outcome"] not in {"PASS", "FAIL", "UNAVAILABLE"} or not row.get("baseline")):
            raise ValueError("Invalid duration, outcome, interactions or baseline")
        unique[key] = row
    pairs = []
    for (person, task, mode), agent in unique.items():
        baseline = unique.get((person, task, "baseline"))
        if mode == "agent" and baseline and agent["outcome"] == baseline["outcome"] == "PASS" and agent["baseline"] == baseline["baseline"]:
            pairs.append({"participant": person, "task": task, "saved_seconds": baseline["seconds"] - agent["seconds"],
                          "saved_interactions": baseline["interactions"] - agent["interactions"]})
    return {"participants": len({row["participant"] for row in records}), "trials": len(records), "successful_pairs": len(pairs),
            "not_available": sum(row["outcome"] == "UNAVAILABLE" for row in records),
            "failures": sum(row["outcome"] == "FAIL" for row in records),
            "median_saved_seconds": statistics.median([p["saved_seconds"] for p in pairs]) if pairs else None,
            "median_saved_interactions": statistics.median([p["saved_interactions"] for p in pairs]) if pairs else None,
            "pairs": pairs, "limitation": "主持人记录，不是独立审计；仅成功且同一对照流程的成对任务参与耗时比较"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    files = [Path(name) for pattern in args.inputs for name in glob.glob(str(pattern))]
    if not files:
        raise ValueError("No participant exports found; no study result can be inferred")
    rows = [row for file in files for row in json.loads(file.read_text(encoding="utf-8"))["records"]]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evaluate(rows), ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
