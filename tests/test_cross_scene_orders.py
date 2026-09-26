from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import main


def test_birthday_order_needs_fresh_approval_and_spends_only_reserved_budget(monkeypatch):
    monkeypatch.delenv("BANK_ADMIN_AUDIT_TOKEN", raising=False)
    main.reset_state()
    headers = {"X-User-Id": "user_zhang"}
    event_at = (datetime.now(timezone.utc) + timedelta(days=4)).isoformat()
    body = {"user_id": "user_zhang", "amount": 100.0, "reason": "生日预算", "event_at": event_at}
    with TestClient(main.app) as client:
        pre = client.post("/api/v1/risk/precheck/lock", json=body, headers=headers)
        assert pre.status_code == 200, pre.text
        token = pre.json()["auth_token"]
        assert client.post("/api/v1/auth/confirm", json={}, headers={**headers, "X-Auth-Token": token}).status_code == 200
        locked = client.post("/api/v1/accounts/lock_fund", json=body, headers={**headers, "X-Auth-Token": token})
        assert locked.status_code == 200, locked.text
        draft = main.TASK_STORE.get(locked.json()["order_task_ids"][0])
        assert draft is not None
        draft.run_at = "2000-01-01T00:00:00+00:00"
        assert not main.MOCK_ORDERS
        before = main.ACCOUNTS["user_zhang"]["balance"]
        assert client.post("/api/v1/tasks/run-due", headers=headers).status_code == 200
        assert draft.status == "WAITING_USER"
        original_item = draft.payload["item"]
        draft.payload["item"] = "未列入目录的商品"
        refused = client.post(f"/api/v1/tasks/{draft.task_id}/precheck", json={"user_id": "user_zhang"}, headers=headers)
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "ORDER_QUOTE_CHANGED"
        draft.payload["item"] = original_item
        task_pre = client.post(f"/api/v1/tasks/{draft.task_id}/precheck", json={"user_id": "user_zhang"}, headers=headers)
        assert task_pre.status_code == 200, task_pre.text
        assert task_pre.json()["amount"] == 49.0
        task_token = task_pre.json()["auth_token"]
        assert client.post("/api/v1/auth/confirm", json={}, headers={**headers, "X-Auth-Token": task_token}).status_code == 200
        placed = client.post(f"/api/v1/tasks/{draft.task_id}/execute", json={"user_id": "user_zhang", "auth_token": task_token}, headers={**headers, "Idempotency-Key": "birthday-flower-1"})
        assert placed.status_code == 200, placed.text
        result = placed.json()
        assert result["order_status"] == "PLACED" and result["funds_debited"] is True
        assert main.ACCOUNTS["user_zhang"]["balance"] == before - 49.0
        assert main.ACCOUNTS["user_zhang"]["locked_amount"] == 51.0
        receipt = client.get(f"/api/v1/mock-merchants/orders/{result['mock_order_id']}", headers=headers)
        assert receipt.status_code == 200
        assert receipt.json()["data"]["status"] == "PLACED"
        replay = client.post(f"/api/v1/tasks/{draft.task_id}/execute", json={"user_id": "user_zhang", "auth_token": task_token}, headers={**headers, "Idempotency-Key": "birthday-flower-1"})
        assert replay.json() == result
        assert len(main.MOCK_ORDERS) == 1
    main.reset_state()
