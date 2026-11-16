"""A bot the venue is already running counts as a reading, even before the
performance snapshot lists it.

Reproduces the window observed on a live box: ``get_active_bots_status()``
answers the moment a deploy exists, while ``get_latest_controller_performance()``
lags it by a tick. ``resolve_bots`` keeps a base only when an instance is in both,
so in that window the base resolved to nothing and the reading stayed
``missing`` — which the risk gate reads as "no drawdown" and refuses the tick.
A freshly deployed bot-mode agent therefore deployed, blocked, and only traded
once the snapshot caught up.
"""

import asyncio

from condor.agents.performance import fetch_agent_performance_batch
from condor.fetchers.bot_performance import clear_live_names_cache, clear_snapshot_cache

BASE = "quad-e4"
LIVE_INSTANCE = "quad-e4-20261115-092151"


def _snapshot(bot_name, realized=0.0, unrealized=0.0, volume=0.0):
    return {
        "bot_name": bot_name,
        "timestamp": "2026-11-15T09:21:51",
        "performance": {
            "realized_pnl_quote": realized,
            "unrealized_pnl_quote": unrealized,
            "volume_traded": volume,
            "positions_summary": [],
        },
    }


class _Orchestration:
    def __init__(self, snapshots, active):
        self._snapshots = snapshots
        self._active = active

    async def get_latest_controller_performance(self, bot_name=None):
        return {"data": self._snapshots}

    async def get_active_bots_status(self):
        return self._active


class _Executors:
    async def search_executors(self, controller_ids, limit, cursor=None):
        return {"executors": []}


class _Client:
    def __init__(self, snapshots, active):
        self.executors = _Executors()
        self.bot_orchestration = _Orchestration(snapshots, active)


def _run(snapshots, active):
    clear_snapshot_cache()
    clear_live_names_cache()
    client = _Client(snapshots, active)
    return asyncio.run(
        fetch_agent_performance_batch(
            client, ["desk.orchestrate_1"], {"desk.orchestrate_1": [BASE]}
        )
    )["desk.orchestrate_1"]


def test_a_deploy_the_snapshot_has_not_listed_yet_is_still_a_reading():
    # The venue says the instance is running; the snapshot does not know it yet.
    perf = _run(
        [], {"status": "success", "data": {LIVE_INSTANCE: {"status": "running"}}}
    )
    assert perf.reading == "ok"


def test_the_lagging_deploy_is_named_but_not_priced():
    # The fix raises the reading alone: no figures are invented for an instance
    # the snapshot has never seen.
    perf = _run(
        [], {"status": "success", "data": {LIVE_INSTANCE: {"status": "running"}}}
    )
    assert perf.reading == "ok"
    assert perf.realized_pnl == 0.0
    assert perf.unrealized_pnl == 0.0
    assert perf.volume == 0.0
    assert perf.bot_names == [BASE]


def test_nothing_running_leaves_the_reading_missing():
    perf = _run([], {"status": "success", "data": {}})
    assert perf.reading == "missing"


def test_a_live_instance_under_another_base_does_not_raise_this_one():
    perf = _run(
        [],
        {
            "status": "success",
            "data": {"other-bot-20261115-000000": {"status": "running"}},
        },
    )
    assert perf.reading == "missing"


def test_the_snapshot_still_decides_when_it_has_the_instance():
    # Snapshot and listing agree: the aggregate's own reading is what counts.
    snap = _snapshot(LIVE_INSTANCE, realized=3.0, volume=250.0)
    perf = _run(
        [snap], {"status": "success", "data": {LIVE_INSTANCE: {"status": "running"}}}
    )
    assert perf.reading == "ok"
    assert perf.realized_pnl == 3.0
    assert perf.volume == 250.0


class _BrokenOrchestration(_Orchestration):
    async def get_latest_controller_performance(self, bot_name=None):
        raise RuntimeError("snapshot down")


def test_a_degraded_snapshot_does_not_become_a_reading():
    """A failed performance fetch is *unknown*, even if the bot is still listed live.

    `fetch_bot_universe_checked` degrades to {} with degraded=True when the
    snapshot fetch raises. The liveness listing still names the running
    instance, so an unguarded rescue would report `ok` for a tick that read
    nothing — the tri-state's whole point is to keep those apart.
    """
    clear_snapshot_cache()
    clear_live_names_cache()
    client = _Client(
        [], {"status": "success", "data": {LIVE_INSTANCE: {"status": "running"}}}
    )
    client.bot_orchestration = _BrokenOrchestration(
        [], {"status": "success", "data": {LIVE_INSTANCE: {"status": "running"}}}
    )
    perf = asyncio.run(
        fetch_agent_performance_batch(
            client, ["desk.orchestrate_1"], {"desk.orchestrate_1": [BASE]}
        )
    )["desk.orchestrate_1"]
    assert (
        perf.reading == "missing"
    ), "a failed snapshot must not read as a valid reading"
