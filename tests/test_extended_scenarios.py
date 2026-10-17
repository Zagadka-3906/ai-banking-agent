from __future__ import annotations

from datetime import datetime, timedelta

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import main
from bank_core import cards
from bank_core.routes import cards as card_routes
import security


@pytest.fixture()
def client():
    main.reset_state()
    with TestClient(main.app) as value:
        yield value
    main.reset_state()


def headers(token: str | None = None):
    result = {"X-User-Id": "user_zhang"}
    if token:
        result["X-Auth-Token"] = token
    return result


def approve(client: TestClient, operation: str, **kwargs):
    body = {"user_id": "user_zhang", "operation": operation, **kwargs}
    pre = client.post("/api/v1/risk/precheck/operation", json=body, headers=headers())
    assert pre.status_code == 200, pre.text
    token = pre.json()["auth_token"]
    ok = client.post("/api/v1/auth/confirm", json={}, headers=headers(token))
    assert ok.status_code == 200, ok.text
    return token


def pay_split(client: TestClient, split_id: str, payer: str, amount: float):
    payer_headers = {"X-User-Id": payer}
    body = {"from_user": payer, "target_name_or_id": "user_zhang", "amount": amount, "remark": f"AA:{split_id}"}
    pre = client.post("/api/v1/risk/precheck", json=body, headers=payer_headers)
    assert pre.status_code == 200, pre.text
    token = pre.json()["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers={**payer_headers, "X-Auth-Token": token}).status_code == 200
    return client.post("/api/v1/transfers/execute", json=body, headers={**payer_headers, "X-Auth-Token": token})


def test_aa_payment_note_is_parsed_as_transfer_not_new_request():
    from agent import local_parse

    parsed = local_parse("给张三转 33.33 元，备注 AA:SPLABC123")
    assert parsed["intent"] == "TRANSFER"
    assert parsed["target"] == "张三"
    assert parsed["amount"] == 33.33
    assert "AA:SPLABC123" in parsed["extra_info"]


def face_attest(client: TestClient, token: str) -> str:
    """Run the challenge → simulated-device-sign flow for red-zone MFA."""

    from support import sign_face_attestation

    challenge = client.post("/api/v1/auth/mfa/face/challenge", json={"auth_token": token, "user_id": "user_zhang"}, headers=headers(token))
    assert challenge.status_code == 200, challenge.text
    return sign_face_attestation(challenge.json()["challenge_id"], user_id="user_zhang", token_jti=challenge.json()["auth_token_jti"])


def approve_red(client: TestClient, operation: str, **kwargs):
    token = approve(client, operation, **kwargs)
    mfa = client.post("/api/v1/auth/mfa/verify", json={"otp": "888888", "face_attestation": face_attest(client, token)}, headers=headers(token))
    assert mfa.status_code == 200, mfa.text
    return token, mfa.json()["mfa_proof"]


def test_last_month_query_is_filtered(client: TestClient):
    """Boundary rows are injected relative to today so the test never expires."""

    now = datetime.now()
    last_month_day = (now.replace(day=1) - timedelta(days=1)).replace(day=15, hour=10, minute=0, second=0, microsecond=0)
    this_month_day = now.replace(hour=10, minute=0, second=0, microsecond=0)
    main.TRANSACTIONS.append({"tx_id": "TX-LAST-MONTH", "account_id": "user_zhang", "merchant": "上月商户", "category": "餐饮", "amount": 12.0, "time": last_month_day.strftime("%Y-%m-%d %H:%M:%S")})
    main.TRANSACTIONS.append({"tx_id": "TX-THIS-MONTH", "account_id": "user_zhang", "merchant": "本月商户", "category": "餐饮", "amount": 13.0, "time": this_month_day.strftime("%Y-%m-%d %H:%M:%S")})

    response = client.get("/api/v1/accounts/user_zhang/transactions?date_scope=LAST_MONTH", headers=headers())
    assert response.status_code == 200
    rows = response.json()["data"]
    assert rows
    ids = {row["tx_id"] for row in rows}
    assert "TX-LAST-MONTH" in ids
    assert "TX-THIS-MONTH" not in ids
    assert all(row["account_id"] == "user_zhang" for row in rows)


def test_subscription_cancel_and_card_apply_use_approval_gate(client: TestClient):
    token = approve(client, "SUBSCRIPTION_CANCEL", target="Apple")
    cancelled = client.post(
        "/api/v1/operations/execute",
        json={"user_id": "user_zhang", "operation": "SUBSCRIPTION_CANCEL", "target": "Apple", "merchant": "Apple", "auth_token": token},
        headers=headers(token),
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"]["subscription"]["status"] == "CANCELLED"

    token = approve(client, "CARD_APPLY")
    issued = client.post(
        "/api/v1/cards/apply",
        json={"user_id": "user_zhang", "auth_token": token},
        headers=headers(token),
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["data"]["card"]["type"] == "VIRTUAL"


def test_wealth_purchase_requires_server_mfa(client: TestClient):
    # 账户初始未测评：先完成风险测评（演示用旧三要素路径），才能申购。
    assessed = client.post(
        "/api/v1/wealth/risk-assessment",
        json={"user_id": "user_zhang", "horizon_years": 3, "loss_tolerance": "MEDIUM", "experience": "BASIC"},
        headers=headers(),
    )
    assert assessed.status_code == 200, assessed.text
    pre = client.post(
        "/api/v1/risk/precheck/operation",
        json={"user_id": "user_zhang", "operation": "WEALTH_PURCHASE", "product_id": "LX00118", "amount": 1000, "reason": "长期储蓄"},
        headers=headers(),
    )
    assert pre.status_code == 200 and pre.json()["risk_level"] == "RED"
    token = pre.json()["auth_token"]
    client.post("/api/v1/auth/confirm", json={}, headers=headers(token))
    blocked = client.post("/api/v1/operations/execute", json={"user_id": "user_zhang", "operation": "WEALTH_PURCHASE", "product_id": "LX00118", "amount": 1000, "reason": "长期储蓄", "auth_token": token}, headers=headers(token))
    assert blocked.status_code in {403, 428}
    # The Android/IM contract has no client-asserted biometric boolean.
    boolean_only = client.post("/api/v1/auth/mfa/verify", json={"otp": "888888", "face_verified": True}, headers=headers(token))
    assert boolean_only.status_code == 403
    mfa = client.post("/api/v1/auth/mfa/verify", json={"otp": "888888", "face_attestation": face_attest(client, token)}, headers=headers(token))
    assert mfa.status_code == 200
    completed = client.post("/api/v1/operations/execute", json={"user_id": "user_zhang", "operation": "WEALTH_PURCHASE", "product_id": "LX00118", "amount": 1000, "reason": "长期储蓄", "auth_token": token, "mfa_proof": mfa.json()["mfa_proof"]}, headers={**headers(token), "X-MFA-Proof": mfa.json()["mfa_proof"]})
    assert completed.status_code == 200, completed.text


def test_risk_assessment_is_available_to_agent_and_compare_is_source_bound(client: TestClient):
    assessment = client.post(
        "/api/v1/wealth/risk-assessment",
        json={"user_id": "user_zhang", "horizon_years": 3, "loss_tolerance": "MEDIUM", "experience": "BASIC"},
        headers=headers(),
    )
    assert assessment.status_code == 200, assessment.text
    assert assessment.json()["risk_profile"] == "BALANCED"
    assert main.WEALTH_RISK_PROFILES["user_zhang"]["risk_profile"] == "BALANCED"

    comparison = client.get(
        "/api/v1/wealth/compare?product_ids=LX00118,HQ30060",
        headers=headers(),
    )
    assert comparison.status_code == 200, comparison.text
    assert [item["product_id"] for item in comparison.json()["data"]] == ["LX00118", "HQ30060"]


def test_purchase_requires_a_recorded_risk_assessment(client: TestClient):
    main.WEALTH_RISK_PROFILES.pop("user_zhang", None)
    blocked = client.post(
        "/api/v1/risk/precheck/operation",
        json={"user_id": "user_zhang", "operation": "WEALTH_PURCHASE", "product_id": "LX00118", "amount": 100},
        headers=headers(),
    )
    assert blocked.status_code == 428
    assert blocked.json()["detail"]["code"] == "RISK_ASSESSMENT_REQUIRED"


def test_reading_the_risk_profile_never_echoes_the_score_or_the_answers(client: TestClient):
    """The questionnaire grades server-side; the scoring key must not travel.

    ``score`` plus the answer sheet is exactly the mapping the suitability gate
    is built on.  A client holding both can compute the cheapest answer set for
    any grade and grant itself whatever products it likes, so the read path has
    to withhold them the same way the submit path does.
    """

    answers = {"1": "A", "2": "D", "3": "D", "4": "E", "5": "D", "6": "E", "7": "E", "8": "E"}
    submitted = client.post(
        "/api/v1/wealth/risk-assessment",
        json={"user_id": "user_zhang", "answers": answers},
        headers=headers(),
    )
    assert submitted.status_code == 200, submitted.text
    assert "score" not in submitted.json(), "the submit response must not carry the score either"
    stored = main.WEALTH_RISK_PROFILES["user_zhang"]
    assert stored["risk_profile"] == "C5", stored
    assert "score" in stored and "answers" in stored, "the server still keeps them on file"

    fetched = client.get("/api/v1/wealth/risk-profile", headers=headers())
    assert fetched.status_code == 200, fetched.text
    profile = fetched.json()["data"]
    assert profile["risk_profile"] == stored["risk_profile"]
    assert "score" not in profile, profile
    assert "answers" not in profile, profile
    # The fields the client actually renders must survive the projection.
    assert profile["assessed_at"] and profile["source"] == "QUESTIONNAIRE"


def test_a_questionnaire_grade_can_actually_buy(client: TestClient):
    """The quiz grades C1-C5; the purchase gate must accept those codes.

    It used to accept only the older CONSERVATIVE/BALANCED/AGGRESSIVE vocabulary,
    so a user who had just finished the questionnaire was told to go and complete
    the assessment - the flow the quiz exists for was unreachable.
    """

    answers = {"1": "A", "2": "D", "3": "D", "4": "E", "5": "D", "6": "E", "7": "E", "8": "E"}
    graded = client.post(
        "/api/v1/wealth/risk-assessment",
        json={"user_id": "user_zhang", "answers": answers},
        headers=headers(),
    )
    assert graded.status_code == 200, graded.text
    assert main.WEALTH_RISK_PROFILES["user_zhang"]["risk_profile"] == "C5"

    # An R4 product is within a C5 file's reach; the precheck must not answer 428.
    allowed = client.post(
        "/api/v1/risk/precheck/operation",
        json={"user_id": "user_zhang", "operation": "WEALTH_PURCHASE", "product_id": "YH50365", "amount": 100},
        headers=headers(),
    )
    assert allowed.status_code != 428, allowed.text

    # A conservative file must still be refused an aggressive product.
    main.WEALTH_RISK_PROFILES["user_zhang"] = {"risk_profile": "C1", "source": "QUESTIONNAIRE"}
    refused = client.post(
        "/api/v1/risk/precheck/operation",
        json={"user_id": "user_zhang", "operation": "WEALTH_PURCHASE", "product_id": "YH50365", "amount": 100},
        headers=headers(),
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"]["code"] == "RISK_PROFILE_MISMATCH"

    # No assessment at all is still a 428, not a silent allow.
    main.WEALTH_RISK_PROFILES.pop("user_zhang", None)
    missing = client.post(
        "/api/v1/risk/precheck/operation",
        json={"user_id": "user_zhang", "operation": "WEALTH_PURCHASE", "product_id": "YH50365", "amount": 100},
        headers=headers(),
    )
    assert missing.status_code == 428, missing.text


def test_a_card_operation_never_guesses_which_card(client: TestClient):
    """Two cards plus no explicit id is ambiguous, and must be refused as such.

    The core used to fall back to "the first ACTIVE card" both when the id was
    missing *and* when it did not resolve, so a two-card user who pointed at their
    second card could freeze their salary card instead - and a typo retargeted the
    operation at whatever card happened to be first.
    """

    main.CARDS["user_zhang"] = [
        {"card_id": "CARD-001", "number_masked": "**** **** **** 3082", "type": "借记卡", "status": "ACTIVE", "daily_limit": 5000.0, "label": "工资卡"},
        {"card_id": "CARD-002", "number_masked": "**** **** **** 9999", "type": "借记卡", "status": "ACTIVE", "daily_limit": 5000.0, "label": "副卡"},
    ]

    ambiguous = client.post(
        "/api/v1/risk/precheck/operation",
        json={"user_id": "user_zhang", "operation": "CARD_FREEZE"},
        headers=headers(),
    )
    assert ambiguous.status_code == 409, ambiguous.text
    assert ambiguous.json()["detail"]["code"] == "CARD_ID_REQUIRED"
    assert all(card["status"] == "ACTIVE" for card in main.CARDS["user_zhang"]), "nothing may be frozen by a guess"

    # A named card resolves, and freezing it must freeze *that* card.
    token, proof = approve_red(client, "CARD_FREEZE", card_id="CARD-002")
    frozen = client.post(
        "/api/v1/cards/freeze",
        json={"user_id": "user_zhang", "auth_token": token, "card_id": "CARD-002"},
        headers={**headers(token), "X-MFA-Proof": proof},
    )
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["data"]["card"]["card_id"] == "CARD-002", frozen.text
    statuses = {card["card_id"]: card["status"] for card in main.CARDS["user_zhang"]}
    assert statuses == {"CARD-001": "ACTIVE", "CARD-002": "FROZEN"}, statuses

    # An id that does not resolve is an error, never a silent fallback.
    unknown = client.post(
        "/api/v1/risk/precheck/operation",
        json={"user_id": "user_zhang", "operation": "CARD_FREEZE", "card_id": "CARD-404"},
        headers=headers(),
    )
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["detail"]["code"] == "CARD_NOT_FOUND"

    # A single eligible card still resolves without an id (the common case).
    main.CARDS["user_zhang"] = [{"card_id": "CARD-001", "number_masked": "**** **** **** 3082", "type": "借记卡", "status": "ACTIVE", "daily_limit": 5000.0}]
    only = client.post(
        "/api/v1/risk/precheck/operation",
        json={"user_id": "user_zhang", "operation": "CARD_FREEZE"},
        headers=headers(),
    )
    assert only.status_code == 200, only.text

    # Un-reporting targets LOST cards, so ambiguity must be judged against the
    # states the operation can act on - not against ACTIVE alone.
    main.CARDS["user_zhang"] = [
        {"card_id": "CARD-001", "number_masked": "**** **** **** 3082", "type": "借记卡", "status": "ACTIVE", "daily_limit": 5000.0},
        {"card_id": "CARD-002", "number_masked": "**** **** **** 9999", "type": "借记卡", "status": "LOST", "daily_limit": 5000.0},
    ]
    lost = client.post(
        "/api/v1/risk/precheck/operation",
        json={"user_id": "user_zhang", "operation": "CARD_UNREPORT_LOST"},
        headers=headers(),
    )
    assert lost.status_code == 200, lost.text


def test_self_service_card_issuance_is_capped_per_user(client: TestClient, monkeypatch):
    """Cards are ledger state and the snapshot is rewritten in full, so an
    un-capped add is unbounded growth with quadratic disk work - and every card
    carries its own daily spending limit."""

    for module in (cards, card_routes):  # the add endpoint and the CARD_APPLY issuance path
        monkeypatch.setattr(module, "CARD_MAX_PER_USER", 3)
    main.CARDS["user_zhang"] = []
    monkeypatch.setitem(main.MOCK_CARD_VAULT, "MOCK-PAN-TEST-004", {"card_number": "4000000000000002", "owner_user_id": "user_zhang", "owner_name": "张三", "issuer_name": "招商银行", "pin_hash": main.MOCK_CARD_VAULT["MOCK-PAN-001"]["pin_hash"]})
    monkeypatch.setitem(main.MOCK_CARD_VAULT, "MOCK-PAN-TEST-005", {"card_number": "4242424242424242", "owner_user_id": "user_zhang", "owner_name": "张三", "issuer_name": "招商银行", "pin_hash": main.MOCK_CARD_VAULT["MOCK-PAN-001"]["pin_hash"]})

    numbers = ["4111111111111111", "5555555555554444", "4000000000000002", "4242424242424242", "4111111111111111"]
    codes = [
        client.post("/api/v1/cards/user_zhang/add", json={"card_type": "借记卡", "card_number": number, "source": "MANUAL", "user_confirmed": True}, headers=headers()).status_code
        for number in numbers
    ]

    assert codes[:3] == [200, 200, 200], codes
    assert codes[3:] == [409, 409], codes
    assert len(main.CARDS["user_zhang"]) == 3
    refused = client.post("/api/v1/cards/user_zhang/add", json={"card_type": "借记卡", "card_number": "5555555555554444", "source": "MANUAL", "user_confirmed": True}, headers=headers())
    assert refused.json()["detail"]["code"] == "CARD_LIMIT_REACHED"


def test_card_apply_cannot_bypass_the_per_user_card_ceiling(client: TestClient, monkeypatch):
    """Regression: asking the assistant for a card skipped the wallet cap.

    ``/cards/{user_id}/add`` enforced ``CARD_MAX_PER_USER`` while the
    ``CARD_APPLY`` operation appended straight to the wallet, so the same
    outcome was reachable two ways with only one of them bounded.
    """

    for module in (cards, card_routes):  # the add endpoint and the CARD_APPLY issuance path
        monkeypatch.setattr(module, "CARD_MAX_PER_USER", 2)
    main.CARDS["user_zhang"] = []

    def apply_card():
        precheck = client.post(
            "/api/v1/risk/precheck/operation",
            json={"user_id": "user_zhang", "operation": "CARD_APPLY"},
            headers=headers(),
        )
        assert precheck.status_code == 200, precheck.text
        token = precheck.json()["auth_token"]
        client.post("/api/v1/auth/confirm", json={}, headers=headers(token))
        return client.post("/api/v1/cards/apply", json={"user_id": "user_zhang", "auth_token": token}, headers=headers(token))

    assert apply_card().status_code == 200
    assert apply_card().status_code == 200
    refused = apply_card()

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "CARD_LIMIT_REACHED"
    assert len(main.CARDS["user_zhang"]) == 2


def test_card_limit_cannot_be_raised_to_the_transfer_ceiling(client: TestClient):
    """``CARD_LIMIT`` is bounded by card policy, not by the transfer maximum.

    The only ceiling used to be ``BANK_MAX_TRANSFER_CENTS`` (10,000 yuan), so a
    single MFA approval could turn a 5,000-yuan daily limit into 1,000,000.
    """

    assert main.CARD_MAX_DAILY_LIMIT < 1_000_000
    token, proof = approve_red(client, "CARD_LIMIT", card_id="CARD-001", new_limit=1_000_000)
    refused = client.post(
        "/api/v1/operations/execute",
        json={"user_id": "user_zhang", "operation": "CARD_LIMIT", "card_id": "CARD-001", "new_limit": 1_000_000, "auth_token": token},
        headers={**headers(token), "X-MFA-Proof": proof},
    )
    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"]["code"] == "CARD_LIMIT_OUT_OF_RANGE"
    assert main.CARDS["user_zhang"][0]["daily_limit"] == 5000.0

    allowed_token, allowed_proof = approve_red(client, "CARD_LIMIT", card_id="CARD-001", new_limit=8000)
    allowed = client.post(
        "/api/v1/operations/execute",
        json={"user_id": "user_zhang", "operation": "CARD_LIMIT", "card_id": "CARD-001", "new_limit": 8000, "auth_token": allowed_token},
        headers={**headers(allowed_token), "X-MFA-Proof": allowed_proof},
    )
    assert allowed.status_code == 200, allowed.text
    assert main.CARDS["user_zhang"][0]["daily_limit"] == 8000.0


def test_wealth_purchase_obeys_the_card_daily_limit(client: TestClient, monkeypatch):
    """A purchase is money leaving the account, so the card limit applies.

    Regression: ``daily_limit`` was enforced only in ``execute_transfer``, so
    the same card could ignore its limit entirely by buying a wealth product.
    """

    monkeypatch.delenv("BANK_ADMIN_AUDIT_TOKEN", raising=False)
    client.post(
        "/api/v1/wealth/risk-assessment",
        json={"user_id": "user_zhang", "answers": {str(number): "D" for number in range(1, 9)}},
        headers=headers(),
    )
    main.CARDS["user_zhang"][0]["daily_limit"] = 100.0
    main.CARD_DAILY_SPENT.clear()
    main.CARD_DAILY_SPENT_DATE.clear()
    balance_before = main.ACCOUNTS["user_zhang"]["balance"]

    # Refused at precheck, before the user is asked for SMS and device verification.
    early = client.post("/api/v1/risk/precheck/operation", headers=headers(),
                        json={"user_id": "user_zhang", "operation": "WEALTH_PURCHASE", "product_id": "LX00118", "amount": 5000, "reason": "大额申购"})
    assert early.status_code == 400 and early.json()["detail"]["code"] == "CARD_LIMIT_EXCEEDED"
    assert "¥100.00" in early.json()["detail"]["message"]

    # Settlement still enforces it if the limit is lowered after the precheck.
    main.CARDS["user_zhang"][0]["daily_limit"] = 50000.0
    token, proof = approve_red(client, "WEALTH_PURCHASE", product_id="LX00118", amount=5000, reason="大额申购")
    main.CARDS["user_zhang"][0]["daily_limit"] = 100.0
    refused = client.post(
        "/api/v1/operations/execute",
        json={"user_id": "user_zhang", "operation": "WEALTH_PURCHASE", "product_id": "LX00118", "amount": 5000, "reason": "大额申购", "auth_token": token},
        headers={**headers(token), "X-MFA-Proof": proof},
    )

    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"]["code"] == "CARD_LIMIT_EXCEEDED"
    assert main.ACCOUNTS["user_zhang"]["balance"] == balance_before
    assert main.WEALTH_HOLDINGS.get("user_zhang", []) == []
    assert main.CARD_DAILY_SPENT.get("CARD-001", 0.0) == 0.0


def test_wealth_purchase_records_the_card_spend_when_it_settles(client: TestClient, monkeypatch):
    monkeypatch.delenv("BANK_ADMIN_AUDIT_TOKEN", raising=False)
    client.post(
        "/api/v1/wealth/risk-assessment",
        json={"user_id": "user_zhang", "answers": {str(number): "D" for number in range(1, 9)}},
        headers=headers(),
    )
    main.CARDS["user_zhang"][0]["daily_limit"] = 5000.0
    main.CARD_DAILY_SPENT.clear()
    main.CARD_DAILY_SPENT_DATE.clear()
    balance_before = main.ACCOUNTS["user_zhang"]["balance"]

    token, proof = approve_red(client, "WEALTH_PURCHASE", product_id="LX00118", amount=1000, reason="申购")
    settled = client.post(
        "/api/v1/operations/execute",
        json={"user_id": "user_zhang", "operation": "WEALTH_PURCHASE", "product_id": "LX00118", "amount": 1000, "reason": "申购", "auth_token": token},
        headers={**headers(token), "X-MFA-Proof": proof},
    )

    assert settled.status_code == 200, settled.text
    assert main.ACCOUNTS["user_zhang"]["balance"] == balance_before - 1000.0
    assert main.CARD_DAILY_SPENT["CARD-001"] == 1000.0
    assert len(main.WEALTH_HOLDINGS["user_zhang"]) == 1


def test_password_change_is_red_and_card_lost_state_has_explicit_unreport(client: TestClient):
    token, proof = approve_red(client, "PASSWORD_CHANGE")
    version_before = main.ACCOUNTS["user_zhang"]["credential_version"]
    hash_before = main.ACCOUNTS["user_zhang"]["password_hash"]
    changed = client.post(
        "/api/v1/auth/password/change",
        json={"user_id": "user_zhang", "auth_token": token, "new_password": "Rotation@2026"},
        headers={**headers(token), "X-MFA-Proof": proof},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["data"]["status"] == "PASSWORD_CHANGED"
    assert main.ACCOUNTS["user_zhang"]["credential_version"] == version_before + 1
    # The rotation must be real, not cosmetic.  Checked against the stored digest
    # rather than by attempting a login: a failed login is itself a behaviour
    # signal (AUTH_FAILURE), and adding one here would perturb the risk score
    # this module exists to exercise.  The login-path coverage lives in
    # tests/test_login.py.
    assert main.ACCOUNTS["user_zhang"]["password_hash"] != hash_before
    assert security.verify_password("Rotation@2026", main.ACCOUNTS["user_zhang"]["password_hash"])
    assert not security.verify_password("Bank@2026", main.ACCOUNTS["user_zhang"]["password_hash"])

    lost_token, lost_proof = approve_red(client, "CARD_REPORT_LOST")
    lost = client.post(
        "/api/v1/cards/report-lost",
        json={"user_id": "user_zhang", "auth_token": lost_token},
        headers={**headers(lost_token), "X-MFA-Proof": lost_proof},
    )
    assert lost.status_code == 200
    restore_token, restore_proof = approve_red(client, "CARD_UNREPORT_LOST")
    restored = client.post(
        "/api/v1/cards/unreport-lost",
        json={"user_id": "user_zhang", "auth_token": restore_token},
        headers={**headers(restore_token), "X-MFA-Proof": restore_proof},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["card"]["status"] == "ACTIVE"


def test_task_takeover_is_owner_scoped(client: TestClient):
    token = client.post("/api/v1/risk/precheck/lock", json={"user_id": "user_zhang", "amount": 10, "reason": "测试", "event_at": "2099-01-01T09:00:00+08:00"}, headers=headers()).json()["auth_token"]
    client.post("/api/v1/auth/confirm", json={}, headers=headers(token))
    locked = client.post("/api/v1/accounts/lock_fund", json={"user_id": "user_zhang", "amount": 10, "reason": "测试", "event_at": "2099-01-01T09:00:00+08:00"}, headers=headers(token)).json()
    # The reservation task is already SUCCEEDED; human takeover applies to
    # the pending reminder/work item instead of reopening a committed step.
    task_id = locked.get("reminder_task_id") or locked["task_id"]
    takeover = client.post(f"/api/v1/tasks/{task_id}/takeover", json={"reason": "需要人工复核"}, headers=headers())
    assert takeover.status_code == 200
    assert takeover.json()["data"]["status"] == "HUMAN_TAKEOVER"
    # Another subject must not be able to take over (or even see) the task.
    cross_user = client.post(f"/api/v1/tasks/{task_id}/takeover", json={"reason": "越权接管"}, headers={"X-User-Id": "user_li"})
    assert cross_user.status_code == 404
    hidden = client.get(f"/api/v1/tasks/{task_id}", headers={"X-User-Id": "user_li"})
    assert hidden.status_code == 404
    assert client.get(f"/api/v1/tasks/{task_id}", headers=headers()).status_code == 200


def test_security_health_reports_sandbox(client: TestClient):
    response = client.get("/api/v1/security/health")
    assert response.status_code == 200
    sandbox = response.json()["sandbox"]
    assert sandbox["mode"] == "read_only_expression"
    assert sandbox["network"] == "denied_by_api"
    assert "group_spend" in sandbox["safe_functions"]


def _transfer(client: TestClient, amount: float, target: str = "李四"):
    """Run a yellow transfer end to end and return the settle response."""

    precheck = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": target, "amount": amount},
        headers=headers(),
    )
    assert precheck.status_code == 200, precheck.text
    token = precheck.json()["auth_token"]
    assert client.post("/api/v1/auth/confirm", json={}, headers=headers(token)).status_code == 200
    return client.post(
        "/api/v1/transfers/execute",
        json={"from_user": "user_zhang", "target_name_or_id": target, "amount": amount, "auth_token": token},
        headers=headers(token),
    )


def test_card_daily_limit_is_enforced_on_settlement(client: TestClient, monkeypatch):
    """The card's daily_limit constrains real payments, not just display.

    It is checked at both ends now: the precheck refuses before a credential is
    issued (so the user is not sent through approval and MFA for a payment that
    cannot settle), and settlement re-checks because the limit can move in
    between.
    """

    monkeypatch.delenv("BANK_ADMIN_AUDIT_TOKEN", raising=False)
    card = main.CARDS["user_zhang"][0]
    card["daily_limit"] = 100.0
    balance_before = main.ACCOUNTS["user_zhang"]["balance"]

    at_limit = _transfer(client, 100.0)
    assert at_limit.status_code == 200, at_limit.text
    assert main.ACCOUNTS["user_zhang"]["balance"] == balance_before - 100.0

    # Over the limit is refused at precheck, before any token exists.
    over_limit = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 0.01},
        headers=headers(),
    )
    assert over_limit.status_code == 400, over_limit.text
    assert over_limit.json()["detail"]["code"] == "CARD_LIMIT_EXCEEDED"
    assert main.ACCOUNTS["user_zhang"]["balance"] == balance_before - 100.0

    cards = client.get("/api/v1/cards/user_zhang", headers=headers()).json()["data"][0]
    assert cards["daily_spent_today"] == 100.0
    assert cards["daily_remaining"] == 0.0

    # A new day resets the counter and frees the limit again.
    main.CARD_DAILY_SPENT_DATE[cards["card_id"]] = "2000-01-01"
    next_day = _transfer(client, 50.0)
    assert next_day.status_code == 200, next_day.text
    assert main.ACCOUNTS["user_zhang"]["balance"] == balance_before - 150.0


def test_the_settlement_card_is_the_card_that_is_charged(client: TestClient, monkeypatch):
    """Regression: enforcement read one card and the counter wrote another.

    ``enforce_card_limit`` checked the first *ACTIVE* card while
    ``record_card_spend`` wrote ``cards[0]``, so with one frozen and one
    active card the payment was authorised against the active card's limit and
    recorded on the frozen one - leaving both cards' displayed figures wrong.
    """

    monkeypatch.delenv("BANK_ADMIN_AUDIT_TOKEN", raising=False)
    main.CARDS["user_zhang"] = [
        {"card_id": "CARD-001", "number_masked": "**** **** **** 3082", "type": "借记卡", "status": "FROZEN", "daily_limit": 5000.0, "label": "工资卡"},
        {"card_id": "CARD-002", "number_masked": "**** **** **** 9999", "type": "借记卡", "status": "ACTIVE", "daily_limit": 100.0, "label": "小额卡"},
    ]
    main.CARD_DAILY_SPENT.clear()
    main.CARD_DAILY_SPENT_DATE.clear()

    assert _transfer(client, 80.0).status_code == 200

    # The spend lands on the card that paid, and only on that one.
    assert main.CARD_DAILY_SPENT == {"CARD-002": 80.0}

    cards = {row["card_id"]: row for row in client.get("/api/v1/cards/user_zhang", headers=headers()).json()["data"]}
    assert cards["CARD-002"]["daily_spent_today"] == 80.0
    assert cards["CARD-002"]["daily_remaining"] == 20.0
    assert cards["CARD-001"]["daily_spent_today"] == 0.0
    assert cards["CARD-001"]["daily_remaining"] == 5000.0

    # And the limit that binds is the paying card's 100, not the frozen 5000.
    refused = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 30.0},
        headers=headers(),
    )
    assert refused.status_code == 400
    assert refused.json()["detail"]["code"] == "CARD_LIMIT_EXCEEDED"


def test_renewal_reminders_are_idempotent_and_cascade_on_cancel(client: TestClient, monkeypatch):
    monkeypatch.delenv("BANK_ADMIN_AUDIT_TOKEN", raising=False)

    first = client.post("/api/v1/subscriptions/user_zhang/reminders", headers=headers())
    assert first.status_code == 200, first.text
    created = {task["payload"]["subscription_id"]: task for task in first.json()["data"]}
    assert set(created) == {"SUB-APPLE", "SUB-VIDEO"}
    # Two days before the charge date, at 09:00 local.
    assert created["SUB-APPLE"]["run_at"].startswith("2026-10-01T09:00")
    video_charge = next(row["next_charge"] for row in main.SUBSCRIPTIONS["user_zhang"] if row["subscription_id"] == "SUB-VIDEO")
    video_reminder = (datetime.fromisoformat(video_charge) - timedelta(days=2)).strftime("%Y-%m-%dT09:00")
    assert created["SUB-VIDEO"]["run_at"].startswith(video_reminder)

    repeat = client.post("/api/v1/subscriptions/user_zhang/reminders", headers=headers())
    assert repeat.status_code == 200
    assert {task["task_id"] for task in repeat.json()["data"]} == {task["task_id"] for task in first.json()["data"]}

    # Cancelling the subscription withdraws its pending reminder only.
    token = approve(client, "SUBSCRIPTION_CANCEL", target="Apple")
    cancelled = client.post(
        "/api/v1/operations/execute",
        json={"user_id": "user_zhang", "operation": "SUBSCRIPTION_CANCEL", "target": "Apple", "auth_token": token},
        headers=headers(token),
    )
    assert cancelled.status_code == 200, cancelled.text
    tasks = {task["task_id"]: task for task in client.get("/api/v1/tasks", headers=headers()).json()["data"]}
    assert tasks[created["SUB-APPLE"]["task_id"]]["status"] == "CANCELLED"
    assert tasks[created["SUB-VIDEO"]["task_id"]]["status"] == "PENDING"

    # When due, a reminder is delivered (SUCCEEDED) rather than silently charging.
    for task in main.TASK_STORE.list(owner_id="user_zhang"):
        if task.task_id == created["SUB-VIDEO"]["task_id"]:
            task.run_at = "2000-01-01T00:00:00+00:00"
    due = client.post("/api/v1/tasks/run-due", headers=headers())
    assert due.status_code == 200
    refreshed = {task["task_id"]: task for task in client.get("/api/v1/tasks", headers=headers()).json()["data"]}
    assert refreshed[created["SUB-VIDEO"]["task_id"]]["status"] == "SUCCEEDED"


def test_aa_split_requires_each_payer_and_reconciles_cents(client: TestClient):
    """The initiator cannot debit anyone; each payer authorizes their share."""

    initiator_before = main.ACCOUNTS["user_zhang"]["balance"]
    li_before = main.ACCOUNTS["user_li"]["balance"]
    wife_before = main.ACCOUNTS["user_wife"]["balance"]

    token = approve(client, "SPLIT_BILL", amount=100.0, participants=["李四", "王美"])
    settled = client.post(
        "/api/v1/operations/execute",
        json={"user_id": "user_zhang", "operation": "SPLIT_BILL", "amount": 100.0, "participants": ["李四", "王美"], "auth_token": token},
        headers=headers(token),
    )
    assert settled.status_code == 200, settled.text
    data = settled.json()["data"]
    assert data["status"] == "PENDING"
    assert data["per_person"] == 33.33
    assert data["initiator_share"] == 33.34
    # 33.33 * 2 + 33.34 == 100.00 exactly.
    assert round(data["per_person"] * 2 + data["initiator_share"], 2) == 100.0
    assert data["collected_amount"] == 0.0
    assert data["outstanding_amount"] == 66.66
    assert main.ACCOUNTS["user_li"]["balance"] == li_before
    assert main.ACCOUNTS["user_wife"]["balance"] == wife_before
    assert main.ACCOUNTS["user_zhang"]["balance"] == initiator_before

    assert pay_split(client, data["split_id"], "user_li", 33.33).status_code == 200
    assert pay_split(client, data["split_id"], "user_wife", 33.33).status_code == 200
    assert main.SPLIT_REQUESTS[0]["status"] == "SETTLED"
    assert main.ACCOUNTS["user_li"]["balance"] == round(li_before - 33.33, 2)
    assert main.ACCOUNTS["user_wife"]["balance"] == round(wife_before - 33.33, 2)
    assert main.ACCOUNTS["user_zhang"]["balance"] == round(initiator_before + 66.66, 2)
    aa_rows = [row for row in main.TRANSACTIONS if str(row.get("category", "")).startswith("AA")]
    assert len(aa_rows) == 2
    assert sum(1 for row in aa_rows if row["account_id"] == "user_zhang") == 2


def test_aa_split_reports_partial_collection_when_one_payer_lacks_funds(client: TestClient):
    main.ACCOUNTS["user_li"]["balance"] = 10.0
    li_before = main.ACCOUNTS["user_li"]["balance"]
    initiator_before = main.ACCOUNTS["user_zhang"]["balance"]

    token = approve(client, "SPLIT_BILL", amount=100.0, participants=["李四", "王美"])
    settled = client.post(
        "/api/v1/operations/execute",
        json={"user_id": "user_zhang", "operation": "SPLIT_BILL", "amount": 100.0, "participants": ["李四", "王美"], "auth_token": token},
        headers=headers(token),
    )
    assert settled.status_code == 200, settled.text
    data = settled.json()["data"]
    assert data["status"] == "PENDING"
    assert pay_split(client, data["split_id"], "user_wife", 33.33).status_code == 200
    refused = pay_split(client, data["split_id"], "user_li", 33.33)
    assert refused.status_code == 400
    assert main.SPLIT_REQUESTS[0]["status"] == "PARTIAL"
    assert main.SPLIT_REQUESTS[0]["collected_amount"] == 33.33
    # Only the failing participant's share is outstanding; the initiator's own
    # share is never reported as arrears.
    assert main.SPLIT_REQUESTS[0]["outstanding_amount"] == 33.33
    assert next(row for row in main.SPLIT_REQUESTS[0]["collection"] if row["account_id"] == "user_li")["status"] == "PENDING"
    assert main.ACCOUNTS["user_li"]["balance"] == li_before
    assert main.ACCOUNTS["user_zhang"]["balance"] == round(initiator_before + 33.33, 2)


def test_aa_decline_keeps_other_pending_share_open(client: TestClient):
    token = approve(client, "SPLIT_BILL", amount=90.0, participants=["李四", "王美"])
    created = client.post(
        "/api/v1/operations/execute",
        json={"user_id": "user_zhang", "operation": "SPLIT_BILL", "amount": 90.0,
              "participants": ["李四", "王美"], "auth_token": token},
        headers=headers(token),
    )
    assert created.status_code == 200, created.text
    split_id = created.json()["data"]["split_id"]
    declined = client.post(f"/api/v1/splits/{split_id}/decline", headers={"X-User-Id": "user_li"})
    assert declined.status_code == 200, declined.text
    assert main.SPLIT_REQUESTS[0]["status"] == "PENDING"
    assert pay_split(client, split_id, "user_wife", 30.0).status_code == 200
    assert main.SPLIT_REQUESTS[0]["status"] == "PARTIAL"


def test_a_zero_card_limit_is_refused_with_a_precise_error(client: TestClient):
    """Zero means "stop this card paying", not "make it a one-cent card".

    The generic operation-amount helper folds "no amount" into an internal
    0.01 sentinel, so an explicit zero had to be rejected before it could be
    silently rounded up into an instruction the caller did not give.
    """

    for value in (0, 0.0):
        refused = client.post(
            "/api/v1/risk/precheck/operation",
            json={"user_id": "user_zhang", "operation": "CARD_LIMIT", "card_id": "CARD-001", "new_limit": value},
            headers=headers(),
        )
        assert refused.status_code == 400, refused.text
        assert refused.json()["detail"]["code"] == "INVALID_CARD_LIMIT"
    assert main.CARDS["user_zhang"][0]["daily_limit"] == 5000.0
