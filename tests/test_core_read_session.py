from fastapi.testclient import TestClient

import main
from support import login_session


def test_private_core_reads_require_session_and_bind_the_subject(monkeypatch):
    monkeypatch.setenv("BANK_REQUIRE_SESSION", "1")
    main.reset_state()
    with TestClient(main.app) as client:
        private_paths = (
            "/api/v1/accounts/user_zhang/balance",
            "/api/v1/accounts/user_zhang/transactions",
            "/api/v1/subscriptions/user_zhang",
            "/api/v1/cards/user_zhang",
            "/api/v1/splits/inbox",
        )
        for path in private_paths:
            refused = client.get(path, headers={"X-User-Id": "user_zhang"})
            assert refused.status_code == 401, (path, refused.text)
            assert refused.json()["detail"]["code"] == "SESSION_REQUIRED"
        assert client.get("/healthz").status_code == 200
        token = login_session(client)
        assert client.get(private_paths[0], headers={"X-Session-Token": token}).status_code == 200
        other = client.get("/api/v1/accounts/user_li/balance", headers={"X-Session-Token": token, "X-User-Id": "user_li"})
        assert other.status_code == 403
    main.reset_state()


def test_session_gate_defaults_to_enabled_without_env_setting(monkeypatch):
    monkeypatch.delenv("BANK_REQUIRE_SESSION", raising=False)
    with TestClient(main.app) as client:
        response = client.get("/api/v1/accounts/user_zhang/balance", headers={"X-User-Id": "user_zhang"})
    assert response.status_code == 401
