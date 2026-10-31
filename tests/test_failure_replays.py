"""Tests for failure replay and regression facilities."""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from scripts.evaluate_dialogue_tasks import isolated_settings
from scripts.replay_dialogue_failures import FAILURE_CASES, run_replay


@pytest.fixture
def isolated_gateway(tmp_path, monkeypatch):
    settings = isolated_settings(tmp_path)
    for k, v in settings.items():
        monkeypatch.setenv(k, v)

    import agent_api
    import main as bank_main
    import conversation

    bank_main.reset_state()
    conversation.SERVICE.reset()

    core = TestClient(bank_main.app)
    login = core.post("/api/v1/auth/login", json={"user_id": "user_zhang", "password": "Bank@2026"})
    token = login.json()["session_token"]

    gateway = TestClient(agent_api.app)
    gateway.headers["X-Session-Token"] = token
    return core, gateway


def test_failure_cases_registry_has_required_structure():
    assert len(FAILURE_CASES) >= 4
    for name, case in FAILURE_CASES.items():
        assert "description" in case
        assert "turns" in case
        assert len(case["turns"]) >= 1
        for turn in case["turns"]:
            assert "user" in turn
            assert "expected" in turn


def test_replay_runner_verifies_expectations_correctly(isolated_gateway):
    core, gateway = isolated_gateway

    mock_case = {
        "description": "mock verification",
        "turns": [
            {
                "user": "你好",
                "expected": {
                    "must_contain": ["您好"],
                    "must_not_contain": ["转账成功"],
                    "must_not_create_approval": True,
                },
            }
        ],
    }

    def fake_post(url, **kwargs):
        class FakeResponse:
            status_code = 200
            def json(self):
                return {"reply": "您好！我是您的智能银行助手。", "intent": "GREETING", "approval": None}
        return FakeResponse()

    with patch.object(gateway, "post", fake_post):
        result = run_replay("mock_test", mock_case, core, gateway)
        assert result["status"] == "PASS"
        assert len(result["turns"]) == 1
        assert result["turns"][0]["status"] == "PASS"


def test_replay_runner_flags_violations(isolated_gateway):
    core, gateway = isolated_gateway

    mock_case = {
        "description": "mock violation",
        "turns": [
            {
                "user": "查余额",
                "expected": {
                    "must_contain": ["可用余额"],
                    "must_not_contain": ["越权"],
                    "must_not_create_approval": True,
                },
            }
        ],
    }

    def fake_post(url, **kwargs):
        class FakeResponse:
            status_code = 200
            def json(self):
                return {"reply": "检测到越权行为", "intent": "UNKNOWN", "approval": {"id": "bad"}}
        return FakeResponse()

    with patch.object(gateway, "post", fake_post):
        result = run_replay("mock_violation", mock_case, core, gateway)
        assert result["status"] == "FAIL"
        failures = result["turns"][0]["failures"]
        assert any("缺少预期内容" in f for f in failures)
        assert any("包含禁止内容" in f for f in failures)
        assert any("非预期创建了审批提案" in f for f in failures)
