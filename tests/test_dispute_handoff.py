from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import main
from agent import _local_parse
from planner import build_task_plan, missing_slots, risk_for_intent
from scheduler import TASK_STORE


REQUEST_TEXT = "对 TX1004 申请争议退款，陈述：本人未在美国消费，凭证 EVIDENCE-001，转人工客服"
DISPUTE_FIELDS = {
    "transaction_id": "TX1004",
    "statement": "本人未在美国消费",
    "evidence_ref": "EVIDENCE-001",
}


@pytest.fixture()
def client():
    main.reset_state()
    with TestClient(main.app) as value:
        yield value
    main.reset_state()


def headers(token: str | None = None, user: str = "user_zhang") -> dict[str, str]:
    result = {"X-User-Id": user}
    if token:
        result["X-Auth-Token"] = token
    return result


def precheck(client: TestClient, *, key: str = "dispute-pre", **overrides):
    body = {"user_id": "user_zhang", "operation": "DISPUTE_SUBMIT", **DISPUTE_FIELDS, **overrides}
    return client.post("/api/v1/risk/precheck/operation", json=body, headers={**headers(), "Idempotency-Key": key})


def confirm(client: TestClient, token: str) -> None:
    response = client.post("/api/v1/auth/confirm", json={}, headers=headers(token))
    assert response.status_code == 200, response.text


def execute(client: TestClient, token: str, *, key: str = "dispute-execute", **overrides):
    body = {"user_id": "user_zhang", "operation": "DISPUTE_SUBMIT", **DISPUTE_FIELDS, "auth_token": token, **overrides}
    return client.post("/api/v1/operations/execute", json=body, headers={**headers(token), "Idempotency-Key": key})


def error_code(response) -> str | None:
    detail = response.json().get("detail", {})
    return detail.get("code") if isinstance(detail, dict) else None


def test_parse_missing_slots_risk_and_ordered_dag():
    parsed = _local_parse(REQUEST_TEXT)
    assert parsed["intent"] == "DISPUTE_HANDOFF"
    assert parsed["slots"] == DISPUTE_FIELDS
    assert risk_for_intent(parsed["intent"]) == "YELLOW"
    assert missing_slots(parsed) == []
    assert [task.action for task in build_task_plan(parsed).tasks] == [
        "VERIFY_TRANSACTION",
        "COLLECT_STATEMENT",
        "COLLECT_EVIDENCE",
        "USER_CONFIRM",
        "DISPUTE_SUBMIT",
        "HUMAN_TAKEOVER",
    ]
    incomplete = _local_parse("对 TX1004 申请争议退款，转人工客服")
    assert incomplete["intent"] == "DISPUTE_HANDOFF"
    assert missing_slots(incomplete) == ["statement", "evidence_ref"]


def test_precheck_is_yellow_returns_owned_transaction_and_does_not_mutate(client):
    before = (deepcopy(main.ACCOUNTS), deepcopy(main.TRANSACTIONS), deepcopy(main.CARDS))
    response = precheck(client)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["risk_level"] == "YELLOW"
    assert payload["required_actions"] == ["USER_CLICK_CONFIRM"]
    assert payload["transaction"]["tx_id"] == "TX1004"
    assert payload["target"] == "TX1004"
    assert main.DISPUTE_CASES == []
    assert (main.ACCOUNTS, main.TRANSACTIONS, main.CARDS) == before


@pytest.mark.parametrize("missing", ["transaction_id", "statement", "evidence_ref"])
def test_precheck_requires_every_collected_field(client, missing):
    response = precheck(client, key=f"missing-{missing}", **{missing: None})
    assert response.status_code >= 400
    assert main.DISPUTE_CASES == []


def test_direct_confirmation_claim_cannot_bypass_server_confirmation(client):
    token = precheck(client, key="bypass-pre").json()["auth_token"]
    response = execute(client, token, key="bypass-execute", user_confirmed=True)
    assert response.status_code == 428
    assert error_code(response) == "USER_CONFIRMATION_REQUIRED"
    assert main.DISPUTE_CASES == []


