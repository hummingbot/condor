"""CORR-142: the store's result dict is bounded and never outlives its instance.

``RoutineStore._results`` only ever grew: every ``execute()`` mints a fresh
instance_id, and neither ``remove_instance`` nor ``stop`` dropped the matching
result — so each one-shot run pinned a ``RoutineResult`` forever, ``chart_image``
raw PNG bytes included, in a process designed to run for weeks.

Results still have to *outlive their run*: the dashboard renders a run's detail
after it finishes and fetches the chart PNG only when the user opens that run,
and an MCP agent that submitted with ``run_async`` reads it back through
``get_instance`` an unbounded time later. So the fix is a bound, not a TTL —
a result is dropped when its instance goes away (every read path resolves the
instance first, so it is unreachable by then anyway) and, as a backstop, the
oldest entries are evicted once the dict passes ``_MAX_RESULTS``.
"""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

import condor.reports as rep
from condor.routine_store import _MAX_RESULTS, RoutineStore
from routines.base import RoutineResult


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(rep, "CHARTS_DIR", tmp_path)
    monkeypatch.setattr(rep, "INDEX_FILE", tmp_path / "reports_index.json")
    return tmp_path


def _routine(run_fn):
    class Config(BaseModel):
        pass

    return SimpleNamespace(
        name="probe", source="global", config_class=Config, run_fn=run_fn
    )


def _store() -> RoutineStore:
    store = RoutineStore()
    store._instances["i1"] = store._new_instance_meta(
        "probe", {}, "srv", 0, source="web"
    )
    return store


async def _returns_text(config, ctx):
    return "run output"


def _run(store: RoutineStore, routine) -> None:
    asyncio.run(
        store._execute_and_record(
            "i1", routine, {}, "srv", status_after="completed", fire_hooks=False
        )
    )


# ── The result dies with its instance ──


def test_removing_an_instance_drops_its_result(reports_dir):
    store = _store()
    _run(store, _routine(_returns_text))
    assert store.get_result("i1") is not None

    store.remove_instance("i1")

    assert store.get_result("i1") is None
    assert "i1" not in store._results


def test_stopping_an_instance_drops_its_result(reports_dir):
    store = _store()
    _run(store, _routine(_returns_text))

    assert store.stop("i1") is True

    assert store.get_result("i1") is None
    assert "i1" not in store._results


def test_no_orphan_results_after_execute_and_stop(reports_dir):
    """set(_results) <= set(_instances) survives the store's own lifecycle."""
    store = _store()
    assert set(store._results) <= set(store._instances)

    _run(store, _routine(_returns_text))
    assert set(store._results) <= set(store._instances)

    store.stop("i1")
    assert set(store._results) <= set(store._instances)

    store.remove_instance("i1")
    assert set(store._results) <= set(store._instances)


# ── The bound is enforced ──


def test_results_never_exceed_the_cap():
    store = RoutineStore()
    for n in range(_MAX_RESULTS + 50):
        store.store_result(f"run-{n}", RoutineResult(text=str(n)))

    assert len(store._results) == _MAX_RESULTS


def test_the_oldest_results_are_the_ones_evicted():
    store = RoutineStore()
    for n in range(_MAX_RESULTS + 50):
        store.store_result(f"run-{n}", RoutineResult(text=str(n)))

    # The first 50 are gone; everything after them is still resident.
    assert store.get_result("run-0") is None
    assert store.get_result("run-49") is None
    assert store.get_result("run-50") is not None


def test_a_recent_result_is_still_retrievable_after_eviction():
    """The read-latency requirement: a finished run stays readable, chart included."""
    store = RoutineStore()
    store.store_result("charted", RoutineResult(text="with chart", chart_image=b"PNG"))
    for n in range(_MAX_RESULTS - 1):
        store.store_result(f"run-{n}", RoutineResult(text=str(n)))

    result = store.get_result("charted")
    assert result is not None
    assert result.chart_image == b"PNG"


def test_restoring_a_result_refreshes_its_recency():
    """A continuous instance storing every tick must not age out under itself."""
    store = RoutineStore()
    store.store_result("ticker", RoutineResult(text="tick 1"))
    for n in range(_MAX_RESULTS - 1):
        store.store_result(f"run-{n}", RoutineResult(text=str(n)))

    store.store_result("ticker", RoutineResult(text="tick 2"))
    for n in range(_MAX_RESULTS - 1):
        store.store_result(f"later-{n}", RoutineResult(text=str(n)))

    assert len(store._results) == _MAX_RESULTS
    assert store.get_result("ticker").text == "tick 2"


# ── Eviction does not disturb a run in flight ──


def test_eviction_does_not_disturb_a_run_in_flight(reports_dir):
    store = _store()
    started, released = asyncio.Event(), asyncio.Event()

    async def slow(config, ctx):
        started.set()
        await released.wait()
        return "in-flight output"

    async def scenario():
        task = asyncio.create_task(
            store._execute_and_record(
                "i1",
                _routine(slow),
                {},
                "srv",
                status_after="completed",
                fire_hooks=False,
            )
        )
        await started.wait()
        # Flood the store past the cap while the run is suspended.
        for n in range(_MAX_RESULTS + 10):
            store.store_result(f"flood-{n}", RoutineResult(text=str(n)))
        released.set()
        await task

    asyncio.run(scenario())

    assert store.get_result("i1").text == "in-flight output"
    assert store._instances["i1"]["last_result"] == "in-flight output"
    assert store._instances["i1"]["status"] == "completed"
    assert len(store._results) == _MAX_RESULTS


def test_a_cancel_landing_after_stop_does_not_resurrect_the_result(reports_dir):
    """_execute_and_record records the cancel *after* stop() removed the instance."""
    store = _store()
    started = asyncio.Event()

    async def hangs(config, ctx):
        started.set()
        await asyncio.Event().wait()
        return "never"

    async def scenario():
        task = asyncio.create_task(
            store._execute_and_record(
                "i1",
                _routine(hangs),
                {},
                "srv",
                status_after="completed",
                fire_hooks=False,
            )
        )
        store._tasks["i1"] = task
        await started.wait()
        assert store.stop("i1") is True
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())

    assert "i1" not in store._instances
    assert "i1" not in store._results
    assert store.get_result("i1") is None
