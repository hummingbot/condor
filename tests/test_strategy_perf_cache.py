"""Tests for the closed-run performance freeze (PERF-058).

``_compute_agent_performance`` must fetch executors only for ACTIVE run ids
(live engines + the newest non-experiment run) after the 30s rollup cache
expires; closed runs are immutable and get served from ``_CLOSED_PERF_CACHE``
after one final successful fetch. Runs are RunStore streams (§7.1) — the run
id is an opaque ULID and executors tag ``controller_id == run_id``.
"""

import asyncio
import time
from collections import Counter
from types import SimpleNamespace

import pytest

from condor.agents import agent as agent_module
from condor.agents import engine as engine_module
from condor.agents import runstore as runstore_module
from condor.agents.agent import Agent
from condor.agents.runstore import RunStore
from condor.web.routes import agents as agents_routes

RUN_KEY = "my_agent"


class _FakeExecutorsApi:
    """Counts search_executors calls per controller_id and serves canned rows."""

    def __init__(self, rows_by_aid: dict[str, list[dict]], fail_ids=()):
        self.rows_by_aid = rows_by_aid
        self.fail_ids = set(fail_ids)
        self.calls: Counter = Counter()

    async def search_executors(self, **kwargs):
        (aid,) = kwargs["controller_ids"]
        self.calls[aid] += 1
        if aid in self.fail_ids:
            raise RuntimeError("backend down")
        return {"executors": self.rows_by_aid.get(aid, [])}


class _FakeClient:
    def __init__(self, executors_api):
        self.executors = executors_api


def _closed_executor(pnl=1.0):
    return {
        "id": "x",
        "status": "TERMINATED",
        "net_pnl_quote": pnl,
        "filled_amount_quote": 10.0,
        "cum_fees_quote": 0.1,
        "config": {"type": "position_executor", "entry_price": 1.0},
    }


def _running_executor(pnl=0.5):
    ex = _closed_executor(pnl)
    ex["status"] = "RUNNING"
    return ex


def _make_runs(n: int, kind: str = "session") -> list[str]:
    """Create ``n`` completed RunStore runs, oldest first. Returns run ids."""
    store = runstore_module.get_run_store()
    run_ids = []
    for _ in range(n):
        rid = store.start_run(RUN_KEY, kind)
        store.end_run(rid, "completed")
        run_ids.append(rid)
        time.sleep(0.02)  # distinct mtimes/started_at so ordering is stable
    return run_ids


@pytest.fixture()
def perf_env(tmp_path, monkeypatch):
    """Isolated caches + engine registry + RunStore + a client factory hook."""
    monkeypatch.setattr(agents_routes, "_PERF_CACHE", {})
    monkeypatch.setattr(agents_routes, "_CLOSED_PERF_CACHE", {})
    monkeypatch.setattr(engine_module, "_engines", {})
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(runstore_module, "_store", RunStore(tmp_path))

    agent = Agent(slug=RUN_KEY, name="My Agent")
    agent.agent_dir.mkdir(parents=True, exist_ok=True)

    def use_client(client):
        async def _fake_get_client(a):
            return client, "srv"

        monkeypatch.setattr(agents_routes, "_get_client_for_agent", _fake_get_client)

    return agent, use_client


def _compute(agent):
    return asyncio.run(agents_routes._compute_agent_performance(agent))


def test_closed_runs_fetched_once_only_active_refetched(perf_env):
    agent, use_client = perf_env
    r1, r2, r3 = _make_runs(3)
    api = _FakeExecutorsApi(
        {
            r1: [_closed_executor(1.0)],
            r2: [_closed_executor(2.0)],
            r3: [_running_executor(0.5)],
        }
    )
    use_client(_FakeClient(api))
    # Run 3 (the newest) has a live engine.
    engine_module._engines[r3] = SimpleNamespace(agent_id=r3)

    sessions1, totals1 = _compute(agent)
    assert api.calls == {r1: 1, r2: 1, r3: 1}

    # Simulate the 30s rollup cache expiring.
    agents_routes._PERF_CACHE.clear()
    sessions2, totals2 = _compute(agent)

    # Only the active run was re-fetched; closed ones came frozen.
    assert api.calls[r1] == 1
    assert api.calls[r2] == 1
    assert api.calls[r3] == 2

    # Totals identical between fresh and frozen paths.
    assert totals2 == totals1
    assert [s.agent_id for s in sessions2] == [s.agent_id for s in sessions1]
    assert totals1["total_pnl"] == pytest.approx(3.5)


def test_newest_run_stays_fresh_without_engine(perf_env):
    agent, use_client = perf_env
    r1, r2 = _make_runs(2)
    api = _FakeExecutorsApi(
        {
            r1: [_closed_executor(1.0)],
            r2: [_closed_executor(2.0)],
        }
    )
    use_client(_FakeClient(api))

    _compute(agent)
    agents_routes._PERF_CACHE.clear()
    _compute(agent)

    # No engines at all: the newest run (r2) still gets re-fetched so a just
    # -closed run sees one more pass; older ones are frozen.
    assert api.calls[r1] == 1
    assert api.calls[r2] == 2


def test_open_executors_prevent_freezing(perf_env):
    agent, use_client = perf_env
    r1, r2 = _make_runs(2)
    # Run 1 is old but left a RUNNING executor: unrealized PnL still moves.
    api = _FakeExecutorsApi(
        {
            r1: [_running_executor(0.5)],
            r2: [_closed_executor(2.0)],
        }
    )
    use_client(_FakeClient(api))

    _compute(agent)
    agents_routes._PERF_CACHE.clear()
    _compute(agent)

    assert api.calls[r1] == 2  # never frozen while open_count > 0
    assert r1 not in agents_routes._CLOSED_PERF_CACHE


def test_failed_fetch_is_not_frozen(perf_env):
    agent, use_client = perf_env
    r1, r2 = _make_runs(2)
    api = _FakeExecutorsApi({r2: [_closed_executor(2.0)]}, fail_ids={r1})
    use_client(_FakeClient(api))

    _compute(agent)
    assert r1 not in agents_routes._CLOSED_PERF_CACHE

    # Backend recovers: the previously failed id is fetched again and frozen.
    api.fail_ids.clear()
    api.rows_by_aid[r1] = [_closed_executor(1.0)]
    agents_routes._PERF_CACHE.clear()
    _compute(agent)
    assert api.calls[r1] == 2
    assert r1 in agents_routes._CLOSED_PERF_CACHE

    agents_routes._PERF_CACHE.clear()
    _, totals = _compute(agent)
    assert api.calls[r1] == 2  # frozen now
    assert totals["total_pnl"] == pytest.approx(3.0)


def test_reactivated_id_evicts_frozen_entry(perf_env):
    agent, use_client = perf_env
    r1, r2 = _make_runs(2)
    api = _FakeExecutorsApi(
        {
            r1: [_closed_executor(1.0)],
            r2: [_closed_executor(2.0)],
        }
    )
    use_client(_FakeClient(api))

    _compute(agent)
    assert r1 in agents_routes._CLOSED_PERF_CACHE

    # Run 1's engine comes back (e.g. restored after restart): the stale
    # frozen entry must be evicted and the id fetched fresh.
    engine_module._engines[r1] = SimpleNamespace(agent_id=r1)
    agents_routes._PERF_CACHE.clear()
    _compute(agent)
    assert api.calls[r1] == 2
    assert r1 not in agents_routes._CLOSED_PERF_CACHE
