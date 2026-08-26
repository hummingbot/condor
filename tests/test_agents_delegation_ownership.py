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
from condor.agents import delegation_history as history_module
from condor.agents.delegate import DelegateTask
from condor.web.models import WebUser
from condor.web.routes.agents import (
    get_delegation_events,
    get_delegation_status,
    list_delegation_history,
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


# ── The same gate on records read from disk (FEAT-035) ──


def _on_disk(monkeypatch, *records):
    """Pretend these delegations are on disk and nothing is in the registry.

    The stub honours the scope the routes now pass, because that scope *is* the
    guard (FEAT-051): ``user_id=None`` is the admin read, anything else opens
    one person's directory and can only ever see what is in it.
    """
    by_id = {r["task_id"]: r for r in records}

    def visible(user_id):
        if user_id is None:
            return list(by_id.values())
        return [r for r in by_id.values() if r.get("user_id") == user_id]

    monkeypatch.setattr(
        history_module, "list_history", lambda *, user_id=None, **kw: visible(user_id)
    )
    monkeypatch.setattr(
        history_module,
        "read_history",
        lambda user_id, tid: next(
            (r for r in visible(user_id) if r["task_id"] == tid), None
        ),
    )
    monkeypatch.setattr(
        history_module, "read_history_events", lambda user_id, tid: ([], "# md")
    )


def _record(task_id: str, user_id: int, status: str = "done") -> dict:
    return {
        "task_id": task_id,
        "agent": "scout",
        "user_id": user_id,
        "status": status,
        "task": "an old task",
        "result": "an old answer",
        "error": "",
        "started_at": 1.0,
    }


def test_history_shows_only_own_records(monkeypatch):
    _on_disk(
        monkeypatch, _record("h-owner", OWNER.id), _record("h-stranger", STRANGER.id)
    )
    delegate_module._delegations.clear()

    assert _ids(asyncio.run(list_delegation_history(user=OWNER))) == {"h-owner"}
    assert _ids(asyncio.run(list_delegation_history(user=ADMIN))) == {
        "h-owner",
        "h-stranger",
    }


def test_history_hides_unowned_records_from_everyone_but_admin(monkeypatch):
    """A transcript from before user ids were recorded belongs to nobody."""
    _on_disk(monkeypatch, _record("h-orphan", 0))
    delegate_module._delegations.clear()

    assert _ids(asyncio.run(list_delegation_history(user=OWNER))) == set()
    assert _ids(asyncio.run(list_delegation_history(user=ADMIN))) == {"h-orphan"}
    # 404, not 403: it is outside every user directory, so a scoped read cannot
    # name it at all. Unreachable is a stronger answer than refused.
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_delegation_status("h-orphan", user=OWNER))
    assert exc.value.status_code == 404


def test_history_rows_carry_no_result_body(monkeypatch):
    """A hundred rows must not ship a hundred answers — the sheet fetches it."""
    _on_disk(monkeypatch, _record("h-owner", OWNER.id))
    delegate_module._delegations.clear()

    row = asyncio.run(list_delegation_history(user=OWNER))["delegations"][0]
    assert "result" not in row and "error" not in row
    assert asyncio.run(get_delegation_status("h-owner", user=OWNER))["result"] == (
        "an old answer"
    )


def test_a_live_task_shadows_its_own_disk_copy(monkeypatch):
    """The registry is the authority for anything still running here."""
    _on_disk(monkeypatch, _record("t-owner", OWNER.id, status="running"))

    rows = asyncio.run(list_delegation_history(user=OWNER))["delegations"]
    assert [r["task_id"] for r in rows] == ["t-owner"]
    assert rows[0]["task"] == "owner's task"  # the live record, not the disk one


def test_detail_and_events_fall_back_to_disk(monkeypatch):
    _on_disk(monkeypatch, _record("h-owner", OWNER.id))
    delegate_module._delegations.clear()

    assert asyncio.run(get_delegation_status("h-owner", user=OWNER))["status"] == "done"
    payload = asyncio.run(get_delegation_events("h-owner", user=OWNER))
    assert payload["events"] == [] and payload["markdown"] == "# md"


def test_stopping_a_finished_task_answers_honestly(monkeypatch):
    """Not 404 — the task existed; it just isn't ours to cancel any more."""
    _on_disk(monkeypatch, _record("h-owner", OWNER.id))
    delegate_module._delegations.clear()

    assert asyncio.run(stop_delegation_route("h-owner", user=OWNER)) == {
        "stopped": False
    }
