"""What a run put into the world (FEAT-100).

The ledger is an assembly of values ``get_session_executors`` already holds, so
these pin the assembly rather than the route: which rows appear, where each
one's window comes from, and the two facts the surface exists to stop getting
wrong — that liveness is read off ownership and never off a performance
snapshot's ``status``, and that an unjoinable tick is blank rather than guessed.
"""

from condor.agents.actions import AgentAction
from condor.agents.attribution import build_deployments
from condor.agents.ownership import OwnedBot
from condor.agents.performance import AgentPerformance

AGENT_ID = "brigado.brl_mm_3"


def _Perf(
    *, bot_names=(), bot_instances=(), controllers=(), executors=(), base_windows=None
):
    """An ``AgentPerformance`` carrying only what the ledger reads.

    The real dataclass, not a look-alike: the session route projects it onto
    the wire model by dataclass field (``AgentPerformanceModel.from_perf``).
    """
    return AgentPerformance(
        agent_id=AGENT_ID,
        bot_names=list(bot_names),
        bot_instances=list(bot_instances),
        controllers=list(controllers),
        executors=list(executors),
        base_windows=dict(base_windows or {}),
    )


def _controller(bot_name, cid, pnl=0.0, volume=0.0, status="running"):
    return {
        "bot_name": bot_name,
        "controller_id": cid,
        "controller_name": "pmm_simple",
        "connector": "binance",
        "trading_pair": "SOL-USDC",
        "status": status,
        "realized_pnl_quote": pnl,
        "unrealized_pnl_quote": 0.0,
        "volume_traded": volume,
        "cum_fees_quote": 0.0,
        "closed_trades": 0,
        "close_type_counts": {},
    }


def _executor(eid, controller_id=AGENT_ID, timestamp=2000.0, close_timestamp=0.0):
    return {
        "id": eid,
        "type": "grid_executor",
        "connector": "binance",
        "pair": "SOL-USDC",
        "status": "RUNNING",
        "close_type": "",
        "pnl": -0.31,
        "volume": 2400.0,
        "timestamp": timestamp,
        "close_timestamp": close_timestamp,
        "controller_id": controller_id,
    }


def _by_kind(rows, kind):
    return [r for r in rows if r.kind == kind]


# ── A deployed bot with two controllers ──


def _deployed_run():
    owned = [
        OwnedBot(base="brigado-brl_mm", origin="deployed", since=1000.0, last_seen=9e9)
    ]
    perf = _Perf(
        bot_names=["brigado-brl_mm-20260807-022100"],
        bot_instances=["brigado-brl_mm-20260807-022100"],
        controllers=[
            _controller("brigado-brl_mm-20260807-022100", "ema_sol_1h", 0.05, 193.0),
            _controller("brigado-brl_mm-20260807-022100", "ema_btc_4h", -0.01, 12.0),
        ],
    )
    actions = [
        AgentAction(
            tick=10,
            at=980.0,
            tool="manage_bots",
            verb="manage_bots:deploy",
            summary="Deploy brigado-brl_mm",
            ok=True,
        )
    ]
    return build_deployments(owned, ["brigado-brl_mm"], perf, actions, AGENT_ID)


def test_a_deployed_bot_and_its_controllers_are_all_rows():
    rows = _deployed_run()
    assert [r.kind for r in rows] == ["bot", "controller", "controller"]
    assert _by_kind(rows, "bot")[0].label == "brigado-brl_mm"
    assert {r.label for r in _by_kind(rows, "controller")} == {
        "ema_sol_1h",
        "ema_btc_4h",
    }


def test_the_bot_row_folds_its_controllers_money():
    bot = _by_kind(_deployed_run(), "bot")[0]
    assert round(bot.pnl, 4) == 0.04
    assert bot.volume == 205.0


# ── A bot row reports the run's slice, not the bot's lifetime (CORR-661) ──


