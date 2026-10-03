"""The hackathon race monitor posts what the bots page shows, counted from the start."""

from __future__ import annotations

import asyncio
import json

import pytest

from condor.agents.fleet_map import FleetOwner
from condor.agents.performance import AgentPerformance
from scripts import hackathon_monitor as monitor
from scripts.hackathon_monitor import Totals


def _owner(agent: str, strategy: str, sessions: int = 1, **extra) -> FleetOwner:
    run_key = f"{agent}.{strategy}"
    return FleetOwner(
        run_key=run_key,
        agent_slug=agent,
        agent_name=agent,
        strategy_slug=strategy,
        strategy_name=strategy,
        namespace=f"{agent}-{strategy}",
        agent_ids=[f"{run_key}_{n}" for n in range(1, sessions + 1)],
        **extra,
    )


def test_the_race_url_is_built_from_the_base_and_the_slug():
    assert (
        monitor.race_url("https://race.example/", "agent-builders-cup-1")
        == "https://race.example/api/hackathons/agent-builders-cup-1/race-data"
    )


@pytest.mark.parametrize(
    "payload",
    [
        ["grid_a", "mm_b"],
        {"agents": ["grid_a", "mm_b"]},
        {"agents": [{"agent_id": "grid_a"}, {"agent_id": "mm_b", "name": "B"}]},
        {"agent_ids": ["grid_a", "mm_b", "grid_a"]},
        {"data": [{"agent_id": "grid_a"}, {"agent_id": "mm_b"}]},
    ],
)
def test_agent_ids_are_read_from_any_reasonable_shape(payload):
    assert monitor.parse_agent_ids(payload) == ["grid_a", "mm_b"]


def test_an_unreadable_agent_list_is_empty_not_an_error():
    assert monitor.parse_agent_ids({"error": "nope"}) == []
    assert monitor.parse_agent_ids(None) == []


def test_an_id_matches_its_strategy_slug_under_every_agent():
    owners = [
        _owner("alice", "grid_a"),
        _owner("bob", "grid_a"),
        _owner("bob", "mm_b"),
        # A pseudo-run: no namespace, never a race agent.
        FleetOwner("condor.chat", "condor", "Condor", "chat", "Chat", namespace=""),
    ]

    matched = monitor.match_owners(["grid_a", "bob.mm_b", "chat", "ghost"], owners)

    assert [o.run_key for o in matched["grid_a"]] == ["alice.grid_a", "bob.grid_a"]
    assert [o.run_key for o in matched["bob.mm_b"]] == ["bob.mm_b"]
    assert matched["chat"] == []
    assert matched["ghost"] == []


def test_totals_sum_an_owners_sessions_and_attach_its_bots_once(monkeypatch):
    calls = {}

    async def fake_batch(client, agent_ids, bot_names, failed_ids=None):
        calls["agent_ids"] = agent_ids
        calls["bot_names"] = bot_names
        failed_ids.add("bob.mm_b_1")
        return {
            "alice.grid_a_1": AgentPerformance(
                "alice.grid_a_1", total_pnl=10, volume=100
            ),
            "alice.grid_a_2": AgentPerformance(
                "alice.grid_a_2", total_pnl=-4, volume=50
            ),
            "bob.mm_b_1": AgentPerformance("bob.mm_b_1"),
            "carol.new_c": AgentPerformance("carol.new_c", total_pnl=1, volume=2),
        }

    monkeypatch.setattr(
        "condor.agents.performance.fetch_agent_performance_batch", fake_batch
    )
    matched = {
        "grid_a": [_owner("alice", "grid_a", sessions=2, declared_bots=["legacy-bot"])],
        "mm_b": [_owner("bob", "mm_b")],
        # Deployed a bot but never opened a session: its run key carries the bots.
        "new_c": [_owner("carol", "new_c", sessions=0)],
        "ghost": [],
    }

    totals, degraded = asyncio.run(monitor.collect_totals(object(), matched))

    assert totals["grid_a"] == Totals(pnl=6, volume=150)
    assert totals["new_c"] == Totals(pnl=1, volume=2)
    assert totals["ghost"] == Totals(), "no strategy has traded nothing"
    assert degraded == {"mm_b"}
    assert calls["bot_names"] == {
        "alice.grid_a_1": ["alice-grid_a", "legacy-bot"],
        "bob.mm_b_1": ["bob-mm_b"],
        "carol.new_c": ["carol-new_c"],
    }
    assert "alice.grid_a_2" in calls["agent_ids"]


