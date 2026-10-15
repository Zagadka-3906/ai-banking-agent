import pytest
from scripts.evaluate_usability import evaluate


def trial(mode, outcome="PASS"):
    return dict(participant="test-fixture", task="BALANCE", mode=mode, source="human_observed", consent=True,
                seconds=10 if mode == "agent" else 15, interactions=2, outcome=outcome, baseline="test-only")


def test_only_completed_pairs_can_produce_improvement_metrics():
    assert evaluate([trial("agent")])["median_saved_seconds"] is None
    assert evaluate([trial("agent"), trial("baseline", "UNAVAILABLE")])["successful_pairs"] == 0
    assert evaluate([trial("agent"), trial("baseline")])["median_saved_seconds"] == 5
    assert evaluate([])["participants"] == 0


def test_invalid_or_duplicate_records_are_rejected():
    with pytest.raises(ValueError):
        evaluate([trial("agent"), trial("agent")])
    with pytest.raises(ValueError):
        evaluate([{**trial("agent"), "seconds": float("nan")}])
