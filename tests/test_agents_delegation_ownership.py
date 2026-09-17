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

import condor.code_runs as code_runs_module
import config_manager
from condor.agents import delegate as delegate_module
from condor.agents import delegation_history as history_module
from condor.agents.delegate import DelegateTask
from condor.web.models import WebUser
from condor.web.routes import code as code_routes
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

    def get_user_preference(self, user_id: int, key: str, default=None):
        # Nobody here holds the `code_run` grant, so `_may_run_code` answers
        # True for the admin alone — the only caller that reaches the code-run
        # source these tests stub out below.
        return default


class _EmptyCodeRunStore:
    """No snippet was ever run: the third source of the history list is empty."""

    def list(self, **kwargs):
        return []


@pytest.fixture(autouse=True)
def _registry_and_admin(monkeypatch):
    """Two delegations owned by different users, plus a known admin id.

    ``list_delegation_history`` reaches outside this module for two singletons,
    and both are pinned here rather than left to whatever the process happens to
    hold (CORR-701). ``condor.web.routes.code`` binds ``get_config_manager`` by
    name at import time, so patching ``config_manager`` alone decided nothing:
    which object that module ended up with depended on whether some earlier test
    file had already imported it — run alone it captured this fake and blew up on
    the missing ``get_user_preference``, run after ``test_code_run_*`` it kept the
    real one and these tests read the real ``config.yml``. Importing the module at
    the top of this file and setting the name on it makes the answer the same
    either way. ``get_code_run_store`` is patched on ``condor.code_runs``, not on
    the route module, because the lazy import inside ``list_delegation_history``
    reads it off there at call time — and an unpatched one builds the real
    on-disk store and caches it in a module global for the rest of the session.
    """
    monkeypatch.setattr(config_manager, "get_config_manager", _FakeConfigManager)
    monkeypatch.setattr(code_routes, "get_config_manager", _FakeConfigManager)
    monkeypatch.setattr(
        code_runs_module, "get_code_run_store", lambda: _EmptyCodeRunStore()
    )
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


def _strangers_live_tasks_newer_than(monkeypatch, *disk_records):
    """Three live STRANGER tasks that sort above every on-disk record (CORR-688)."""
    _on_disk(monkeypatch, *disk_records)
    delegate_module._delegations.clear()
    for n in range(3):
        delegate_module._delegations[f"t-live-{n}"] = DelegateTask(
            task_id=f"t-live-{n}",
            agent_slug="scout",
            user_id=STRANGER.id,
            chat_id=STRANGER.id,
            server_name=None,
            task="stranger's live task",
            started_at=100.0 + n,
        )


def test_history_page_is_not_eaten_by_foreign_live_tasks(monkeypatch):
    """The visibility filter runs before `limit`, so a page is full (CORR-688).

    Before: the three newer foreign live rows took both slots of ``limit=2`` and
    were then filtered out, answering zero rows while two of OWNER's were on disk.
    """
    _strangers_live_tasks_newer_than(
        monkeypatch, _record("h-owner-1", OWNER.id), _record("h-owner-2", OWNER.id)
    )

    rows = asyncio.run(list_delegation_history(kind="delegate", limit=2, user=OWNER))[
        "delegations"
    ]
    assert {r["task_id"] for r in rows} == {"h-owner-1", "h-owner-2"}


def test_history_page_for_admin_still_takes_the_newest_rows(monkeypatch):
    _strangers_live_tasks_newer_than(
        monkeypatch, _record("h-owner-1", OWNER.id), _record("h-owner-2", OWNER.id)
    )

    rows = asyncio.run(list_delegation_history(kind="delegate", limit=2, user=ADMIN))[
        "delegations"
    ]
    assert [r["task_id"] for r in rows] == ["t-live-2", "t-live-1"]


def test_history_page_skips_unowned_records_before_the_limit(monkeypatch):
    """The final guard also filters before the cut, not only the registry scope."""
    orphan = {**_record("h-orphan", 0), "started_at": 50.0}
    _on_disk(monkeypatch, orphan, _record("h-owner", OWNER.id))
    delegate_module._delegations.clear()
    # A scoped stub would never hand OWNER the orphan; force it through so the
    # guard is what stands between it and the page.
    monkeypatch.setattr(
        history_module, "list_history", lambda **kw: [orphan, _record("h-owner", 1)]
    )

    rows = asyncio.run(list_delegation_history(kind="delegate", limit=1, user=OWNER))[
        "delegations"
    ]
    assert [r["task_id"] for r in rows] == ["h-owner"]


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


# ── The isolation the history tests depend on (CORR-701) ──


def test_the_history_list_reads_no_real_singleton(monkeypatch):
    """Every door `list_delegation_history` opens outward is pinned to a fake.

    This file's four history tests pass or fail on which object those two names
    hold, and nothing in their own assertions says so — run alone they crashed
    on the fake's missing `get_user_preference`, run after a test file that had
    already imported `condor.web.routes.code` they quietly questioned the real
    `config.yml` and built the real on-disk code-run store. Asserting it here
    means removing either patch fails on the sentence that describes it rather
    than somewhere else, in one import order only.
    """
    assert code_routes.get_config_manager is _FakeConfigManager
    assert isinstance(code_runs_module.get_code_run_store(), _EmptyCodeRunStore)
    # The admin is the one caller that gets past `_may_run_code` and therefore
    # the one that would reach a real store; nobody else holds the grant.
    assert code_routes._may_run_code(ADMIN.id) is True
    assert code_routes._may_run_code(OWNER.id) is False
