from scripts.benchmark_intents import percentile, score_case, summarize


def test_benchmark_does_not_count_unrun_or_model_error_as_success():
    rows = [{"status": "PASS", "elapsed_ms": 5, "issues": []},
            {"status": "MODEL_ERROR", "elapsed_ms": 20, "issues": []},
            {"status": "NOT_RUN", "elapsed_ms": 0, "issues": []}]
    assert summarize(rows)["passed"] == 1
    assert summarize(rows)["not_run"] == 1
    assert summarize(rows)["p95_ms"] == 20
    rows[0]["checked_fields"] = ["intent"]
    rows[1]["checked_fields"] = ["intent"]
    assert summarize(rows)["field_metrics"]["intent"]["accuracy"] == .5


def test_benchmark_scores_omitted_and_ambiguous_slots():
    assert score_case({"expect": {"amount": None, "clarify": True}}, {"amount": 300}, False) == [
        {"field": "amount", "expected": None, "actual": 300}, {"field": "clarify", "expected": True, "actual": False}]
    assert percentile([], .95) is None
