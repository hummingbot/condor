"""Tests for the closed-session performance freeze (PERF-058).

``_compute_strategy_performance`` must fetch executors only for ACTIVE ids
(live engines + the newest session) after the 30s rollup cache expires; closed
sessions/experiments are immutable and get served from ``_CLOSED_PERF_CACHE``
after one final successful fetch.
"""

import ast
import asyncio
import inspect
import json
import textwrap
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
        async def _fake_get_client(strategy_dir, default_config, principal):
            return client, "srv", ""

        monkeypatch.setattr(agents_routes, "_get_client_for_strategy", _fake_get_client)

    return tmp_path, use_client


def _compute(strategy_dir):
    sessions, totals, _unavailable = asyncio.run(
        agents_routes._compute_strategy_performance(RUN_KEY, strategy_dir, None, 1)
    )
    return sessions, totals


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


def test_failed_fetch_is_not_cached_in_rollup(perf_env):
    """CORR-666: a render with a failed executor fetch skips the 30s rollup cache."""
    strategy_dir, use_client = perf_env
    _make_sessions(strategy_dir, [1, 2])
    api = _FakeExecutorsApi(
        {f"{RUN_KEY}_2": [_closed_executor(2.0)]}, fail_ids={f"{RUN_KEY}_1"}
    )
    use_client(_FakeClient(api))

    _compute(strategy_dir)
    assert agents_routes._PERF_CACHE == {}
    # No manual cache clear: the next poll must go back to the backend.
    _compute(strategy_dir)
    assert api.calls[f"{RUN_KEY}_1"] == 2

    api.fail_ids.clear()
    api.rows_by_aid[f"{RUN_KEY}_1"] = [_closed_executor(1.0)]
    _, totals = _compute(strategy_dir)
    assert api.calls[f"{RUN_KEY}_1"] == 3
    assert totals["total_pnl"] == pytest.approx(3.0)
    # A clean result is cached again.
    assert len(agents_routes._PERF_CACHE) == 1


# ── CORR-700: the bot snapshot is the rollup's other fetch ──

#: Takeover instant for the ledgers below; any fixed past epoch will do, since
#: the histories these tests serve are empty and only the open window matters.
_SINCE = 1751328000.0  # 2026-07-01T00:00:00+00:00


def _write_bot_ledger(strategy_dir, num: int, base: str) -> None:
    """A session that owns one bot, still open, as ``BotLedger`` serializes it.

    Without a ledger a strategy is direct-executor and ``apply_bot_mode_pnl``
    returns before asking the backend anything — which is exactly why the
    executor-fetch tests above never touch the snapshot.
    """
    (strategy_dir / "sessions" / f"session_{num}" / "owned_bots.json").write_text(
        json.dumps(
            {
                "namespace": "ns",
                "declared": [],
                "bots": {
                    base: {
                        "base": base,
                        "origin": "deployed",
                        "since": _SINCE,
                        "last_seen": _SINCE,
                        "until": 0.0,
                    }
                },
                "violations": [],
            }
        )
    )


class _FakeBotClient:
    """The rollup's other half: controller-performance, liveness, archives.

    ``fail`` makes the snapshot endpoint raise the way an outage does — the one
    failure ``fetch_bot_universe`` degrades to an empty live set instead of
    propagating.
    """

    def __init__(self, executors_api, instance: str, unrealized=0.0, fail=False):
        self.executors = executors_api
        self._instance = instance
        self._unrealized = unrealized
        self.fail = fail
        self.snapshot_calls = 0
        self.bot_orchestration = self
        self.archived_bots = SimpleNamespace(list_databases=self._list_databases)

    async def get_latest_controller_performance(self):
        self.snapshot_calls += 1
        if self.fail:
            raise RuntimeError("controller-performance/latest timed out")
        return [
            {
                "bot_name": self._instance,
                "controller_id": f"{self._instance}-c1",
                "timestamp": "2026-07-04T00:00:00+00:00",
                "status": "RUNNING",
                "performance": {
                    "realized_pnl_quote": 0.0,
                    "unrealized_pnl_quote": self._unrealized,
                    "volume_traded": 0.0,
                    "positions_summary": [
                        {
                            "trading_pair": "BTC-USD",
                            "connector_name": "hyperliquid",
                            "side": "TradeType.BUY",
                            "amount": 1.0,
                            "breakeven_price": 100.0,
                            "unrealized_pnl_quote": self._unrealized,
                            "cum_fees_quote": 0.0,
                        }
                    ],
                },
            }
        ]

    async def get_active_bots_status(self):
        return {"data": {self._instance: {}}}

    async def get_controller_performance_history(self, bot_name, interval, limit):
        return {"data": []}

    async def _list_databases(self):
        return []


