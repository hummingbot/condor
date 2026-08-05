"""Tests for the conversations REST surface (FEAT-015).

A transcript is the most personal thing the runtime stores, so the property
that matters most here is not the CRUD — it is that the store is partitioned by
owner and that naming someone else is an admin-only act.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.conversations as routes
from condor.runtime import conversations
from condor.runtime.conversations import TurnEntry, append_turn, new_conversation
from condor.web.auth import get_current_user
from condor.web.models import WebUser

USER = WebUser(id=111, username="u", first_name="U", role="user")
ADMIN = WebUser(id=999, username="a", first_name="A", role="admin")
OTHER_ID = 222


class FakeConfigManager:
    def __init__(self, admins):
        self._admins = admins

    def is_admin(self, user_id):
        return user_id in self._admins


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A throwaway conversation root, plus a real router to drive it."""
    monkeypatch.setattr(conversations, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(
        routes, "get_config_manager", lambda: FakeConfigManager({ADMIN.id})
    )
    return tmp_path


def _client(user: WebUser) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _seed(user_id: int, text: str = "hello"):
    meta = new_conversation(user_id, "web", agent_key="claude-code")
    append_turn(user_id, meta.id, TurnEntry(role="user", text=text))
    append_turn(user_id, meta.id, TurnEntry(role="assistant", text="hi back"))
    return meta


# ── Reading ──


def test_list_returns_only_your_own(store):
    mine = _seed(USER.id)
    _seed(OTHER_ID)

    body = _client(USER).get("/conversations").json()
    assert [c["id"] for c in body] == [mine.id]
    assert body[0]["title"] == "hello"
    assert body[0]["turn_count"] == 2


def test_get_returns_meta_and_transcript(store):
    meta = _seed(USER.id, "what is my pnl?")

    body = _client(USER).get(f"/conversations/{meta.id}").json()
    assert body["meta"]["id"] == meta.id
    assert [t["role"] for t in body["turns"]] == ["user", "assistant"]
    assert body["turns"][0]["text"] == "what is my pnl?"


def test_unknown_conversation_is_404(store):
    assert _client(USER).get("/conversations/nope").status_code == 404


def test_malformed_id_is_400_not_a_traversal(store):
    resp = _client(USER).get("/conversations/..%2F..%2Fetc")
    assert resp.status_code in (400, 404)


# ── Ownership ──


def test_another_users_conversation_is_403(store):
    _seed(OTHER_ID)
    resp = _client(USER).get(f"/conversations?user_id={OTHER_ID}")
    assert resp.status_code == 403


def test_an_admin_can_read_another_users_conversation(store):
    meta = _seed(OTHER_ID, "their private chat")

    body = _client(ADMIN).get(f"/conversations/{meta.id}?user_id={OTHER_ID}").json()
    assert body["turns"][0]["text"] == "their private chat"


def test_a_conversation_is_invisible_under_the_wrong_owner(store):
    """Even without the query param, the store is partitioned by owner."""
    meta = _seed(OTHER_ID)
    assert _client(USER).get(f"/conversations/{meta.id}").status_code == 404


def test_deleting_another_users_conversation_is_403(store):
    meta = _seed(OTHER_ID)
    resp = _client(USER).delete(f"/conversations/{meta.id}?user_id={OTHER_ID}")
    assert resp.status_code == 403
    assert conversations.get_conversation(OTHER_ID, meta.id) is not None


# ── Writing ──


def test_rename(store):
    meta = _seed(USER.id)
    body = (
        _client(USER)
        .patch(f"/conversations/{meta.id}", json={"title": "Funding arb"})
        .json()
    )

    assert body["title"] == "Funding arb"
    assert conversations.get_conversation(USER.id, meta.id).title == "Funding arb"


def test_delete_is_the_only_way_to_lose_a_transcript(store):
    meta = _seed(USER.id)

    body = _client(USER).delete(f"/conversations/{meta.id}").json()
    assert body["deleted"] is True
    assert conversations.get_conversation(USER.id, meta.id) is None
    assert _client(USER).get("/conversations").json() == []
