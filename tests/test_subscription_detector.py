from datetime import date, timedelta

from fastapi.testclient import TestClient

import main
from subscription_detector import detect_recurring_debits


def _charge(user: str, merchant: str, day: date, tx_id: str, amount: float = 29.0):
    return {"account_id": user, "merchant": merchant, "amount": amount, "time": day.isoformat() + " 09:00:00", "tx_id": tx_id, "category": "订阅代扣"}


def test_detect_recurring_debits_requires_consistent_verified_history():
    today = date.today()
    rows = [_charge("user_zhang", "新视频服务", today - timedelta(days=60), "A"),
            _charge("user_zhang", "新视频服务", today - timedelta(days=30), "B"),
            _charge("user_zhang", "新视频服务", today, "C"),
            _charge("user_li", "其他账户服务", today - timedelta(days=30), "D"),
            _charge("user_li", "其他账户服务", today, "E"),
            _charge("user_zhang", "单次购物", today, "F")]
    found = detect_recurring_debits(rows, user_id="user_zhang", today=today)
    assert len(found) == 1
    assert found[0]["merchant"] == "新视频服务"
    assert found[0]["status"] == "CANDIDATE"
    assert found[0]["period"] == "MONTHLY"
    assert found[0]["evidence_tx_ids"] == ["A", "B", "C"]


def test_detect_endpoint_does_not_create_cancellable_mandate():
    main.reset_state()
    today = date.today()
    main.TRANSACTIONS.extend([_charge("user_zhang", "新视频服务", today - timedelta(days=30), "NEW1"),
                              _charge("user_zhang", "新视频服务", today, "NEW2")])
    with TestClient(main.app) as client:
        headers = {"X-User-Id": "user_zhang"}
        # Yellow read: no unconfirmed route, and the nonce needs a confirmation.
        assert client.get("/api/v1/subscriptions/user_zhang/detect", headers=headers).status_code in {404, 405}
        body = {"user_id": "user_zhang", "operation": "SUBSCRIPTION_QUERY"}
        pre = client.post("/api/v1/risk/precheck/operation", json=body, headers=headers)
        assert pre.status_code == 200 and pre.json()["risk_level"] == "YELLOW", pre.text
        token = pre.json()["auth_token"]
        assert client.post("/api/v1/operations/execute", json=body, headers={**headers, "X-Auth-Token": token}).status_code == 428
        pre = client.post("/api/v1/risk/precheck/operation", json=body, headers=headers)
        token = pre.json()["auth_token"]
        assert client.post("/api/v1/auth/confirm", json={}, headers={**headers, "X-Auth-Token": token}).status_code == 200
        found = client.post("/api/v1/operations/execute", json=body, headers={**headers, "X-Auth-Token": token})
        assert found.status_code == 200, found.text
        candidate = next(row for row in found.json()["data"]["candidates"] if row["merchant"] == "新视频服务")
        assert candidate["status"] == "CANDIDATE"
        assert all(row["subscription_id"] != candidate["subscription_id"] for row in main.SUBSCRIPTIONS["user_zhang"])
    main.reset_state()
