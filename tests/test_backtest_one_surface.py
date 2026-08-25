"""FEAT-039: the ``backtest_chart`` routine is the only surface for a backtest.

Three things had to become true for the routine to replace the MCP tools, and each
one is pinned here:

  * **it saves** — a run from any seat lands in ``BacktestStore`` under the
    server-side ``task_id``, in the same envelope the web route stores, so
    ``backtest_compare`` can rank it without anyone opening the dashboard;
  * **it is quiet on demand** — ``chart=False`` pushes no photo into the chat, which
    is what makes a 20-config sweep usable;
  * **it returns data** — the metrics come back as a ``table_data`` row, so an agent
    ranking ten configs never parses English.

The chart renderer and the report builder are stubbed: neither is under test here,
and both are slow (kaleido) or write to the repo.
"""

import asyncio
import io

import pytest

import condor.backtest_store as store_mod
import condor.backtesting as core
import condor.reports as reports_mod
from condor.backtest_store import BacktestStore
from tests.conftest import load_shared_routine

bc = load_shared_routine("backtest_chart")

CONFIG_NAME = "ema_eth_20_100"
SERVER = "srv-a"

# The exact envelope GET /backtesting/tasks/{id} returns — BacktestTask.to_dict():
# the payload sits under "result" (singular), the parameters under "config".
_PAYLOAD = {
    "executors": [],
    "processed_data": {
        "timestamp": [1745280000, 1745280060],
        "open": [1800.0, 1801.0],
        "high": [1802.0, 1803.0],
        "low": [1799.0, 1800.0],
        "close": [1801.0, 1802.5],
    },
    "results": {
        "net_pnl_quote": 12.5,
        "net_pnl": 0.0125,
        "total_fees_quote": 1.25,
        "max_drawdown_usd": -3.0,
        "max_drawdown_pct": -0.0147,
        "total_volume": 5000.0,
        "sharpe_ratio": 1.42,
        "profit_factor": 1.31,
        "total_executors": 74,
        "accuracy": 0.4459,
        "win_signals": 33,
        "loss_signals": 41,
    },
    "pnl_timeseries": [],
    "position_held_timeseries": [],
}


def _envelope(task_id: str, status: str = "completed", error=None) -> dict:
    return {
        "task_id": task_id,
        "status": status,
        "config": {
            "start_time": 1745280000,  # 2025-04-22
            "end_time": 1745366400,  # 2025-04-23
            "backtesting_resolution": "1m",
            "trade_cost": 0.0002,
            "config": {"id": CONFIG_NAME, "controller_name": "ema_trend_v1"},
        },
        "created_at": "2026-08-05T00:00:00+00:00",
        "error": error,
        **({"result": _PAYLOAD} if status == "completed" else {}),
    }


class FakeBacktesting:
    """Submit/poll against a scripted sequence of task states."""

    def __init__(self, task_id: str, states: list[dict]):
        self.task_id = task_id
        self.states = list(states)
        self.submitted: dict | None = None
        self.polls = 0

    async def submit_task(self, **kwargs):
        self.submitted = kwargs
        return {"task_id": self.task_id, "status": "pending"}

    async def get_task(self, task_id):
        self.polls += 1
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]


class FakeControllers:
    async def get_controller_config(self, name):
        # A YAML-loaded config: the numbers arrive as strings.
        return {
            "id": name,
            "controller_name": "ema_trend_v1",
            "total_amount_quote": "100",
        }


class FakeClient:
    def __init__(self, backtesting):
        self.backtesting = backtesting
        self.controllers = FakeControllers()


class FakeBot:
    def __init__(self):
        self.photos = 0

    async def send_photo(self, **kwargs):
        self.photos += 1


class FakeContext:
    """A Telegram chat: the server comes from the active-server preference."""

    def __init__(self, chat_id=42):
        self._chat_id = chat_id
        self.bot = FakeBot()
        self.user_data = {"user_preferences": {"general": {"active_server": SERVER}}}


