"""Contact book: per-user payee management behind the transfer allow-list.

Adding a contact (name, phone, relationship, birthday) must make that contact
a first-class payee: a transfer "给 <联系人> 转 300 元" resolves through the
book exactly like a built-in contact.  A phone that already belongs to a demo
account links to it; anything else gets a zero-balance shadow payee account
whose password digest is unusable, so enrollment can never become a login.
"""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402


@pytest.fixture()
def client():
    main.reset_state()
    yield TestClient(main.app)
    main.reset_state()


def _add(client, payload, user_id="user_zhang"):
    return client.post(
        f"/api/v1/contacts/{user_id}/book",
        json=payload,
        headers={"X-User-Id": user_id},
    )


def _transfer(client, target, amount=300, user_id="user_zhang"):
    headers = {"X-User-Id": user_id}
    precheck = client.post(
        "/api/v1/risk/precheck",
        json={"from_user": user_id, "target_name_or_id": target, "amount": amount},
        headers=headers,
    )
    assert precheck.status_code == 200, precheck.text
    token = precheck.json()["auth_token"]
    confirmed = client.post("/api/v1/auth/confirm", json={}, headers={**headers, "X-Auth-Token": token})
    assert confirmed.status_code == 200, confirmed.text
    return client.post(
        "/api/v1/transfers/execute",
        json={"from_user": user_id, "target_name_or_id": target, "amount": amount},
        headers={**headers, "X-Auth-Token": token},
    )


def test_add_list_and_remove_a_contact(client):
    added = _add(client, {"name": "王五", "phone": "13700011122", "relationship": "朋友", "birthday": "1995-06-18"})
    assert added.status_code == 200, added.text
    row = added.json()["data"]
    assert row["name"] == "王五"
    assert row["relationship"] == "FRIEND"
    assert row["relationship_label"] == "朋友"
    assert row["birthday"] == "1995-06-18"
    assert row["linked_existing"] is False
    assert row["account_id"]

    listed = client.get("/api/v1/contacts/user_zhang/book", headers={"X-User-Id": "user_zhang"})
    assert listed.status_code == 200
    assert [item["contact_id"] for item in listed.json()["data"]] == [row["contact_id"]]

    removed = client.post(
        f"/api/v1/contacts/user_zhang/book/{row['contact_id']}/remove",
        headers={"X-User-Id": "user_zhang"},
    )
    assert removed.status_code == 200
    listed = client.get("/api/v1/contacts/user_zhang/book", headers={"X-User-Id": "user_zhang"})
    assert listed.json()["data"] == []


def test_a_contact_by_phone_links_to_the_existing_account(client):
    added = _add(client, {"name": "老李", "phone": "13900139000", "relationship": "同事"})
    assert added.status_code == 200, added.text
    assert added.json()["data"]["linked_existing"] is True
    assert added.json()["data"]["account_id"] == "user_li"

    # The saved name is now a first-class payee, and the money lands in 李四's account.
    before = main.ACCOUNTS["user_li"]["balance"]
    settled = _transfer(client, "老李")
    assert settled.status_code == 200, settled.text
    assert main.ACCOUNTS["user_li"]["balance"] == before + 300


def test_a_new_contact_gets_a_transferable_shadow_account(client):
    added = _add(client, {"name": "王五", "phone": "13700011122", "relationship": "家人", "birthday": "1995-06-18"})
    account_id = added.json()["data"]["account_id"]

    # Shadow account exists with zero balance...
    assert main.ACCOUNTS[account_id]["balance"] == 0.0
    # ...and can never be logged into.
    login = client.post("/api/v1/auth/login", json={"user_id": account_id, "password": "anything-at-all"})
    assert login.status_code == 401

    settled = _transfer(client, "王五", amount=120)
    assert settled.status_code == 200, settled.text
    assert main.ACCOUNTS[account_id]["balance"] == 120.0

    # By phone works too.
    settled = _transfer(client, "13700011122", amount=30)
    assert settled.status_code == 200, settled.text
    assert main.ACCOUNTS[account_id]["balance"] == 150.0


def test_duplicate_contact_is_refused(client):
    payload = {"name": "王五", "phone": "13700011122", "relationship": "朋友"}
    assert _add(client, payload).status_code == 200
    duplicate = _add(client, payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "CONTACT_EXISTS"


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"name": "", "phone": "13700011122", "relationship": "朋友"}, "CONTACT_NAME_REQUIRED"),
        ({"name": "王五", "phone": "not-a-phone", "relationship": "朋友"}, "INVALID_CONTACT_PHONE"),
        ({"name": "王五", "phone": "13700011122", "relationship": "老板"}, "INVALID_RELATIONSHIP"),
        ({"name": "王五", "phone": "13700011122", "relationship": "朋友", "birthday": "1995/06/18"}, "INVALID_BIRTHDAY"),
        ({"name": "王五", "phone": "13700011122", "relationship": "朋友", "birthday": "2995-06-18"}, "INVALID_BIRTHDAY"),
    ],
)
def test_invalid_contact_fields_are_refused(client, payload, code):
    response = _add(client, payload)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == code
    assert main.USER_CONTACTS.get("user_zhang", []) == []


def test_contact_writes_require_a_presented_identity(client):
    response = client.post(
        "/api/v1/contacts/user_zhang/book",
        json={"name": "王五", "phone": "13700011122", "relationship": "朋友"},
    )
    assert response.status_code == 401
    assert main.USER_CONTACTS.get("user_zhang", []) == []


def test_one_user_cannot_read_or_mutate_another_users_book(client):
    assert _add(client, {"name": "王五", "phone": "13700011122", "relationship": "朋友"}).status_code == 200

    # user_li sees an empty book...
    listed = client.get("/api/v1/contacts/user_li/book", headers={"X-User-Id": "user_li"})
    assert listed.json()["data"] == []
    # ...and cannot delete user_zhang's contact even knowing the id.
    contact_id = main.USER_CONTACTS["user_zhang"][0]["contact_id"]
    stolen = client.post(f"/api/v1/contacts/user_li/book/{contact_id}/remove", headers={"X-User-Id": "user_li"})
    assert stolen.status_code == 404
    assert len(main.USER_CONTACTS["user_zhang"]) == 1


def test_the_book_survives_a_restart(tmp_path, monkeypatch):
    store = tmp_path / "ledger.json"
    monkeypatch.setenv("BANK_LEDGER_STORE_PATH", str(store))
    main.load_ledger_store()
    client = TestClient(main.app)
    main.reset_state()

    assert _add(client, {"name": "王五", "phone": "13700011122", "relationship": "朋友", "birthday": "1995-06-18"}).status_code == 200

    main.reset_state()  # back to the seed ledger, like a restart
    summary = main.load_ledger_store()
    assert summary["loaded_state"] is True
    rows = main.USER_CONTACTS["user_zhang"]
    assert rows[0]["name"] == "王五"
    # The provisioned shadow payee account came back with it.
    assert main.ACCOUNTS[rows[0]["account_id"]]["balance"] == 0.0
