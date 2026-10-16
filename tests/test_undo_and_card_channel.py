"""Conversational rollback of reservations, and card payment-channel switches."""

from __future__ import annotations

import pytest

import main
import security
from bank_agent.parsing import local_parse
from bank_core.payments import charge_card_and_balance
from test_agent_gateway import _headers, _send, gateway  # noqa: F401

PRINCIPAL = "user_zhang"


def _approve(client, envelope):
    assert envelope["status"] == "APPROVAL_REQUIRED", envelope
    response = client.post(f"/api/v1/agent/approvals/{envelope['approval']['approval_id']}", json={"decision": "approve"}, headers=_headers())
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(("text", "kind"), [
    ("撤销刚才那笔预约", "SCHEDULED_TRANSFER"),
    ("取消明天给李四的预约转账", "SCHEDULED_TRANSFER"),
    ("撤回AA收款请求", "SPLIT_BILL"),
    ("取消生日锁定", "FUND_RESERVATION"),
    ("把锁定的1000元释放", "FUND_RESERVATION"),
    ("回退上一步", "LAST"),
    ("撤销刚才的转账", "SETTLED_TRANSFER"),
])
def test_rollback_phrases_are_not_read_as_new_actions(text, kind):
    # Previously each of these parsed as the very action it asks to undo.
    parsed = local_parse(text)
    assert parsed["intent"] == "UNDO_OPERATION" and parsed["slots"]["undo_kind"] == kind


@pytest.mark.parametrize(("chip", "kind"), [
    ("撤销这笔预约", "SCHEDULED_TRANSFER"),
    ("撤回这笔 AA 请求", "SPLIT_BILL"),
    ("取消生日锁定", "FUND_RESERVATION"),
])
def test_android_rollback_chips_parse_as_the_matching_undo(chip, kind):
    # The Android follow-up chips (UNDO_SUGGESTIONS in ChatViewModel.kt) send
    # these exact phrases; each must reach the rollback of the same kind.
    parsed = local_parse(chip)
    assert parsed["intent"] == "UNDO_OPERATION" and parsed["slots"]["undo_kind"] == kind


@pytest.mark.parametrize(("text", "channel", "enabled"), [
    ("关闭境外交易", "OVERSEAS", False), ("开通境外支付", "OVERSEAS", True),
    ("关闭网上支付", "ONLINE", False), ("尾号3082的卡禁止网购", "ONLINE", False), ("恢复网上支付", "ONLINE", True),
])
def test_channel_switch_phrases(text, channel, enabled):
    slots = local_parse(text)["slots"]
    assert local_parse(text)["intent"] == "CARD_CHANNEL" and slots == {"card_channel": channel, "channel_enabled": enabled}


@pytest.mark.parametrize("text", ["取消Apple订阅", "那笔境外消费不是我的", "给李四转300元", "解冻卡片"])
def test_existing_intents_are_unchanged(text):
    assert local_parse(text)["intent"] not in {"UNDO_OPERATION", "CARD_CHANNEL"}


def test_scheduled_transfer_is_undone_with_a_yellow_confirmation(gateway):  # noqa: F811
    client, core = gateway
    _approve(client, _send(client, "明天上午十点给李四转100元", conversation_id="undo-schedule"))
    schedule = main.SCHEDULED_TRANSFERS[-1]
    assert schedule["status"] == "SCHEDULED"
    pending = _send(client, "撤销刚才那笔预约", conversation_id="undo-schedule")
    assert pending["risk_level"] == "YELLOW"
    # The card describes Core's own record, not the user's paraphrase.
    assert "撤销预约转账" in pending["approval"]["summary"] and "李四" in pending["approval"]["summary"]
    done = _approve(client, pending)
    assert "已撤销这笔预约转账" in done["reply"] and "已完成的资金操作不受影响" in done["reply"]
    assert schedule["status"] == "CANCELLED"
    assert main.ACCOUNTS[PRINCIPAL]["balance"] == 15000.0
    again = _send(client, "撤销刚才那笔预约", conversation_id="undo-schedule-2")
    assert again["approval"] is None and "当前没有可撤销的预约转账" in again["reply"]


