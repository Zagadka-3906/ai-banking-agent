from types import SimpleNamespace

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