def test_submit_creates_unique_human_review_without_financial_or_card_mutation(client):
    account_before = deepcopy(main.ACCOUNTS["user_zhang"])
    transactions_before = deepcopy(main.TRANSACTIONS)
    cards_before = deepcopy(main.CARDS["user_zhang"])
    token = precheck(client, key="valid-pre").json()["auth_token"]
    confirm(client, token)

    first = execute(client, token, key="valid-execute")
    assert first.status_code == 200, first.text
    payload = first.json()["data"]
    dispute_case = payload["dispute_case"]
    review_task = payload["review_task"]
    assert payload["status"] == "HUMAN_TAKEOVER"
    assert payload["funds_debited"] is False
    assert dispute_case["status"] == "HUMAN_REVIEW"
    assert dispute_case["review_channel"] == "MOCK_HUMAN_SUPPORT"
    assert dispute_case["decision_authority"] == "HUMAN_ONLY"
    assert dispute_case["refund_status"] == "AWAITING_HUMAN_DECISION"
    assert dispute_case["evidence_refs"] == ["EVIDENCE-001"]
    assert review_task["task_type"] == "DISPUTE_HUMAN_REVIEW"
    assert review_task["status"] == "HUMAN_TAKEOVER"

    retry = execute(client, token, key="valid-execute")
    assert retry.status_code == 200
    assert retry.json()["data"]["dispute_case"]["case_id"] == dispute_case["case_id"]
    assert retry.json()["data"]["review_task"]["task_id"] == review_task["task_id"]
    assert len(main.DISPUTE_CASES) == 1
    assert len([task for task in TASK_STORE.list(owner_id="user_zhang") if task.task_type == "DISPUTE_HUMAN_REVIEW"]) == 1
    assert main.ACCOUNTS["user_zhang"] == account_before
    assert main.TRANSACTIONS == transactions_before
    assert main.CARDS["user_zhang"] == cards_before


def test_wrong_owner_and_context_tampering_are_rejected(client):
    wrong_owner = client.post(
        "/api/v1/risk/precheck/operation",
        json={"user_id": "user_li", "operation": "DISPUTE_SUBMIT", **DISPUTE_FIELDS},
        headers={"X-User-Id": "user_li", "Idempotency-Key": "wrong-owner"},
    )
    assert wrong_owner.status_code == 404

    token = precheck(client, key="tamper-pre").json()["auth_token"]
    confirm(client, token)
    main.TRANSACTIONS[-1]["amount"] = 1.0
    changed = execute(client, token, key="tamper-execute")
    assert changed.status_code >= 400
    assert main.DISPUTE_CASES == []


def test_statement_or_evidence_cannot_change_after_confirmation(client):
    token = precheck(client, key="fields-pre").json()["auth_token"]
    confirm(client, token)
    changed = execute(client, token, key="fields-execute", evidence_ref="EVIDENCE-OTHER")
    assert changed.status_code >= 400
    assert main.DISPUTE_CASES == []


@pytest.mark.parametrize("operation", ["REFUND", "DISPUTE_REFUND", "DISPUTE_HANDOFF"])
def test_refund_and_internal_intent_names_are_not_core_operations(client, operation):
    state_before = (deepcopy(main.ACCOUNTS), deepcopy(main.TRANSACTIONS), deepcopy(main.CARDS), deepcopy(main.DISPUTE_CASES))
    response = client.post(
        "/api/v1/risk/precheck/operation",
        json={"user_id": "user_zhang", "operation": operation, **DISPUTE_FIELDS},
        headers={**headers(), "Idempotency-Key": f"forged-{operation.lower()}"},
    )
    assert response.status_code == 400
    assert error_code(response) == "OPERATION_NOT_ALLOWED"
    assert (main.ACCOUNTS, main.TRANSACTIONS, main.CARDS, main.DISPUTE_CASES) == state_before