class FakeReportBuilder:
    saved: list[str] = []

    def __init__(self, title="Report"):
        self.title = title

    def source(self, *a, **k):
        return self

    def tags(self, *a, **k):
        return self

    def markdown(self, *a, **k):
        return self

    def plotly(self, *a, **k):
        return self

    async def save(self, report_id=None):
        FakeReportBuilder.saved.append(self.title)
        return "report-1"


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A BacktestStore under tmp_path, installed as the process singleton."""
    fresh = BacktestStore(tmp_path / "backtests")
    monkeypatch.setattr(store_mod, "_store", fresh)
    return fresh


@pytest.fixture
def stubs(monkeypatch):
    """Stub the chart renderer and the report builder; record what they were asked."""
    calls = {"charts": 0, "render_png": None}

    def fake_generate_chart(*args, render_png=True, **kwargs):
        calls["charts"] += 1
        calls["render_png"] = render_png
        # Mirrors the real contract, pinned against plotly in
        # test_the_renderer_can_skip_the_png.
        return (io.BytesIO(b"png-bytes") if render_png else None), object()

    monkeypatch.setattr(bc, "generate_chart", fake_generate_chart)
    monkeypatch.setattr(reports_mod, "ReportBuilder", FakeReportBuilder)
    FakeReportBuilder.saved = []
    return calls


@pytest.fixture
def routine(monkeypatch, store, stubs):
    """Drive ``backtest_chart.run`` against a scripted client. Returns the runner."""

    def run(client, context=None, **config_kwargs):
        async def _client(*a, **k):
            return client

        monkeypatch.setattr(bc, "get_client", _client)
        config = bc.Config(
            config_name=CONFIG_NAME,
            start_date="2025-04-22",
            end_date="2025-04-23",
            **config_kwargs,
        )
        return asyncio.run(bc.run(config, context or FakeContext()))

    return run


def _completed(task_id="task-1") -> FakeClient:
    return FakeClient(FakeBacktesting(task_id, [_envelope(task_id)]))


# ── One surface, reachable from every seat ────────────────────────────────────


def test_both_routines_are_published_to_every_assistant(monkeypatch):
    """Deleting the MCP tools is only safe because agents resolve these."""
    from condor import paths
    from routines.base import assistant_routines, discover_routines

    # A fact about the *shipped* library, so it reads the real ``agents/``
    # rather than the suite's tmp one. Read-only: nothing here writes, which is
    # the only condition under which dropping the isolation knob is allowed.
    monkeypatch.delenv(paths.AGENTS_ROOT_ENV, raising=False)

    chat = discover_routines(force_reload=True)
    agent = assistant_routines("directional_trader", force_reload=True)

    for name in ("backtest_chart", "backtest_compare"):
        # The chat's catalog and /routines are unchanged: bare name, general library.
        assert chat[name].source == "global"
        assert agent[name].source == "global"
    assert chat["backtest_chart"].category == "Bot Analysis"


# ── One coercion ──────────────────────────────────────────────────────────────


def test_the_web_route_shares_the_one_coercion():
    """No private copy survives in the web route (nor in the routine)."""
    import condor.web.routes.backtesting as web

    assert web.coerce_controller_config is core.coerce_controller_config
    assert not hasattr(web, "_coerce_numeric_values")


def test_coercion_reaches_the_engine(routine):
    client = _completed()
    routine(client)

    submitted = client.backtesting.submitted["config"]
    assert submitted["total_amount_quote"] == 100, "YAML strings must arrive as numbers"


# ── It saves ──────────────────────────────────────────────────────────────────


def test_a_run_lands_in_the_store_with_the_web_routes_envelope(store, routine):
    """The record a routine writes must be the record the dashboard writes."""
    routine(_completed("task-1"))

    saved = store.get_result("task-1")
    assert saved is not None, "nobody had to open the dashboard for this"
    # Exactly {"server": ..., **task} — what web/routes/backtesting.py persists.
    assert saved == {"server": SERVER, **_envelope("task-1")}
    assert saved["status"] == "completed"
    assert saved["result"]["results"]["sharpe_ratio"] == 1.42


def test_a_stored_run_re_renders_with_its_original_parameters(routine):
    """task_id= must reproduce the run, not the form defaults."""
    routine(_completed("task-1"))

    loaded = bc._load_saved_task("task-1")
    assert not isinstance(loaded, str), loaded
    payload, restored = loaded

    assert restored.config_name == CONFIG_NAME
    assert restored.start_date == "2025-04-22"
    assert restored.end_date == "2025-04-23"
    assert restored.resolution == "1m"
    assert restored.trade_cost == 0.0002
    assert payload["results"]["net_pnl_quote"] == 12.5


def test_backtest_compare_picks_up_routine_runs(store, routine):
    """The whole point of saving: ranking saved runs now sees them."""
    cmp = load_shared_routine("backtest_compare")

    routine(_completed("task-1"))
    routine(_completed("task-2"))

    latest = cmp._latest_ids(store, 2, SERVER)
    assert set(latest) == {"task-1", "task-2"}

    runs = [cmp._load_run(store, tid) for tid in latest]
    assert all(r is not None for r in runs)
    assert {r.metrics["net_pnl_quote"] for r in runs} == {12.5}


def test_an_agent_run_is_filed_under_the_server_it_was_launched_with(store, routine):
    """No chat, no preferences — the seat a shared routine actually runs from."""
    from condor.routine_store import WebRoutineContext

    context = WebRoutineContext(SERVER, bot=FakeBot(), chat_id=0)
    routine(_completed("task-agent"), context=context)

    assert store.get_result("task-agent")["server"] == SERVER
    assert store.list_results(SERVER), "an agent's run must be rankable too"


def test_the_task_id_is_reported(routine):
    result = routine(_completed("task-1"))

    assert "task-1" in result.text


# ── The async path ────────────────────────────────────────────────────────────


def test_a_pending_task_is_polled_until_it_completes(store, routine, monkeypatch):
    """A long window must not need the sync endpoint any more."""
    monkeypatch.setattr(core, "DEFAULT_POLL_INTERVAL", 0.0)
    task_id = "task-slow"
    client = FakeClient(
        FakeBacktesting(
            task_id,
            [
                _envelope(task_id, status="pending"),
                _envelope(task_id, status="running"),
                _envelope(task_id),
            ],
        )
    )

    result = routine(client)

    assert client.backtesting.polls == 3
    assert store.get_result(task_id) is not None
    assert "failed" not in result.text.lower()


def test_a_failed_task_explains_itself(store, routine):
    """And fails the run while it does.

    Returning the message as a result would file the run as a success: the
    instance keeps status "completed" with no error, the dashboard dot is blue
    and the conversation is told "✅ Routine backtest_chart done" above the
    failure it was just handed. Raising is what keeps all three honest.
    """
    task_id = "task-bad"
    envelope = _envelope(task_id, status="failed", error="No candles for ETH-USDT")

    with pytest.raises(core.BacktestError, match="No candles for ETH-USDT"):
        routine(FakeClient(FakeBacktesting(task_id, [envelope])))

    assert store.get_result(task_id) is None, "a failure is not a rankable run"


def test_polling_gives_up_with_the_task_id_in_hand(routine, monkeypatch):
    """A timeout must leave the caller able to read the run back later."""
    monkeypatch.setattr(core, "DEFAULT_TIMEOUT", 0.0)
    task_id = "task-forever"
    client = FakeClient(
        FakeBacktesting(task_id, [_envelope(task_id, status="running")])
    )

    with pytest.raises(core.BacktestError) as excinfo:
        routine(client)

    assert task_id in str(excinfo.value)
    assert "run_async" in str(excinfo.value)


# ── It is quiet on demand ─────────────────────────────────────────────────────


def test_the_renderer_can_skip_the_png():
    """The real contract behind chart=False: a figure, no image bytes."""
    candle_df = bc._build_candle_df(_PAYLOAD["processed_data"])

    buf, fig = bc.generate_chart(
        candle_df, [], [], _PAYLOAD["results"], bc.Config(), render_png=False
    )

    assert buf is None
    assert fig is not None, "the web report still needs the figure"


def test_chart_false_sends_no_photo_but_still_reports(routine, stubs, store):
    """A 20-config sweep must not push 20 images into the chat."""
    context = FakeContext()
    result = routine(_completed("task-quiet"), context=context, chart=False)

    assert stubs["render_png"] is False
    assert context.bot.photos == 0
    assert result.chart_image is None
    # Everything else survives: text, the metrics row, the web report, the store.
    assert result.text
    assert result.table_data
    assert FakeReportBuilder.saved == ["Backtest: " + CONFIG_NAME]
    assert store.get_result("task-quiet") is not None


def test_chart_true_still_sends_the_photo(routine):
    context = FakeContext()
    result = routine(_completed(), context=context)

    assert context.bot.photos == 1
    assert result.chart_image == b"png-bytes"


def test_a_chat_less_run_never_sends_a_photo(routine):
    """A shared routine runs under agents, which have no chat."""
    from condor.routine_store import WebRoutineContext

    context = WebRoutineContext(SERVER, bot=FakeBot(), chat_id=0)
    routine(_completed("task-agent"), context=context)

    assert context.bot.photos == 0


# ── It returns data ───────────────────────────────────────────────────────────


def test_the_result_carries_a_metrics_row(routine):
    """An agent ranking ten configs reads numbers, never prose."""
    result = routine(_completed("task-1"))

    assert result.table_columns == list(bc.METRIC_COLUMNS)
    assert len(result.table_data) == 1
    row = result.table_data[0]

    assert set(row) == set(bc.METRIC_COLUMNS)
    assert row["task_id"] == "task-1", "the handle to re-render or compare this run"
    assert row["config_name"] == CONFIG_NAME
    assert row["net_pnl_quote"] == 12.5
    assert row["net_pnl_pct"] == 1.25, "ratios are carried as percentages"
    assert row["sharpe_ratio"] == 1.42
    assert row["max_drawdown_pct"] == -1.47
    assert row["accuracy_pct"] == 44.59
    assert row["profit_factor"] == 1.31
    assert row["total_executors"] == 74
    assert row["win_signals"] == 33
    assert row["loss_signals"] == 41
    assert row["total_fees_quote"] == 1.25
    assert row["total_volume"] == 5000.0


def test_rows_from_several_runs_concatenate(routine):
    rows = [
        routine(_completed("task-1")).table_data[0],
        routine(_completed("task-2")).table_data[0],
    ]

    assert [r["task_id"] for r in rows] == ["task-1", "task-2"]
    assert all(set(r) == set(bc.METRIC_COLUMNS) for r in rows)


def test_a_missing_metric_is_none_not_a_crash(routine):
    """The engine omits metrics on a thin run; the row must still be a row."""
    row = bc._metrics_row({}, bc.Config(config_name=CONFIG_NAME))

    assert set(row) == set(bc.METRIC_COLUMNS)
    assert row["sharpe_ratio"] is None


# ── A metric travels with the parameters it was measured under ────────────────


def test_the_row_carries_the_runs_window_resolution_and_cost(routine):
    """A sweep concatenates rows; a Sharpe alone is not comparable to the row above."""
    row = routine(_completed("task-1")).table_data[0]

    assert row["start_date"] == "2025-04-22"
    assert row["end_date"] == "2025-04-23"
    assert row["resolution"] == "1m"
    assert row["trade_cost"] == 0.0002


def test_a_re_rendered_run_reports_its_own_parameters_not_the_form_defaults(
    routine, store
):
    """task_id= renders a stored run: the row must describe THAT run's window."""
    routine(_completed("task-1"), resolution="15m", trade_cost=0.0006)

    row = asyncio.run(bc.run(bc.Config(task_id="task-1"), FakeContext())).table_data[0]

    assert row["resolution"] == "1m", "the stored run's resolution, not the default"
    assert row["trade_cost"] == 0.0002
    assert row["start_date"] == "2025-04-22"
    assert row["end_date"] == "2025-04-23"


