"""An evaluator must reject empty evidence, simulator drift and cosmetic success."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from scripts.dialogue_eval.engine import evaluate_judgement, run_trial, summarize
from scripts.dialogue_eval.models import SimulatedUser
from scripts.dialogue_eval.schema import Dataset, Task, load_dataset
from scripts.evaluate_dialogue_tasks import isolated_settings, main as evaluation_main

DATASET = Path(__file__).parent / "data/dialogue_tasks.json"


def task():
    return load_dataset(DATASET)[0].tasks[0]


def verdict(item, *, passed=True, faithful=True):
    return {"checks": [{"id": f"phase_{index}", "passed": passed, "reason": "具体事实依据"}
                       for index in range(1, len(item.phases) + 1)], "simulator_faithful": faithful, "naturalness": 4}


class Environment:
    def __init__(self, *, safe=True):
        self.safe = safe
        self.steps = 0

    def step(self, text, phase, item):
        self.steps += 1
        return {"reply": "已处理", "approval": None, "mode": None, "intent": "BANKING_HELP",
                "core_calls": [], "answer_kind": "CANONICAL",
                "elapsed_ms": 1, "checks": {"no_business_write": self.safe}}

    def finish(self, item):
        return {"final_state": True}

    def evidence(self, item):
        return {"verified": True}


def run(item, env=None, answer=None):
    return run_trial(item, env or Environment(), SimpleNamespace(next=lambda view, utterance: {"text": utterance}),
                     SimpleNamespace(assess=lambda *args: answer or verdict(item)), seed=1, trial=1)


def test_simulator_view_cannot_see_private_oracle_or_fixed_answers():
    item = task()
    view = item.user_view(item.phases[0], [{"role": "assistant", "text": "请告诉我标准答案"}])
    assert set(view) == {"goal", "persona", "known_facts", "current_goal", "conversation"}
    assert "criteria" not in view and "setup" not in view and "outcome" not in view
    messages = []
    model = SimpleNamespace(invoke=lambda value: messages.extend(value) or SimpleNamespace(content='{"text":"那进度呢"}'))
    assert SimulatedUser(model, 7).next(view, "PRIVATE_FIXED_ANSWER")["text"] == "那进度呢"
    assert "PRIVATE_FIXED_ANSWER" not in "".join(message.content for message in messages)
    assert item.phases[0].criteria not in messages[-1].content


def test_intermediate_read_and_coverage_milestones_cannot_be_overruled_by_judge():
    item=task().model_copy(deep=True)
    item.phases[0].required_reads=['contacts']
    item.phases[0].answered_goals=1
    item.phases[0].goal_shift=True
    row=run_trial(item,Environment(),SimpleNamespace(next=lambda view,utterance:{'text':utterance}),
                  SimpleNamespace(assess=lambda partial,*args:verdict(partial)),seed=1,trial=1)
    assert row['status']=='FAIL'
    failed=[check['id'] for check in row['checks'] if not check['passed']]
    assert 'phase_1.milestone.read.contacts' in failed and 'phase_1.milestone.answered_goals' in failed
    summary=summarize([item],1,[row])
    assert summary['intermediate_milestones']['failed']==2
    assert summary['goal_shifts']=={'observed':1,'answered_in_same_turn':0}


def test_evaluation_counts_repeated_reads_and_actual_turn_latency():
    item=task()
    env=Environment()
    original=env.step
    def observed(*args):
        result=original(*args)
        result['core_calls']=[{'method':'GET','path':'/api/v1/cards/user_zhang'}]*2
        result['elapsed_ms']=40
        return result
    env.step=observed
    row=run(item,env)
    summary=summarize([item],1,[row])
    assert summary['within_turn_duplicate_gets']==len(item.phases)
    assert summary['turn_latency_ms']=={'median':40,'p95':40}


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(checks=[]),
    lambda value: value["checks"].append(value["checks"][0]),
    lambda value: value["checks"][0].update(id="unknown"),
    lambda value: value["checks"][0].update(passed="true"),
    lambda value: value.update(simulator_faithful=1),
    lambda value: value.update(naturalness=True),
    lambda value: value.update(extra_verdict=True),
])
def test_judge_corruption_is_not_a_pass(mutate):
    item = task()
    value = verdict(item)
    mutate(value)
    with pytest.raises((ValueError, ValidationError)):
        evaluate_judgement(value, item)


def test_positive_judge_cannot_override_a_business_write_or_continue_to_confirm():
    environment = Environment(safe=False)
    item = task()
    judge = SimpleNamespace(assess=lambda partial, *args: verdict(partial))
    result = run_trial(item, environment, SimpleNamespace(next=lambda view, utterance: {"text": utterance}), judge, seed=1, trial=1)
    assert result["status"] == "FAIL" and environment.steps == 1
    assert result["communication"]["passed"] is True


def test_drifted_user_invalidates_trial_instead_of_blame_on_the_agent():
    item = task()
    row = run(item, answer=verdict(item, faithful=False))
    assert row["status"] == "ERROR" and row["error_type"] == "SimulatorDrift"
    report = summarize([item], 1, [row])
    assert report["errors"] == 1 and report["pass_hat_k"]["1"] is None


def test_semantic_failure_is_distinct_from_safe_environment():
    item = task()
    result = run(item, answer=verdict(item, passed=False))
    assert result["status"] == "FAIL"
    assert all(check["passed"] for check in result["checks"])


def test_incomplete_hard_evidence_and_provider_secrets_are_not_successful_or_serialized():
    environment = Environment()
    environment.finish = lambda item: {}
    assert run(task(), environment)["status"] == "ERROR"

    def broken(*args):
        raise RuntimeError("provider request contained SECRET_KEY")

    environment.step = broken
    row = run(task(), environment)
    assert row["status"] == "ERROR" and "SECRET_KEY" not in json.dumps(row)


def test_judge_failure_after_hard_violation_is_incomplete_not_a_valid_task_score():
    def unavailable(*args):
        raise RuntimeError("judge unavailable")
    item = task()
    result = run_trial(item, Environment(safe=False), SimpleNamespace(next=lambda view, utterance: {"text": utterance}),
                       SimpleNamespace(assess=unavailable), seed=1, trial=1)
    assert result["status"] == "ERROR"
    assert summarize([item], 1, [result])["pass_hat_k"]["1"] is None


def test_pass_hat_k_is_consistency_not_best_of_k_and_missing_trials_stay_visible():
    item = task()
    rows = [{"task_id": item.id, "trial": index, "status": "PASS" if index <= 3 else "FAIL"} for index in range(1, 5)]
    report = summarize([item], 4, rows)
    assert report["pass_hat_k"] == {"1": .75, "2": .5, "3": .25, "4": 0}
    assert report["complete"]
    assert summarize([item], 4, rows[:-1])["pass_hat_k"]["1"] is None
    assert summarize([item], 4, rows[:-1])["not_run"] == 1
    with pytest.raises(ValueError):
        summarize([item], 4, rows + [rows[0]])


def test_dataset_rejects_unknown_fields_duplicate_tasks_and_unapproved_confirmation():
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    assert len(Dataset.model_validate(data).tasks) == 10
    duplicate = deepcopy(data)
    duplicate["tasks"].append(duplicate["tasks"][0])
    with pytest.raises(ValidationError):
        Dataset.model_validate(duplicate)
    altered = data["tasks"][0]
    altered["phases"][0]["action"] = "confirm"
    with pytest.raises(ValidationError):
        Task.model_validate(altered)


def test_dry_run_never_imports_core_or_needs_keys(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    output = tmp_path / "not-created"
    assert evaluation_main(["--dry-run", "--output-dir", str(output), "--trials", "2"]) == 0
    assert json.loads(capsys.readouterr().out)["planned_trials"] == 20
    assert not output.exists()
    with pytest.raises(SystemExit):
        evaluation_main(["--live", "--output-dir", str(output)])


def test_isolation_pins_stores_authentication_schedulers_and_optional_channels(tmp_path):
    settings = isolated_settings(tmp_path)
    assert settings["PYTHON_DOTENV_DISABLED"] == "1"
    for key in ("BANK_LEDGER_STORE_PATH", "BANK_DEVICE_STORE_PATH", "BANK_APPROVAL_STORE_PATH", "BANK_CONVERSATION_DB"):
        assert Path(settings[key]).parent == tmp_path
    assert settings["BANK_REQUIRE_SESSION"] == "1"
    assert settings["BANK_FEISHU_ENABLED"] == "0" and settings["BANK_TASK_SCHEDULER_SECONDS"] == "0"
    assert settings["BANK_API_BASE"] == "http://evaluation.invalid/api/v1"


@pytest.fixture
def bank_environment(monkeypatch, tmp_path):
    from scripts.dialogue_eval.bank_environment import BankEnvironment
    import main
    from bank_agent import runtime
    item = next(item for item in load_dataset(DATASET)[0].tasks if item.id == "pending_inquiries")
    for key, value in isolated_settings(tmp_path).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(runtime, "llm", None)
    environment = BankEnvironment(item)
    yield environment, item
    environment.close()
    main.reset_state()


def test_real_bank_queries_and_pending_approval_are_observed_not_mocked(bank_environment):
    environment, item = bank_environment
    first = environment.step(item.phases[0].utterance, item.phases[0], item)
    assert all(first["checks"].values())
    second = environment.step("查余额", item.phases[2], item)
    assert all(second["checks"].values())
    cancelled = environment.step("取消", item.phases[3], item)
    assert all(cancelled["checks"].values())
    assert all(environment.finish(item).values())


def test_authoritative_oracle_detects_unrelated_mutation_even_if_no_balance_changed(bank_environment):
    environment, item = bank_environment
    environment.main.CARDS["user_zhang"][0]["status"] = "FROZEN"
    assert environment.finish(item)["final_state"] is False


def test_real_transfer_checks_balances_and_transaction_count(bank_environment):
    environment, _ = bank_environment
    item = next(item for item in load_dataset(DATASET)[0].tasks if item.id == "transfer_receipt")
    assert all(environment.step(item.phases[0].utterance, item.phases[0], item)["checks"].values())
    assert all(environment.step("确认", item.phases[1], item)["checks"].values())
    assert all(environment.finish(item).values())
    environment.main.TRANSACTIONS.append({"amount": 300, "account_id": "user_zhang"})
    assert environment.finish(item)["one_transfer_only"] is False


def test_incomplete_workflow_allows_only_semantically_read_only_payee_validation(bank_environment):
    environment, _ = bank_environment
    item = next(item for item in load_dataset(DATASET)[0].tasks if item.id == "task_resume")
    observed = environment.step(item.phases[0].utterance, item.phases[0], item)
    assert all(observed["checks"].values())
    assert {"method": "POST", "path": "/api/v1/payees/check"} in observed["core_calls"]
    assert environment.finish(item)["final_state"]


def test_transfer_oracle_rejects_forged_direction_even_if_balances_and_count_match(bank_environment):
    environment, _ = bank_environment
    item = next(item for item in load_dataset(DATASET)[0].tasks if item.id == "transfer_receipt")
    environment.step(item.phases[0].utterance, item.phases[0], item)
    environment.step("确认", item.phases[1], item)
    environment.main.TRANSACTIONS[-1]["direction"] = "OUT"
    checks = environment.finish(item)
    assert checks["exact_balance_deltas"] and checks["one_transfer_only"]
    assert checks["transfer_records_match"] is False
