"""The loop session's living report (FEAT-036).

Two things carry the whole feature and are asserted here: the report updates
*in place* under a stable id (so a session produces one report, not one per
tick), and its ``source_name`` matches the prefix ``get_strategy_reports``
filters on (so it reaches the strategy page with no backend or frontend change).
"""

import asyncio
from types import SimpleNamespace

import pytest

import condor.reports as rep
from condor.agents import canvas
from condor.agents.session_report import SessionReport


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(rep, "CHARTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(rep, "INDEX_FILE", tmp_path / "reports" / "reports_index.json")
    return tmp_path


@pytest.fixture
def session_dir(tmp_path):
    d = tmp_path / "session_3"
    d.mkdir()
    return d


INFO = {
    "agent_id": "brigado.grid_1",
    "status": "running",
    "tick_count": 4,
    "daily_pnl": 12.5,
    "realized_pnl": 8.0,
    "unrealized_pnl": 4.5,
    "total_volume": 42000.0,
    "open_executors": 2,
}


class _FakeJournal:
    """Only the two read methods the report actually calls."""

    def __init__(self, pnl_series=None, decisions=""):
        self._series = pnl_series or []
        self._decisions = decisions

    def get_pnl_series(self):
        return self._series

    def get_recent_decisions(self, count=3):
        return self._decisions


def _report(frequency_sec=60):
    return SessionReport("brigado", "grid", 3, frequency_sec=frequency_sec)


def _update(report, session_dir, *, journal=None, executors=None):
    return asyncio.run(
        report.update(
            info=INFO,
            journal=journal if journal is not None else _FakeJournal(),
            session_dir=session_dir,
            executors=executors,
        )
    )


def _html(report_id):
    raw, _ = rep.get_report_raw_html(report_id)
    return raw


# ── Attribution: how the report reaches the strategy page ──


def test_source_name_matches_the_strategy_report_filter(reports_dir, session_dir):
    report_id = _update(_report(), session_dir)
    entry = rep.get_report(report_id)

    # get_strategy_reports filters source_type="routine" + this exact prefix.
    assert entry["source_type"] == "routine"
    assert entry["source_name"] == "brigado.grid/session_3"
    assert entry["source_name"].startswith("brigado.grid/")


def test_report_is_tagged_for_the_agent(reports_dir, session_dir):
    report_id = _update(_report(), session_dir)
    assert rep.get_report(report_id)["tags"] == ["agent", "session", "brigado"]


# ── One session, one report ──


def test_second_update_reuses_the_same_report_id(reports_dir, session_dir):
    report = _report()
    first = _update(report, session_dir)
    second = _update(report, session_dir)

    assert first == second
    assert len(rep.list_reports()[0]) == 1


def test_update_refreshes_the_numbers_in_place(reports_dir, session_dir):
    report = _report()
    report_id = _update(report, session_dir)
    assert "$12.50" in _html(report_id)

    moved = dict(INFO, daily_pnl=-3.25)
    asyncio.run(
        report.update(info=moved, journal=_FakeJournal(), session_dir=session_dir)
    )
    html = _html(report_id)
    assert "$-3.25" in html
    assert "$12.50" not in html


# ── Blocks ──


def test_kpis_come_from_engine_data(reports_dir, session_dir):
    html = _html(_update(_report(), session_dir))
    assert "$8.00" in html  # realized
    assert "$4.50" in html  # unrealized
    assert "$42,000" in html  # volume
    assert "Running" in html


def test_narrative_renders_the_canvas_and_its_age(reports_dir, session_dir):
    canvas.write_section(session_dir, "thesis", "Range-bound, quoting wide.", tick=4)
    html = _html(_update(_report(), session_dir))

    assert "Range-bound, quoting wide." in html
    assert "own words" in html
    assert "last revised at tick #4" in html


def test_narrative_says_so_when_the_agent_has_written_nothing(reports_dir, session_dir):
    html = _html(_update(_report(), session_dir))
    assert "has not written its thesis yet" in html


def test_agent_markdown_cannot_inject_markup(reports_dir, session_dir):
    canvas.write_section(session_dir, "thesis", "<script>alert(1)</script>", tick=1)
    html = _html(_update(_report(), session_dir))
    assert "<script>alert(1)</script>" not in html


def test_narrative_sits_directly_under_the_kpis(reports_dir, session_dir):
    """Manual order: without it, markdown sorts to the bottom of the report."""
    canvas.write_section(session_dir, "thesis", "Thesis body here.", tick=1)
    html = _html(
        _update(
            _report(),
            session_dir,
            journal=_FakeJournal(decisions="- **#1** (10:00) opened a grid"),
        )
    )
    assert html.index("kpi-bar") < html.index("Thesis body here.")
    assert html.index("Thesis body here.") < html.index("opened a grid")


def test_equity_curve_needs_at_least_two_points(reports_dir, session_dir):
    # The class also appears in the stylesheet; the rendered div is the marker.
    chart_div = '<div class="section plotly-chart report-panel">'

    one = _FakeJournal(pnl_series=[{"timestamp": "2026-08-05 10:00", "pnl": 1.0}])
    assert chart_div not in _html(_update(_report(), session_dir, journal=one))

    two = _FakeJournal(
        pnl_series=[
            {"timestamp": "2026-08-05 10:00", "pnl": 1.0},
            {"timestamp": "2026-08-05 10:01", "pnl": 2.0},
        ]
    )
    assert chart_div in _html(_update(_report(), session_dir, journal=two))


def test_executor_table_uses_live_performance_rows(reports_dir, session_dir):
    executors = [
        {
            "pair": "SOL-USDC",
            "side": "BUY",
            "status": "RUNNING",
            "amount": 100.0,
            "pnl": 2.5,
            "volume": 900.0,
        }
    ]
    html = _html(_update(_report(), session_dir, executors=executors))
    assert "SOL-USDC" in html
    assert "$2.50" in html


def test_belief_history_lists_every_revision_newest_first(reports_dir, session_dir):
    canvas.write_section(session_dir, "thesis", "first belief", tick=1)
    canvas.write_section(session_dir, "thesis", "second belief", tick=5)

    html = _html(_update(_report(), session_dir))
    assert "Belief history" in html
    assert html.index("second belief") < html.index("first belief")


def test_blocks_absent_when_there_is_nothing_to_show(reports_dir, session_dir):
    html = _html(_update(_report(), session_dir))
    assert "<h2>Belief history</h2>" not in html
    assert "<h2>Recent decisions</h2>" not in html
    assert "<h2>Executors</h2>" not in html


# ── Live behaviour ──


def test_auto_refresh_tracks_the_tick_cadence(reports_dir, session_dir):
    report_id = _update(_report(frequency_sec=30), session_dir)
    assert '"auto_refresh_seconds":30' in _html(report_id)


def test_report_is_recreated_after_a_prune(reports_dir, session_dir, monkeypatch):
    report = _report()
    first = _update(report, session_dir)

    # LRU pruning removed it out from under the live session.
    monkeypatch.setattr(rep, "MAX_REPORTS", 0)
    rep.store._write_index([])

    second = _update(report, session_dir)
    assert second != first
    assert rep.get_report(second) is not None


# ── Engine wiring ──


def _engine(tmp_path, monkeypatch, config):
    from condor.agents import strategy as strategy_module
    from condor.agents.agent import Agent
    from condor.agents.engine import TickEngine
    from condor.agents.strategy import Strategy

    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    strategy = Strategy(agent_slug="brigado", name="Grid")
    strategy.home.mkdir(parents=True, exist_ok=True)
    agent = Agent(slug="brigado", name="Brigado")
    return TickEngine(
        agent=agent, strategy=strategy, config=dict(config), chat_id=1, user_id=1
    )


def test_loop_session_gets_a_report_and_a_nudge_tracker(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch, {"execution_mode": "loop"})

    assert engine._session_report is not None
    assert engine._session_report.run_key == "brigado.grid"
    assert engine._nudge is not None


@pytest.mark.parametrize("mode", ["dry_run", "run_once"])
def test_experiments_get_neither(tmp_path, monkeypatch, mode):
    engine = _engine(tmp_path, monkeypatch, {"execution_mode": mode})

    assert engine._session_report is None
    assert engine._nudge is None


def test_canvas_enabled_false_opts_the_session_out(tmp_path, monkeypatch):
    engine = _engine(
        tmp_path, monkeypatch, {"execution_mode": "loop", "canvas_enabled": False}
    )

    assert engine._session_report is None
    assert engine._nudge is None


def test_nudge_config_reaches_the_tracker(tmp_path, monkeypatch):
    engine = _engine(
        tmp_path,
        monkeypatch,
        {"execution_mode": "loop", "canvas_nudge_ticks": 4, "canvas_band_usd": 5.0},
    )

    assert engine._nudge.nudge_ticks == 4
    assert engine._nudge.band_usd == 5.0


def test_a_failing_report_does_not_fail_the_tick(tmp_path, monkeypatch, caplog):
    """The guard is load-bearing: charting must never kill a trading tick."""
    engine = _engine(tmp_path, monkeypatch, {"execution_mode": "loop"})

    class _Exploding:
        async def update(self, **kwargs):
            raise RuntimeError("report index on fire")

    engine._session_report = _Exploding()
    ticks = []
    monkeypatch.setattr(engine, "_get_client", _async(object()))
    monkeypatch.setattr(engine, "_adopt_running_bots", _async(None))
    monkeypatch.setattr(engine, "_create_client", _async(_FakeACP()))
    monkeypatch.setattr(engine, "_notify", _async(None))
    monkeypatch.setattr(engine, "_collect_stream", lambda *a, **k: _empty_stream())
    monkeypatch.setattr(engine.provider_registry, "run_core_providers", _async({}))
    monkeypatch.setattr(
        engine.journal, "record_tick", lambda **kw: (ticks.append(kw) or 1)
    )

    asyncio.run(engine._tick())

    assert ticks, "the tick must complete despite the report blowing up"
    assert "session report update failed" in caplog.text


def _async(result):
    async def go(*args, **kwargs):
        return result

    return go


async def _empty_stream():
    return
    yield  # pragma: no cover — makes this an async generator


class _FakeACP:
    async def start(self):
        return None

    async def stop(self):
        return None


def _silent_tick(engine, monkeypatch):
    """Drive one real tick in which the agent says nothing at all."""
    monkeypatch.setattr(engine, "_get_client", _async(object()))
    monkeypatch.setattr(engine, "_adopt_running_bots", _async(None))
    monkeypatch.setattr(engine, "_create_client", _async(_FakeACP()))
    monkeypatch.setattr(engine, "_notify", _async(None))
    monkeypatch.setattr(engine, "_collect_stream", lambda *a, **k: _empty_stream())
    monkeypatch.setattr(engine.provider_registry, "run_core_providers", _async({}))
    asyncio.run(engine._tick())


def test_a_live_session_publishes_one_report_the_strategy_page_finds(
    reports_dir, tmp_path, monkeypatch
):
    """End to end: two quiet ticks, one report, reachable by the strategy filter."""
    engine = _engine(tmp_path / "agents", monkeypatch, {"execution_mode": "loop"})

    _silent_tick(engine, monkeypatch)
    _silent_tick(engine, monkeypatch)

    # Exactly what condor/web/routes/agents.py:get_strategy_reports does.
    reports, _ = rep.list_reports(source_type="routine", search="brigado.grid")
    matched = [r for r in reports if r["source_name"].startswith("brigado.grid/")]

    assert len(matched) == 1
    assert matched[0]["source_name"] == f"brigado.grid/session_{engine.session_num}"
    # A quiet tick writes no canvas, but the report still refreshed.
    assert engine.journal.tick_count == 2
    assert not (engine.session_dir / canvas.CANVAS_FILE).exists()


# ── Attribution block (PnL unification) ──
# "Total PnL $0.00" is equally the shape of a flat session and of one whose bots
# are invisible to the aggregator. The report must not render the two alike.


def _blocks(info):
    from condor.agents.session_report import SessionReport
    from condor.reports.builder import ReportBuilder

    b = ReportBuilder()
    b.manual_order()
    SessionReport._attribution(b, info)
    return b


def _rendered(b) -> str:
    return b._render_sections()


def test_attribution_names_the_bots_the_pnl_covers():
    out = _rendered(_blocks({"bot_names": ["ema_trend_loop-20260807-045821"]}))

    assert "Attribution" in out
    assert "ema_trend_loop-20260807-045821" in out


def test_attribution_flags_a_bot_the_aggregator_cannot_see():
    out = _rendered(
        _blocks({"bot_names": ["vanished"], "unresolved_bases": ["vanished"]})
    )

    assert "Incomplete" in out
    assert "FLOOR" in out
    assert "vanished" in out


def test_attribution_is_silent_for_a_pure_executor_session():
    """No bots, nothing missing — the agent_id tag is the whole story."""
    assert _blocks({})._sections == []
