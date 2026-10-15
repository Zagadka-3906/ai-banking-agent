import json
from types import SimpleNamespace as Obj
from unittest.mock import Mock

import pytest

from scripts.feishu_acceptance import AcceptanceSession, wait_for_access
import scripts.feishu_acceptance as acceptance
import scripts.diagnose_feishu_access as diagnostic
import feishu_access


def event(text="本轮验收", *, event_id="event-1", open_id="private-person", created=1000):
    return Obj(header=Obj(app_id="test-app", event_id=event_id), event=Obj(
        message=Obj(chat_id="private-chat", chat_type="p2p", message_type="text",
                    create_time=str(created * 1000), content=json.dumps({"text": text})),
        sender=Obj(sender_type="user", sender_id=Obj(open_id=open_id))))


def session(send=None):
    records = []
    send = send or Mock(return_value=True)
    return AcceptanceSession("test-app", send, records.append, clock=lambda: 1000), send, records


def test_text_and_card_have_distinct_delivery_ids_and_require_human_confirmation():
    check, send, records = session()
    check.handle(event())
    assert [call.args[1].msg_type for call in send.call_args_list] == ["text", "interactive"]
    assert send.call_args_list[0].args[2] != send.call_args_list[1].args[2]
    assert [item["phase"] for item in records] == ["delivery_text", "delivery_card"]
    assert check.summary()["both_delivered"] == 1 and not check.summary()["complete"]
    check.handle(event("验收通过", event_id="event-2", created=1001))
    assert check.summary()["complete"]
    assert "private-person" not in repr(records) and "private-chat" not in repr(records)
    assert "本轮验收" not in repr(records)
    check.handle(event("验收失败", event_id="event-3", created=1002))
    assert check.summary()["complete"]  # Duplicate verdicts cannot overwrite the recorded trial.


@pytest.mark.parametrize("outcomes", [[False, True, True], [True, False, True], [False, False, True]])
def test_human_claim_does_not_hide_a_failed_send(outcomes):
    check, _, _ = session(Mock(side_effect=outcomes))
    check.handle(event())
    check.handle(event("验收通过", event_id="event-2", created=1001))
    assert not check.summary()["complete"] and check.summary()["both_delivered"] == 0


def test_failed_human_rendering_is_retained():
    check, _, records = session()
    check.handle(event())
    check.handle(event("验收失败", event_id="event-2", created=1001))
    assert check.summary()["human_failed"] == 1 and not check.summary()["complete"]
    assert records[-1]["human_rendering_passed"] is False


def test_duplicate_event_and_another_participant_cannot_complete_trial():
    check, send, records = session()
    check.handle(event())
    check.handle(event())
    check.handle(event("验收通过", event_id="event-2", open_id="another-person"))
    assert send.call_count == 2 and len(records) == 2 and not check.summary()["complete"]


def test_new_trial_does_not_inherit_confirmation_and_rejects_older_verdict():
    check, _, _ = session()
    check.handle(event(created=990))
    check.handle(event("验收通过", event_id="event-2", created=991))
    assert check.summary()["complete"]
    check.handle(event(event_id="event-3", created=1000))
    check.handle(event("验收通过", event_id="event-4", created=992))
    assert check.summary()["human_passed"] == 0 and not check.summary()["complete"]


@pytest.mark.parametrize("alteration", ["wrong_app", "group", "bot", "stale", "future", "malformed", "nontext", "unrelated"])
def test_acceptance_ignores_unrelated_or_invalid_events(alteration):
    check, send, records = session()
    item = event()
    if alteration == "wrong_app":
        item.header.app_id = "another-app"
    elif alteration == "group":
        item.event.message.chat_type = "group"
    elif alteration == "bot":
        item.event.sender.sender_type = "app"
    elif alteration == "stale":
        item.event.message.create_time = "0"
    elif alteration == "future":
        item.event.message.create_time = "2000000"
    elif alteration == "malformed":
        item.event.message.content = "[]"
    elif alteration == "nontext":
        item.event.message.message_type = "interactive"
    else:
        item = event("查询余额")
    check.handle(item)
    assert not send.called and not records


def test_sdk_exception_details_never_enter_records():
    check, _, records = session(Mock(side_effect=RuntimeError("private-token and private-chat")))
    check.handle(event())
    assert all(record["transport_success"] is False for record in records)
    assert "private" not in repr(records)


def test_recovery_wait_is_bounded_and_continues_only_after_access_succeeds():
    now, reports, sleeps = [0], [], []
    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
    denied = {"ok": False, "kind": "platform_policy_denied", "code": 21001}
    success = {"ok": True, "kind": "available", "code": 0}
    probe = Mock(side_effect=[denied, success])
    assert wait_for_access(probe, reports.append, wait_seconds=60, poll_seconds=1,
                           clock=lambda: now[0], sleep=sleep) == success
    assert sleeps == [15] and reports == [denied, success]
    now[0] = 0
    reports.clear()
    sleeps.clear()
    assert wait_for_access(lambda: denied, reports.append, wait_seconds=20,
                           clock=lambda: now[0], sleep=sleep) == denied
    assert sleeps == [20] and len(reports) == 2


def test_missing_credentials_do_not_start_a_recovery_poll_loop():
    missing = {"ok": False, "kind": "not_configured"}
    sleep = Mock()
    assert wait_for_access(lambda: missing, lambda _: None, wait_seconds=3600, sleep=sleep) == missing
    assert not sleep.called


def test_denied_access_returns_nonzero_without_starting_listener(monkeypatch, tmp_path):
    monkeypatch.setattr(acceptance, "ROOT", tmp_path)
    folder = tmp_path / "build" / "acceptance"
    monkeypatch.setattr(acceptance.sys, "argv", ["acceptance", "--output-dir", str(folder)])
    monkeypatch.setattr(feishu_access, "probe_access", lambda: {"ok": False, "code": 21001, "kind": "platform_policy_denied"})
    assert acceptance.main() == 2
    assert json.loads((folder / "summary.json").read_text())["complete"] is False
    assert not (folder / "status.json").exists()
    # Existing evidence must not be overwritten by a second invocation.
    with pytest.raises(SystemExit, match="2"):
        acceptance.main()


def test_diagnostic_compares_routes_and_keeps_reports_out_of_source(monkeypatch, tmp_path):
    # The root is determined from __file__, rather than an untrusted CLI argument.
    monkeypatch.setattr(diagnostic, "__file__", str(tmp_path / "scripts" / "diagnose_feishu_access.py"))
    output = tmp_path / "build" / "report.json"
    monkeypatch.setattr(acceptance.sys, "argv", ["diagnose", "--compare-network", "--output", str(output)])
    probe = Mock(return_value={"ok": False, "code": 21001, "kind": "platform_policy_denied"})
    monkeypatch.setattr(feishu_access, "probe_access", probe)
    assert diagnostic.main() == 2
    assert probe.call_args_list[0].kwargs == {} and probe.call_args_list[1].kwargs == {"direct": True}
    report = json.loads(output.read_text())
    assert report["stage"] == "tenant_token" and report["direct"]["code"] == 21001
    outside = tmp_path / "source-report.json"
    monkeypatch.setattr(acceptance.sys, "argv", ["diagnose", "--output", str(outside)])
    with pytest.raises(SystemExit, match="2"):
        diagnostic.main()
    assert not outside.exists()