def test_bot_row_pnl_matches_the_sliced_kpi():
    """An adopted bot: lifetime realized 100, this run's slice 60, open book 7.

    The KPI strip reads 67; the ledger row beneath it must too, not 107.
    """
    inst = "ns-bot-20260701-000000"
    c1 = {
        **_controller(inst, "c1", pnl=70.0, volume=6000.0),
        "unrealized_pnl_quote": 4.0,
    }
    c2 = {
        **_controller(inst, "c2", pnl=30.0, volume=3000.0),
        "unrealized_pnl_quote": 3.0,
    }
    perf = _Perf(
        bot_names=[inst],
        bot_instances=[inst],
        controllers=[c1, c2],
        base_windows={"ns-bot": (60.0, 5000.0, 3, 5.0)},
    )

    held = [OwnedBot(base="ns-bot", origin="adopted", since=2000.0, last_seen=9e9)]
    bot = _by_kind(build_deployments(held, ["ns-bot"], perf, [], AGENT_ID), "bot")[0]
    assert bot.live is True
    assert bot.pnl == 67.0
    assert bot.volume == 5000.0

    released = [
        OwnedBot(
            base="ns-bot",
            origin="adopted",
            since=2000.0,
            last_seen=3000.0,
            until=3000.0,
        )
    ]
    bot = _by_kind(build_deployments(released, [], perf, [], AGENT_ID), "bot")[0]
    assert bot.live is False
    # The open book belongs to whoever holds the bot now.
    assert bot.pnl == 60.0
    assert bot.volume == 5000.0

    # Controller rows stay lifetime: the history is per instance, not controller.
    assert [
        c.pnl
        for c in _by_kind(
            build_deployments(held, ["ns-bot"], perf, [], AGENT_ID), "controller"
        )
    ] == [74.0, 33.0]


def test_an_unpriced_controller_is_flagged_and_left_out_of_its_bots_row():
    """A quote with no USD rate stays at face value (CORR-703): its row says so,
    and the bot row does not fold native money into USD figures (CORR-707)."""
    inst = "ns-bot-20260701-000000"
    usd = {
        **_controller(inst, "usd", pnl=20.0, volume=2000.0),
        "unrealized_pnl_quote": 2.0,
    }
    eur = {
        **_controller(inst, "eur", pnl=10.0, volume=900.0),
        "unrealized_pnl_quote": 5.0,
        "quote": "EUR",
        "usd_converted": False,
    }
    perf = _Perf(bot_names=[inst], bot_instances=[inst], controllers=[usd, eur])
    held = [OwnedBot(base="ns-bot", origin="deployed", since=1.0, last_seen=9e9)]

    # No window: the lifetime fallback sums only the priced controller.
    rows = build_deployments(held, ["ns-bot"], perf, [], AGENT_ID)
    bot = _by_kind(rows, "bot")[0]
    assert (bot.pnl, bot.volume, bot.usd_converted) == (22.0, 2000.0, True)
    ctrls = {c.label: c for c in _by_kind(rows, "controller")}
    assert ctrls["eur"].usd_converted is False
    assert ctrls["usd"].usd_converted is True
    assert (ctrls["usd"].pnl, ctrls["usd"].volume) == (22.0, 2000.0)

    # With a window: the open book takes only the priced controller's unrealized.
    windowed = _Perf(
        bot_names=[inst],
        bot_instances=[inst],
        controllers=[usd, eur],
        base_windows={"ns-bot": (15.0, 1500.0, 2, 1.0)},
    )
    bot = _by_kind(build_deployments(held, ["ns-bot"], windowed, [], AGENT_ID), "bot")[
        0
    ]
    assert (bot.pnl, bot.volume) == (17.0, 1500.0)


def test_a_stopped_bot_row_carries_its_sliced_window():
    """No live snapshot, so no controllers — but the KPI counts what it realized."""
    owned = [
        OwnedBot(
            base="old-bot",
            origin="deployed",
            since=1000.0,
            last_seen=4000.0,
            until=4000.0,
        )
    ]
    perf = _Perf(
        bot_instances=["old-bot-20260701-000000"],
        base_windows={"old-bot": (12.5, 800.0, 2, 0.4)},
    )
    bot = _by_kind(build_deployments(owned, [], perf, [], AGENT_ID), "bot")[0]
    assert bot.pnl == 12.5
    assert bot.volume == 800.0


def test_a_controller_inherits_its_bots_window_and_tick():
    """A controller has no creating call: it arrived with the deploy."""
    rows = _deployed_run()
    bot = _by_kind(rows, "bot")[0]
    for c in _by_kind(rows, "controller"):
        assert c.created_tick == bot.created_tick == 10
        assert c.started_at == bot.started_at == 1000.0