# ── The trade-count validity gate ─────────────────────────────────────────────


def _thin(task_id="task-thin", executors=8) -> FakeClient:
    """A run with too few executors for any metric to mean anything."""
    envelope = _envelope(task_id)
    envelope["result"] = {
        **_PAYLOAD,
        "results": {**_PAYLOAD["results"], "total_executors": executors},
    }
    return FakeClient(FakeBacktesting(task_id, [envelope]))


def test_a_thin_run_is_disqualified_above_its_own_numbers(routine):
    """The gate is stated by every playbook; here it is unmissable in the result."""
    text = routine(_thin()).text

    assert "8 executors" in text
    assert f"{bc.MIN_VALID_TRADES}-trade validity gate" in text
    assert text.index("validity gate") < text.index(
        "Sharpe Ratio"
    ), "a thin run is disqualified before anyone reads its metrics, not after"


def test_a_valid_run_is_not_nagged(routine):
    assert "validity gate" not in routine(_completed("task-1")).text


def test_the_gate_reaches_an_agent_as_data(routine):
    """``get_instance`` hands sections back — the reader that never parses prose."""
    thin = routine(_thin())
    healthy = routine(_completed("task-1"))

    (kpi,) = [s for s in thin.sections if s["label"] == "Trades"]
    assert kpi["value"] == "8"
    assert kpi["trend"] == "down"
    assert f"below {bc.MIN_VALID_TRADES}-trade gate" == kpi["delta"]

    (ok,) = [s for s in healthy.sections if s["label"] == "Trades"]
    assert ok["value"] == "74"
    assert ok["trend"] == "flat"
    assert ok["delta"] is None


def test_a_run_at_the_threshold_clears_the_gate(routine):
    assert "validity gate" not in routine(_thin(executors=bc.MIN_VALID_TRADES)).text


def test_a_missing_trade_count_does_not_manufacture_a_warning(routine):
    """The engine omits metrics on a broken run; absence is not 'below the gate'."""
    assert bc._trade_count_warning({}) is None
    assert bc._sections({})[0]["value"] == "—"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
