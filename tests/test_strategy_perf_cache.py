"""Tests for the closed-session performance freeze (PERF-058).

``_compute_strategy_performance`` must fetch executors only for ACTIVE ids
(live engines + the newest session) after the 30s rollup cache expires; closed
sessions/experiments are immutable and get served from ``_CLOSED_PERF_CACHE``
after one final successful fetch.
"""

import asyncio
from collections import Counter, OrderedDict
from types import SimpleNamespace

import pytest

from condor.runtime import loops as loops_module
from condor.web.routes import agents as agents_routes

RUN_KEY = "my_agent.my_strategy"


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


def _make_sessions(strategy_dir, nums):
    for n in nums:
        (strategy_dir / "sessions" / f"session_{n}").mkdir(parents=True)


@pytest.fixture()
def perf_env(tmp_path, monkeypatch):
    """Isolated caches + engine registry + a client factory hook."""
    monkeypatch.setattr(agents_routes, "_PERF_CACHE", {})
    monkeypatch.setattr(agents_routes, "_CLOSED_PERF_CACHE", OrderedDict())
    # The running-engine registry moved into the supervisor (FEAT-012).
    monkeypatch.setattr(loops_module.get_supervisor(), "_engines", {})

    def use_client(client):
        async def _fake_get_client(strategy_dir, default_config):
            return client, "srv"

        monkeypatch.setattr(agents_routes, "_get_client_for_strategy", _fake_get_client)

    return tmp_path, use_client


def _compute(strategy_dir):
    return asyncio.run(
        agents_routes._compute_strategy_performance(RUN_KEY, strategy_dir, None)
    )


def test_closed_sessions_fetched_once_only_active_refetched(perf_env):
    strategy_dir, use_client = perf_env
    _make_sessions(strategy_dir, [1, 2, 3])
    api = _FakeExecutorsApi(
        {
            f"{RUN_KEY}_1": [_closed_executor(1.0)],
            f"{RUN_KEY}_2": [_closed_executor(2.0)],
            f"{RUN_KEY}_3": [_running_executor(0.5)],
        }
    )
    use_client(_FakeClient(api))
    # Session 3 has a live engine.
    aid3 = f"{RUN_KEY}_3"
    loops_module.get_supervisor()._engines[aid3] = SimpleNamespace(agent_id=aid3)

    sessions1, totals1 = _compute(strategy_dir)
    assert api.calls == {f"{RUN_KEY}_1": 1, f"{RUN_KEY}_2": 1, f"{RUN_KEY}_3": 1}

    # Simulate the 30s rollup cache expiring.
    agents_routes._PERF_CACHE.clear()
    sessions2, totals2 = _compute(strategy_dir)

    # Only the active session was re-fetched; closed ones came frozen.
    assert api.calls[f"{RUN_KEY}_1"] == 1
    assert api.calls[f"{RUN_KEY}_2"] == 1
    assert api.calls[f"{RUN_KEY}_3"] == 2

    # Totals identical between fresh and frozen paths.
    assert totals2 == totals1
    assert [s.agent_id for s in sessions2] == [s.agent_id for s in sessions1]
    assert totals1["total_pnl"] == pytest.approx(3.5)


def test_newest_session_stays_fresh_without_engine(perf_env):
    strategy_dir, use_client = perf_env
    _make_sessions(strategy_dir, [1, 2])
    api = _FakeExecutorsApi(
        {
            f"{RUN_KEY}_1": [_closed_executor(1.0)],
            f"{RUN_KEY}_2": [_closed_executor(2.0)],
        }
    )
    use_client(_FakeClient(api))

    _compute(strategy_dir)
    agents_routes._PERF_CACHE.clear()
    _compute(strategy_dir)

    # No engines at all: the newest session (2) still gets re-fetched so a just
    # -closed session sees one more pass; older ones are frozen.
    assert api.calls[f"{RUN_KEY}_1"] == 1
    assert api.calls[f"{RUN_KEY}_2"] == 2


def test_open_executors_prevent_freezing(perf_env):
    strategy_dir, use_client = perf_env
    _make_sessions(strategy_dir, [1, 2])
    # Session 1 is old but left a RUNNING executor: unrealized PnL still moves.
    api = _FakeExecutorsApi(
        {
            f"{RUN_KEY}_1": [_running_executor(0.5)],
            f"{RUN_KEY}_2": [_closed_executor(2.0)],
        }
    )
    use_client(_FakeClient(api))

    _compute(strategy_dir)
    agents_routes._PERF_CACHE.clear()
    _compute(strategy_dir)

    assert api.calls[f"{RUN_KEY}_1"] == 2  # never frozen while open_count > 0
    assert f"{RUN_KEY}_1" not in agents_routes._CLOSED_PERF_CACHE


