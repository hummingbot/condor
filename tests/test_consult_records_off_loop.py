"""PERF-293: recording a consult never blocks the loop it was asked on.

A consult is the plentiful kind of run -- dozens where a delegation happens
once -- and since FEAT-058 each one leaves a record. The *terminal* write is
the expensive half: it also prunes, and pruning reads one ``status.json`` per
record this owner has, up to both caps together. ``run_consult`` is awaited on
the single loop uvicorn, the Telegram poller and every routine share, so that
walk was tens of milliseconds of blocking IO paid by the whole install on every
consult that finished.

What is pinned here is not "it is fast" -- it is *which thread* each write and
the retention walk happen on, and that the two writes are deliberately split:

* the **start** write stays inline (it must land before the engine starts, and
  it is one small merge with no retention behind it),
* the **cancelled** write stays inline too (a cancelled task cannot be relied on
  to await a fresh thread hop, and it must still stamp ``stopped``),
* the **terminal** writes -- the ones that prune -- go to a worker thread.

The second cost is the walk itself, which is O(every record this owner has
ever kept). A store below its cap now answers "nothing to evict" from one
``scandir``, without opening a status file at all.

Sync tests driving coroutines with ``asyncio.run``: ``pytest-asyncio`` is a dev
dependency but is not installed in this venv.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

import config_manager
from condor.agents import consult as consult_module
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


class _Spy:
    """Which thread each status write and each retention walk ran on."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, threading.Thread]] = []
        self.walks: list[threading.Thread] = []

    @property
    def states(self) -> list[str]:
        return [state for state, _thread in self.writes]

    def thread_for(self, state: str) -> threading.Thread:
        for written, thread in self.writes:
            if written == state:
                return thread
        raise AssertionError(f"no {state!r} write was recorded: {self.states}")


@pytest.fixture
def spy(monkeypatch) -> _Spy:
    seen = _Spy()
    write_status, terminal_record_dirs = (
        registry_file.write_status,
        history_module.terminal_record_dirs,
    )

    def spy_write(session_dir, filename=registry_file.STATUS_FILENAME, **fields):
        seen.writes.append((fields.get("state", ""), threading.current_thread()))
        return write_status(session_dir, filename, **fields)

    def spy_walk(user_id, states, kind=None):
        seen.walks.append(threading.current_thread())
        return terminal_record_dirs(user_id, states, kind=kind)

    monkeypatch.setattr(registry_file, "write_status", spy_write)
    monkeypatch.setattr(history_module, "terminal_record_dirs", spy_walk)
    return seen


def _off_loop(*threads: threading.Thread) -> bool:
    """True when none of these ran on the thread driving the loop.

    ``asyncio.run`` drives the loop on the main thread, so "not the current
    thread" is exactly "not the event loop".
    """
    assert threads, "nothing was recorded -- the spy never fired"
    return all(thread is not threading.current_thread() for thread in threads)


def _consult(monkeypatch, *, answer="the funding is 3bps", boom=None, **kw):
    """One consult through the real ``run_consult``, with a stubbed engine."""

    async def fake_engine(**_kw):
        if boom is not None:
            raise boom
        return answer

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", fake_engine)
    monkeypatch.setattr(
        consult_module, "_build_consult_permission_cb", lambda *a, **k: None
    )

    async def scenario():
        return await consult_module.run_consult(
            slug=kw.pop("slug", "scout"),
            user_id=kw.pop("user_id", USER),
            chat_id=kw.pop("chat_id", 42),
            server_name=kw.pop("server_name", "local"),
            task=kw.pop("task", "what is the funding on HYPE right now"),
            **kw,
        )

    return asyncio.run(scenario())


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


def _at_the_cap(monkeypatch, cap=1):
    """A store already holding more consult records than it may keep."""
    monkeypatch.setattr(delegate_module, "MAX_CONSULT_RECORDS", cap)
    for i in range(cap + 1):
        _write_record(USER, f"scout-consult-{i}", kind=KIND_CONSULT, started_at=1.0 + i)


# ── the write, and the walk behind it ──────────────────────────────────────


def test_a_finished_consult_records_and_prunes_off_the_loop(monkeypatch, spy):
    """The expensive half of the bookkeeping never runs on the shared loop."""
    _at_the_cap(monkeypatch)
    spy.writes.clear()

    assert _consult(monkeypatch) == "the funding is 3bps"

    assert spy.states == ["running", "done"]
    # The start write is sub-millisecond, must land before the engine starts and
    # must not be able to race the terminal one: it stays where it was.
    assert spy.thread_for("running") is threading.current_thread()
    assert _off_loop(spy.thread_for("done")), "the terminal write ran on the loop"
    assert _off_loop(*spy.walks), "the retention walk ran on the loop"


