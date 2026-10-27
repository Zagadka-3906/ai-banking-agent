"""Separate hard environment/action checks from an external conversation judge."""
from __future__ import annotations

from collections import Counter
from math import comb, ceil
from statistics import median
import time
from typing import Any

from pydantic import Field

from scripts.dialogue_eval.schema import StrictModel, Task


class UserMessage(StrictModel):
    text: str = Field(min_length=1, max_length=500)


class CommunicationCheck(StrictModel):
    id: str
    passed: bool
    reason: str = Field(min_length=1, max_length=500)


class Judgement(StrictModel):
    checks: list[CommunicationCheck]
    simulator_faithful: bool
    naturalness: int = Field(ge=1, le=5)


def milestone_checks(phase, observed: dict) -> dict[str, bool]:
    """Check observable intermediate facts without fixing a reasoning trace."""
    routes = {'balance': '/balance', 'cards': '/cards/', 'transactions': '/transactions',
              'contacts': '/book', 'wealth_products': '/wealth/products', 'wealth_holdings': '/wealth/holdings',
              'risk_profile': '/wealth/risk-profile', 'tasks': '/tasks', 'disputes': '/disputes'}
    calls = observed['core_calls']
    checks = {'milestone.read.' + resource: any(call['method'] == 'GET' and routes[resource] in call['path'] for call in calls)
              for resource in phase.required_reads}
    if phase.answered_goals is not None:
        checks['milestone.answered_goals'] = sum(item.get('status') == 'ANSWERED' for item in observed.get('coverage', [])) >= phase.answered_goals
    return checks


def evaluate_judgement(raw: dict, task: Task) -> dict:
    verdict = Judgement.model_validate(raw)
    expected = [f"phase_{index + 1}" for index in range(len(task.phases))]
    if sorted(check.id for check in verdict.checks) != sorted(expected):
        raise ValueError("Judge must assess every phase exactly once")
    return {**verdict.model_dump(), "passed": verdict.simulator_faithful and all(check.passed for check in verdict.checks)}


def run_trial(task: Task, environment: Any, simulator: Any, judge: Any, *, seed: int, trial: int) -> dict:
    """Each environment is a fresh process. Never infer success from a reply."""
    started = time.monotonic()
    result: dict = {"task_id": task.id, "trial": trial, "seed": seed, "status": "ERROR", "turns": [], "checks": []}
    history: list[dict] = []
    try:
        for index, phase in enumerate(task.phases, 1):
            # Confirm/reject are explicit harness actions, never model-controlled tools.
            text = (UserMessage.model_validate(simulator.next(task.user_view(phase, history), phase.utterance)).text
                    if phase.action in {"inquiry", "clarify", "proposal"} else phase.utterance)
            observed = environment.step(text, phase, task)
            required = {"reply", "approval", "mode", "intent", "checks", "elapsed_ms", "core_calls", "answer_kind"}
            if not required <= set(observed) or set(observed) - required - {'coverage'}:
                raise ValueError("Incomplete environment observation")
            if not isinstance(observed["checks"], dict) or not observed["checks"]:
                raise ValueError("Missing hard checks")
            if any(type(value) is not bool for value in observed["checks"].values()):
                raise ValueError("Hard checks must be booleans")
            observed['checks'] = {**observed['checks'], **milestone_checks(phase, observed)}
            result["turns"].append({"phase": index, "user": text, 'goal_shift': phase.goal_shift, **observed})
            history.extend([{"role": "user", "text": text}, {"role": "assistant", "text": observed["reply"],
                            "approval": observed["approval"]}])
            result["checks"].extend({"id": f"phase_{index}.{key}", "passed": value} for key, value in observed["checks"].items())
            # Stop on a hard violation; no subsequent automated confirmation.
            if not all(observed["checks"].values()):
                result["status"] = "FAIL"
                # Score simulator fidelity even for an aborted trajectory. A user
                # that changed the goal is not evidence of agent unreliability.
                partial = task.model_copy(update={"phases": task.phases[:index]})
                verdict = evaluate_judgement(judge.assess(partial, history, environment.evidence(task)), partial)
                result["communication"] = verdict
                if not verdict["simulator_faithful"]:
                    result["status"] = "ERROR"
                    result["error_type"] = "SimulatorDrift"
                return result
        final = environment.finish(task)
        if not final or any(type(value) is not bool for value in final.values()):
            raise ValueError("Missing final environment checks")
        result["checks"].extend({"id": key, "passed": value} for key, value in final.items())
        if not all(final.values()):
            result["status"] = "FAIL"
            return result
        verdict = evaluate_judgement(judge.assess(task, history, environment.evidence(task)), task)
        result["communication"] = verdict
        if not verdict["simulator_faithful"]:
            result["error_type"] = "SimulatorDrift"
            result["status"] = "ERROR"
        else:
            result["status"] = "PASS" if verdict["passed"] else "FAIL"
    except Exception as error:
        # Never serialize provider exceptions/HTTP bodies, which may contain keys.
        result["error_type"] = type(error).__name__
        result["status"] = "ERROR"
    finally:
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result


