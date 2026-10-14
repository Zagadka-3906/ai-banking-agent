"""Exercise the shipped health probe against Core's default session boundary."""

import sys

import pytest
import requests
from fastapi.testclient import TestClient

import main
import security
from scripts import smoke_test


@pytest.fixture()
def probe(monkeypatch):
    monkeypatch.setenv("BANK_REQUIRE_SESSION", "1")
    monkeypatch.setenv("BANK_DEMO_USER_ID", "user_li")
    monkeypatch.setenv("BANK_DEMO_PASSWORD", "Bank@2026")
    monkeypatch.setenv("BANK_LOGIN_REQUIRE_OTP", "0")
    monkeypatch.delenv("BANK_LOGIN_OTP", raising=False)
    monkeypatch.setattr(sys, "argv", ["smoke_test.py", "http://testserver/"])
    main.reset_state()
    calls = []
    with TestClient(main.app) as client:
        def request(method, url, **kwargs):
            kwargs.pop("timeout", None)
            response = client.request(method, url, **kwargs)
            calls.append((method, url, response.status_code))
            result = requests.Response()
            result.status_code = response.status_code
            result._content = response.content
            result.url = url
            return result

        monkeypatch.setattr(smoke_test.requests, "get", lambda url, **kw: request("GET", url, **kw))
        monkeypatch.setattr(smoke_test.requests, "post", lambda url, **kw: request("POST", url, **kw))
        yield calls
    main.reset_state()


def test_probe_authenticates_configured_account_and_revokes_session(probe):
    assert smoke_test.main() == 0
    assert ("GET", "http://testserver/api/v1/accounts/user_li/balance", 200) in probe
    assert probe[-1] == ("POST", "http://testserver/api/v1/auth/logout", 200)
    assert not security.SESSION_TOKENS


def test_probe_supports_login_otp(probe, monkeypatch):
    monkeypatch.setenv("BANK_LOGIN_REQUIRE_OTP", "1")
    monkeypatch.setattr(security, "MFA_TEST_OTP", "123456")
    monkeypatch.setenv("BANK_LOGIN_OTP", "123456")
    assert smoke_test.main() == 0
    assert not security.SESSION_TOKENS


def test_probe_fails_when_login_is_rejected(probe, monkeypatch, capsys):
    monkeypatch.setenv("BANK_DEMO_PASSWORD", "wrong-password")
    assert smoke_test.main() == 1
    assert len(probe) == 2
    assert "smoke test failed" in capsys.readouterr().out


def test_probe_revokes_session_when_balance_validation_fails(probe, capsys):
    main.ACCOUNTS["user_li"]["balance"] = -1
    assert smoke_test.main() == 1
    assert probe[-1] == ("POST", "http://testserver/api/v1/auth/logout", 200)
    assert not security.SESSION_TOKENS
    assert "smoke test failed" in capsys.readouterr().out
