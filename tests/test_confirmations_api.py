"""The REST view of a pending approval (FEAT-010, READ-597).

The WS ``permission_request`` event is a fire-and-forget push, so a reload
mid-approval leaves the agent waiting behind a page that shows no prompt. This
module is the read that fixes that, and the dashboard calls it on every socket
open — which makes two things load-bearing here: it must list only *your*
pending approvals, and each one must say which conversation asked, so a click
cannot authorize a different agent's tool call.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.runtime.confirmations as confirmations_module
import condor.web.routes.confirmations as routes
from condor.runtime.confirmations import ConfirmationRegistry
from condor.web.auth import get_current_user
from condor.web.models import WebUser

USER = WebUser(id=111, username="u", first_name="U", role="user")
OTHER_ID = 222


@pytest.fixture
def registry(monkeypatch):
    """A throwaway process-global registry for one test."""
    fresh = ConfirmationRegistry()
    monkeypatch.setattr(confirmations_module, "_registry", fresh)
    return fresh


def _client(user: WebUser = USER) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _register(registry, user_id: int, summary: str, slot: str = "s1"):
    return registry.register(
        session_key=f"web:{user_id}:{slot}",
        user_id=user_id,
        summary=summary,
        tool_call={"name": "place_order"},
        options=[],
    )


def test_list_carries_the_slot_that_asked(registry):
    pending = _register(registry, USER.id, "Place a 1 SOL order?", slot="chat-2")

    body = _client().get("/confirmations").json()

    assert [p["id"] for p in body] == [pending.id]
    assert body[0]["slot_id"] == "chat-2"
    assert body[0]["summary"] == "Place a 1 SOL order?"


def test_list_is_scoped_to_the_asking_user(registry):
    mine = _register(registry, USER.id, "mine")
    _register(registry, OTHER_ID, "not mine")

    body = _client().get("/confirmations").json()

    assert [p["id"] for p in body] == [mine.id]


def test_a_non_canonical_session_key_is_listed_unaddressed(registry):
    """A Telegram-raised approval has no slot; it is still answerable."""
    registry.register(
        session_key="not-a-canonical-key",
        user_id=USER.id,
        summary="from somewhere else",
        tool_call={},
        options=[],
    )

    body = _client().get("/confirmations").json()

    assert body[0]["slot_id"] == ""


def test_an_answered_approval_leaves_the_list(registry):
    pending = _register(registry, USER.id, "mine")
    client = _client()

    assert len(client.get("/confirmations").json()) == 1

    client.post(f"/confirmations/{pending.id}/resolve", json={"approved": True})

    assert client.get("/confirmations").json() == []