def test_failed_bot_snapshot_is_not_cached_in_rollup(perf_env):
    """CORR-700: a snapshot outage is an outage, not a strategy with no open book.

    The executor fetch is only half of what the rollup asks the backend for; the
    other half is the bot snapshot, whose failure degrades silently to an empty
    live set. Before this, that degraded row — no unrealized PnL, no open
    positions — was cached for the full 30s TTL and kept being served after the
    backend came back.
    """
    strategy_dir, use_client = perf_env
    _make_sessions(strategy_dir, [1])
    _write_bot_ledger(strategy_dir, 1, "ns-bot")
    api = _FakeExecutorsApi({f"{RUN_KEY}_1": [_closed_executor(1.0)]})
    client = _FakeBotClient(api, "ns-bot-20260701-000000", unrealized=7.0, fail=True)
    use_client(client)

    sessions, totals = _compute(strategy_dir)
    assert client.snapshot_calls == 1
    # The degraded render still serves what it has — the executor row — but
    # without the live open book it is not the whole truth.
    assert totals["unrealized_pnl"] == 0.0
    assert totals["open_positions"] == 0
    assert agents_routes._PERF_CACHE == {}

    # No manual cache clear: the next poll must go back to the backend.
    _compute(strategy_dir)
    assert client.snapshot_calls == 2

    # Recovered: the full result, and only now is it worth keeping.
    client.fail = False
    sessions, totals = _compute(strategy_dir)
    assert client.snapshot_calls == 3
    assert totals["unrealized_pnl"] == pytest.approx(7.0)
    assert totals["open_positions"] == 1
    assert totals["total_pnl"] == pytest.approx(8.0)
    assert len(agents_routes._PERF_CACHE) == 1

    # And the healthy result is genuinely cached, not re-fetched.
    _compute(strategy_dir)
    assert client.snapshot_calls == 3


def test_healthy_bot_snapshot_alone_does_not_block_the_rollup_cache(perf_env):
    """The new flag must mean "the snapshot failed", not "a bot was attributed"."""
    strategy_dir, use_client = perf_env
    _make_sessions(strategy_dir, [1])
    _write_bot_ledger(strategy_dir, 1, "ns-bot")
    api = _FakeExecutorsApi({f"{RUN_KEY}_1": [_closed_executor(1.0)]})
    client = _FakeBotClient(api, "ns-bot-20260701-000000", unrealized=7.0)
    use_client(client)

    _compute(strategy_dir)
    assert client.snapshot_calls == 1
    assert len(agents_routes._PERF_CACHE) == 1


def test_batch_raise_is_not_cached_in_rollup(perf_env, monkeypatch):
    """CORR-666: when the batch call itself raises, the empty rollup is not cached."""
    from condor.agents import performance as performance_module

    strategy_dir, use_client = perf_env
    _make_sessions(strategy_dir, [1, 2])
    api = _FakeExecutorsApi(
        {
            f"{RUN_KEY}_1": [_closed_executor(1.0)],
            f"{RUN_KEY}_2": [_closed_executor(2.0)],
        }
    )
    use_client(_FakeClient(api))

    real_batch = performance_module.fetch_agent_performance_batch

    async def _raising_batch(*args, **kwargs):
        raise RuntimeError("batch down")

    monkeypatch.setattr(
        performance_module, "fetch_agent_performance_batch", _raising_batch
    )
    sessions, totals = _compute(strategy_dir)
    assert sessions == []
    assert totals["total_pnl"] == 0
    assert agents_routes._PERF_CACHE == {}

    monkeypatch.setattr(performance_module, "fetch_agent_performance_batch", real_batch)
    _, totals = _compute(strategy_dir)
    assert api.calls[f"{RUN_KEY}_1"] == 1
    assert api.calls[f"{RUN_KEY}_2"] == 1
    assert totals["total_pnl"] == pytest.approx(3.0)


def test_no_client_rollup_is_still_cached(perf_env):
    """CORR-666: an offline/unpriced server (no client) keeps its cached rollup."""
    strategy_dir, use_client = perf_env
    _make_sessions(strategy_dir, [1])
    use_client(None)

    sessions, totals = _compute(strategy_dir)
    assert sessions == []
    assert totals["total_pnl"] == 0
    assert len(agents_routes._PERF_CACHE) == 1


def test_priced_is_bound_once_as_the_access_predicate():
    """READ-696: ``priced`` names only the SEC-334 access bool that picks the
    cache bucket; the experiment de-dup set has its own name, so a later
    ``if priced:`` can never silently test set emptiness."""
    src = textwrap.dedent(
        inspect.getsource(agents_routes._compute_strategy_performance)
    )
    tree = ast.parse(src)
    bindings = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id == "priced"
    ]
    assert len(bindings) == 1
    call = bindings[0]
    assert isinstance(call, ast.Call) and call.func.id == "_may_use_strategy_server"
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "experiments_with_rows" in names
