# ruff: noqa: F811  # imported pytest fixtures are injected by parameter name
"""Human transfer conversations against the real graph and in-process core.

The imported gateway fixture redirects every core request into TestClient and
uses an in-memory checkpointer. No running service or developer ledger is used.
"""

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

import conversation
import main
import security
from support import login_session, sign_face_attestation
import agent
from bank_tools import BankAPIError
from test_agent_gateway import gateway, _headers, _send  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_gateway_rate_limit(monkeypatch):
    import agent_api

    monkeypatch.setattr(agent_api, "RATE_LIMIT_STATE", {})


def _state(conversation_id):
    thread = conversation.conversation_thread_id("user_zhang", conversation_id)
    return conversation.get_graph().get_state({"configurable": {"thread_id": thread}}).values


def _ask(client, text, cid, **kwargs):
    return _send(client, text, conversation_id=cid, **kwargs)


def _approve(client, envelope):
    assert envelope["status"] == "APPROVAL_REQUIRED", envelope
    response = client.post(
        f"/api/v1/agent/approvals/{envelope['approval']['approval_id']}",
        json={"decision": "approve"}, headers=_headers(),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _clarifies(envelope, cid, word=None):
    assert envelope["approval"] is None, envelope
    assert _state(cid).get("clarification_required"), envelope
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000.0
    if word:
        assert word in envelope["reply"], envelope


def _old_approval_refused(client, approval_id):
    replay = client.post(f"/api/v1/agent/approvals/{approval_id}", json={"decision": "approve"}, headers=_headers())
    assert replay.status_code >= 400, replay.text


def test_name_then_phone_are_the_same_real_recipient(gateway):
    client, _ = gateway
    before = main.ACCOUNTS["user_li"]["balance"]
    for text in ("麻烦给李四转50块", "再给13900139000转20元"):
        result = _approve(client, _ask(client, text, "human-name-phone"))
        assert result["result"]["execution_result"]["type"] == "TRANSFER_SUCCESS"
    assert main.ACCOUNTS["user_li"]["balance"] == before + 70
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14930


def test_saved_contact_remark_name_is_a_payee(gateway):
    client, core = gateway
    added = core.post("/api/v1/contacts/user_zhang/book", json={"name": "老李", "phone": "13900139000", "relationship": "同事"}, headers=_headers())
    assert added.status_code == 200, added.text
    first = _ask(client, "给通讯录里的老李转账", "human-saved-name")
    _clarifies(first, "human-saved-name", "金额")
    result = _approve(client, _ask(client, "二十元", "human-saved-name"))
    assert result["result"]["execution_result"]["data"]["transferred_amount"] == 20


def test_phone_number_is_never_guessed_as_an_amount(gateway):
    client, _ = gateway
    cid = "human-phone-not-amount"
    first = _ask(client, "给13900139000转账", cid)
    _clarifies(first, cid, "金额")
    assert _state(cid)["amount"] is None
    _approve(client, _ask(client, "五十", cid))
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14950


def test_vague_amount_stays_pending_until_exact_amount(gateway):
    client, _ = gateway
    cid = "human-vague-amount"
    _clarifies(_ask(client, "给李四转账", cid), cid, "金额")
    _clarifies(_ask(client, "大概一百多元吧", cid), cid, "金额")
    _approve(client, _ask(client, "那就八十元", cid))
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14920


@pytest.mark.parametrize("vague_amount", ["两三百", "一两百", "100多", "一百多"])
def test_vague_bare_amount_requires_an_exact_followup(gateway, vague_amount):
    client, _ = gateway
    cid = "human-vague-bare-amount"
    _clarifies(_ask(client, "给李四转账", cid), cid, "金额")
    _clarifies(_ask(client, vague_amount, cid), cid, "金额")
    assert _state(cid)["amount"] is None
    assert not security.AUTH_TOKENS, "an imprecise amount must not reach risk precheck"
    explicit = _ask(client, "80", cid)
    assert explicit["status"] == "APPROVAL_REQUIRED", explicit
    assert _state(cid)["amount"] == 80
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000
    _approve(client, explicit)
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14920


def test_colloquial_transfer_with_a_vague_amount_asks_then_resumes(gateway):
    client, _ = gateway
    cid = "human-transfer-a-few-hundred"
    first = _ask(client, "给李四转个几百块", cid)
    _clarifies(first, cid, "金额")
    assert _state(cid)["intent"] == "TRANSFER"
    assert _state(cid)["target"] == "李四"
    assert _state(cid)["amount"] is None
    assert not security.AUTH_TOKENS
    _approve(client, _ask(client, "80元", cid))
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14920


def test_overseas_question_is_a_read_only_analysis_without_a_fraud_claim(gateway):
    client, _ = gateway
    response = _ask(client, "那笔境外消费怎么回事", "human-overseas-question")
    assert response["approval"] is None, response
    assert response["intent"] == "BILL_ANALYSIS", response
    assert response["result"]["execution_result"]["type"] == "BILL_ANALYSIS"
    assert all(card["status"] == "ACTIVE" for card in main.CARDS["user_zhang"])
    assert not main.DISPUTE_CASES


def test_product_comparison_accepts_ids_next_to_chinese_text(gateway):
    client, _ = gateway
    cid = "human-adjacent-product-ids"
    response = _ask(client, "帮我比较CJ01002和AY90204", cid)
    assert response["approval"] is None, response
    assert response["intent"] == "WEALTH_COMPARE", response
    assert _state(cid)["product_ids"] == ["CJ01002", "AY90204"]
    assert response["result"]["execution_result"]["type"] == "WEALTH_COMPARE"


@pytest.mark.parametrize("text", [
    "比较CJ010020和AY90204", "比较XCJ01002和AY90204", "比较CJ01002A和AY90204",
])
def test_known_product_id_is_not_extracted_from_a_longer_identifier(text):
    parsed = agent.local_parse(text)
    assert parsed["product_ids"] == ["AY90204"]
    assert parsed["target"] != "CJ01002"


def test_missing_recipient_can_be_answered_with_only_phone(gateway):
    client, _ = gateway
    cid = "human-target-slot"
    _clarifies(_ask(client, "帮我转账50元", cid), cid, "收款人")
    _approve(client, _ask(client, "13900139000", cid))
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14950


def test_new_transfer_without_reference_does_not_reuse_last_payee(gateway):
    client, _ = gateway
    cid = "human-no-invented-payee"
    _approve(client, _ask(client, "给李四转10元", cid))
    second = _ask(client, "帮我转账20元", cid)
    assert second["approval"] is None, second
    assert "收款人" in second["reply"]
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14990


def test_explicit_pronoun_reuses_payee_but_requires_new_approval(gateway):
    client, _ = gateway
    cid = "human-pronoun"
    first = _ask(client, "给李四转10元", cid)
    _approve(client, first)
    second = _ask(client, "再给他转20元", cid)
    assert second["status"] == "APPROVAL_REQUIRED", second
    assert second["approval"]["approval_id"] != first["approval"]["approval_id"]
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14990
    _approve(client, second)
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14970


def test_transfer_note_survives_amount_clarification(gateway):
    client, _ = gateway
    cid = "human-preserve-note"
    _clarifies(_ask(client, "给李四转账，备注午饭钱", cid), cid, "金额")
    _approve(client, _ask(client, "50元", cid))
    rows = [row for row in main.TRANSACTIONS if row.get("account_id") == "user_zhang" and row.get("category") == "转账支出"]
    assert "午饭钱" in rows[-1]["remark"], rows[-1]


def test_correction_replaces_pending_amount_and_invalidates_old_approval(gateway):
    client, _ = gateway
    cid = "human-correct-amount"
    first = _ask(client, "给李四转100元", cid)
    second = _ask(client, "不对，改成50元", cid)
    assert second["status"] == "APPROVAL_REQUIRED", second
    assert second["approval"]["approval_id"] != first["approval"]["approval_id"], second
    assert _state(cid)["amount"] == 50
    _old_approval_refused(client, first["approval"]["approval_id"])
    _approve(client, second)
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14950


def test_correction_changes_only_recipient(gateway):
    client, _ = gateway
    cid = "human-correct-recipient"
    first = _ask(client, "给李四转50元", cid)
    li_before = main.ACCOUNTS["user_li"]["balance"]
    second = _ask(client, "收款人换成王美", cid)
    assert second["status"] == "APPROVAL_REQUIRED", second
    assert second["approval"]["approval_id"] != first["approval"]["approval_id"], second
    assert _state(cid)["target"] == "王美"
    assert _state(cid)["amount"] == 50
    _old_approval_refused(client, first["approval"]["approval_id"])
    _approve(client, second)
    assert main.ACCOUNTS["user_li"]["balance"] == li_before
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14950


def test_natural_cancel_removes_pending_approval_without_debit(gateway):
    client, core = gateway
    cid = "human-cancel"
    first = _ask(client, "给李四转50元", cid)
    old_token = _state(cid)["auth_token"]
    cancelled = _ask(client, "算了，这笔不转了", cid)
    assert cancelled["approval"] is None, cancelled
    assert "取消" in cancelled["reply"] or "终止" in cancelled["reply"], cancelled
    _old_approval_refused(client, first["approval"]["approval_id"])
    replay = core.post("/api/v1/auth/confirm", json={}, headers={**_headers(), "X-Auth-Token": old_token})
    assert replay.status_code >= 400, replay.text
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000


def test_cancelled_clarification_cannot_resurrect_from_bare_amount(gateway):
    client, _ = gateway
    cid = "human-cancel-missing"
    _clarifies(_ask(client, "给李四转账", cid), cid, "金额")
    cancelled = _ask(client, "算了，不转了", cid)
    assert cancelled["approval"] is None
    later = _ask(client, "50元", cid)
    assert later["approval"] is None, later
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000


def test_repeated_delivery_and_approval_do_not_double_debit(gateway):
    client, _ = gateway
    cid = "human-replay"
    first = _ask(client, "给李四转50元", cid, message_id="same-message")
    same = _ask(client, "给李四转50元", cid, message_id="same-message")
    assert first["approval"]["approval_id"] == same["approval"]["approval_id"]
    _approve(client, first)
    _old_approval_refused(client, first["approval"]["approval_id"])
    _ask(client, "给李四转50元", cid, message_id="same-message")
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14950


def test_tomorrow_afternoon_is_scheduled_and_keeps_explicit_time(gateway):
    client, _ = gateway
    cid = "human-tomorrow"
    first = _ask(client, "明天下午三点给李四转50元", cid)
    assert _state(cid)["intent"] == "SCHEDULED_TRANSFER", first
    # “明天” is the business day in Beijing time, whatever the runner's zone is.
    beijing = timezone(timedelta(hours=8))
    scheduled = datetime.fromisoformat(_state(cid)["scheduled_at"]).astimezone(beijing)
    assert scheduled.date() == (datetime.now(beijing) + timedelta(days=1)).date()
    assert scheduled.hour == 15
    result = _approve(client, first)
    assert result["result"]["execution_result"]["type"] == "SCHEDULED_TRANSFER"
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000


def test_schedule_date_answer_keeps_target_amount_and_note(gateway):
    client, _ = gateway
    cid = "human-schedule-slot"
    _clarifies(_ask(client, "预约给李四转账50元，备注房租", cid), cid, "日期")
    second = _ask(client, "2099-10-03 下午三点", cid)
    assert _state(cid)["intent"] == "SCHEDULED_TRANSFER", second
    assert datetime.fromisoformat(_state(cid)["scheduled_at"]).hour == 15
    assert "房租" in _state(cid)["extra_info"]
    result = _approve(client, second)
    assert result["result"]["execution_result"]["type"] == "SCHEDULED_TRANSFER"
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000


def test_aa_keeps_both_named_participants_and_counts_initiator(gateway):
    client, _ = gateway
    cid = "human-aa-names"
    first = _ask(client, "和李四、王美 AA 分摊300元", cid)
    assert set(_state(cid)["participants"]) == {"李四", "王美"}, first
    result = _approve(client, first)
    data = result["result"]["execution_result"]["data"]["data"]
    assert data["participants_total"] == 2
    assert data["per_person"] == 100
    assert data["initiator_share"] == 100
    assert "当前尚未收款" in result["reply"]
    assert "人均 ¥100.00" in result["reply"]


def test_aa_headcount_alone_requires_names_then_keeps_total(gateway):
    client, _ = gateway
    cid = "human-aa-headcount"
    _clarifies(_ask(client, "三个人吃饭一共300元，帮我AA", cid), cid, "参与人")
    second = _ask(client, "我和李四、王美", cid)
    assert second["status"] == "APPROVAL_REQUIRED", second
    assert _state(cid)["amount"] == 300
    assert set(_state(cid)["participants"]) == {"李四", "王美"}
    data = _approve(client, second)["result"]["execution_result"]["data"]["data"]
    assert data["per_person"] == 100


def test_repeat_with_explicit_new_amount_does_not_restore_old_amount(gateway):
    client, _ = gateway
    cid = "human-repeat-modified"
    _approve(client, _ask(client, "给李四转10元", cid))
    second = _ask(client, "再转一次，不过这次20元", cid)
    assert second["status"] == "APPROVAL_REQUIRED", second
    assert _state(cid)["amount"] == 20, second
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14990
    _approve(client, second)
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14970


# The 18 dialogue cases above are supplemented with boundary checks for the
# cancellation primitive needed to make corrections safe on a real channel.


def test_cancel_nonce_is_subject_bound_idempotent_and_invalidates_mfa(gateway):
    _, core = gateway
    pre = core.post("/api/v1/risk/precheck", json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 2000}, headers=_headers())
    assert pre.status_code == 200, pre.text
    token = pre.json()["auth_token"]
    auth_headers = {**_headers(), "X-Auth-Token": token}
    challenge = core.post("/api/v1/risk/mfa/face/challenge", json={"auth_token": token}, headers=auth_headers)
    assert challenge.status_code == 200, challenge.text
    challenge_id = challenge.json()["challenge_id"]
    attestation = sign_face_attestation(challenge_id, user_id="user_zhang", token_jti=challenge.json()["auth_token_jti"])
    mfa = core.post("/api/v1/risk/mfa/verify", json={"otp": "888888", "face_attestation": attestation}, headers=auth_headers)
    assert mfa.status_code == 200, mfa.text
    proof = mfa.json()["mfa_proof"]
    wrong = core.post("/api/v1/risk/cancel", json={}, headers={"X-User-Id": "user_li", "X-Auth-Token": token})
    assert wrong.status_code == 403, wrong.text
    assert security.AUTH_TOKENS[token]["used"] is False
    for _ in range(2):
        cancelled = core.post("/api/v1/risk/cancel", json={}, headers=auth_headers)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["cancelled"] is True
    assert security.AUTH_TOKENS[token]["used"] is True
    assert security.MFA_PROOFS[proof]["used"] is True
    assert security.FACE_CHALLENGES[challenge_id]["used"] is True
    assert core.post("/api/v1/risk/confirm", json={}, headers=auth_headers).status_code >= 400
    assert core.post("/api/v1/risk/mfa/verify", json={"otp": "888888", "face_attestation": attestation}, headers=auth_headers).status_code >= 400
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000


