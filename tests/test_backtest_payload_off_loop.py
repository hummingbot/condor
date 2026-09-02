"""PERF-297: the gzip and the gunzip of a backtest payload never run on the loop.

A payload runs to 137 MB and the store's own ``migrate`` docstring measures
~2.5 s to compress one and ~0.7 s to parse it back. Condor has exactly one
event loop: it polls Telegram, serves every dashboard request and runs every
routine. So each of these seconds was a freeze of the whole process, once per
completed backtest and once per chart opened.

What is pinned here is not "it is fast" -- it is *which thread* the compression
and the decompression happen on. Every path that writes or reads a payload from
a coroutine must hand it to a worker thread, so the recorded thread is never the
one running the loop.

Sync tests driving coroutines with ``asyncio.run``: ``pytest-asyncio`` is a dev
dependency but is not installed in this venv.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

import condor.backtest_store as store_mod
import condor.backtesting as core
import condor.web.routes.backtesting as bt_routes
from condor.backtest_store import BacktestStore
from condor.web.models import WebUser
from tests.conftest import load_shared_routine

# Imported at module scope like the other archive suites: the autouse isolation
# fixture repoints $CONDOR_AGENTS_ROOT, so a load from inside a test would look
# for the shipped routine under a directory it was never copied to.
_chart = load_shared_routine("backtest_chart")
_compare = load_shared_routine("backtest_compare")

ALICE = WebUser(id=1, username="alice", first_name="A", role="user")


def _envelope(task_id: str = "t-1") -> dict:
    return {
        "task_id": task_id,
        "status": "completed",
        "completed_at": time.time(),
        "config": {
            "config": {
                "id": "ema",
                "controller_name": "pmm_simple",
                "trading_pair": "BTC-USDT",
                "connector_name": "binance",
            },
            "start_time": 1_751_328_000,
            "end_time": 1_759_104_000,
            "backtesting_resolution": "1m",
            "trade_cost": 0.0002,
        },
        "result": {
            "results": {"net_pnl_quote": 12.5},
            "processed_data": {"close": {str(i): i for i in range(200)}},
            "executors": [],
            "pnl_timeseries": [],
        },
    }


class _Threads:
    """Records the thread each payload write/read actually ran on."""

    def __init__(self) -> None:
        self.wrote: list[threading.Thread] = []
        self.read: list[threading.Thread] = []


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = BacktestStore(data_dir=tmp_path / "backtests")
    monkeypatch.setattr(store_mod, "get_backtest_store", lambda: store)
    monkeypatch.setattr(bt_routes, "get_backtest_store", lambda: store)
    return store


@pytest.fixture
def threads(store, monkeypatch) -> _Threads:
    seen = _Threads()
    write, read = store._write_payload, store._read_payload

    def spy_write(task_id, task):
        seen.wrote.append(threading.current_thread())
        return write(task_id, task)

    def spy_read(task_id):
        seen.read.append(threading.current_thread())
        return read(task_id)

    monkeypatch.setattr(store, "_write_payload", spy_write)
    monkeypatch.setattr(store, "_read_payload", spy_read)
    return seen


def _off_loop(recorded: list[threading.Thread]) -> bool:
    """True when every recorded call ran somewhere other than this thread.

    ``asyncio.run`` drives the loop on the main thread, so "not the current
    thread" is exactly "not the event loop".
    """
    assert recorded, "nothing was recorded -- the spy never fired"
    return all(t is not threading.current_thread() for t in recorded)


# ── the save path ─────────────────────────────────────────────────────────────


def test_run_and_save_compresses_off_the_loop(store, threads, monkeypatch):
    """Every completed backtest pays the compression; none of them may freeze."""
    task = _envelope("task-1")

    async def submit_task(**kwargs):
        return {"task_id": "task-1"}

    async def get_task(task_id):
        return task

    client = SimpleNamespace(
        backtesting=SimpleNamespace(submit_task=submit_task, get_task=get_task)
    )

    task_id, saved = asyncio.run(
        core.run_and_save(client, "srv", {"id": "ema"}, 0, 1, poll_interval=0)
    )

    assert task_id == "task-1"
    assert saved["status"] == "completed"
    assert store.get_result("task-1")["server"] == "srv"
    assert _off_loop(threads.wrote), "gzip level 6 ran on the event loop"


def test_fetch_and_save_compresses_off_the_loop(store, threads):
    """The recovery path saves the same 137 MB envelope the run path does."""

    async def get_task(task_id):
        return _envelope(task_id)

    client = SimpleNamespace(backtesting=SimpleNamespace(get_task=get_task))

    assert asyncio.run(core.fetch_and_save(client, "srv", "t-1")) is not None
    assert _off_loop(threads.wrote)


def test_a_store_failure_is_still_swallowed(store, monkeypatch):
    """Offloading must not change the contract: a store error never loses a run."""

    def boom(task_id, task):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_write_payload", boom)

    async def get_task(task_id):
        return _envelope(task_id)

    client = SimpleNamespace(backtesting=SimpleNamespace(get_task=get_task))

    assert asyncio.run(core.fetch_and_save(client, "srv", "t-1")) is not None


# ── the read paths ────────────────────────────────────────────────────────────


def test_the_archive_route_gunzips_off_the_loop(store, threads, monkeypatch):
    """Opening one chart must not stall every other request for the parse."""
    store.save_result("local", "task-1", _envelope("task-1"))
    threads.wrote.clear()
    monkeypatch.setattr(
        bt_routes,
        "get_config_manager",
        lambda: SimpleNamespace(
            list_servers=lambda: {"local": {}},
            has_server_access=lambda *a, **k: True,
        ),
    )

    body = asyncio.run(bt_routes.get_archived_backtest("task-1", user=ALICE))

    assert body["saved"] is True
    assert _off_loop(threads.read), "gunzip + parse ran on the event loop"


def test_the_chart_routine_gunzips_off_the_loop(store, threads):
    """A routine shares the loop with the poller exactly like a route does."""
    store.save_result("local", "task-1", _envelope("task-1"))
    threads.wrote.clear()

    loaded = asyncio.run(_chart._load_saved_task("task-1"))

    assert not isinstance(loaded, str), loaded
    assert _off_loop(threads.read)


def test_the_compare_routine_gunzips_off_the_loop(store, threads):
    """One thread hop per run, sequentially: a comparison opens several payloads."""
    store.save_result("local", "task-1", _envelope("task-1"))
    store.save_result("local", "task-2", _envelope("task-2"))
    threads.wrote.clear()

    runs = [asyncio.run(_compare._load_run(store, tid)) for tid in ("task-1", "task-2")]

    assert all(r is not None for r in runs)
    assert _off_loop(threads.read)