def test_a_row_links_into_the_fleet_at_its_own_address():
    rows = _deployed_run()
    assert _by_kind(rows, "bot")[0].scope == "bot:brigado-brl_mm-20260807-022100"
    assert (
        _by_kind(rows, "controller")[0].scope
        == "ctrl:brigado-brl_mm-20260807-022100:ema_sol_1h"
    )


def test_the_bot_row_links_to_the_live_deploy_not_the_base():
    """A base is not a fleet node; the instance it deployed under is."""
    owned = [OwnedBot(base="b", origin="deployed", since=1.0, last_seen=1.0)]
    perf = _Perf(bot_names=["b-20260807-022100"], bot_instances=["b-20260101-000000"])
    rows = build_deployments(owned, ["b"], perf, [], AGENT_ID)
    assert rows[0].scope == "bot:b-20260807-022100"


# ── Liveness is ownership, not status ──


def test_a_bot_released_mid_run_reads_closed_even_while_it_runs_on():
    """`until` is what stops a finished run claiming PnL it had nothing to do with."""
    owned = [
        OwnedBot(
            base="brigado-brl_mm",
            origin="deployed",
            since=1000.0,
            last_seen=5000.0,
            until=5000.0,
        )
    ]
    perf = _Perf(
        bot_names=["brigado-brl_mm-20260807-022100"],
        controllers=[_controller("brigado-brl_mm-20260807-022100", "c1")],
    )
    # The instance is still deployed and its snapshot still says "running", but
    # this session handed it over: `bot_bases` no longer carries the base.
    rows = build_deployments(owned, [], perf, [], AGENT_ID)
    bot = _by_kind(rows, "bot")[0]
    assert bot.live is False
    assert bot.ended_at == 5000.0


def test_a_live_bot_has_no_end():
    rows = _deployed_run()
    assert _by_kind(rows, "bot")[0].live is True
    assert _by_kind(rows, "bot")[0].ended_at is None


# ── Standalone executors ──


def test_only_the_executors_this_run_created_itself_are_listed():
    perf = _Perf(
        executors=[
            _executor("e1"),
            _executor("e2", controller_id="someone_elses_controller"),
        ]
    )
    rows = build_deployments([], [], perf, [], AGENT_ID)
    assert [r.label for r in rows] == ["grid SOL-USDC"]
    assert rows[0].scope == "exec:e1"
    assert rows[0].pnl == -0.31


def test_a_closed_executor_reads_closed_with_its_end_time():
    perf = _Perf(executors=[_executor("e1", close_timestamp=3000.0)])
    rows = build_deployments([], [], perf, [], AGENT_ID)
    assert rows[0].live is False
    assert rows[0].ended_at == 3000.0


def test_an_executor_is_credited_to_the_tick_that_created_it():
    actions = [
        AgentAction(
            tick=4,
            at=1900.0,
            tool="create_grid_executor",
            verb="create_grid_executor",
            summary="Create grid executor on SOL-USDC",
            ok=True,
        )
    ]
    perf = _Perf(executors=[_executor("e1", timestamp=2000.0)])
    rows = build_deployments([], [], perf, actions, AGENT_ID)
    assert rows[0].created_tick == 4


# ── Runs that predate the log, and runs that did nothing ──


def test_a_legacy_run_with_no_ledger_and_no_log_still_renders_its_executors():
    """Every session on disk when this shipped has neither file."""
    perf = _Perf(executors=[_executor("e1")])
    rows = build_deployments([], [], perf, [], AGENT_ID)
    assert len(rows) == 1
    assert rows[0].created_tick is None


def test_a_run_with_an_owned_bot_but_no_actions_log_leaves_the_tick_blank():
    owned = [
        OwnedBot(base="ema_trend_loop", origin="deployed", since=1000.0, last_seen=1.0)
    ]
    rows = build_deployments(owned, ["ema_trend_loop"], _Perf(), [], AGENT_ID)
    assert rows[0].label == "ema_trend_loop"
    assert rows[0].detail == "deployed"
    assert rows[0].created_tick is None