def test_failed_fetch_is_not_frozen(perf_env):
    strategy_dir, use_client = perf_env
    _make_sessions(strategy_dir, [1, 2])
    api = _FakeExecutorsApi(
        {f"{RUN_KEY}_2": [_closed_executor(2.0)]}, fail_ids={f"{RUN_KEY}_1"}
    )
    use_client(_FakeClient(api))

    _compute(strategy_dir)
    assert f"{RUN_KEY}_1" not in agents_routes._CLOSED_PERF_CACHE

    # Backend recovers: the previously failed id is fetched again and frozen.
    api.fail_ids.clear()
    api.rows_by_aid[f"{RUN_KEY}_1"] = [_closed_executor(1.0)]
    agents_routes._PERF_CACHE.clear()
    _compute(strategy_dir)
    assert api.calls[f"{RUN_KEY}_1"] == 2
    assert f"{RUN_KEY}_1" in agents_routes._CLOSED_PERF_CACHE

    agents_routes._PERF_CACHE.clear()
    _, totals = _compute(strategy_dir)
    assert api.calls[f"{RUN_KEY}_1"] == 2  # frozen now
    assert totals["total_pnl"] == pytest.approx(3.0)


def test_reactivated_id_evicts_frozen_entry(perf_env):
    strategy_dir, use_client = perf_env
    _make_sessions(strategy_dir, [1, 2])
    api = _FakeExecutorsApi(
        {
            f"{RUN_KEY}_1": [_closed_executor(1.0)],
            f"{RUN_KEY}_2": [_closed_executor(2.0)],
        }
    )
    use_client(_FakeClient(api))

    _compute(strategy_dir)
    assert f"{RUN_KEY}_1" in agents_routes._CLOSED_PERF_CACHE

    # Session 1's engine comes back (e.g. restored after restart): the stale
    # frozen entry must be evicted and the id fetched fresh.
    aid1 = f"{RUN_KEY}_1"
    loops_module.get_supervisor()._engines[aid1] = SimpleNamespace(agent_id=aid1)
    agents_routes._PERF_CACHE.clear()
    _compute(strategy_dir)
    assert api.calls[f"{RUN_KEY}_1"] == 2
    assert f"{RUN_KEY}_1" not in agents_routes._CLOSED_PERF_CACHE


def test_closed_cache_never_exceeds_cap(perf_env, monkeypatch):
    """PERF-186: the frozen cache is a bounded LRU, not a monotonic dict."""
    cap = 4
    monkeypatch.setattr(agents_routes, "_CLOSED_PERF_CACHE_MAX", cap)
    n_sessions = cap + 4  # 7 closed + the newest (never frozen)
    strategy_dir, use_client = perf_env
    _make_sessions(strategy_dir, range(1, n_sessions + 1))
    api = _FakeExecutorsApi(
        {f"{RUN_KEY}_{n}": [_closed_executor(1.0)] for n in range(1, n_sessions + 1)}
    )
    use_client(_FakeClient(api))

    sessions, totals = _compute(strategy_dir)

    assert len(sessions) == n_sessions  # all sessions still render
    # Exactly `cap` closed sessions survive (freeze order follows iterdir(),
    # so which ones is filesystem-dependent); the newest is never frozen.
    assert len(agents_routes._CLOSED_PERF_CACHE) == cap
    closed_ids = {f"{RUN_KEY}_{n}" for n in range(1, n_sessions)}
    assert set(agents_routes._CLOSED_PERF_CACHE) <= closed_ids
    assert totals["total_pnl"] == pytest.approx(float(n_sessions))


def test_evicted_session_refetched_and_refrozen(perf_env, monkeypatch):
    """An LRU-evicted closed session flows through the fetch path again."""
    cap = 2
    monkeypatch.setattr(agents_routes, "_CLOSED_PERF_CACHE_MAX", cap)
    strategy_dir, use_client = perf_env
    _make_sessions(strategy_dir, [1, 2, 3, 4])
    api = _FakeExecutorsApi(
        {f"{RUN_KEY}_{n}": [_closed_executor(float(n))] for n in [1, 2, 3, 4]}
    )
    use_client(_FakeClient(api))

    sessions1, totals1 = _compute(strategy_dir)
    # Cap 2 with 3 closed sessions: exactly one closed session got evicted
    # (freeze order follows iterdir(), so which one is filesystem-dependent).
    closed_ids = {f"{RUN_KEY}_{n}" for n in [1, 2, 3]}
    cached = set(agents_routes._CLOSED_PERF_CACHE)
    evicted = closed_ids - cached
    assert len(cached) == cap
    assert len(evicted) == 1

    agents_routes._PERF_CACHE.clear()
    sessions2, totals2 = _compute(strategy_dir)

    # The evicted session was re-fetched; the still-frozen ones were not.
    for aid in evicted:
        assert api.calls[aid] == 2
    for aid in cached:
        assert api.calls[aid] == 1
    assert sorted(s.agent_id for s in sessions2) == sorted(
        s.agent_id for s in sessions1
    )
    assert totals2 == totals1
    assert totals2["total_pnl"] == pytest.approx(10.0)