def test_a_failing_consult_records_off_the_loop_and_still_raises(monkeypatch, spy):
    """The error branch pays the same walk, so it takes the same thread hop."""
    _at_the_cap(monkeypatch)
    spy.writes.clear()

    with pytest.raises(RuntimeError, match="backend on fire"):
        _consult(monkeypatch, boom=RuntimeError("backend on fire"))

    assert _off_loop(spy.thread_for("error"))
    assert _off_loop(*spy.walks)


def test_a_cancelled_consult_still_stamps_stopped_inline(monkeypatch, spy):
    """The one write that must not be deferred to a thread it may never reach."""

    async def never_returns(**_kw):
        await asyncio.Event().wait()

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", never_returns)
    monkeypatch.setattr(
        consult_module, "_build_consult_permission_cb", lambda *a, **k: None
    )

    async def scenario():
        task = asyncio.ensure_future(
            consult_module.run_consult(
                slug="scout",
                user_id=USER,
                chat_id=42,
                server_name=None,
                task="summarise the LP ranges that went out of range",
            )
        )
        await asyncio.sleep(0)  # let the start record land
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert spy.states == ["running", "stopped"]
    assert spy.thread_for("stopped") is threading.current_thread()
    records = list_history(user_id=USER, limit=100)
    assert [r["status"] for r in records] == ["stopped"]


def test_the_records_are_the_same_whichever_thread_wrote_them(monkeypatch):
    """Offloading is a scheduling change, not a change to what lands on disk."""
    answer = _consult(monkeypatch, caller="condor")

    records = list_history(user_id=USER, limit=100)
    assert len(records) == 1, records
    record = records[0]
    assert record["kind"] == KIND_CONSULT
    assert record["status"] == "done"
    assert record["result"] == answer
    assert record["caller"] == "condor"
    assert record["ended_at"] >= record["started_at"] > 0


# ── the walk that no longer happens at all ─────────────────────────────────


def test_a_store_below_its_cap_never_opens_a_status_file(monkeypatch, spy):
    """The second cost: retention is O(records), so it must not run per consult.

    Under the cap the answer is knowable from a directory count, and that is
    every install for most of its life.
    """
    monkeypatch.setattr(delegate_module, "MAX_CONSULT_RECORDS", 300)
    monkeypatch.setattr(delegate_module, "MAX_DELEGATION_RECORDS", 500)
    for i in range(5):
        _write_record(USER, f"scout-consult-{i}", kind=KIND_CONSULT, started_at=1.0 + i)
    _write_record(USER, "scout-delegate-precious", kind=KIND_DELEGATE, started_at=1.0)
    spy.walks.clear()

    _consult(monkeypatch)

    assert spy.walks == [], "the retention walk read the store for nothing"


def test_the_cheap_check_never_skips_an_eviction_that_was_due(monkeypatch):
    """Correctness of the short-circuit: it only ever rules eviction *out*."""
    monkeypatch.setattr(delegate_module, "MAX_CONSULT_RECORDS", 2)
    monkeypatch.setattr(delegate_module, "MAX_DELEGATION_RECORDS", 500)
    for i in range(4):
        _write_record(USER, f"scout-consult-{i}", kind=KIND_CONSULT, started_at=1.0 + i)
    _write_record(USER, "scout-delegate-precious", kind=KIND_DELEGATE, started_at=1.0)

    assert delegate_module.prune_delegation_records(USER, kind=KIND_CONSULT) == 2

    kept = {r["task_id"] for r in list_history(user_id=USER, limit=100)}
    assert kept == {"scout-consult-2", "scout-consult-3", "scout-delegate-precious"}


def test_an_unreadable_store_evicts_nothing_rather_than_raising(monkeypatch):
    """The count fails exactly where the walk would have: nothing to evict."""
    monkeypatch.setattr(delegate_module, "MAX_CONSULT_RECORDS", 1)

    assert delegate_module.prune_delegation_records(USER, kind=KIND_CONSULT) == 0


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
    _write_record(USER, "scout-consult-0", kind=KIND_CONSULT, started_at=1.0)

    rows = asyncio.run(list_delegation_history(user=ALICE))["delegations"]

    assert [r["task_id"] for r in rows] == ["scout-consult-0"]
    assert _off_loop(*seen), "the history walk ran on the event loop"