def test_cancel_nonce_requires_its_original_session(gateway):
    _, core = gateway
    sessions = [login_session(core) for _ in range(2)]
    pre = core.post("/api/v1/risk/precheck", json={"from_user": "user_zhang", "target_name_or_id": "李四", "amount": 20}, headers={**_headers(), "X-Session-Token": sessions[0]})
    assert pre.status_code == 200, pre.text
    token = pre.json()["auth_token"]
    for session in (None, sessions[1]):
        headers = {**_headers(), "X-Auth-Token": token}
        if session:
            headers["X-Session-Token"] = session
        refused = core.post("/api/v1/risk/cancel", json={}, headers=headers)
        assert refused.status_code == 401, refused.text
        assert security.AUTH_TOKENS[token]["used"] is False
    ok = core.post("/api/v1/risk/cancel", json={}, headers={**_headers(), "X-Auth-Token": token, "X-Session-Token": sessions[0]})
    assert ok.status_code == 200, ok.text


def test_failed_core_cancel_does_not_replace_or_consume_approval(gateway, monkeypatch):
    client, _ = gateway
    cid = "human-cancel-core-failure"
    first = _ask(client, "给李四转50元", cid)
    old_token = _state(cid)["auth_token"]
    factory = conversation.SERVICE._client_factory

    def unavailable(principal, session_token=None):
        value = factory(principal, session_token)

        def cancel(*_args, **_kwargs):
            raise BankAPIError("暂时不可用", status_code=503, payload={"detail": {"code": "UNAVAILABLE", "message": "暂时不可用"}})

        value.cancel = cancel
        return value

    monkeypatch.setattr(conversation.SERVICE, "_client_factory", unavailable)
    response = client.post("/api/v1/agent/messages", json={"conversation_id": cid, "text": "改成20元"}, headers=_headers())
    assert response.status_code >= 400, response.text
    approval = conversation.SERVICE._approvals[first["approval"]["approval_id"]]
    assert not approval.resolved
    assert security.AUTH_TOKENS[old_token]["used"] is False
    assert _state(cid)["amount"] == 50
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000


