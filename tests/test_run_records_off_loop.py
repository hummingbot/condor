"""PERF-293: the retention walk is O(records), so it must not run for nothing.

Every terminal run record prunes, and pruning reads one ``status.json`` per
record this owner has, up to every kind's cap together. That walk happens on the
single loop uvicorn, the Telegram poller and every routine share, so its cost is
the whole install's.

Two things keep it survivable, and both are pinned here:

* **The walk usually does not happen at all.** A store below its cap answers
  "nothing to evict" from one ``scandir``, without opening a status file — and
  that is every install for most of its life. The short-circuit only ever rules
  eviction *out*: at the cap the walk is inherent, because "evict the oldest"
  cannot be answered without ordering the records.
* **The history route's own walk is offloaded**, since it reads the same
  directory with the same reader on the same loop.

These used to be pinned through ``run_consult``, which was the plentiful kind of
run and paid this on every question one agent asked another. That channel is
gone and the only writer left is the rare, deliberate delegation, so the costs
are exercised directly against the store instead.

Sync tests driving coroutines with ``asyncio.run``: ``pytest-asyncio`` is a dev
dependency but is not installed in this venv.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

import config_manager
from condor.agents import delegate as delegate_module
from condor.agents import delegation_history as history_module
from condor.agents.delegation_history import list_history
from condor.agents.run_records import KIND_CONSULT, KIND_DELEGATE
from condor.runtime import registry_file
from condor.web.models import WebUser
from condor.web.routes.agents import list_delegation_history

USER = 7
ALICE = WebUser(id=USER, username="alice", first_name="A", role="user")


@pytest.fixture(autouse=True)
def _clean_registry():
    delegate_module._delegations.clear()
    yield
    delegate_module._delegations.clear()


@pytest.fixture
def walks(monkeypatch) -> list[threading.Thread]:
    """Which thread each retention walk ran on, and whether one ran at all."""
    seen: list[threading.Thread] = []
    terminal_record_dirs = history_module.terminal_record_dirs

    def spy_walk(user_id, states, kind=None):
        seen.append(threading.current_thread())
        return terminal_record_dirs(user_id, states, kind=kind)

    monkeypatch.setattr(history_module, "terminal_record_dirs", spy_walk)
    return seen


def _off_loop(*threads: threading.Thread) -> bool:
    """True when none of these ran on the thread driving the loop.

    ``asyncio.run`` drives the loop on the main thread, so "not the current
    thread" is exactly "not the event loop".
    """
    assert threads, "nothing was recorded -- the spy never fired"
    return all(thread is not threading.current_thread() for thread in threads)


def _write_record(user_id, task_id, *, kind, state="done", started_at=1.0):
    from condor import paths

    record_dir = paths.delegation_dir(user_id, task_id)
    record_dir.mkdir(parents=True, exist_ok=True)
    registry_file.write_status(
        record_dir,
        state=state,
        task_id=task_id,
        agent_slug="scout",
        user_id=user_id,
        kind=kind,
        started_at=started_at,
        task=f"task {task_id}",
    )


# ── the walk that no longer happens at all ─────────────────────────────────


def test_a_store_below_its_cap_never_opens_a_status_file(monkeypatch, walks):
    """Under the cap the answer is knowable from a directory count alone."""
    monkeypatch.setattr(delegate_module, "MAX_DELEGATION_RECORDS", 500)
    monkeypatch.setattr(delegate_module, "MAX_CONSULT_RECORDS", 300)
    for i in range(5):
        _write_record(
            USER, f"scout-delegate-{i}", kind=KIND_DELEGATE, started_at=1.0 + i
        )
    _write_record(USER, "scout-consult-archived", kind=KIND_CONSULT, started_at=1.0)
    walks.clear()

    assert delegate_module.prune_delegation_records(USER, kind=KIND_DELEGATE) == 0

    assert walks == [], "the retention walk read the store for nothing"


def test_the_cheap_check_never_skips_an_eviction_that_was_due(monkeypatch):
    """Correctness of the short-circuit: it only ever rules eviction *out*."""
    monkeypatch.setattr(delegate_module, "MAX_DELEGATION_RECORDS", 2)
    monkeypatch.setattr(delegate_module, "MAX_CONSULT_RECORDS", 300)
    for i in range(4):
        _write_record(
            USER, f"scout-delegate-{i}", kind=KIND_DELEGATE, started_at=1.0 + i
        )
    _write_record(USER, "scout-consult-archived", kind=KIND_CONSULT, started_at=1.0)

    assert delegate_module.prune_delegation_records(USER, kind=KIND_DELEGATE) == 2

    kept = {r["task_id"] for r in list_history(user_id=USER, limit=100)}
    assert kept == {
        "scout-delegate-2",
        "scout-delegate-3",
        "scout-consult-archived",
    }


def test_an_unreadable_store_evicts_nothing_rather_than_raising(monkeypatch):
    """The count fails exactly where the walk would have: nothing to evict."""
    monkeypatch.setattr(delegate_module, "MAX_DELEGATION_RECORDS", 1)

    assert delegate_module.prune_delegation_records(USER, kind=KIND_DELEGATE) == 0


# ── the sibling walk: the history route ────────────────────────────────────


class _FakeConfigManager:
    def is_admin(self, user_id: int) -> bool:
        return False

    def get_user_preference(self, user_id: int, key: str, default=None):
        return False


def test_the_history_route_walks_the_store_off_the_loop(monkeypatch):
    """Same directory, same reader, same loop: the list route offloads it too."""
    monkeypatch.setattr(config_manager, "get_config_manager", _FakeConfigManager)
    seen: list[threading.Thread] = []
    real = history_module.list_history

    def spy_list_history(**kwargs):
        seen.append(threading.current_thread())
        return real(**kwargs)

    monkeypatch.setattr(history_module, "list_history", spy_list_history)
    _write_record(USER, "scout-delegate-0", kind=KIND_DELEGATE, started_at=1.0)

    rows = asyncio.run(list_delegation_history(user=ALICE))["delegations"]

    assert [r["task_id"] for r in rows] == ["scout-delegate-0"]
    assert _off_loop(*seen), "the history walk ran on the event loop"
