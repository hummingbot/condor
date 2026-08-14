"""CORR-163: the store's instance dict is bounded, and only for finished runs.

``RoutineStore._instances`` only ever grew: every ``execute()`` mints a fresh
instance id and the only removals were the explicit ``remove_instance`` and
``stop`` paths, so a one-shot that simply completed was never reaped — a slow
but monotonic leak in a process designed to run for weeks with scheduled
routines firing on an interval.

The fix is retention, not deletion-on-completion. A finished one-shot is what
the dashboard's detail panel renders after the run ends and what an MCP agent
reads back through ``get_instance`` after a ``run_async``, so the record has to
outlive its run; what it must not do is outlive the cap. Only *terminal*
instances count, and only once their task is off the loop, so a run in flight, a
continuous routine and a scheduled one between ticks are never candidates.
"""

import asyncio
import time
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

import condor.reports as rep
from condor.routine_store import _MAX_TERMINAL_INSTANCES, RoutineStore
from routines.base import RoutineResult


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(rep, "CHARTS_DIR", tmp_path)
    monkeypatch.setattr(rep, "INDEX_FILE", tmp_path / "reports_index.json")
    return tmp_path


def _routine():
    class Config(BaseModel):
        pass

    async def run_fn(config, ctx):
        return "run output"

    return SimpleNamespace(
        name="probe", source="global", config_class=Config, run_fn=run_fn
    )


def _terminal(store: RoutineStore, iid: str, *, age: float, status="completed"):
    """A finished run, already off the loop, that ran ``age`` seconds ago."""
    meta = store._new_instance_meta("probe", {}, "srv", 0, source="web")
    meta["status"] = status
    meta["last_run_at"] = time.time() - age
    store._instances[iid] = meta
    store._results[iid] = RoutineResult(text=iid)


# ── The bound is enforced ──


def test_one_shot_runs_stop_growing_the_instance_dict(reports_dir):
    """The leak itself: N one-shots used to leave N instances behind forever."""
    store = RoutineStore()
    routine = _routine()

    for n in range(_MAX_TERMINAL_INSTANCES + 50):
        iid = f"run-{n:04d}"
        store._instances[iid] = store._new_instance_meta(
            "probe", {}, "srv", 0, source="web"
        )
        asyncio.run(
            store._execute_and_record(
                iid, routine, {}, "srv", status_after="completed", fire_hooks=False
            )
        )

    # +1: the run that triggers a prune is still executing its own task, so it
    # is exempt from that pass and settles on the next one.
    assert len(store._instances) <= _MAX_TERMINAL_INSTANCES + 1
    assert set(store._results) <= set(store._instances)


def test_the_oldest_finished_runs_are_the_ones_evicted():
    store = RoutineStore()
    for n in range(_MAX_TERMINAL_INSTANCES + 10):
        # Descending age: run-0 is the oldest.
        _terminal(store, f"run-{n:04d}", age=10_000 - n)

    store._prune_instances()

    assert len(store._instances) == _MAX_TERMINAL_INSTANCES
    assert "run-0000" not in store._instances
    assert "run-0009" not in store._instances
    assert "run-0010" in store._instances
    assert f"run-{_MAX_TERMINAL_INSTANCES + 9:04d}" in store._instances


def test_evicting_an_instance_takes_its_result_with_it():
    """No orphan in ``_results`` — CORR-142's invariant survives eviction."""
    store = RoutineStore()
    for n in range(_MAX_TERMINAL_INSTANCES + 10):
        _terminal(store, f"run-{n:04d}", age=10_000 - n)

    store._prune_instances()

    assert store.get_result("run-0000") is None
    assert set(store._results) <= set(store._instances)


def test_a_terminal_instance_is_not_evicted_below_the_cap():
    store = RoutineStore()
    _terminal(store, "old", age=10_000_000)

    store._prune_instances()

    assert "old" in store._instances
    assert store.get_result("old") is not None


# ── Live instances are exempt ──


def test_a_running_instance_is_never_evicted():
    """An in-flight one-shot outlives any amount of churn around it."""
    store = RoutineStore()
    inflight = store._new_instance_meta("probe", {}, "srv", 0, source="web")
    inflight["created_at"] = 0.0  # the oldest thing in the dict
    store._instances["inflight"] = inflight
    store._tasks["inflight"] = SimpleNamespace(done=lambda: False)
    for n in range(_MAX_TERMINAL_INSTANCES + 10):
        _terminal(store, f"run-{n:04d}", age=1000 - n)

    store._prune_instances()

    assert "inflight" in store._instances
    assert store._instances["inflight"]["status"] == "running"


def test_a_scheduled_instance_is_never_evicted():
    """A schedule sits idle between ticks — old by every clock, still alive."""
    store = RoutineStore()
    scheduled = store._new_instance_meta(
        "probe", {}, "srv", 0, source="web", status="scheduled"
    )
    scheduled["created_at"] = 0.0
    scheduled["last_run_at"] = 0.0
    store._instances["sched"] = scheduled
    for n in range(_MAX_TERMINAL_INSTANCES + 10):
        _terminal(store, f"run-{n:04d}", age=1000 - n)

    store._prune_instances()

    assert "sched" in store._instances
    # And it does not count against the cap: the terminal ones alone fill it.
    assert len(store._instances) == _MAX_TERMINAL_INSTANCES + 1


def test_a_terminal_instance_whose_task_is_still_alive_is_exempt():
    """Belt and braces: status says stopped, the loop says otherwise."""
    store = RoutineStore()
    _terminal(store, "cancelling", age=10_000_000, status="stopped")
    store._tasks["cancelling"] = SimpleNamespace(done=lambda: False)
    for n in range(_MAX_TERMINAL_INSTANCES + 10):
        _terminal(store, f"run-{n:04d}", age=1000 - n)

    store._prune_instances()

    assert "cancelling" in store._instances


def test_telegram_instances_are_not_touched():
    """Telegram-owned instances stay ``running`` for life and are removed by hand."""
    store = RoutineStore()
    meta = store._new_instance_meta("probe", {}, "srv", 0, source="telegram")
    meta["created_at"] = 0.0
    store.add_instance("tg", meta)
    for n in range(_MAX_TERMINAL_INSTANCES + 10):
        _terminal(store, f"run-{n:04d}", age=1000 - n)

    store.add_instance("tg2", dict(meta))

    assert "tg" in store._instances
    assert "tg2" in store._instances