def test_streamed_correction_then_cancellation_uses_the_same_safe_path(gateway):
    client, _ = gateway
    cid = "human-stream-control"
    first = _ask(client, "给李四转50元", cid)

    def stream(text, message_id):
        events = list(conversation.SERVICE.start_turn_stream(
            principal="user_zhang", conversation_id=cid, message_id=message_id, text=text,
            session_token=_headers()["X-Session-Token"],
        ))
        terminals = [item for item in events if item["event"] in {"result", "approval", "error"}]
        assert len(terminals) == 1, json.dumps(events, ensure_ascii=False)
        assert terminals[0]["event"] != "error", terminals[0]
        return terminals[0]["data"]

    second = stream("改成20元", "stream-correct")
    assert second["status"] == "APPROVAL_REQUIRED", second
    assert _state(cid)["amount"] == 20
    _old_approval_refused(client, first["approval"]["approval_id"])
    result = stream("取消这笔转账", "stream-cancel")
    assert result["approval"] is None, result
    _old_approval_refused(client, second["approval"]["approval_id"])
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000


@pytest.mark.parametrize("text", [
    "给13900139000转账", "调额CARD-001", "预约2099-10-03给李四转账",
    "给李四转-50元", "给李四转账负五十元", "给李四转0元",
    "给李四转一两百元", "给李四转100多元", "给李四转账100到200元",
    "给李四转1.234元", "给李四转账大概100元",
    "给李四转 - 50 元", "尾号1111的卡调额", "给李四转至少50元",
])
def test_identifier_negative_or_imprecise_values_cannot_be_money(text):
    assert agent.amount_from_text(text) is None