def test_the_baseline_round_trips_and_is_subtracted(tmp_path):
    path = tmp_path / "nested" / "race.json"
    assert monitor.load_baseline(path) is None

    monitor.save_baseline(path, {"grid_a": Totals(5, 1000)}, now=1_800_000_000)
    baseline = monitor.load_baseline(path)

    assert baseline == {"grid_a": Totals(5, 1000)}
    now = {"grid_a": Totals(12, 1500), "late": Totals(3, 40)}
    assert monitor.since_start(now, baseline) == {
        "grid_a": Totals(7, 500),
        "late": Totals(3, 40),
    }
    assert monitor.since_start(now, None) == now, "no baseline posts lifetime"


def test_the_payload_keeps_the_race_order_and_drops_agents_with_no_figure():
    payload = monitor.build_payload(
        ["mm_b", "grid_a", "down"],
        {"grid_a": Totals(1.23456789, 10), "mm_b": Totals(-2, 0)},
        now=1_800_000_000,
    )

    assert payload == {
        "timestamp": "2027-01-15T08:00:00Z",
        "agents": [
            {"agent_id": "mm_b", "pnl_quote": -2, "volume_quote": 0},
            {"agent_id": "grid_a", "pnl_quote": 1.234568, "volume_quote": 10},
        ],
    }
    json.dumps(payload)


def test_an_agent_is_summed_across_servers():
    last_good: dict = {}
    answers = {
        "local": ({"grid_a": Totals(10, 100), "mm_b": Totals(1, 5)}, set()),
        "remote": ({"grid_a": Totals(-3, 40), "mm_b": Totals(0, 0)}, set()),
    }

    totals, unknown = monitor.combine(["grid_a", "mm_b"], answers, last_good)

    assert totals == {"grid_a": Totals(7, 140), "mm_b": Totals(1, 5)}
    assert unknown == {}
    assert last_good["remote"]["grid_a"] == Totals(-3, 40)


def test_a_server_that_stops_answering_keeps_its_last_figure():
    """An outage must not read as an agent that gave its PnL back."""
    last_good: dict = {}
    ids = ["grid_a", "mm_b"]
    up = {"grid_a": Totals(10, 100), "mm_b": Totals(1, 5)}
    remote = {"grid_a": Totals(5, 50), "mm_b": Totals(2, 20)}
    monitor.combine(ids, {"local": (up, set()), "remote": (remote, set())}, last_good)

    # The remote is down, and the local one degraded for a single agent.
    later = {"grid_a": Totals(0, 0), "mm_b": Totals(3, 9)}
    totals, unknown = monitor.combine(
        ids, {"local": (later, {"grid_a"}), "remote": None}, last_good
    )

    assert totals == {"grid_a": Totals(15, 150), "mm_b": Totals(5, 29)}
    assert unknown == {}


def test_a_server_never_heard_from_counts_as_nothing_and_is_named():
    totals, unknown = monitor.combine(
        ["grid_a"], {"local": ({"grid_a": Totals(4, 8)}, set()), "dead": None}, {}
    )

    assert totals == {"grid_a": Totals(4, 8)}
    assert unknown == {"dead": {"grid_a"}}


def test_last_figures_survive_a_restart_beside_the_baseline(tmp_path):
    path = tmp_path / "race.json"
    monitor.save_last_good(path, {"remote": {"grid_a": Totals(5, 50)}})
    monitor.save_baseline(path, {"grid_a": Totals(1, 2)}, now=1_800_000_000)
    monitor.save_last_good(path, {"remote": {"grid_a": Totals(6, 60)}})

    assert monitor.load_last_good(path) == {"remote": {"grid_a": Totals(6, 60)}}
    assert monitor.load_baseline(path) == {"grid_a": Totals(1, 2)}


def test_one_api_under_two_names_is_read_once():
    servers = {
        "local": {"host": "localhost", "port": 8000},
        "alias": {"host": "localhost", "port": 8000},
        "remote": {"host": "box.example", "port": 8000},
    }

    assert monitor.distinct_servers(servers, []) == ["local", "remote"]
    assert monitor.distinct_servers(servers, ["remote"]) == ["remote"]
    with pytest.raises(ValueError, match="nope"):
        monitor.distinct_servers(servers, ["nope"])


