"""Natural-language wealth/card journeys through the real graph and core.

Only HTTP transport is replaced. Each test gets an in-memory conversation graph,
fresh ledger, real authorization gates, and a redacted JSONL turn transcript.
No listener, paid model, developer ledger, or external network is used.
"""

import json
import os
from urllib.parse import urlsplit

import pytest
import requests

# Pin before importing either graph or service, not only inside the fixture.
os.environ["BANK_CONVERSATION_DB"] = "memory"

pytest.importorskip("langgraph")
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

import agent
import conversation
import main
import security
from bank_tools import BankClient


USER = "user_zhang"
QUIZ_ANSWERS = "1B 2B 3B 4B 5B 6B 7B 8B"


class _InProcessTransport:
    def __init__(self, core):
        self.core = core

    def request(self, method, url, *, json=None, headers=None, timeout=None):
        parsed = urlsplit(url)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        return self.core.request(method, path, json=json, headers=headers)


class Dialogue:
    def __init__(self, service, core, case_id, transcript, settlements):
        self.service, self.core = service, core
        self.case_id, self.transcript = case_id, transcript
        self.last = None
        self.turn = 0
        self.settlements = settlements

    @property
    def state(self):
        thread = conversation.conversation_thread_id(USER, self.case_id)
        return conversation.get_graph().get_state({"configurable": {"thread_id": thread}}).values

    def record(self, text, envelope):
        self.last = envelope
        row = {
            "turn": self.turn, "input": text,
            "status": envelope.get("status"), "intent": self.state.get("intent"),
            "target": self.state.get("target"), "amount": self.state.get("amount"),
            "reply": envelope.get("reply"), "risk_level": envelope.get("risk_level"),
            "request_id": envelope.get("request_id"),
            "approval_id": (envelope.get("approval") or {}).get("approval_id"),
            "balance": main.ACCOUNTS[USER]["balance"],
            "cards": [{k: card.get(k) for k in ("card_id", "status", "daily_limit")} for card in main.CARDS[USER]],
        }
        with self.transcript.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        return envelope

    def say(self, text):
        self.turn += 1
        envelope = self.service.start_turn(
            principal=USER, conversation_id=self.case_id,
            message_id=f"turn-{self.turn}", text=text,
        )
        return self.record(text, envelope)

    def decide(self, decision="approve"):
        assert self.last and self.last.get("approval"), self.last
        approval = self.last["approval"]
        kwargs = {}
        if decision == "approve" and self.last["risk_level"] == "RED":
            challenge = self.service.face_challenge(principal=USER, approval_id=approval["approval_id"])
            kwargs = {"otp": "888888", "face_attestation": security.sign_face_attestation(
                challenge["challenge_id"], user_id=USER, token_jti=challenge["auth_token_jti"],
            )}
        envelope = self.service.resolve_approval(
            principal=USER, approval_id=approval["approval_id"], decision=decision, **kwargs,
        )
        self.turn += 1
        return self.record(f"[{decision}]", envelope)

    def assessed(self):
        assert (self.say("我想做风险测评").get("result") or {}).get("risk_quiz")
        envelope = self.say(QUIZ_ANSWERS)
        assert (envelope.get("result") or {}).get("profile_result"), envelope
        assert main.WEALTH_RISK_PROFILES.get(USER)

    def pending(self, intent, *, target=None, amount=None):
        assert self.last["status"] == "APPROVAL_REQUIRED", self.last
        parsed = {key: self.state.get(key) for key in ("intent", "target", "amount")}
        assert parsed["intent"] == intent, parsed
        if target is not None:
            assert parsed["target"] == target, parsed
        if amount is not None:
            assert parsed["amount"] == amount, parsed
        assert self.last["risk_level"] in {"YELLOW", "RED"}

    def completed(self, intent):
        assert self.last["status"] == "COMPLETED", self.last
        assert self.last["intent"] == intent, self.last
        assert self.last.get("request_id"), self.last