def test_an_adopted_bot_has_no_creating_call_by_definition():
    owned = [OwnedBot(base="b", origin="adopted", since=1000.0, last_seen=1.0)]
    actions = [
        AgentAction(
            tick=2,
            at=990.0,
            tool="manage_bots",
            verb="manage_bots:stop",
            summary="",
            ok=True,
        )
    ]
    rows = build_deployments(owned, ["b"], _Perf(), actions, AGENT_ID)
    assert rows[0].detail == "adopted"
    assert rows[0].created_tick is None


def test_a_run_that_deployed_nothing_has_an_empty_ledger():
    assert build_deployments([], [], _Perf(), [], AGENT_ID) == []


# ── The response ──


class _User:
    id = 1
    username = "u"


def _strategy_on_disk(tmp_path):
    sdir = tmp_path / "ag" / "strategies" / "st"
    (sdir / "sessions" / "session_1").mkdir(parents=True)
    (sdir / "strategy.md").write_text("# playbook\n")
    return sdir


def test_a_session_with_no_server_still_answers_with_every_field(tmp_path, monkeypatch):
    """The no-client branch gained the key too, so no caller has to feature-test."""
    import asyncio

    from condor.web.routes import agents as agents_route

    sdir = _strategy_on_disk(tmp_path)
    monkeypatch.setattr(
        agents_route,
        "_get_strategy",
        lambda slug, sslug: type("S", (), {"home": sdir, "default_config": {}})(),
    )

    async def _no_client(*a, **kw):
        return None, None, "no_server"

    monkeypatch.setattr(agents_route, "_get_client_for_strategy", _no_client)
    out = asyncio.run(agents_route.get_session_executors("ag", "st", 1, user=_User()))
    assert set(out) == {
        "executors",
        "performance",
        "pnl_series",
        "deployments",
        "unavailable",
    }
    assert out["deployments"] == []


def test_the_endpoint_serves_the_ledger_the_session_recorded(tmp_path, monkeypatch):
    """End to end over the session dir: ledger on disk, log on disk, rows on the wire."""
    import asyncio
    import json

    from condor.agents import performance as perf_mod
    from condor.web.routes import agents as agents_route

    sdir = _strategy_on_disk(tmp_path)
    session_dir = sdir / "sessions" / "session_1"
    (session_dir / "owned_bots.json").write_text(
        json.dumps(
            {
                "namespace": "ag-st",
                "bots": {
                    "ag-st": {
                        "base": "ag-st",
                        "origin": "deployed",
                        "since": 1000.0,
                        "last_seen": 2000.0,
                        "until": 0.0,
                    }
                },
            }
        )
    )
    (session_dir / "actions.jsonl").write_text(
        json.dumps(
            {
                "tick": 10,
                "at": 980.0,
                "tool": "manage_bots",
                "verb": "manage_bots:deploy",
                "summary": "Deploy ag-st",
                "ok": True,
                "error": "",
            }
        )
        + "\n"
    )

    monkeypatch.setattr(
        agents_route,
        "_get_strategy",
        lambda slug, sslug: type("S", (), {"home": sdir, "default_config": {}})(),
    )

    async def _client(*a, **kw):
        return object(), None, ""

    async def _perf(*a, **kw):
        return _Perf(
            bot_names=["ag-st-20260807-022100"],
            bot_instances=["ag-st-20260807-022100"],
            controllers=[_controller("ag-st-20260807-022100", "c1", 0.05, 193.0)],
        )

    async def _series(*a, **kw):
        return []

    monkeypatch.setattr(agents_route, "_get_client_for_strategy", _client)
    monkeypatch.setattr(perf_mod, "fetch_agent_performance", _perf)
    monkeypatch.setattr(perf_mod, "fetch_agent_pnl_series", _series)

    out = asyncio.run(agents_route.get_session_executors("ag", "st", 1, user=_User()))
    rows = out["deployments"]
    assert [r["kind"] for r in rows] == ["bot", "controller"]
    assert rows[0]["label"] == "ag-st"
    assert rows[0]["created_tick"] == 10
    assert rows[0]["live"] is True
    assert rows[1]["scope"] == "ctrl:ag-st-20260807-022100:c1"
