"""Durable approval registry: a pending approval survives a gateway restart.

The conversation checkpointer already survives a restart, but the approval
handle that pauses on it used to live only in the process: after a gateway
recreate the client kept an ``approval_id`` the new process had never heard
of.  These tests pin the file-backed registry:

* a registered approval is persisted and restored (including the nonce it
  resolves to, so the resumed turn can still settle);
* a resolved approval is *not* resurrected - a consumed handle must not look
  pending again after a reload;
* a file that fails its integrity digest is refused and writes are frozen.
"""

import json

import pytest

import conversation


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    path = tmp_path / "approvals.json"
    monkeypatch.setenv("BANK_APPROVAL_STORE_PATH", str(path))
    conversation.SERVICE.reset()
    yield path
    # reset() also unfreezes writes, mirroring reset_security_state.
    conversation.SERVICE.reset()


def _register(service, principal="user_zhang"):
    return service._register_approval(
        principal=principal,
        thread_id=f"app:{principal}:c1",
        conversation_id="c1",
        payload={
            "request_id": "req-1",
            "risk_level": "YELLOW",
            "required_actions": ["USER_CLICK_CONFIRM"],
            "message": "确认转账",
            "auth_token": "token-abc",
        },
    )


def test_a_pending_approval_survives_a_restart(clean_state):
    service = conversation.ConversationService()
    approval = _register(service)
    assert clean_state.exists()

    successor = conversation.ConversationService()  # what a restarted process builds
    restored = successor._require_approval("user_zhang", approval.approval_id)
    assert restored.auth_token == "token-abc"
    assert restored.risk_level == "YELLOW"
    assert restored.required_actions == ["USER_CLICK_CONFIRM"]
    assert successor._pending_approval("app:user_zhang:c1") is not None


def test_a_resolved_approval_is_not_resurrected(clean_state):
    service = conversation.ConversationService()
    approval = _register(service)
    service._mark_resolved(approval)

    successor = conversation.ConversationService()
    with pytest.raises(conversation.ConversationError):
        successor._require_approval("user_zhang", approval.approval_id)


def test_an_approval_is_only_resolvable_by_its_principal(clean_state):
    service = conversation.ConversationService()
    approval = _register(service)

    successor = conversation.ConversationService()
    with pytest.raises(conversation.ConversationError) as exc_info:
        successor._require_approval("user_li", approval.approval_id)
    assert exc_info.value.code == "APPROVAL_PRINCIPAL_MISMATCH"


def test_a_tampered_registry_is_refused_and_writes_frozen(clean_state):
    service = conversation.ConversationService()
    approval = _register(service)
    payload = json.loads(clean_state.read_text(encoding="utf-8"))
    payload["approvals"][approval.approval_id]["auth_token"] = "forged-nonce"
    clean_state.write_text(json.dumps(payload), encoding="utf-8")

    successor = conversation.ConversationService()
    assert successor._approvals == {}
    assert conversation.approval_store_status()["writes_frozen"] is True

    # The evidence is not overwritten by the next approval either.
    before = clean_state.read_text(encoding="utf-8")
    _register(successor)
    assert clean_state.read_text(encoding="utf-8") == before