@pytest.fixture
def dialogue(monkeypatch, tmp_path, request):
    monkeypatch.setenv("BANK_CONVERSATION_DB", "memory")
    monkeypatch.setattr(conversation, "_GRAPH", None)
    monkeypatch.setattr(conversation, "_GRAPH_BACKEND", "uninitialised")
    monkeypatch.setattr(agent, "llm", None)
    main.reset_state()
    core = TestClient(main.app)
    settlements = []

    def in_process_http(method, path, *, state, json_body=None, headers=None):
        merged = {"X-Request-Id": state.get("request_id") or "natural-test"}
        merged.update(headers or {})
        if method == "POST" and path == "operations/execute":
            settlements.append((dict(json_body or {}), dict(merged)))
        return core.request(method, "/api/v1/" + path.lstrip("/"), json=json_body, headers=merged)

    def no_network(*args, **kwargs):
        raise AssertionError("Natural dialogue tests must not use the external network")

    monkeypatch.setattr(agent, "_http", in_process_http)
    monkeypatch.setattr(requests.sessions.Session, "request", no_network)
    service = conversation.ConversationService(client_factory=lambda principal, session_token=None: BankClient(
        base_url="http://inprocess/api/v1", user_id=principal,
        session_token=session_token, session=_InProcessTransport(core),
    ))
    transcript = tmp_path / "turns.jsonl"
    request.node.user_properties.append(("transcript", str(transcript)))
    try:
        yield Dialogue(service, core, request.node.name, transcript, settlements)
    finally:
        service.reset()
        core.close()
        main.reset_state()


def test_browse_then_compare_real_products(dialogue):
    dialogue.say("现在有哪些在售理财？")
    dialogue.completed("WEALTH_QUERY")
    assert "CJ01002" in dialogue.last["reply"] and "QK36509" in dialogue.last["reply"]
    dialogue.say("帮我比较 CJ01002 和 AY90204")
    dialogue.completed("WEALTH_COMPARE")
    assert "CJ01002" in dialogue.last["reply"] and "AY90204" in dialogue.last["reply"]
    assert not main.WEALTH_RISK_PROFILES


def test_compare_accepts_bare_product_pair_after_clarification(dialogue):
    assert "两个" in dialogue.say("比较理财")["reply"]
    dialogue.say("CJ01002 和 AY90204")
    dialogue.completed("WEALTH_COMPARE")
    assert dialogue.state["product_ids"] == ["CJ01002", "AY90204"]


def test_recommendation_quiz_and_profile_persist_across_turns(dialogue):
    assert (dialogue.say("我有点闲钱，想买点稳健的理财").get("result") or {}).get("risk_quiz")
    assert (dialogue.say(QUIZ_ANSWERS).get("result") or {}).get("profile_result")
    dialogue.say("再推荐适合我的理财")
    dialogue.completed("WEALTH_RECOMMEND")
    assert not ((dialogue.last.get("result") or {}).get("risk_quiz") or {}).get("active")
    assert "CJ01002" in dialogue.last["reply"]
    # The B answers produce C2: subsequent turns must use that saved profile,
    # rather than silently reverting to the core endpoint's conservative default.
    assert main.WEALTH_RISK_PROFILES[USER]["risk_profile"] == "C2"
    assert "AY90204" in dialogue.last["reply"], dialogue.last


def test_invalid_quiz_answer_can_be_corrected_or_cancelled(dialogue):
    dialogue.say("我想做风险测评")
    assert "无效" in dialogue.say("1Z")["reply"]
    assert not main.WEALTH_RISK_PROFILES
    assert "已记录 1/8" in dialogue.say("1B")["reply"]
    assert "退出" in dialogue.say("取消测评")["reply"]
    dialogue.say("查一下余额")
    dialogue.completed("BALANCE_QUERY")
    assert not main.WEALTH_RISK_PROFILES


def test_purchase_missing_amount_does_not_use_product_digits(dialogue):
    dialogue.assessed()
    assert "金额" in dialogue.say("申购 CJ01002")["reply"]
    assert not dialogue.last.get("approval")
    dialogue.say("100元")
    dialogue.pending("WEALTH_PURCHASE", target="CJ01002", amount=100)
    assert main.ACCOUNTS[USER]["balance"] == 15000
    dialogue.decide("reject")
    assert not main.WEALTH_HOLDINGS[USER]