def test_birthday_lock_is_released_with_its_reminder_and_order_drafts(gateway):  # noqa: F811
    client, core = gateway
    _approve(client, _send(client, "我爱人生日是2099-10-03，锁定1000元，提前两天提醒买花和蛋糕", conversation_id="undo-lock"))
    assert main.ACCOUNTS[PRINCIPAL]["locked_amount"] == 1000
    pending = _send(client, "取消生日锁定", conversation_id="undo-lock")
    assert "释放 ¥1,000.00" in pending["approval"]["summary"]
    done = _approve(client, pending)
    assert "已解除资金锁定" in done["reply"]
    assert main.ACCOUNTS[PRINCIPAL]["locked_amount"] == 0
    statuses = {task.task_type: task.status for task in main.TASK_STORE.list(owner_id=PRINCIPAL) if task.task_type in {"REMINDER", "ORDER_DRAFT"}}
    assert statuses == {"REMINDER": "CANCELLED", "ORDER_DRAFT": "CANCELLED"}


def test_settled_transfer_is_explained_not_reversed(gateway):  # noqa: F811
    client, _ = gateway
    _approve(client, _send(client, "给李四转 300 元", conversation_id="undo-settled"))
    balance = main.ACCOUNTS[PRINCIPAL]["balance"]
    reply = _send(client, "撤销刚才的转账", conversation_id="undo-settled")
    assert reply["approval"] is None and "已完成的转账无法撤销" in reply["reply"]
    assert main.ACCOUNTS[PRINCIPAL]["balance"] == balance


def test_paid_aa_and_handed_over_tasks_are_not_undoable(gateway):  # noqa: F811
    client, core = gateway
    _approve(client, _send(client, "和李四、王美AA 90元聚餐", conversation_id="undo-aa"))
    split = main.SPLIT_REQUESTS[-1]
    split["collected_amount"] = 30.0  # one participant already paid
    listed = core.get("/api/v1/tasks/undoable", headers=_headers()).json()["data"]
    assert all(row["task_type"] != "SPLIT_BILL" for row in listed)
    reply = _send(client, "撤回AA收款请求", conversation_id="undo-aa")
    assert reply["approval"] is None and "当前没有可撤销" in reply["reply"]
    # A direct precheck is refused by Core as well, whatever the channel says.
    task_id = next(task.task_id for task in main.TASK_STORE.list(owner_id=PRINCIPAL) if task.task_type == "SPLIT_BILL")
    pre = core.post("/api/v1/risk/precheck/operation", json={"user_id": PRINCIPAL, "operation": "UNDO_TASK", "target": task_id}, headers=_headers())
    assert pre.status_code == 409 and pre.json()["detail"]["code"] == "UNDO_TOO_LATE"


def test_closing_a_channel_is_yellow_opening_it_is_red(gateway):  # noqa: F811
    client, core = gateway
    pending = _send(client, "关闭境外交易", conversation_id="channel")
    assert pending["risk_level"] == "YELLOW" and "关闭卡片 CARD-001 的境外交易" in pending["approval"]["summary"]
    done = _approve(client, pending)
    assert "已关闭卡片 CARD-001 的境外交易" in done["reply"]
    card = main.CARDS[PRINCIPAL][0]
    assert card["channels"] == {"ONLINE": True, "OVERSEAS": False}
    listing = _send(client, "我的卡片", conversation_id="channel-list")
    assert "已关闭 境外交易" in listing["reply"]
    reopen = _send(client, "开通境外交易", conversation_id="channel-reopen")
    assert reopen["risk_level"] == "RED" and reopen["approval"]["mfa_required"] is True


def test_channel_token_cannot_be_replayed_for_the_other_direction(gateway):  # noqa: F811
    _, core = gateway
    body = {"user_id": PRINCIPAL, "operation": "CARD_CHANNEL", "card_id": "CARD-001", "channel": "ONLINE", "enable": False}
    token = core.post("/api/v1/risk/precheck/operation", json=body, headers=_headers()).json()["auth_token"]
    core.post("/api/v1/auth/confirm", json={}, headers={**_headers(), "X-Auth-Token": token})
    replay = core.post("/api/v1/operations/execute", json={**body, "enable": True}, headers={**_headers(), "X-Auth-Token": token})
    assert replay.status_code == 400 and replay.json()["detail"]["code"] == "TOKEN_CONTEXT_MISMATCH"
    assert main.CARDS[PRINCIPAL][0].get("channels") is None


def test_closed_online_channel_refuses_merchant_order_payment():
    main.reset_state()
    main.CARDS[PRINCIPAL][0]["channels"] = {"ONLINE": False, "OVERSEAS": True}
    with pytest.raises(security.SecurityError) as refused:
        charge_card_and_balance(PRINCIPAL, 49.0, channel="ONLINE")
    assert refused.value.code == "CARD_CHANNEL_DISABLED" and "网上支付" in refused.value.detail
    # Transfers are not card-channel payments and still go through.
    assert charge_card_and_balance(PRINCIPAL, 49.0)["card_id"] == "CARD-001"
    main.reset_state()