def test_starting_a_race_twice_is_refused(tmp_path, capsys):
    state = tmp_path / "race.json"
    base = ["--url", "https://x", "--token", "t", "--state", str(state)]
    # Remembered figures alone are not a baseline.
    monitor.save_last_good(state, {"local": {"grid_a": Totals(1, 1)}})
    args = monitor.parse_args([*base, "--start-race", "--servers", "local, remote"])
    assert args.servers == ["local", "remote"]

    monitor.save_baseline(state, {"grid_a": Totals(1, 1)}, now=1_800_000_000)
    with pytest.raises(SystemExit):
        monitor.parse_args([*base, "--start-race"])

    assert "already holds a baseline" in capsys.readouterr().err


# ── Stopped trading still counts (the race is cumulative) ──


class _StopClient:
    """One agent's trading in every stopped and running shape, as the API serves it.

    - ``alice-grid_a-20261003-100000`` is a live bot with two controllers; the
      agent paused ``c_killed`` (``manual_kill_switch``), which keeps it listed in
      the bot's reports with its final figures.
    - ``alice-grid_a-20261002-090000`` is a bot the agent stopped and archived;
      its last snapshot holds a frozen unrealized mark nobody owns any more.
    - ``alice.grid_a_1`` tagged two standalone executors, one closed, one open.
    """

    base_url = ""  # no cross-test caching

    LIVE = "alice-grid_a-20261003-100000"
    STOPPED = "alice-grid_a-20261002-090000"

    def __init__(self):
        def snap(bot, cid, realized, unrealized, volume):
            return {
                "bot_name": bot,
                "controller_id": cid,
                "trading_pair": "BTC-USDT",
                "timestamp": "2026-10-03T10:00:00",
                "performance": {
                    "realized_pnl_quote": realized,
                    "unrealized_pnl_quote": unrealized,
                    "volume_traded": volume,
                },
            }

        snapshots = [
            snap(self.LIVE, "c_live", 5.0, 2.0, 100.0),
            snap(self.LIVE, "c_killed", 7.0, 0.0, 300.0),
            snap(self.STOPPED, "c_old", -3.0, 4.0, 200.0),
        ]
        live = self.LIVE

        def executor(status, pnl, volume):
            return {
                "id": f"ex-{status}",
                "status": status,
                "net_pnl_quote": pnl,
                "filled_amount_quote": volume,
                "cum_fees_quote": 0.0,
                "config": {"type": "order_executor"},
            }

        rows = {
            "alice.grid_a_1": [
                executor("TERMINATED", 1.5, 50.0),
                executor("RUNNING", -0.5, 20.0),
            ]
        }

        class _Orchestration:
            async def get_latest_controller_performance(self, bot_name=None):
                return {"data": snapshots}

            async def get_active_bots_status(self):
                return {"status": "success", "data": {live: {"status": "running"}}}

        class _Executors:
            async def search_executors(self, controller_ids, limit, cursor=None):
                return {"executors": rows.get(controller_ids[0], [])}

        stopped = self.STOPPED

        class _Archived:
            async def list_databases(self):
                return [f"bots/archived/{stopped}/data/{stopped}.sqlite"]

        self.bot_orchestration = _Orchestration()
        self.executors = _Executors()
        self.archived_bots = _Archived()


@pytest.fixture
def _clean_perf_caches():
    from condor.fetchers import bot_performance as bp

    bp.clear_snapshot_cache()
    bp.clear_history_cache()
    bp.clear_archived_cache()
    bp.clear_live_names_cache()
    yield
    bp.clear_snapshot_cache()
    bp.clear_history_cache()
    bp.clear_archived_cache()
    bp.clear_live_names_cache()


def test_stopped_controllers_bots_and_executors_stay_in_the_agents_total(
    _clean_perf_caches,
):
    """Through the real aggregator: stopping anything never takes it off the board."""
    matched = {"grid_a": [_owner("alice", "grid_a")]}

    totals, degraded = asyncio.run(monitor.collect_totals(_StopClient(), matched))

    # live bot (both controllers, the paused one included) + the stopped bot's
    # realized (its frozen unrealized mark is not counted) + both executors.
    assert totals["grid_a"].pnl == pytest.approx((5 + 2) + 7 + (-3) + 1.5 + (-0.5))
    assert totals["grid_a"].volume == pytest.approx(100 + 300 + 200 + 50 + 20)
    assert degraded == set()
