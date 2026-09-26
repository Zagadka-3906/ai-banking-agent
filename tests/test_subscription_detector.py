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
        found = client.get("/api/v1/subscriptions/user_zhang/detect", headers={"X-User-Id": "user_zhang"})
        assert found.status_code == 200
        candidate = next(row for row in found.json()["data"] if row["merchant"] == "新视频服务")
        assert candidate["status"] == "CANDIDATE"
        assert all(row["subscription_id"] != candidate["subscription_id"] for row in main.SUBSCRIPTIONS["user_zhang"])
    main.reset_state()
