from types import SimpleNamespace

import pytest

import feishu_access


def test_policy_failure_probe_never_exposes_response_secrets(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "test-app")
    monkeypatch.setenv("FEISHU_APP_SECRET", "private-secret")
    def post(url, **kwargs):
        assert url.startswith("https://open.feishu.cn/")
        assert kwargs["allow_redirects"] is False
        return SimpleNamespace(status_code=200, json=lambda: {"code": 21001, "msg": "private-secret"})
    monkeypatch.setattr(feishu_access.requests, "post", post)
    result = feishu_access.probe_access()
    assert result["kind"] == "platform_policy_denied" and result["ok"] is False
    assert "private-secret" not in repr(result)


def test_successful_probe_does_not_return_the_token(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "test-app")
    monkeypatch.setenv("FEISHU_APP_SECRET", "test-secret")
    monkeypatch.setattr(feishu_access.requests, "post", lambda *a, **kw: SimpleNamespace(status_code=200,
        json=lambda: {"code": 0, "tenant_access_token": "private-token"}))
    result = feishu_access.probe_access()
    assert result["ok"] is True and "private-token" not in repr(result)


def test_probe_retains_only_valid_platform_correlation_ids(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "test-app")
    monkeypatch.setenv("FEISHU_APP_SECRET", "test-secret")
    request_id = "11111111-2222-3333-4444-555555555555"
    log_id = "20260101000000ABCDEF0123456789AB"
    monkeypatch.setattr(feishu_access.requests, "post", lambda *a, **kw: SimpleNamespace(status_code=200,
        headers={"X-Tt-Logid": log_id, "X-Request-Id": request_id, "Authorization": "private-token"},
        json=lambda: {"code": 21001, "msg": "private-secret", "error": {"token": "private-token"}}))
    result = feishu_access.probe_access()
    assert result["request_id"] == request_id and result["log_id"] == log_id
    assert "private" not in repr(result)


def test_probe_discards_arbitrary_header_values(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "test-app")
    monkeypatch.setenv("FEISHU_APP_SECRET", "test-secret")
    monkeypatch.setattr(feishu_access.requests, "post", lambda *a, **kw: SimpleNamespace(status_code=200,
        headers={"x-tt-logid": "private-secret", "x-request-id": "https://private-token"}, json=lambda: {"code": 21001}))
    result = feishu_access.probe_access()
    assert "log_id" not in result and "request_id" not in result
    assert "private" not in repr(result)


def test_direct_comparison_disables_only_its_session_environment(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", " test-app ")
    monkeypatch.setenv("FEISHU_APP_SECRET", " test-secret ")
    monkeypatch.setenv("HTTPS_PROXY", "http://unchanged-proxy")
    class Session:
        trust_env = True
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, **kwargs):
            assert self.trust_env is False
            assert kwargs["json"] == {"app_id": "test-app", "app_secret": "test-secret"}
            assert kwargs["allow_redirects"] is False
            return SimpleNamespace(status_code=200, json=lambda: {"code": 21001})
    monkeypatch.setattr(feishu_access.requests, "Session", Session)
    assert feishu_access.probe_access(direct=True)["kind"] == "platform_policy_denied"
    assert feishu_access.os.environ["HTTPS_PROXY"] == "http://unchanged-proxy"


@pytest.mark.parametrize("code", [False, True, "0", None])
def test_non_integer_platform_codes_never_count_as_success(monkeypatch, code):
    monkeypatch.setenv("FEISHU_APP_ID", "test-app")
    monkeypatch.setenv("FEISHU_APP_SECRET", "test-secret")
    monkeypatch.setattr(feishu_access.requests, "post", lambda *a, **kw: SimpleNamespace(status_code=200,
        json=lambda: {"code": code, "tenant_access_token": "private-token"}))
    result = feishu_access.probe_access()
    assert result["ok"] is False and result["code"] is None


@pytest.mark.parametrize("message, expected", [
    ("Access denied due to platform policy. If you have questions, contact customer support for help.", "contact_platform_support"),
    ("Access denied by access control policy. Contact the administrator for assistance.", "contact_administrator"),
    ("private-secret contact customer support", None),
])
def test_policy_hint_uses_only_exact_known_messages(monkeypatch, message, expected):
    monkeypatch.setenv("FEISHU_APP_ID", "test-app")
    monkeypatch.setenv("FEISHU_APP_SECRET", "test-secret")
    monkeypatch.setattr(feishu_access.requests, "post", lambda *a, **kw: SimpleNamespace(status_code=200,
        json=lambda: {"code": 21001, "msg": message}))
    result = feishu_access.probe_access()
    assert result.get("policy_hint") == expected
    assert message not in repr(result)
