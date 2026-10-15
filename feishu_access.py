"""Safe tenant-token diagnostics; never return credentials to a caller."""
import os

import requests


def probe_access():
    app_id, secret = os.getenv("FEISHU_APP_ID", ""), os.getenv("FEISHU_APP_SECRET", "")
    if not app_id or not secret:
        return {"ok": False, "code": None, "kind": "not_configured"}
    try:
        response = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": secret}, timeout=8, allow_redirects=False)
        payload = response.json()
        code = payload.get("code") if isinstance(payload, dict) else None
        ok = response.status_code == 200 and type(code) is int and code == 0 and bool(payload.get("tenant_access_token"))
        return {"ok": ok, "http_status": response.status_code, "code": code if isinstance(code, int) else None,
                "kind": "available" if ok else "platform_policy_denied" if code == 21001 else "platform_request_failed"}
    except (requests.RequestException, ValueError, TypeError):
        return {"ok": False, "code": None, "kind": "transport_or_protocol_failure"}