@pytest.mark.parametrize(("text", "intent", "amount"), [
    ("银行卡尾号3082限额改为5000元", "CARD_LIMIT", 5000),
    ("买点日日盈，投200块", "WEALTH_PURCHASE", 200),
])
def test_shared_money_parser_preserves_explicit_card_and_product_actions(text, intent, amount):
    parsed = agent.local_parse(text)
    assert parsed["intent"] == intent
    assert parsed["amount"] == amount


def test_simultaneous_duplicate_correction_creates_only_one_new_approval(gateway):
    client, _ = gateway
    cid = "human-concurrent-correction"
    first = _ask(client, "给李四转50元", cid)
    rendezvous = threading.Barrier(2)

    def revise():
        # Synchronize callers before entering the service. Cache reads now
        # share the conversation lock with approval resolution, so waiting
        # for both callers inside a cache lookup would deadlock the test.
        rendezvous.wait(timeout=5)
        return conversation.SERVICE.start_turn(
            principal="user_zhang", conversation_id=cid,
            message_id="same-edit", text="改成20元",
            session_token=_headers()["X-Session-Token"],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: revise(), range(2)))
    assert results[0]["status"] == "APPROVAL_REQUIRED", results
    assert results[0]["approval"]["approval_id"] == results[1]["approval"]["approval_id"]
    assert results[0]["approval"]["approval_id"] != first["approval"]["approval_id"]
    active = [item for item in conversation.SERVICE._approvals.values() if not item.resolved]
    assert len(active) == 1
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000


