"""Session and lifetime scopes, reported side by side and each labelled (FEAT-135).

GitHub #262 reports a session summary that folded earlier stopped generations of
the same bot name into its own totals — "$120 from the current bot plus $80 from
previous runs appears as $200". Its race comment reports the inverse need: a
cumulative leaderboard that understated after every stop/archive until it summed
exactly those stopped generations, and "fixing that mid-race moved the public
board". So neither scope can be dropped and neither is a correction of the other;
what was missing is a label saying which one a figure has.

Pinned here: both scopes and their figures, the instance list each was summed
over, the unavailable case that must never fall back to the other scope, the
deliberate *absence* of a lifetime twin for the live book (#234 — unrealized PnL,
open positions and exposure are never summed across generations), and the two
surfaces that render the label.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from condor.agents.attribution import OwnershipWindow
from condor.agents.performance import AgentPerformance, fetch_agent_performance
from condor.fetchers.bot_performance import (
    clear_archived_cache,
    clear_history_cache,
    clear_live_names_cache,
    clear_snapshot_cache,
)

_CACHES = (
    clear_snapshot_cache,
    clear_history_cache,
    clear_archived_cache,
    clear_live_names_cache,
)


@pytest.fixture(autouse=True)
def _no_cache_bleed():
    """The fetchers cache per server; a test double shares no key but clear anyway."""
    for clear in _CACHES:
        clear()
    yield
    for clear in _CACHES:
        clear()


# ── The universe under test ──
#
# One stable base, ``grid``, deployed three times. The session took it over at
# TAKEOVER, which is after the first generation had already stopped — so the two
# scopes differ by exactly that generation's contribution, which is the whole
# point of labelling them.

BASE = "grid"
TAKEOVER = "2026-08-05T00:00:00+00:00"
GEN1 = "grid-20260801-000000"  # stopped before the session took over
GEN2 = "grid-20260806-000000"  # deployed and stopped inside the window
GEN3 = "grid-20260808-000000"  # still running


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def _hist_row(ts: str, realized: float, volume: float) -> dict:
    return {
        "timestamp": ts,
        "controller_id": "grid_1",
        "performance": {"realized_pnl_quote": realized, "volume_traded": volume},
    }


def _snapshot(bot_name: str, realized: float, volume: float) -> dict:
    return {
        "bot_name": bot_name,
        "controller_id": "grid_1",
        "trading_pair": "BTC-USDT",
        "performance": {
            "realized_pnl_quote": realized,
            "unrealized_pnl_quote": 0.0,
            "volume_traded": volume,
        },
    }


def _client(history: dict, snapshots: list, live: set, archived=()) -> SimpleNamespace:
    class _Orch:
        async def get_latest_controller_performance(self, bot_name=None):
            return {"data": snapshots}

        async def get_controller_performance_history(
            self, bot_name, interval, limit=None, cursor=None
        ):
            return {"data": history.get(bot_name, [])}

        async def get_active_bots_status(self):
            return {"data": {n: {} for n in live}}

    class _Archived:
        async def list_databases(self):
            return [f"bots/archived/{n}/data/{n}.sqlite" for n in archived]

    class _Executors:
        async def search_executors(self, controller_ids, limit, cursor=None):
            return {"executors": []}  # nothing carries the agent_id

    return SimpleNamespace(
        base_url="",
        executors=_Executors(),
        bot_orchestration=_Orch(),
        archived_bots=_Archived(),
    )


# Each generation's history reaches its own lifetime cumulative; the first one
# finishes before the window opens, so differencing it over the window yields 0.
THREE_GENERATIONS = {
    GEN1: [
        _hist_row("2026-08-01T01:00:00+00:00", 0.0, 0.0),
        _hist_row("2026-08-02T01:00:00+00:00", 10.0, 100.0),
    ],
    GEN2: [
        _hist_row("2026-08-06T01:00:00+00:00", 0.0, 0.0),
        _hist_row("2026-08-07T01:00:00+00:00", 20.0, 200.0),
    ],
    GEN3: [
        _hist_row("2026-08-08T01:00:00+00:00", 0.0, 0.0),
        _hist_row("2026-08-09T01:00:00+00:00", 30.0, 300.0),
    ],
}
THREE_SNAPSHOTS = [
    _snapshot(GEN1, 10.0, 100.0),
    _snapshot(GEN2, 20.0, 200.0),
    _snapshot(GEN3, 30.0, 300.0),
]


def _perf(*, windows=None, bot_names=None, **kw) -> AgentPerformance:
    client = _client(
        kw.get("history", THREE_GENERATIONS),
        kw.get("snapshots", THREE_SNAPSHOTS),
        kw.get("live", {GEN3}),
        kw.get("archived", ()),
    )
    return asyncio.run(
        fetch_agent_performance(
            client, "agent.grid_1", bot_names=bot_names, windows=windows
        )
    )


# ── Both scopes, each over the instances it names ──


def test_three_generations_report_both_scopes_with_their_instances():
    """One live generation and two stopped ones under a stable name.

    Session covers only the window the session held the base over; lifetime
    covers all three. Both are right, which is why both are reported.
    """
    perf = _perf(windows={BASE: OwnershipWindow(_epoch(TAKEOVER))})

    # GEN2 + GEN3 — GEN1 finished before the takeover and differences to zero.
    assert perf.session_realized_pnl == pytest.approx(50.0)
    assert perf.session_volume == pytest.approx(500.0)
    assert perf.session_instances == [GEN2, GEN3]

    # Every generation ever deployed under the base, stopped ones included.
    assert perf.lifetime_realized_pnl == pytest.approx(60.0)
    assert perf.lifetime_volume == pytest.approx(600.0)
    assert perf.lifetime_instances == [GEN1, GEN2, GEN3]


def test_the_262_case_reports_120_as_the_session_and_200_as_the_lifetime():
    """The issue verbatim: $120 from the current bot, $80 from previous runs.

    "$200 appears as the session's" is the defect. Both numbers exist; what must
    never happen is 200 landing in a field that means *this session*.
    """
    prior, current = "grid-20260801-000000", "grid-20260810-000000"
    history = {
        prior: [
            _hist_row("2026-08-01T01:00:00+00:00", 0.0, 0.0),
            _hist_row("2026-08-02T01:00:00+00:00", 80.0, 800.0),
        ],
        current: [
            _hist_row("2026-08-10T01:00:00+00:00", 0.0, 0.0),
            _hist_row("2026-08-11T01:00:00+00:00", 120.0, 1200.0),
        ],
    }
    perf = _perf(
        windows={BASE: OwnershipWindow(_epoch("2026-08-10T00:00:00+00:00"))},
        history=history,
        snapshots=[_snapshot(prior, 80.0, 800.0), _snapshot(current, 120.0, 1200.0)],
        live={current},
    )

    assert perf.session_realized_pnl == pytest.approx(120.0)
    assert perf.lifetime_realized_pnl == pytest.approx(200.0)
    assert perf.session_instances == [current]
    assert perf.lifetime_instances == [prior, current]
    # No field that means "this session" carries the cumulative 200.
    assert perf.session_realized_pnl != pytest.approx(200.0)
    assert perf.realized_pnl == pytest.approx(120.0)
    assert perf.session_volume == pytest.approx(1200.0)
    assert perf.session_volume != pytest.approx(2000.0)


def test_the_lifetime_scope_counts_a_generation_the_snapshot_has_forgotten():
    """The normal end state of a stopped bot: archived, and gone from the snapshot.

    Its whole realized PnL then lives only in the controller-performance history
    ([[CORR-633]]), so a lifetime scope read off the snapshot alone would report
    one generation of three — understating exactly the way #262's leaderboard did
    after every stop/archive.
    """
    perf = _perf(
        windows={BASE: OwnershipWindow(_epoch(TAKEOVER))},
        snapshots=[_snapshot(GEN3, 30.0, 300.0)],  # the archived two are gone
        archived=(GEN1, GEN2),
    )

    assert perf.lifetime_realized_pnl == pytest.approx(60.0)
    assert perf.lifetime_volume == pytest.approx(600.0)
    assert perf.lifetime_instances == [GEN1, GEN2, GEN3]
    # And the session scope still stops at its own window.
    assert perf.session_realized_pnl == pytest.approx(50.0)
    assert perf.session_instances == [GEN2, GEN3]


def test_the_session_scope_never_exceeds_the_lifetime_one():
    """Both scopes are cut from the same histories, which is what guarantees it.

    Sourcing the two from different places (a window slice against a snapshot
    aggregate) could put the part above the whole on identical data, and no
    reader could tell which number to distrust.
    """
    perf = _perf(windows={BASE: OwnershipWindow(_epoch(TAKEOVER))})

    assert perf.session_realized_pnl <= perf.lifetime_realized_pnl
    assert perf.session_volume <= perf.lifetime_volume
    assert set(perf.session_instances) <= set(perf.lifetime_instances)


def test_a_session_with_no_window_reports_its_scope_unavailable():
    """The silent fallback to lifetime IS the #262 defect, so it must read as absent.

    The aggregate still carries the lifetime figure — reporting zero for a live
    bot would be worse — but the session-scoped fields say they have nothing,
    rather than restating the lifetime total under a session name.
    """
    perf = _perf(bot_names=[BASE])

    assert perf.session_realized_pnl is None
    assert perf.session_volume is None
    assert perf.session_instances == []
    # ...and emphatically not the lifetime totals wearing a session label.
    assert perf.lifetime_realized_pnl == pytest.approx(60.0)
    assert perf.lifetime_volume == pytest.approx(600.0)


def test_a_base_with_no_instance_anywhere_has_no_lifetime_scope_either():
    """ "Unknown" and "flat" must not render alike at either scope."""
    perf = _perf(
        windows={"vanished": OwnershipWindow(_epoch(TAKEOVER))},
        history={},
        snapshots=[],
        live=set(),
    )

    assert perf.unresolved_bases == ["vanished"]
    assert perf.lifetime_realized_pnl is None
    assert perf.lifetime_volume is None
    assert perf.lifetime_instances == []


def test_an_executor_only_agent_has_no_scope_to_label():
    """No bot, no generations — the agent_id tag is already the session's own scope."""
    perf = _perf()

    assert perf.session_realized_pnl is None
    assert perf.lifetime_realized_pnl is None
    assert perf.session_instances == []
    assert perf.lifetime_instances == []


