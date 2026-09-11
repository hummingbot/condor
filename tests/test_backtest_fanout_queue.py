"""CORR-277: a batch of backtests is a queue, and nothing falls out of it.

hummingbot-api runs each backtest in its own worker process behind a semaphore
whose default cap is 1, and a submitted run's deadline starts counting at
*submission*. The MM optimizer used to fan seven configs out through a single
``asyncio.gather`` with the module default of 600s each, so the later ones had to
wait out every run ahead of them inside their own window — and when they didn't,
the routine logged a warning, returned ``None``, and published an optimization
report over however many variations happened to survive.

Three things are pinned here, against a fake server that behaves like the real
one (single worker, FIFO queue, a clock that only moves while somebody polls):

  * the client puts at most ``max_concurrent`` runs on the wire at a time;
  * each run gets the time the whole in-flight batch may cost it — the
    characterization test below shows what the unscaled deadline does instead;
  * a config that produces no backtest is named in the report, not dropped.

Sync tests driving coroutines with ``asyncio.run``: ``pytest-asyncio`` is a dev
dependency but is not installed in this venv.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import condor.backtesting as core
from condor.backtesting import BacktestError, run_and_save, run_many

SERVER = "brigado_2"
CTRL_ID = "mm_ctrl"


def _load_routine():
    """Import ``agents/brigado/routines/mm_optimizer_cycle.py`` from its file.

    An agent routine lives outside any package, exactly like the shared ones
    ``tests.conftest.load_shared_routine`` loads, so it has no dotted import path.
    ``brigado`` is one installation's own agent and is not carried in the repo, so
    a checkout without it skips the two tests below rather than failing; what the
    routine leans on lives in ``condor.backtesting`` and is covered either way.
    """
    name = "brigado_routine_mm_optimizer_cycle"
    if name in sys.modules:
        return sys.modules[name]
    path = (
        Path(__file__).resolve().parents[1]
        / "agents"
        / "brigado"
        / "routines"
        / "mm_optimizer_cycle.py"
    )
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mm = _load_routine()

needs_brigado = pytest.mark.skipif(
    mm is None, reason="the brigado agent is not installed in this checkout"
)


_RESULT = {
    "executors": [],
    "results": {
        "net_pnl_quote": 12.5,
        "total_volume": 5000.0,
        "max_drawdown_pct": -0.0147,
        "total_executors": 74,
    },
}


class SerializedServer:
    """A backtesting API with one worker, a FIFO queue and a polled clock.

    ``BACKTESTING_MAX_CONCURRENT`` slots run at a time; everything else waits its
    turn. The clock only advances when someone polls, which is what makes a run's
    queue time measurable without the test sleeping through it.
    """

    def __init__(self, work=500.0, tick=10.0, cap=1, fail_ids=()):
        self.now = 0.0
        self.work = work
        self.tick = tick
        self.cap = cap
        self.fail_ids = set(fail_ids)
        self.queue: list[str] = []
        self.running: dict[str, float] = {}
        self.finished: set[str] = set()
        self.config_of: dict[str, str] = {}
        self.in_flight = 0
        self.peak_in_flight = 0

    # -- the client's view of the API ------------------------------------
    async def submit_task(self, **kwargs):
        task_id = f"t-{len(self.config_of) + 1}"
        self.config_of[task_id] = (kwargs.get("config") or {}).get("id", "?")
        self.queue.append(task_id)
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        return {"task_id": task_id, "status": "pending"}

    async def get_task(self, task_id):
        self._tick()
        if task_id in self.finished or (
            task_id in self.running and self.now >= self.running[task_id]
        ):
            return self._settle(task_id)
        return {"task_id": task_id, "status": "running"}

    # -- the server's own bookkeeping ------------------------------------
    def _tick(self):
        self.now += self.tick
        for done in [t for t, ends in self.running.items() if self.now >= ends]:
            self.running.pop(done)
            self.finished.add(done)
        while len(self.running) < self.cap and self.queue:
            self.running[self.queue.pop(0)] = self.now + self.work

    def _settle(self, task_id):
        self.running.pop(task_id, None)
        self.finished.add(task_id)
        self.in_flight -= 1
        if self.config_of.get(task_id) in self.fail_ids:
            return {
                "task_id": task_id,
                "status": "failed",
                "error": "backtest worker exceeded its budget",
            }
        return {"task_id": task_id, "status": "completed", "result": dict(_RESULT)}


class FakeControllers:
    def __init__(self, configs):
        self.configs = configs
        self.saved: dict[str, dict] = {}

    async def list_controller_configs(self):
        return self.configs

    async def create_or_update_controller_config(self, name, config):
        self.saved[name] = config


class FakeClient:
    def __init__(self, server, configs=()):
        self.backtesting = server
        self.controllers = FakeControllers(list(configs))


@pytest.fixture
def polled_clock(monkeypatch):
    """Read ``condor.backtesting``'s deadlines off the fake server's clock."""

    def install(server):
        monkeypatch.setattr(core, "time", SimpleNamespace(monotonic=lambda: server.now))

    return install


def _configs(n):
    return [{"id": f"cfg-{i}", "controller_name": "pmm_mister"} for i in range(n)]


# ── the fan-out is bounded ──────────────────────────────────────────────────


def test_the_client_never_puts_more_on_the_wire_than_the_cap():
    server = SerializedServer(work=100.0)
    outcomes = asyncio.run(
        run_many(
            FakeClient(server),
            SERVER,
            _configs(7),
            0,
            1,
            max_concurrent=4,
            poll_interval=0.0,
        )
    )

    assert server.peak_in_flight == 4
    assert [o.ok for o in outcomes] == [True] * 7
    assert [o.config["id"] for o in outcomes] == [f"cfg-{i}" for i in range(7)]


def test_a_batch_smaller_than_the_cap_does_not_inflate_the_deadline(monkeypatch):
    """``in_flight`` is what the batch actually costs, not what it was allowed."""
    seen = {}

    async def spy(*args, timeout=None, **kwargs):
        seen["timeout"] = timeout
        return "t-0", {"task_id": "t-0", "status": "completed", "result": _RESULT}

    monkeypatch.setattr(core, "run_and_save", spy)
    asyncio.run(
        run_many(
            FakeClient(SerializedServer()),
            SERVER,
            _configs(2),
            0,
            1,
            max_concurrent=4,
            timeout=100.0,
        )
    )

    assert seen["timeout"] == 200.0


# ── the deadline covers the queue ───────────────────────────────────────────


def test_the_unscaled_deadline_loses_every_run_behind_the_first(polled_clock):
    """Characterization: this is the shape of the bug, and why the scaling exists.

    Seven runs submitted at once against a single worker, each carrying the plain
    600s module default — the first completes and the rest die waiting, which is
    exactly what the optimizer was publishing reports on top of.
    """
    server = SerializedServer(work=500.0)
    polled_clock(server)
    client = FakeClient(server)

    async def race():
        return await asyncio.gather(
            *[
                run_and_save(
                    client, SERVER, cfg, 0, 1, poll_interval=0.0, timeout=600.0
                )
                for cfg in _configs(7)
            ],
            return_exceptions=True,
        )

    results = asyncio.run(race())
    timed_out = [r for r in results if isinstance(r, BacktestError)]

    assert len(timed_out) == 6
    assert "still running after 600s" in str(timed_out[0])


def test_a_bounded_batch_outlasts_the_server_queue(polled_clock):
    """The same server, the same per-run budget — nothing is lost."""
    server = SerializedServer(work=500.0)
    polled_clock(server)

    outcomes = asyncio.run(
        run_many(
            FakeClient(server),
            SERVER,
            _configs(7),
            0,
            1,
            max_concurrent=4,
            poll_interval=0.0,
            timeout=600.0,
        )
    )

    assert [o.ok for o in outcomes] == [True] * 7
    assert all(o.error is None for o in outcomes)


def test_a_failed_run_comes_back_named_instead_of_none():
    server = SerializedServer(work=0.0, fail_ids={"cfg-2"})
    outcomes = asyncio.run(
        run_many(FakeClient(server), SERVER, _configs(4), 0, 1, poll_interval=0.0)
    )

    failed = [o for o in outcomes if not o.ok]
    assert [o.config["id"] for o in failed] == ["cfg-2"]
    assert "exceeded its budget" in failed[0].error
    assert failed[0].task is None


# ── the optimizer reports what it could not run ─────────────────────────────


class FakeReportBuilder:
    """Records the report instead of writing one."""

    last: "FakeReportBuilder | None" = None

    def __init__(self, title="Report"):
        self.title = title
        self.kpis: dict[str, str] = {}
        self.sections: list[tuple[str, str]] = []
        self.tables: list[list[dict]] = []
        self.markdowns: list[str] = []
        FakeReportBuilder.last = self

    def source(self, *a, **k):
        return self

    def tags(self, *a, **k):
        return self

    def kpi(self, label, value, *a, **k):
        self.kpis[label] = value
        return self

    def section(self, title, subtitle="", *a, **k):
        self.sections.append((title, subtitle))
        return self

    def markdown(self, text, *a, **k):
        self.markdowns.append(text)
        return self

    def table(self, rows, columns=None, *a, **k):
        self.tables.append(list(rows))
        return self

    def plotly(self, *a, **k):
        return self

    def manual_order(self, *a, **k):
        return self

    async def save(self, *a, **k):
        return "report-1"


def _run_optimizer(monkeypatch, server, n_variations=6):
    """Drive ``mm_optimizer_cycle.run`` against ``server``; return (summary, report)."""
    base_cfg = {
        "id": CTRL_ID,
        "controller_name": "pmm_mister",
        "buy_spreads": [0.0002],
        "sell_spreads": [0.0002],
        "take_profit": 0.0001,
        "max_active_executors_by_level": 10,
    }
    client = FakeClient(server, [base_cfg])

    async def get_client(name):
        return client

    async def fetch_bots_status(_client):
        return {}

    def extract_bots_list(_raw):
        return [
            {
                "bot_name": "hummingbot-mm-20260101-000000",
                "performance": {
                    CTRL_ID: {
                        "performance": {
                            "positions_summary": [{"trading_pair": "BTC-BRL"}],
                            "realized_pnl_quote": 100.0,
                            "volume_traded": 5000.0,
                        }
                    }
                },
            }
        ]

    async def call_routine(*a, **k):
        return SimpleNamespace(text="regime=neutral")

    monkeypatch.setattr(
        mm, "get_config_manager", lambda: SimpleNamespace(get_client=get_client)
    )
    monkeypatch.setattr(mm, "fetch_bots_status", fetch_bots_status)
    monkeypatch.setattr(mm, "extract_bots_list", extract_bots_list)
    monkeypatch.setattr(mm, "call_routine", call_routine)
    monkeypatch.setattr(mm, "ReportBuilder", FakeReportBuilder)
    # The routine takes run_many's polling defaults; the fake server's clock only
    # moves when it is polled, so waiting between polls buys the test nothing.
    monkeypatch.setattr(core, "DEFAULT_POLL_INTERVAL", 0.0)

    config = mm.Config(server_name=SERVER, n_variations=n_variations)
    summary = asyncio.run(mm.run(config, SimpleNamespace(_chat_id=1)))
    return summary, FakeReportBuilder.last


@needs_brigado
def test_no_variation_is_dropped_without_a_report_entry(monkeypatch):
    """The acceptance criterion: every config the sweep claims is accounted for.

    Two of the six variations die in the server's queue. The report must still
    add up — scored rows plus named failures cover all six — and the summary
    line must say so rather than quietly reporting a four-variation sweep.
    """
    server = SerializedServer(
        work=20.0, fail_ids={f"_opt_{CTRL_ID}_1", f"_opt_{CTRL_ID}_4"}
    )
    summary, report = _run_optimizer(monkeypatch, server)

    assert server.peak_in_flight == core.DEFAULT_MAX_CONCURRENT

    failures = next(rows for rows in report.tables if rows and "error" in rows[0])
    assert {r["config"] for r in failures} == {"var_1", "var_4"}
    assert all("budget" in r["error"] for r in failures)
    assert (
        "Did not complete",
        "2 config(s) produced no backtest — not scored below",
    ) in [(t, s) for t, s in report.sections]

    ranked = next(rows for rows in report.tables if rows and "score" in rows[0])
    scored = {r["config"] for r in ranked if r["config"].startswith("var_")}
    assert scored | {"var_1", "var_4"} == {f"var_{i}" for i in range(6)}

    assert report.kpis["Completed"] == "5/7"
    assert "failed=2" in summary


@needs_brigado
def test_a_clean_sweep_reports_no_failures(monkeypatch):
    server = SerializedServer(work=20.0)
    summary, report = _run_optimizer(monkeypatch, server)

    assert not any(rows and "error" in rows[0] for rows in report.tables)
    assert "Did not complete" not in [t for t, _ in report.sections]
    assert report.kpis["Completed"] == "7/7"
    assert "failed=0" in summary
