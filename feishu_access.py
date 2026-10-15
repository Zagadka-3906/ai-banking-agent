"""Safe tenant-token diagnostics; never return credentials to a caller."""
import os
import re

import requests


def _correlation_ids(response):
    """Only known platform ID formats may leave the response boundary."""
    headers = {str(key).lower(): value for key, value in getattr(response, "headers", {}).items()}
    result = {}
    for header, field, pattern in (
        ("x-tt-logid", "log_id", r"[0-9]{14}[A-Fa-f0-9]{16,50}"),
        ("x-request-id", "request_id", r"[A-Fa-f0-9]{8}(?:-[A-Fa-f0-9]{4}){3}-[A-Fa-f0-9]{12}"),
    ):
        value = headers.get(header, "")
        if isinstance(value, str) and re.fullmatch(pattern, value):
            result[field] = value
    return result


def probe_access(*, direct=False):
    app_id, secret = os.getenv("FEISHU_APP_ID", "").strip(), os.getenv("FEISHU_APP_SECRET", "").strip()
    if not app_id or not secret:
        return {"ok": False, "code": None, "kind": "not_configured"}
    try:
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        options = {"json": {"app_id": app_id, "app_secret": secret}, "timeout": 8, "allow_redirects": False}
        if direct:
            # An explicit diagnostic comparison, not a persistent proxy change.
            with requests.Session() as session:
                session.trust_env = False
                response = session.post(url, **options)
        else:
            response = requests.post(url, **options)
        payload = response.json()
        code = payload.get("code") if isinstance(payload, dict) else None
        ok = response.status_code == 200 and type(code) is int and code == 0 and bool(payload.get("tenant_access_token"))
        return {"ok": ok, "http_status": response.status_code, "code": code if type(code) is int else None,
                "kind": "available" if ok else "platform_policy_denied" if code == 21001 else "platform_request_failed",
                **_correlation_ids(response)}
    except (requests.RequestException, ValueError, TypeError):
        return {"ok": False, "code": None, "kind": "transport_or_protocol_failure"}