# ── The live book has exactly one scope, and no twin ──


LIFETIME_SCOPED = {"lifetime_realized_pnl", "lifetime_volume", "lifetime_instances"}


def test_no_lifetime_twin_exists_for_unrealized_positions_or_exposure():
    """#234: those come only from confirmed-live instances, never summed across.

    The symmetric-looking design is to give every figure both scopes. A
    ``lifetime_unrealized_pnl`` would be a sum of marks nobody holds, so its
    absence is a decision and is asserted as one — on the dataclass, on the wire
    model, and on a serialized payload, which is what a consumer would read.
    """
    from condor.web.routes.agents import AgentPerformanceModel

    perf = AgentPerformance(agent_id="agent.grid_1")
    forbidden = (
        "lifetime_unrealized_pnl",
        "lifetime_unrealized",
        "lifetime_total_pnl",
        "lifetime_open_count",
        "lifetime_exposure",
        "lifetime_total_exposure",
        "lifetime_executors",
        "lifetime_controllers",
        "lifetime_positions",
    )
    for name in forbidden:
        assert not hasattr(perf, name), name
        assert name not in AgentPerformanceModel.model_fields, name

    # Nothing lifetime-scoped on the wire beyond realized PnL and volume.
    payload = AgentPerformanceModel.from_perf(perf).model_dump()
    assert {k for k in payload if k.startswith("lifetime_")} == LIFETIME_SCOPED


