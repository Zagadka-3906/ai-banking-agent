"""Verify the full chain: ConversationJudge response -> trial result -> report -> release gate.

Ensures that the degraded verdict path (stripping extra forbidden keys while preserving
valid required checks) allows a PASS trial through release_gate, while missing fields,
invalid types, or negative judgements fail closed.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace


from scripts.dialogue_eval.engine import run_trial, user_goal_record
from scripts.dialogue_eval.models import ConversationJudge
from scripts.dialogue_eval.release_gate import assess_release
from scripts.dialogue_eval.schema import load_dataset


DATASET_PATH = Path(__file__).parent / "data/dialogue_tasks.json"


def make_judge(raw_output: dict, rubric_version="v3"):
    def invoke(messages):
        return SimpleNamespace(content=json.dumps(raw_output, ensure_ascii=False))
    return ConversationJudge(SimpleNamespace(invoke=invoke), rubric_version)


class MockEnv:
    def __init__(self, *, safe=True):
        self.safe = safe

    def step(self, text, phase, task):
        return {
            "reply": "本产品没有信用卡额度，演示卡日额度为10000元。",
            "approval": None,
            "mode": "CANONICAL",
            "intent": "INQUIRY",
            "answer_kind": "CANONICAL",
            "read_rows": {"cards": 1},
            "coverage": [{"goal_id": "g1", "status": "SATISFIED", "resource": "cards"}],
            "read_failures": [],
            "answer_diagnostics": [],
            "core_calls": [{"method": "GET", "path": "/api/v1/cards"}],
            "checks": {
                "no_business_write": self.safe,
                "no_new_authorization": True,
                "approval_preserved": True,
                "core_get_only": True,
            },
            "elapsed_ms": 10,
        }

    def finish(self, task):
        return {"final_state": True}

    def evidence(self, task):
        return {"unit_evidence": True}

    def close(self):
        pass


def build_minimal_release_manifest(tmp_path, trial_result: dict):
    dataset, digest = load_dataset(DATASET_PATH)
    task = dataset.tasks[0].model_copy(update={"phases": dataset.tasks[0].phases[:1]})
    tasks = [task]

    cases_file = tmp_path / "cases.json"
    raw_cases = json.dumps({
        "version": 1,
        "rubric_version": "v3",
        "split": "development",
        "source": "unit fixture",
        "tasks": [t.model_dump() for t in tasks],
    }).encode("utf-8")
    cases_file.write_bytes(raw_cases)

    proof = user_goal_record(
        {"faithful": True, "expected_act": "INQUIRE", "actual_act": "INQUIRE", "reason": "ok"},
        task.phases[0].goal,
        "查规则"
    )
    rows = []
    for trial_idx in range(1, 6):
        row = deepcopy(trial_result)
        row["task_id"] = task.id
        row["trial"] = trial_idx
        row["turns"] = [{
            "phase": 1,
            "user": "查规则",
            "coverage": [{"goal_id": "g1", "status": "SATISFIED", "resource": "cards"}],
            "read_failures": [],
            "user_goal_check": deepcopy(proof),
            "judge_context": {"version": 2, "phase": 1, "source": "post_step_environment", "evidence": {"unit_evidence": True}},
        }]
        row["simulator_goal_checks"] = [{"phase": 1, "user": "查规则", "check": deepcopy(proof)}]
        rows.append(row)

    report_path = tmp_path / "report.json"
    report_data = {
        "status": "COMPLETED",
        "dataset_sha256": hashlib.sha256(raw_cases).hexdigest(),
        "evaluation_transport_version": 4,
        "judge_schema_diagnostics_version": 1,
        "judge_context_version": 2,
        "simulator_goal_check_version": 3,
        "user": "model",
        "source_sha256": {"source.py": "frozen"},
        "task_ids": [t.id for t in tasks],
        "rubric_version": "v3",
        "trials_per_task": 5,
        "models": {"agent": "test"},
        "prompt_bundle_sha256": "bundle_hash",
        "inference_profile": {
            "thinking": "disabled",
            "agent_temperature": 0.0,
            "user_temperature": 0.8,
            "judge_temperature": 0.0,
            "timeout_seconds": 12,
            "max_output_tokens": 4096,
            "review_model": "test",
            "understanding_hedge": {"delay_seconds": 4, "max_attempts": 2, "requires_idle_worker": True},
        },
        "trials": rows,
    }
    report_path.write_text(json.dumps(report_data, ensure_ascii=False), encoding="utf-8")
    groups = [{"role": "regression", "cases": str(cases_file), "report": str(report_path)}]
    return groups


def test_clean_pass_trial_accepted_by_release_gate(tmp_path):
    judge_output = {
        "checks": [{"id": "phase_1", "passed": True, "reason": "已完整回答"}],
        "simulator_faithful": True,
        "naturalness": 4,
    }
    judge = make_judge(judge_output)
    env = MockEnv()
    dataset, _ = load_dataset(DATASET_PATH)
    task = dataset.tasks[0].model_copy(update={"phases": dataset.tasks[0].phases[:1]})
    user = SimpleNamespace(next=lambda view, utterance: {"text": "查规则"}, transport_failures=[])
    trial = run_trial(task, env, user, judge, seed=1, trial=1, rubric_version="v3")
    trial["model_calls_completed"] = True
    trial["judge_schema_diagnostics"] = judge.schema_failures
    trial["evaluation_transport"] = {"user": [], "judge": []}

    assert trial["status"] == "PASS"
    assert trial["judge_schema_diagnostics"] == []

    groups = build_minimal_release_manifest(tmp_path, trial)
    result = assess_release(groups, {"source.py": "frozen"})
    assert not any("INVALID_JUDGE_SCHEMA_DIAGNOSTICS" in b for b in result["blockers"])


def test_extra_forbidden_stripped_pass_trial_accepted_by_release_gate(tmp_path):
    """When judge output has extra unknown keys, they are stripped and audited.

    The trial passes assertion checks, and release_gate accepts the degraded trial
    without raising INVALID_JUDGE_SCHEMA_DIAGNOSTICS.
    """
    judge_output = {
        "checks": [{
            "id": "phase_1",
            "passed": True,
            "reason": "已完整回答",
            "extra_annotation": "未知标注",
            "unwanted_score": 100,
        }],
        "simulator_faithful": True,
        "naturalness": 4,
        "extra_root_field": "额外根字段",
    }
    judge = make_judge(judge_output)
    env = MockEnv()
    dataset, _ = load_dataset(DATASET_PATH)
    task = dataset.tasks[0].model_copy(update={"phases": dataset.tasks[0].phases[:1]})
    user = SimpleNamespace(next=lambda view, utterance: {"text": "查规则"}, transport_failures=[])
    trial = run_trial(task, env, user, judge, seed=1, trial=1, rubric_version="v3")
    trial["model_calls_completed"] = True
    trial["judge_schema_diagnostics"] = judge.schema_failures
    trial["evaluation_transport"] = {"user": [], "judge": []}

    assert trial["status"] == "PASS"
    assert len(trial["judge_schema_diagnostics"]) == 1
    assert all(err["type"] == "extra_forbidden" for err in trial["judge_schema_diagnostics"][0]["errors"])

    groups = build_minimal_release_manifest(tmp_path, trial)
    result = assess_release(groups, {"source.py": "frozen"})
    assert not any("INVALID_JUDGE_SCHEMA_DIAGNOSTICS" in b for b in result["blockers"])


def test_missing_required_field_in_judge_fails_and_blocks_release_gate(tmp_path):
    """When judge output is missing a required field (e.g. passed), judge raises ValidationError.

    Trial becomes ERROR and release gate blocks.
    """
    judge_output = {
        "checks": [{"id": "phase_1", "reason": "缺少 passed 字段"}],
        "simulator_faithful": True,
        "naturalness": 4,
    }
    judge = make_judge(judge_output)
    env = MockEnv()
    dataset, _ = load_dataset(DATASET_PATH)
    task = dataset.tasks[0].model_copy(update={"phases": dataset.tasks[0].phases[:1]})
    user = SimpleNamespace(next=lambda view, utterance: {"text": "查规则"}, transport_failures=[])

    trial = run_trial(task, env, user, judge, seed=1, trial=1, rubric_version="v3")
    assert trial["status"] == "ERROR"
    assert trial["error_type"] == "ValidationError"
    assert len(judge.schema_failures) == 1
    assert any(err["type"] == "missing" for err in judge.schema_failures[0]["errors"])


def test_extra_field_with_type_error_in_diagnostics_blocks_claimed_pass(tmp_path):
    """If a report claims PASS but carries non-extra schema diagnostics (like type errors),

    release_gate must reject with INVALID_JUDGE_SCHEMA_DIAGNOSTICS.
    """
    trial = {
        "status": "PASS",
        "model_calls_completed": True,
        "judge_schema_diagnostics": [{
            "request": 1,
            "stage": "PHASE_JUDGEMENT",
            "phase": 1,
            "errors": [{"path": ["checks", 0, "passed"], "type": "bool_type"}],
            "response_sha256": "0" * 64,
            "retried": False,
        }],
        "evaluation_transport": {"user": [], "judge": []},
        "checks": [{"id": "no_business_write", "passed": True}],
        "communication": {"simulator_faithful": True, "passed": True, "checks": [{"id": "phase_1", "passed": True}]},
    }
    groups = build_minimal_release_manifest(tmp_path, trial)
    result = assess_release(groups, {"source.py": "frozen"})
    assert any("INVALID_JUDGE_SCHEMA_DIAGNOSTICS" in b for b in result["blockers"])
