import requests

BASE_URL = "http://127.0.0.1:8000/api/v1"

class BankClient:
    @staticmethod
    def get_balance(user_id: str):
        return requests.get(f"{BASE_URL}/accounts/{user_id}/balance").json()

    @staticmethod
    def get_transactions(user_id: str):
        return requests.get(f"{BASE_URL}/accounts/{user_id}/transactions").json().get("data", [])

    @staticmethod
    def precheck_risk(from_user: str, target: str, amount: float):
        payload = {"from_user": from_user, "target_name_or_id": target, "amount": amount}
        return requests.post(f"{BASE_URL}/risk/precheck", json=payload)

    @staticmethod
    def execute_transfer(from_user: str, target: str, amount: float, auth_token: str):
        headers = {"x-auth-token": auth_token}
        payload = {"from_user": from_user, "target_name_or_id": target, "amount": amount}
        return requests.post(f"{BASE_URL}/transfers/execute", headers=headers, json=payload)

    @staticmethod
    def lock_fund(user_id: str, amount: float, reason: str):
        payload = {"user_id": user_id, "amount": amount, "reason": reason}
        return requests.post(f"{BASE_URL}/accounts/lock_fund", json=payload)