def test_purchase_then_redeem_update_the_same_holding(dialogue):
    dialogue.assessed()
    for _ in range(2):
        dialogue.say("申购 CJ01002 100元")
        dialogue.pending("WEALTH_PURCHASE", target="CJ01002", amount=100)
        dialogue.decide()
    assert main.ACCOUNTS[USER]["balance"] == 14800
    assert len(main.WEALTH_HOLDINGS[USER]) == 2
    second_holding_id = main.WEALTH_HOLDINGS[USER][1]["holding_id"]
    dialogue.say("赎回 CJ01002 150元")
    dialogue.pending("WEALTH_REDEEM", target="CJ01002", amount=150)
    dialogue.decide()
    assert main.ACCOUNTS[USER]["balance"] == 14950
    assert sum(item["principal"] for item in main.WEALTH_HOLDINGS[USER]) == 50
    assert len(main.WEALTH_HOLDINGS[USER]) == 1
    remaining = main.WEALTH_HOLDINGS[USER][0]
    assert remaining["holding_id"] == second_holding_id
    assert remaining["estimated_value"] == 50

    # Replay the exact transport request with its original idempotency key.
    # These synthetic authorization credentials remain in memory, never in logs.
    body, headers = dialogue.settlements[-1]
    assert body["operation"] == "WEALTH_REDEEM"
    replay = dialogue.core.post("/api/v1/operations/execute", json=body, headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["redeemed_amount"] == 150
    assert main.ACCOUNTS[USER]["balance"] == 14950
    assert main.WEALTH_HOLDINGS[USER][0]["principal"] == 50


def test_same_product_reference_preserves_target_and_new_amount(dialogue):
    dialogue.assessed()
    dialogue.say("申购 CJ01002 100元")
    dialogue.pending("WEALTH_PURCHASE", target="CJ01002", amount=100)
    dialogue.decide()
    dialogue.say("那个产品再申购20元")
    dialogue.pending("WEALTH_PURCHASE", target="CJ01002", amount=20)
    dialogue.decide("reject")
    assert main.ACCOUNTS[USER]["balance"] == 14900


def test_below_minimum_purchase_never_claims_success(dialogue):
    dialogue.assessed()
    dialogue.say("申购 AY90204 10元")
    assert not dialogue.last.get("approval"), dialogue.last
    assert "起购" in dialogue.last["reply"] or "最低" in dialogue.last["reply"], dialogue.last
    assert main.ACCOUNTS[USER]["balance"] == 15000
    assert not main.WEALTH_HOLDINGS[USER]


def test_risk_mismatch_does_not_offer_a_purchase_approval(dialogue):
    dialogue.assessed()
    assert main.WEALTH_RISK_PROFILES[USER]["risk_profile"] == "C2"
    dialogue.say("申购 ZY18031 10000元")
    assert not dialogue.last.get("approval"), dialogue.last
    assert "风险" in dialogue.last["reply"], dialogue.last
    assert main.ACCOUNTS[USER]["balance"] == 15000
    assert not main.WEALTH_HOLDINGS[USER]


def test_redeeming_more_than_holding_preserves_principal(dialogue):
    dialogue.assessed()
    dialogue.say("申购 CJ01002 100元")
    dialogue.pending("WEALTH_PURCHASE", target="CJ01002", amount=100)
    dialogue.decide()
    dialogue.say("赎回 CJ01002 150元")
    assert not dialogue.last.get("approval"), dialogue.last
    assert any(phrase in dialogue.last["reply"] for phrase in ("不足", "没有足够")), dialogue.last
    assert main.ACCOUNTS[USER]["balance"] == 14900
    assert sum(item["principal"] for item in main.WEALTH_HOLDINGS[USER]) == 100


def test_natural_card_application_is_confirmed_before_issuance(dialogue):
    dialogue.say("帮我看看我的卡")
    dialogue.completed("CARD_QUERY")
    assert len(main.CARDS[USER]) == 1
    dialogue.say("申请一张虚拟卡")
    dialogue.pending("CARD_APPLY")
    assert len(main.CARDS[USER]) == 1
    dialogue.decide()
    assert len(main.CARDS[USER]) == 2
    dialogue.say("再看看我的卡")
    dialogue.completed("CARD_QUERY")


def test_card_limit_clarifies_amount_instead_of_using_card_id(dialogue):
    dialogue.say("尾号3082的卡调额")
    assert not dialogue.last.get("approval"), dialogue.last
    assert dialogue.state.get("amount") is None
    dialogue.say("CARD-001调额")
    assert not dialogue.last.get("approval"), dialogue.last
    assert "额度" in dialogue.last["reply"]
    dialogue.say("5000元")
    dialogue.pending("CARD_LIMIT", target="CARD-001", amount=5000)
    dialogue.decide("reject")


def test_tail_number_selects_only_the_named_card(dialogue):
    second = {**main.CARDS[USER][0], "card_id": "CARD-002", "last4": "1111", "number_masked": "**** **** **** 1111"}
    main.CARDS[USER].append(second)
    dialogue.say("看看我的卡")
    dialogue.say("尾号1111的卡额度改到3000元")
    dialogue.pending("CARD_LIMIT", target="CARD-002", amount=3000)
    dialogue.decide()
    assert main.CARDS[USER][0]["daily_limit"] == 5000
    assert main.CARDS[USER][1]["daily_limit"] == 3000


def test_freeze_then_unlock_requires_two_fresh_approvals(dialogue):
    dialogue.say("冻结卡片 CARD-001")
    dialogue.pending("CARD_FREEZE", target="CARD-001")
    first_approval = dialogue.last["approval"]["approval_id"]
    dialogue.decide()
    assert main.CARDS[USER][0]["status"] == "FROZEN"
    dialogue.say("解锁卡片 CARD-001")
    dialogue.pending("CARD_UNFREEZE", target="CARD-001")
    assert dialogue.last["approval"]["approval_id"] != first_approval
    dialogue.decide()
    assert main.CARDS[USER][0]["status"] == "ACTIVE"


def test_lost_card_can_be_found_and_unreported(dialogue):
    dialogue.say("CARD-001丢了，帮我挂失")
    dialogue.pending("CARD_REPORT_LOST", target="CARD-001")
    dialogue.decide()
    assert main.CARDS[USER][0]["status"] == "LOST"
    dialogue.say("卡找到了，取消挂失 CARD-001")
    dialogue.pending("CARD_UNREPORT_LOST", target="CARD-001")
    dialogue.decide()
    assert main.CARDS[USER][0]["status"] == "ACTIVE"


def test_ambiguous_card_loss_accepts_card_id_on_followup(dialogue):
    main.CARDS[USER].append({**main.CARDS[USER][0], "card_id": "CARD-002", "last4": "1111"})
    dialogue.say("我的卡丢了")
    assert not dialogue.last.get("approval")
    assert "卡" in dialogue.last["reply"]
    dialogue.say("CARD-001")
    dialogue.pending("CARD_REPORT_LOST", target="CARD-001")
    dialogue.decide("reject")
    assert all(item["status"] == "ACTIVE" for item in main.CARDS[USER])


def test_zero_and_negative_card_limits_are_not_positive_mutations(dialogue):
    for text in ("CARD-001额度设为0元", "CARD-001额度设为-500元"):
        dialogue.say(text)
        assert not dialogue.last.get("approval"), dialogue.last
    assert main.CARDS[USER][0]["daily_limit"] == 5000


def test_unknown_card_never_falls_back_to_the_first_card(dialogue):
    dialogue.say("冻结卡片 CARD-404")
    assert not dialogue.last.get("approval")
    assert "不存在" in dialogue.last["reply"], dialogue.last
    assert main.CARDS[USER][0]["status"] == "ACTIVE"
    dialogue.say("查看卡片状态")
    dialogue.completed("CARD_QUERY")
    assert "CARD-001" in dialogue.last["reply"]