def summarize(tasks: list[Task], trials: int, rows: list[dict]) -> dict:
    expected = {(task.id, trial) for task in tasks for trial in range(1, trials + 1)}
    identities = [(row["task_id"], row["trial"]) for row in rows]
    if len(identities) != len(set(identities)) or set(identities) - expected:
        raise ValueError("Duplicate or unexpected trial")
    if any(row.get("status") not in {"PASS", "FAIL", "ERROR"} for row in rows):
        raise ValueError("Unknown trial status")
    counts = Counter(row["status"] for row in rows)
    complete = set(identities) == expected and not counts["ERROR"]
    per_task = []
    for task in tasks:
        selected = [row for row in rows if row["task_id"] == task.id]
        passed = sum(row["status"] == "PASS" for row in selected)
        valid = len(selected) == trials and not any(row["status"] == "ERROR" for row in selected)
        per_task.append({"id": task.id, "passed": passed, "planned": trials,
                         "pass_hat_k": {str(k): comb(passed, k) / comb(trials, k) if valid else None
                                        for k in range(1, trials + 1)}})
    modes = Counter(turn.get("mode") or "CANONICAL_OR_REFUSAL" for row in rows for turn in row.get("turns", []))
    answer_kinds = Counter(turn.get("answer_kind", "UNKNOWN") for row in rows for turn in row.get("turns", []))
    repeats = sum(left["reply"].strip() == right["reply"].strip() and bool(left["reply"].strip())
                  for row in rows for left, right in zip(row.get("turns", []), row.get("turns", [])[1:]))
    coverage = Counter(item.get('status') for row in rows for turn in row.get('turns', []) for item in turn.get('coverage', []))
    turns = [turn for row in rows for turn in row.get('turns', [])]
    latencies = sorted(turn['elapsed_ms'] for turn in turns if isinstance(turn.get('elapsed_ms'), (int, float)))
    duplicate_gets = 0
    for turn in turns:
        paths = [call['path'] for call in turn.get('core_calls', []) if call['method'] == 'GET']
        duplicate_gets += len(paths) - len(set(paths))
    shifts = [(row, turn) for row in rows for turn in row.get('turns', []) if turn.get('goal_shift')]
    recovered = sum(all(turn['checks'].values()) and any(check['id'] == f'phase_{turn["phase"]}' and check['passed']
                    for check in (row.get('communication') or {}).get('checks', [])) for row, turn in shifts)
    return {"complete": complete, "planned_trials": len(expected), "recorded_trials": len(rows),
            'intermediate_milestones': dict(Counter('passed' if check['passed'] else 'failed' for row in rows for check in row.get('checks', []) if '.milestone.' in check['id'])),
            'goal_shifts': {'observed': len(shifts), 'answered_in_same_turn': recovered},
            'within_turn_duplicate_gets': duplicate_gets,
            'turn_latency_ms': {'median': median(latencies) if latencies else None, 'p95': latencies[max(0, ceil(.95 * len(latencies))-1)] if latencies else None},
            'goal_statuses': dict(coverage),
            "passed": counts["PASS"], "failed": counts["FAIL"], "errors": counts["ERROR"],
            "not_run": len(expected - set(identities)), "tasks": per_task,
            "pass_hat_k": {str(k): sum(task["pass_hat_k"][str(k)] for task in per_task) / len(tasks) if complete else None
                           for k in range(1, trials + 1)}, "answer_modes": dict(modes), "answer_kinds": dict(answer_kinds),
            "consecutive_identical_replies": repeats,
            "hard_failures": dict(Counter(check["id"].split(".", 1)[-1] for row in rows for check in row.get("checks", [])
                                          if check["passed"] is False))}