def test_vague_correction_revokes_old_approval_and_waits_for_real_replacement(gateway):
    client, core = gateway
    cid = "human-vague-correction"
    first = _ask(client, "给李四转50元", cid)
    token = _state(cid)["auth_token"]
    vague = _ask(client, "不对改一下", cid)
    _clarifies(vague, cid)
    _old_approval_refused(client, first["approval"]["approval_id"])
    assert core.post("/api/v1/risk/confirm", json={}, headers={**_headers(), "X-Auth-Token": token}).status_code >= 400
    explicit = _ask(client, "金额改成20元", cid)
    assert explicit["status"] == "APPROVAL_REQUIRED", explicit
    assert _state(cid)["amount"] == 20
    _approve(client, explicit)
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14980


def test_same_session_correction_wins_lock_before_old_confirmation(gateway, monkeypatch):
    _, core = gateway
    cid = "human-session-edit-race"
    session = login_session(core)
    service = conversation.SERVICE
    first = service.start_turn(principal="user_zhang", conversation_id=cid, message_id="initial", text="给李四转50元", session_token=session)
    factory = service._client_factory
    cancel_entered, allow_cancel = threading.Event(), threading.Event()

    def gated_factory(principal, session_token=None):
        client = factory(principal, session_token)
        real_cancel = client.cancel

        def cancel(*args, **kwargs):
            cancel_entered.set()
            assert allow_cancel.wait(timeout=5), "cancellation gate timed out"
            return real_cancel(*args, **kwargs)

        client.cancel = cancel
        return client

    monkeypatch.setattr(service, "_client_factory", gated_factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        revision = pool.submit(service.start_turn, principal="user_zhang", conversation_id=cid, message_id="edit", text="改成20元", session_token=session)
        assert cancel_entered.wait(timeout=5)
        old_confirmation = pool.submit(service.resolve_approval, principal="user_zhang", approval_id=first["approval"]["approval_id"], decision="approve", session_token=session)
        allow_cancel.set()
        revised = revision.result(timeout=10)
        with pytest.raises(conversation.ConversationError) as refused:
            old_confirmation.result(timeout=10)
    assert refused.value.code == "APPROVAL_ALREADY_RESOLVED"
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000
    assert revised["approval"]["approval_id"] != first["approval"]["approval_id"]
    service.resolve_approval(principal="user_zhang", approval_id=revised["approval"]["approval_id"], decision="approve", session_token=session)
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14980


def test_measure_word_amount_reaches_the_approval_card(gateway):
    client, _ = gateway
    cid = "human-measure-word-amount"
    envelope = _ask(client, "再转一笔300给李四", cid)
    assert envelope["status"] == "APPROVAL_REQUIRED", envelope
    assert _state(cid)["amount"] == 300
    assert _state(cid)["target"] == "李四"
    _approve(client, envelope)
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14700


def test_second_active_card_must_be_named_by_the_payer(gateway):
    client, _ = gateway
    cid = "human-multi-card-tail"
    main.CARDS["user_zhang"].append({
        **main.CARDS["user_zhang"][0], "card_id": "CARD-002",
        "last4": "1111", "number_masked": "**** **** **** 1111",
    })
    _clarifies(_ask(client, "给李四转500元", cid), cid, "尾号")
    envelope = _ask(client, "用尾号3082的卡", cid)
    assert envelope["status"] == "APPROVAL_REQUIRED", envelope
    assert _state(cid)["target"] == "李四"
    assert _state(cid)["amount"] == 500
    _approve(client, envelope)
    assert main.ACCOUNTS["user_zhang"]["balance"] == 14500
    assert main.CARD_DAILY_SPENT.get("CARD-001") == 500
    assert not main.CARD_DAILY_SPENT.get("CARD-002")


def test_card_tail_in_a_full_restatement_keeps_payee_and_note(gateway):
    client, _ = gateway
    cid = "human-multi-card-restate"
    main.CARDS["user_zhang"].append({
        **main.CARDS["user_zhang"][0], "card_id": "CARD-002",
        "last4": "1111", "number_masked": "**** **** **** 1111",
    })
    _clarifies(_ask(client, "给李四转500元，备注学费", cid), cid, "尾号")
    envelope = _ask(client, "用尾号3082的卡给李四转500元，备注学费", cid)
    assert envelope["status"] == "APPROVAL_REQUIRED", envelope
    assert _state(cid)["target"] == "李四"
    assert "学费" in str(_state(cid)["extra_info"])
    result = _approve(client, envelope)
    assert result["result"]["execution_result"]["type"] == "TRANSFER_SUCCESS"
    assert main.CARD_DAILY_SPENT.get("CARD-001") == 500


def test_unknown_card_tail_never_picks_a_card_for_the_payer(gateway):
    client, _ = gateway
    cid = "human-unknown-tail"
    main.CARDS["user_zhang"].append({
        **main.CARDS["user_zhang"][0], "card_id": "CARD-002",
        "last4": "1111", "number_masked": "**** **** **** 1111",
    })
    envelope = _ask(client, "用尾号9999的卡给李四转500元", cid)
    _clarifies(envelope, cid, "9999")
    assert main.ACCOUNTS["user_zhang"]["balance"] == 15000
