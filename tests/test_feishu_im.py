from __future__ import annotations

import json
import time
from types import SimpleNamespace as Obj
from unittest.mock import Mock

from fastapi.testclient import TestClient

import agent_api
import feishu_im
from conversation import ConversationError


def event(text: str, *, event_id: str = "evt-1", open_id: str = "ou-person", chat_type: str = "p2p", created_ms: int | None = None):
    return Obj(
        header=Obj(app_id="cli_test", event_id=event_id),
        event=Obj(
            sender=Obj(sender_id=Obj(open_id=open_id), sender_type="user"),
            message=Obj(chat_id="oc-private", message_id="om-1", chat_type=chat_type,
                        message_type="text", create_time=created_ms or int(time.time() * 1000),
                        content=json.dumps({"text": text}, ensure_ascii=False)),
        ),
    )


def test_binding_read_only_replay_and_unbind(monkeypatch):
    feishu_im.reset_state()
    monkeypatch.setattr(agent_api, "principal_for", lambda **_: "user_zhang")
    read = Mock()
    read.return_value.json.return_value = {"code": 200, "available_balance": 500.0,
                                           "total_balance": 600.0, "locked_amount": 100.0}
    monkeypatch.setattr(feishu_im.BankClient, "_request", read)
    with TestClient(agent_api.app) as client:
        assert client.post("/api/v1/im/feishu/binding-code", headers={"X-Session-Token": "session"}).status_code == 503
        monkeypatch.setenv("BANK_FEISHU_ENABLED", "1")
        monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
        assert "绑定码" in feishu_im.process_message(event("查余额"))[1]
        code = client.post("/api/v1/im/feishu/binding-code", headers={"X-Session-Token": "session"}).json()["binding_code"]
        assert "绑定成功" in feishu_im.process_message(event(f"/绑定 {code}", event_id="evt-2"))[1]
        result = feishu_im.process_message(event("查余额", event_id="evt-3"))
        assert result and "¥500.00" in result[1]
        assert read.call_args.args == ("GET", "accounts/user_zhang/balance")
        assert feishu_im.process_message(event("查余额", event_id="evt-3")) is None
        assert "Android" in feishu_im.process_message(event("转账 300 元", event_id="evt-4"))[1]
        assert read.call_count == 1
        assert all(call.args[0] == "GET" for call in read.call_args_list)
        assert client.delete("/api/v1/im/feishu/binding", headers={"X-Session-Token": "session"}).json()["removed"] == 1
        assert "绑定码" in feishu_im.process_message(event("查余额", event_id="evt-5"))[1]
    feishu_im.reset_state()


def test_rejects_group_wrong_app_stale_and_invalid_code(monkeypatch):
    feishu_im.reset_state()
    monkeypatch.setenv("BANK_FEISHU_ENABLED", "1")
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    assert feishu_im.process_message(event("查余额", chat_type="group")) is None
    wrong = event("查余额")
    wrong.header.app_id = "cli_other"
    assert feishu_im.process_message(wrong) is None
    assert feishu_im.process_message(event("查余额", created_ms=int((time.time() - 1200) * 1000))) is None
    assert "无效" in feishu_im.process_message(event("/绑定 invalid", event_id="evt-2"))[1]
    assert "已解除" in feishu_im.process_message(event("/解绑", event_id="evt-3"))[1]
    feishu_im.reset_state()


def test_sdk_outbound_text_request_has_dedup_uuid():
    response = Mock()
    response.success.return_value = True
    client = Obj(im=Obj(v1=Obj(message=Obj(create=Mock(return_value=response)))))
    assert feishu_im._send_text(client, "oc-private", "查询结果", "evt-1")
    request = client.im.v1.message.create.call_args.args[0]
    assert request.receive_id_type == "chat_id"
    assert request.request_body.receive_id == "oc-private"
    assert request.request_body.uuid == "evt-1"
    assert json.loads(request.request_body.content) == {"text": "查询结果"}


def test_binding_code_cannot_be_used_after_bank_logout(monkeypatch):
    feishu_im.reset_state()
    monkeypatch.setattr(agent_api, "principal_for", lambda **_: "user_zhang")
    with TestClient(agent_api.app) as client:
        monkeypatch.setenv("BANK_FEISHU_ENABLED", "1")
        monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
        code = client.post("/api/v1/im/feishu/binding-code", headers={"X-Session-Token": "session"}).json()["binding_code"]
        def revoked(**_):
            raise ConversationError("会话失效", 401, "INVALID_SESSION")
        monkeypatch.setattr(agent_api, "principal_for", revoked)
        result = feishu_im.process_message(event(f"/绑定 {code}", event_id="evt-revoked"))
        assert result and "失效" in result[1]
        assert not feishu_im._BINDINGS
    feishu_im.reset_state()


def test_official_sdk_dispatches_receive_v1_into_connector(monkeypatch):
    import lark_oapi as lark

    monkeypatch.setenv("BANK_FEISHU_ENABLED", "1")
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    feishu_im.reset_state()
    seen = []
    handler = (lark.EventDispatcherHandler.builder("", "")
               .register_p2_im_message_receive_v1(lambda received: seen.append(feishu_im.process_message(received)))
               .build())
    payload = {
        "schema": "2.0",
        "header": {"event_id": "sdk-event-1", "event_type": "im.message.receive_v1",
                   "create_time": str(int(time.time() * 1000)), "app_id": "cli_test", "tenant_key": "tenant"},
        "event": {"sender": {"sender_id": {"open_id": "ou-person"}, "sender_type": "user"},
                  "message": {"message_id": "om-1", "chat_id": "oc-private", "chat_type": "p2p",
                              "message_type": "text", "create_time": str(int(time.time() * 1000)),
                              "content": json.dumps({"text": "查余额"}, ensure_ascii=False)}},
    }
    handler._do_without_validation(json.dumps(payload).encode())
    assert seen and seen[0] and seen[0][0] == "oc-private" and "绑定码" in seen[0][1]
    feishu_im.reset_state()
