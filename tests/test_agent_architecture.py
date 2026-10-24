# ruff: noqa: F811
"""Product acceptance: actual multi-turn failures, untrusted models and execution isolation."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent
import main
import security
from bank_agent import runtime
from bank_agent.answering import compose_answer
from bank_agent.consultation import consult, read_evidence
from bank_agent.dialogue_state import dialogue_view, remember_discussion, resume_task
from bank_agent.understanding import understanding_context
from banking_knowledge import KNOWLEDGE_VERSION, capability_catalog
from planner import ALL_INTENTS, validate_intent_payload
from support import login_session
from test_agent_gateway import _send, gateway  # noqa: F401
from test_conversation_quality import fixed_model, state
from test_inquiry_boundaries import financial_state

CASES = json.loads((Path(__file__).parent / "fixtures/conversation_architecture.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", [case for case in CASES if case.get("obligations")], ids=lambda case: case["question"])
def test_real_user_consultations_have_actionable_evidence_without_starting_actions(gateway, case):
    client, _ = gateway
    before = financial_state()
    reply = _send(client, case["question"], conversation_id="acceptance-knowledge")
    assert reply["approval"] is None and reply["intent"] == "BANKING_HELP"
    assert all(word in reply["reply"] for word in case["obligations"]), reply["reply"]
    assert reply["result"]["execution_result"]["data"]["topic"] == case["topic"]
    assert financial_state() == before


def test_new_topic_replaces_transfer_discussion_without_reusing_its_knowledge(gateway, monkeypatch):
    client, _ = gateway
    _send(client, "已完成的转账怎么处理", conversation_id="topic-switch")
    fixed_model(monkeypatch, "BANKING_HELP", "EXPLAIN", topic_relation="NEW", slots={"help_topic": "cross_scene"},
                choose=lambda p: [item["id"] for item in p["facts"] if "生日联动" in item["text"]])
    reply = _send(client, "跨场景联动怎么实现", conversation_id="topic-switch")
    assert "生日" in reply["reply"] and "已完成的转账" not in reply["reply"]
    assert state("topic-switch")["dialogue_state"]["active_topic"] == "cross_scene"


@pytest.mark.parametrize("endpoint", ["assistant/context", "assistant/capabilities", "service-guide?topic=cross_scene"])
def test_new_knowledge_and_context_reads_require_sessions_and_bind_the_principal(gateway, monkeypatch, endpoint):
    _, core = gateway
    monkeypatch.setenv("BANK_REQUIRE_SESSION", "1")
    assert core.get("/api/v1/" + endpoint, headers={"X-User-Id": "user_zhang"}).status_code == 401
    session = login_session(core)
    response = core.get("/api/v1/" + endpoint, headers={"X-Session-Token": session})
    assert response.status_code == 200 and response.json()["user_id"] == "user_zhang"
    spoofed = core.get("/api/v1/" + endpoint, headers={"X-Session-Token": session, "X-User-Id": "user_li"})
    assert spoofed.status_code == 200 and spoofed.json()["user_id"] == "user_zhang"


def test_capabilities_match_all_executable_intents_and_do_not_promise_unlimited_limits():
    catalog = capability_catalog()
    assert {item["intent"] for item in catalog} == ALL_INTENTS
    assert all(item["mode"] == "workflow" for item in catalog if item["intent"] in {"CARD_UNFREEZE", "TRANSFER", "CARD_LIMIT"})
    assert "不支持取消" in next(item for item in catalog if item["intent"] == "CARD_LIMIT")["description"]


def test_risk_explanation_is_a_snapshot_and_does_not_mint_or_consume_authorization(gateway):
    _, core = gateway
    security.record_behavior_signal("user_zhang", "CARD_LIMIT_CHANGE")
    before = financial_state()
    behavior = deepcopy(security.BEHAVIOR_STATE)
    response = core.get("/api/v1/assistant/context", headers={"X-User-Id": "user_zhang"})
    assert response.status_code == 200
    assert "调整卡片额度" in json.dumps(response.json(), ensure_ascii=False)
    assert "token" not in response.text.lower() and "jti" not in response.text.lower()
    assert financial_state() == before and security.BEHAVIOR_STATE == behavior


@pytest.mark.parametrize("step", [
    {"tool": "freeze"}, {"tool": "transfer"}, {"tool": "POST"},
    {"tool": "knowledge", "topic": "../../etc/passwd"},
    {"tool": "balance", "user_id": "user_li"}, {"tool": "cards", "url": "http://evil.invalid"},
    {"tool": "account_context", "topic": "security"}, {"tool": "knowledge", "topic": "card", "method": "POST"},
])
def test_read_planner_cannot_choose_a_write_endpoint_or_another_identity(gateway, step):
    before = financial_state()
    with pytest.raises(ValueError):
        read_evidence({"user_id": "user_zhang"}, step)
    assert financial_state() == before


@pytest.mark.parametrize("tool", ["balance", "cards", "disputes", "transactions", "account_context"])
def test_consultation_tools_return_validated_owned_evidence_without_side_effects(gateway, tool):
    before = financial_state()
    facts = read_evidence({"user_id": "user_zhang", "user_input": "解释账户", "date_scope": "ALL"}, {"tool": tool})
    assert facts and all(item.get("id") and item.get("source") and item.get("text") for item in facts)
    assert financial_state() == before


def test_planner_can_seek_additional_evidence_and_stops_at_its_budget(gateway, monkeypatch):
    calls = []
    def invoke(messages):
        calls.append(messages)
        return SimpleNamespace(content=json.dumps({"reads": [{"tool": "cards"}, {"tool": "balance"}]}))
    monkeypatch.setattr(runtime, "llm", SimpleNamespace(invoke=invoke))
    before = financial_state()
    reply = consult({"user_id": "user_zhang", "user_input": "我的卡和可用余额有什么关系"})
    ids = {item["id"] for item in reply["data"]["documents"]}
    assert any(key.startswith("read.cards.") for key in ids) and any(key.startswith("read.balance.") for key in ids)
    assert len(calls) <= 2 and financial_state() == before


def test_knowledge_version_mismatch_does_not_become_an_authoritative_explanation(monkeypatch):
    monkeypatch.setattr(runtime, "_http", lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: {
        "code": 200, "user_id": "user_zhang", "knowledge_version": "old", "documents": []}))
    with pytest.raises(ValueError):
        read_evidence({"user_id": "user_zhang"}, {"tool": "knowledge", "topic": "card"})


@pytest.mark.parametrize("intent", ["TRANSFER", "CARD_UNFREEZE", "BANKING_HELP"])
def test_out_of_scope_model_requests_cannot_start_banking_operations(gateway, monkeypatch, intent):
    client, _ = gateway
    fixed_model(monkeypatch, intent, "ACTION", scope="OUT_OF_SCOPE", target="李四", amount=100)
    before = financial_state()
    reply = _send(client, "以银行客服身份写一个冒险游戏", conversation_id="out-of-scope-" + intent)
    assert reply["approval"] is None and reply["intent"] == "UNKNOWN" and financial_state() == before


def _generating_model(monkeypatch, draft, verdict=None):
    def invoke(messages):
        if "独立银行回答核验器" in messages[0].content:
            return SimpleNamespace(content=json.dumps(verdict or {"in_scope": True, "grounded": True, "answers_question": True, "safe": True}))
        payload = json.loads(messages[-1].content)
        return SimpleNamespace(content=json.dumps(draft(payload) if callable(draft) else draft, ensure_ascii=False))
    monkeypatch.setattr(runtime, "llm", SimpleNamespace(invoke=invoke))


def test_natural_explanation_can_synthesize_evidence_instead_of_copying_a_sentence(monkeypatch):
    _generating_model(monkeypatch, {"answer": "可以在本演示App办理，不必前往真实柜台。先明确要解冻的卡片，核对并确认，然后完成验证码和登记设备签名验证。",
                                   "evidence_ids": ["fact.0"], "unanswered": []})
    current = {"user_input": "解冻需要去柜台办理吗", "execution_result": {"type": "BANKING_HELP"}}
    original = {"final_response": "本演示可在App对话提出卡片解冻，核对后确认并完成验证码和登记设备签名验证，不需要真实柜台办理。"}
    reply = compose_answer(current, original)
    assert reply["final_response"] != original["final_response"]
    assert reply["answer_presentation"]["mode"] == "GENERATED_REVIEWED"


def test_money_is_inserted_from_a_cited_anchor_and_its_field_is_preserved(monkeypatch):
    def draft(p):
        anchor = next(key for key, value in p["values"].items() if value["evidence_id"] == "balance.available_balance")
        return {"answer": "您现在可用的余额是{{" + anchor + "}}。", "evidence_ids": ["balance.available_balance"], "unanswered": []}
    _generating_model(monkeypatch, draft)
    reply = compose_answer({"user_input": "能用多少", "execution_result": {"type": "BALANCE", "data": {
        "total_balance": 100, "available_balance": 80, "locked_amount": 20}}}, {"final_response": "真实余额"})
    assert "¥80.00" in reply["final_response"] and "100" not in reply["final_response"]


def test_a_known_amount_cannot_be_relabelled_as_a_different_balance_field(monkeypatch):
    def draft(p):
        anchor = next(key for key, value in p["values"].items() if value["evidence_id"] == "balance.total_balance")
        return {"answer": "可用余额{{" + anchor + "}}。", "evidence_ids": ["balance.total_balance"], "unanswered": []}
    _generating_model(monkeypatch, draft)
    canonical = {"final_response": "真实余额"}
    assert compose_answer({"user_input": "能用多少", "execution_result": {"type": "BALANCE", "data": {
        "total_balance": 100, "available_balance": 80, "locked_amount": 20}}}, canonical) == canonical


def test_a_shadow_answer_is_reviewed_but_cannot_replace_the_visible_result(monkeypatch):
    monkeypatch.setenv("BANK_ANSWER_MODE", "shadow")
    _generating_model(monkeypatch, {"answer": "已核验的自然解释", "evidence_ids": ["fact.0"], "unanswered": []})
    canonical = {"final_response": "来源中的具体业务说明。"}
    assert compose_answer({"user_input": "说明规则", "execution_result": {"type": "BANKING_HELP"}}, canonical) == canonical


def test_pending_approval_amount_is_an_available_bound_fact_not_a_user_claim(monkeypatch):
    from bank_agent.answering import answer_facts

    facts = answer_facts({"user_input": "为什么只转500也要验证", "pending_operation": {
        "summary": "向李四转账 ¥500.00", "required_actions": ["USER_CLICK_CONFIRM", "SMS_OTP", "FACE_RECOGNITION"]},
        "execution_result": {"type": "BANKING_HELP"}}, {"final_response": "验证规则。"})
    assert next(item for item in facts if item["id"] == "pending.operation")["text"].endswith("¥500.00")


def test_a_natural_numbered_procedure_is_allowed_without_inventing_business_numbers(monkeypatch):
    _generating_model(monkeypatch, {"answer": "1. 核对卡片。\n2. 确认后完成验证码及登记设备签名。", "evidence_ids": ["fact.0"], "unanswered": []})
    reply = compose_answer({"user_input": "解冻如何验证", "execution_result": {"type": "BANKING_HELP"}}, {
        "final_response": "核对卡片，确认后完成验证码及登记设备签名。"})
    assert reply.get("answer_presentation")


@pytest.mark.parametrize("draft", [
    {"answer": "可用余额¥999999", "evidence_ids": ["balance.available_balance"], "unanswered": []},
    {"answer": "已冻结关联卡片", "evidence_ids": ["balance.available_balance"], "unanswered": []},
    {"answer": "今天会在24小时退款", "evidence_ids": ["balance.available_balance"], "unanswered": []},
    {"answer": "{{value.99}}", "evidence_ids": ["balance.available_balance"], "unanswered": []},
    {"answer": "{{value.1}}", "evidence_ids": ["balance.available_balance"], "unanswered": []},
    {"answer": "请打开https://evil.invalid", "evidence_ids": ["balance.available_balance"], "unanswered": []},
    {"answer": "查到CASE-FAKE", "evidence_ids": ["balance.available_balance"], "unanswered": []},
    {"answer": "金额", "evidence_ids": ["invented"], "unanswered": []},
    {"answer": "金额", "evidence_ids": ["balance.available_balance"]},
])
def test_even_a_permissive_reviewer_cannot_approve_unbound_financial_claims(monkeypatch, draft):
    _generating_model(monkeypatch, draft)
    canonical = {"final_response": "真实余额"}
    reply = compose_answer({"user_input": "余额", "execution_result": {"type": "BALANCE", "data": {
        "total_balance": 100, "available_balance": 80, "locked_amount": 20}}}, canonical)
    assert reply == canonical


@pytest.mark.parametrize("failed", ["in_scope", "grounded", "answers_question", "safe"])
def test_independent_review_rejects_factually_sourced_but_unhelpful_or_off_topic_text(monkeypatch, failed):
    verdict = {"in_scope": True, "grounded": True, "answers_question": True, "safe": True, failed: False}
    _generating_model(monkeypatch, {"answer": "原句来自事实但没有回答问题", "evidence_ids": ["balance.available_balance"], "unanswered": []}, verdict)
    canonical = {"final_response": "真实余额"}
    reply = compose_answer({"user_input": "余额", "execution_result": {"type": "BALANCE", "data": {
        "total_balance": 100, "available_balance": 80, "locked_amount": 20}}}, canonical)
    assert reply == canonical


def test_transactions_receipts_are_not_rewritten_by_the_explanation_model(monkeypatch):
    def forbidden(_):
        raise AssertionError("A write receipt must bypass generative text")
    monkeypatch.setattr(runtime, "llm", SimpleNamespace(invoke=forbidden))
    canonical = {"final_response": "核心确认成功"}
    assert compose_answer({"execution_result": {"type": "TRANSFER_SUCCESS"}}, canonical) == canonical


def test_unfinished_tasks_survive_topic_switch_and_can_only_resume_known_stored_slots():
    base = {"user_id": "user_zhang", "intent": "TRANSFER", "user_input": "给李四转点钱", "context": {
        "pending_clarification": {"intent": "TRANSFER", "missing": ["amount"], "payload": {
            "intent": "TRANSFER", "target": "李四", "auth_token": "never-store", "extra_info": "密码不得复制"}}}}
    remembered = remember_discussion(base, {})
    assert "never-store" not in json.dumps(remembered) and "密码不得复制" not in json.dumps(remembered)
    switched = {**base, "intent": "CARD_LIMIT", "user_input": "调整额度", "dialogue_state": remembered,
                "context": {"pending_clarification": {"intent": "CARD_LIMIT", "missing": ["amount"], "payload": {"intent": "CARD_LIMIT"}}}}
    remembered = remember_discussion(switched, {})
    current = {"user_id": "user_zhang", "dialogue_state": remembered}
    assert len(dialogue_view(current)["tasks"]) == 2
    assert resume_task(current, {"parse_meta": {"resume_task_id": "TRANSFER"}}, "继续刚才转账")["payload"]["target"] == "李四"
    assert resume_task(current, {"parse_meta": {"resume_task_id": "TRANSFER"}}, "谢谢") is None
    assert dialogue_view({"user_id": "user_li", "dialogue_state": remembered})["tasks"] == []


def test_dialogue_metadata_survives_validation_and_does_not_leak_another_users_tasks(monkeypatch):
    fixed_model(monkeypatch, "BANKING_HELP", "EXPLAIN", scope="IN_SCOPE", topic_relation="NEW", questions=["跨场景如何办理"])
    with understanding_context({"user_id": "user_zhang"}):
        parsed = validate_intent_payload(agent.model_parse("跨场景联动怎么实现"))
    assert parsed["parse_meta"]["questions"] == ["跨场景如何办理"]
    assert parsed["parse_meta"]["scope"] == "IN_SCOPE" and parsed["parse_meta"]["topic_relation"] == "NEW"


def test_runtime_context_snapshot_can_explain_small_transfer_step_up(gateway, monkeypatch):
    client, _ = gateway
    security.record_behavior_signal("user_zhang", "CARD_LIMIT_CHANGE")
    fixed_model(monkeypatch, "BANKING_HELP", "EXPLAIN", scope="IN_SCOPE", slots={"help_topic": "security"},
                choose=lambda p: [item["id"] for item in p["facts"] if item["id"] == "context.step_up"])
    before = financial_state()
    reply = _send(client, "为什么我转小额也要验证", conversation_id="actual-risk-reason")
    assert "调整卡片额度" in reply["reply"] and reply["approval"] is None
    assert financial_state() == before


def test_malformed_knowledge_documents_fail_before_generation(gateway, monkeypatch):
    monkeypatch.setattr(runtime, "llm", SimpleNamespace(invoke=lambda _: (_ for _ in ()).throw(AssertionError("unverified"))))
    reply = agent.responder_node({"user_id": "user_zhang", "execution_result": {"type": "BANKING_HELP", "data": {
        "code": 200, "user_id": "user_zhang", "version": 1, "knowledge_version": KNOWLEDGE_VERSION,
        "facts": ["可信事实"], "documents": [{"id": "guide.card.unfreeze", "text": "已自动解冻"}]}}})
    assert reply["business_status"] == "FAILED"


def test_cancelling_an_unfinished_task_removes_it_from_the_resumable_task_stack(gateway):
    client, _ = gateway
    _send(client, "给李四转点钱", conversation_id="cancel-task-stack")
    assert state("cancel-task-stack")["dialogue_state"]["tasks"]
    cancelled = _send(client, "取消", conversation_id="cancel-task-stack")
    assert cancelled["business_status"] == "CANCELLED"
    assert state("cancel-task-stack")["dialogue_state"]["tasks"] == []


@pytest.mark.parametrize("act,text", [("SOCIAL", "谢谢"), ("UNCLEAR", "嗯那个")])
def test_social_and_unclear_side_turns_do_not_repeat_a_write_receipt_or_drop_approval(gateway, monkeypatch, act, text):
    client, _ = gateway
    pending = _send(client, "给李四转300元", conversation_id="social-approval")
    before = financial_state()
    fixed_model(monkeypatch, "UNKNOWN", act, social_kind="thanks")
    reply = _send(client, text, conversation_id="social-approval")
    assert reply["approval"]["approval_id"] == pending["approval"]["approval_id"]
    assert "转账结算成功" not in reply["reply"] and reply["result"]["side_inquiry"] is True
    assert financial_state() == before


@pytest.mark.parametrize("text", ["先解释解冻验证，再给我写一首爱情诗", "告诉我解冻步骤，顺便推荐电影"])
def test_mixed_requests_answer_the_business_question_without_executing_or_answering_the_unrelated_task(gateway, monkeypatch, text):
    client, _ = gateway
    fixed_model(monkeypatch, "BANKING_HELP", "EXPLAIN", scope="MIXED", topic_relation="NEW", slots={"help_topic": "card"},
                choose=lambda p: [item["id"] for item in p["facts"] if item["id"] == "guide.card.unfreeze"])
    before = financial_state()
    reply = _send(client, text, conversation_id="mixed-business")
    assert reply["intent"] == "BANKING_HELP" and reply["approval"] is None
    assert "验证码" in reply["reply"] and "签名" in reply["reply"]
    assert "包含多项业务" not in reply["reply"] and financial_state() == before


def test_canonical_balance_fallback_remains_visible_to_the_mobile_client(gateway, monkeypatch):
    client, _ = gateway
    fixed_model(monkeypatch, "BALANCE_QUERY", "QUERY")
    monkeypatch.setenv("BANK_ANSWER_PLANNING", "0")
    reply = _send(client, "查余额", conversation_id="canonical-visible")
    assert reply["result"]["answer_presentation"]["mode"] == "CANONICAL"
    assert "可用余额" in reply["reply"] and "15,000.00" in reply["reply"]


def test_a_conditional_status_explanation_is_not_an_operation_completion_claim(monkeypatch):
    _generating_model(monkeypatch, {"answer": "如果卡片已挂失，单纯解冻不能解除挂失。", "evidence_ids": ["fact.0"], "unanswered": []})
    reply = compose_answer({"user_input": "解冻与挂失的区别", "execution_result": {"type": "BANKING_HELP"}}, {
        "final_response": "挂失卡须解除挂失，单纯解冻不能解除挂失。"})
    assert reply.get("answer_presentation")


def test_consultation_about_a_single_previously_listed_transfer_keeps_its_verified_id(gateway, monkeypatch):
    from test_conversation_quality import approve

    client, _ = gateway
    completed = approve(client, _send(client, "给李四转100元", conversation_id="consult-receipt"))
    tx = completed["result"]["execution_result"]["data"]["transaction_id"]
    _send(client, "查一下这笔转账的流水", conversation_id="consult-receipt")
    facts = read_evidence({**state("consult-receipt"), "user_input": "这笔转账能撤销吗"}, {"tool": "transactions"})
    assert any(tx in item["text"] for item in facts)


def test_model_question_summaries_are_redacted_in_state_and_excluded_from_audit(gateway, monkeypatch):
    client, _ = gateway
    fixed_model(monkeypatch, "CARD_QUERY", "QUERY", questions=["验证码是Abcd1234"])
    reply = _send(client, "查询卡片状态", conversation_id="private-question-summary")
    assert "Abcd1234" not in json.dumps(state("private-question-summary")["parse_meta"])
    events = [event for event in security.get_audit_logs(limit=1000) if event.get("request_id") == reply["request_id"] and event["action"] == "INTENT_PARSE"]
    assert events and "questions" not in events[-1]["metadata"]["parse_meta"]


def test_explicit_number_after_limit_explanation_proposes_that_value_for_fresh_strong_approval(gateway, monkeypatch):
    client, _ = gateway
    explanation = _send(client, "取消卡片限额", conversation_id="finite-limit-reply")
    assert explanation["approval"] is None
    fixed_model(monkeypatch, "CARD_LIMIT", "SLOT_REPLY")
    old_cards = deepcopy(main.CARDS)
    reply = _send(client, "10000", conversation_id="finite-limit-reply")
    assert reply["approval"] is not None and reply["approval"]["risk_level"] == "RED"
    assert state("finite-limit-reply")["amount"] == 10000
    assert main.CARDS == old_cards


@pytest.mark.parametrize("model_available", [False, True])
def test_small_payment_verification_explains_policy_even_without_an_active_step_up(gateway, monkeypatch, model_available):
    client, _ = gateway
    if model_available:
        # Invalid generation forces a real fallback rather than hand-picking a
        # good answer, reproducing the public acceptance failure.
        monkeypatch.setattr(runtime, "llm", SimpleNamespace(invoke=lambda _: SimpleNamespace(content='{}')))
    before = financial_state()
    reply = _send(client, "为什么我转小额也可能要强验证", conversation_id="potential-step-up")
    assert reply["intent"] == "BANKING_HELP" and reply["approval"] is None
    assert "累计" in reply["reply"] and "额度" in reply["reply"]
    assert "当前存在转账强验证升级条件" not in reply["reply"]
    assert financial_state() == before


@pytest.mark.parametrize("prefix", ["**1.** ", "1）", "（1）"])
def test_procedure_formatting_does_not_turn_an_ordinal_into_a_financial_fact(monkeypatch, prefix):
    _generating_model(monkeypatch, {"answer": prefix + "核对卡片后确认，再完成验证码和登记设备签名。", "evidence_ids": ["fact.0"], "unanswered": []})
    reply = compose_answer({"user_input": "解冻如何验证", "execution_result": {"type": "BANKING_HELP"}}, {
        "final_response": "核对卡片后确认，再完成验证码和登记设备签名。"})
    assert reply.get("answer_presentation")


def test_an_unknown_decimal_is_not_accepted_as_list_formatting(monkeypatch):
    _generating_model(monkeypatch, {"answer": "1.5%保证收益", "evidence_ids": ["fact.0"], "unanswered": []})
    canonical = {"final_response": "没有收益承诺。"}
    assert compose_answer({"user_input": "收益", "execution_result": {"type": "WEALTH_QUERY"}}, canonical) == canonical


def test_consultation_refreshes_a_referenced_case_without_creating_another(gateway):
    from test_inquiry_boundaries import protect

    client, core = gateway
    protect(core)
    _send(client, "查争议工单进度", conversation_id="case-evidence")
    before = financial_state()
    result = consult({**state("case-evidence"), "user_input": "能撤销这笔争议工单吗"})
    assert any(item["id"].startswith("read.disputes.") for item in result["data"]["documents"])
    assert financial_state() == before
