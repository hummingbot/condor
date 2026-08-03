"""Ownership gate on the delegation REST routes (SEC-081).

The four ``/agents/delegations*`` routes authenticated but never authorized, so
any approved user could list, read the full reasoning transcript of, and stop
another user's delegation. The gate follows the idiom already used by
``sessions.py`` (``_require_ownership``) and ``conversations.py`` (``_owner``):
admins see everything, everyone else only their own, 403 on a foreign record.
"""

import asyncio

import pytest
from fastapi import HTTPException

import config_manager
from condor.agents import delegate as delegate_module
from condor.agents.delegate import DelegateTask
from condor.web.models import WebUser
from condor.web.routes.agents import (
    get_delegation_events,
    get_delegation_status,
    list_delegations,
    stop_delegation_route,
)

OWNER = WebUser(id=1, role="user")
STRANGER = WebUser(id=2, role="user")
ADMIN = WebUser(id=99, role="admin")


class _FakeConfigManager:
    def is_admin(self, user_id: int) -> bool:
        return user_id == ADMIN.id


@pytest.fixture(autouse=True)
def _registry_and_admin(monkeypatch):
    """Two delegations owned by different users, plus a known admin id."""
    monkeypatch.setattr(config_manager, "get_config_manager", _FakeConfigManager)
    delegate_module._delegations.clear()
    delegate_module._delegations["t-owner"] = DelegateTask(
        task_id="t-owner",
        agent_slug="scout",
        user_id=OWNER.id,
        chat_id=OWNER.id,
        server_name=None,
        task="owner's task",
        events=[{"type": "thought", "text": "secret reasoning"}],
    )
    delegate_module._delegations["t-stranger"] = DelegateTask(
        task_id="t-stranger",
        agent_slug="scout",
        user_id=STRANGER.id,
        chat_id=STRANGER.id,
        server_name=None,
        task="stranger's task",
    )
    yield
    delegate_module._delegations.clear()


def _ids(payload) -> set[str]:
    return {d["task_id"] for d in payload["delegations"]}


# ── GET /agents/delegations ──


def test_list_shows_only_own_delegations():
    assert _ids(asyncio.run(list_delegations(user=OWNER))) == {"t-owner"}
    assert _ids(asyncio.run(list_delegations(user=STRANGER))) == {"t-stranger"}


def test_list_shows_everything_to_admin():
    assert _ids(asyncio.run(list_delegations(user=ADMIN))) == {"t-owner", "t-stranger"}


# ── GET /agents/delegations/{task_id} ──


def test_status_rejects_non_owner():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_delegation_status("t-owner", user=STRANGER))
    assert exc.value.status_code == 403


def test_status_allows_owner_and_admin():
    for caller in (OWNER, ADMIN):
        payload = asyncio.run(get_delegation_status("t-owner", user=caller))
        assert payload["task_id"] == "t-owner"


def test_status_still_404s_on_unknown_task():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_delegation_status("nope-delegate-x", user=OWNER))
    assert exc.value.status_code == 404


# ── GET /agents/delegations/{task_id}/events ──


def test_events_rejects_non_owner():
    """The transcript is the sensitive payload: tool inputs/outputs, balances, orders."""
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_delegation_events("t-owner", user=STRANGER))
    assert exc.value.status_code == 403


def test_events_allows_owner_and_admin():
    for caller in (OWNER, ADMIN):
        payload = asyncio.run(get_delegation_events("t-owner", user=caller))
        assert payload["task_id"] == "t-owner"
        assert payload["events"][0]["text"] == "secret reasoning"


# ── POST /agents/delegations/{task_id}/stop ──


def test_stop_rejects_non_owner_and_leaves_task_running():
    """The 403 must land before the cancel: a rejected call cancels nothing."""

    async def scenario():
        dt = delegate_module._delegations["t-owner"]
        dt._task = asyncio.create_task(asyncio.sleep(3600))
        try:
            with pytest.raises(HTTPException) as exc:
                await stop_delegation_route("t-owner", user=STRANGER)
            assert exc.value.status_code == 403
            assert dt.status == "running"
            assert not dt._task.cancelled()
        finally:
            dt._task.cancel()

    asyncio.run(scenario())


def test_stop_allows_owner_and_admin():
    async def scenario(caller):
        dt = delegate_module._delegations["t-owner"]
        dt.status = "running"
        dt._task = asyncio.create_task(asyncio.sleep(3600))
        result = await stop_delegation_route("t-owner", user=caller)
        assert result == {"stopped": True}
        assert dt.status == "stopped"

    asyncio.run(scenario(OWNER))
    asyncio.run(scenario(ADMIN))