def test_the_wire_model_carries_both_scopes_from_the_dataclass():
    """``from_perf`` maps by field name, so this is what keeps the two in step."""
    from condor.web.routes.agents import AgentPerformanceModel

    model = AgentPerformanceModel.from_perf(
        AgentPerformance(
            agent_id="agent.grid_1",
            session_realized_pnl=50.0,
            session_volume=500.0,
            session_instances=[GEN2, GEN3],
            lifetime_realized_pnl=60.0,
            lifetime_volume=600.0,
            lifetime_instances=[GEN1, GEN2, GEN3],
        )
    )

    assert model.session_realized_pnl == 50.0
    assert model.session_volume == 500.0
    assert model.session_instances == [GEN2, GEN3]
    assert model.lifetime_realized_pnl == 60.0
    assert model.lifetime_volume == 600.0
    assert model.lifetime_instances == [GEN1, GEN2, GEN3]
    # Unavailable survives the trip as unavailable, never as 0.0.
    assert (
        AgentPerformanceModel.from_perf(
            AgentPerformance(agent_id="a")
        ).session_realized_pnl
        is None
    )


# ── The label reaches the surfaces that render it ──


def test_the_provider_hands_both_scopes_to_the_tick():
    """Everything downstream of the tick reads the provider's data dict."""
    import condor.agents.performance as perf_mod
    from condor.agents.providers.executors import ExecutorsProvider

    perf = AgentPerformance(
        agent_id="agent.grid_1",
        bot_names=[GEN3],
        session_realized_pnl=50.0,
        session_volume=500.0,
        session_instances=[GEN2, GEN3],
        lifetime_realized_pnl=60.0,
        lifetime_volume=600.0,
        lifetime_instances=[GEN1, GEN2, GEN3],
    )

    async def _fake(*a, **kw):
        return perf

    original = perf_mod.fetch_agent_performance
    perf_mod.fetch_agent_performance = _fake
    try:
        result = asyncio.run(
            ExecutorsProvider().execute(
                object(), {"bot_name": BASE}, agent_id="agent.grid_1"
            )
        )
    finally:
        perf_mod.fetch_agent_performance = original

    assert result.data["session_realized_pnl"] == 50.0
    assert result.data["session_volume"] == 500.0
    assert result.data["session_instances"] == [GEN2, GEN3]
    assert result.data["lifetime_realized_pnl"] == 60.0
    assert result.data["lifetime_volume"] == 600.0
    assert result.data["lifetime_instances"] == [GEN1, GEN2, GEN3]


def test_get_info_passes_both_scopes_to_the_session_report():
    import condor.agents.engine as engine_module

    engine = engine_module.TickEngine.__new__(engine_module.TickEngine)
    engine._last_skill_data = {
        "session_realized_pnl": 50.0,
        "session_volume": 500.0,
        "session_instances": [GEN2, GEN3],
        "lifetime_realized_pnl": 60.0,
        "lifetime_volume": 600.0,
        "lifetime_instances": [GEN1, GEN2, GEN3],
    }
    engine.journal = None
    engine.agent_id = "agent.grid_1"
    engine.config = {}
    engine.strategy = SimpleNamespace(name="Grid", slug="grid")
    engine._agent_key = lambda: "agent.grid_1"
    for attr, value in (
        ("session_num", 1),
        ("status", "running"),
        ("_last_tick_at", None),
        ("_last_error", None),
        ("session_dir", None),
        ("is_experiment", False),
    ):
        try:
            setattr(engine, attr, value)
        except AttributeError:  # a read-only property derives it
            pass

    info = engine.get_info()

    assert info["session_realized_pnl"] == 50.0
    assert info["session_volume"] == 500.0
    assert info["session_instances"] == [GEN2, GEN3]
    assert info["lifetime_realized_pnl"] == 60.0
    assert info["lifetime_volume"] == 600.0
    assert info["lifetime_instances"] == [GEN1, GEN2, GEN3]


def _scopes_html(info: dict) -> str:
    from condor.agents.session_report import SessionReport
    from condor.reports.builder import ReportBuilder

    b = ReportBuilder()
    b.manual_order()
    SessionReport._scopes(b, info)
    return b._render_sections()


def test_the_session_report_labels_each_figure_with_its_scope():
    out = _scopes_html(
        {
            "session_realized_pnl": 120.0,
            "session_volume": 1200.0,
            "session_instances": ["grid-20260810-000000"],
            "lifetime_realized_pnl": 200.0,
            "lifetime_volume": 2000.0,
            "lifetime_instances": ["grid-20260801-000000", "grid-20260810-000000"],
        }
    )

    assert "Scopes" in out
    assert "Session (ownership window)" in out
    assert "Lifetime (all generations)" in out
    assert "$120.00" in out and "$200.00" in out
    assert "$1,200" in out and "$2,000" in out
    assert "grid-20260801-000000" in out  # lifetime names the prior generation
    # The two figures cannot be read as one: both are on screen, both labelled.
    assert out.index("Session (ownership window)") < out.index(
        "Lifetime (all generations)"
    )


def test_the_session_report_says_unavailable_not_the_lifetime_total():
    out = _scopes_html(
        {
            "session_realized_pnl": None,
            "session_volume": None,
            "session_instances": [],
            "lifetime_realized_pnl": 200.0,
            "lifetime_volume": 2000.0,
            "lifetime_instances": ["grid-20260801-000000"],
        }
    )

    assert "unavailable" in out
    assert "no ownership window recorded" in out
    # $200.00 appears once — as the lifetime figure, never as the session's.
    assert out.count("$200.00") == 1


def test_the_session_report_omits_the_block_for_an_executor_only_session():
    from condor.agents.session_report import SessionReport
    from condor.reports.builder import ReportBuilder

    b = ReportBuilder()
    b.manual_order()
    SessionReport._scopes(b, {})

    assert b._sections == []
