"""A dry run is a run, and every surface has to be able to see it.

``_compute_strategy_performance`` used to drop experiments two ways over:

* ``if kind == "experiment" and perf.trade_count == 0: continue`` — but a dry
  run simulates and therefore books no trades *by definition*, so the filter
  removed exactly the rows it was meant to tidy;
* the whole pricing block runs ``if client and ids``, so with no Hummingbot
  server configured nothing came back at all — on the setup where a dry run is
  the most useful thing you can do.

Between them, a strategy whose entire history was a dry run rendered as one
that had never run. These tests pin both paths open.
"""

import asyncio
from collections import OrderedDict
from pathlib import Path

import pytest

from condor.runtime import loops as loops_module
from condor.web.routes import agents as agents_routes

RUN_KEY = "brigado.brl_mm"


def _experiment_md(num: int, response: str = "Would have quoted 15bps.") -> str:
    """The shape ``save_experiment_snapshot`` actually writes."""
    return (
        f"# Experiment #{num} — 2026-08-19 23:20 UTC\n"
        "Mode: dry_run\n"
        "Model: ollama:qwen3.5:9\n"
        "\n"
        "## Agent Response\n"
        "\n"
        f"{response}\n"
    )


@pytest.fixture()
def strategy_dir(tmp_path, monkeypatch):
    """A strategy on disk with one dry run and no sessions, caches isolated."""
    monkeypatch.setattr(agents_routes, "_PERF_CACHE", {})
    monkeypatch.setattr(agents_routes, "_CLOSED_PERF_CACHE", OrderedDict())
    monkeypatch.setattr(loops_module.get_supervisor(), "_engines", {})

    d: Path = tmp_path / "brl_mm"
    (d / "dry_runs").mkdir(parents=True)
    (d / "dry_runs" / "experiment_1.md").write_text(_experiment_md(1))
    return d


def _compute(d):
    return asyncio.run(agents_routes._compute_strategy_performance(RUN_KEY, d, None))


def test_a_dry_run_is_listed_with_no_backend_at_all(strategy_dir, monkeypatch):
    async def _no_client(strategy_dir, default_config):
        return None, ""

    monkeypatch.setattr(agents_routes, "_get_client_for_strategy", _no_client)

    sessions, totals = _compute(strategy_dir)

    assert [(s.kind, s.session_num) for s in sessions] == [("experiment", 1)]
    # The row says which kind of experiment it was: a simulation and a single
    # real tick both live in dry_runs/ and only one of them can lose money.
    assert sessions[0].execution_mode == "dry_run"
    # And it books nothing — which is the point, not a reason to hide it.
    assert totals["total_pnl"] == 0


def test_a_dry_run_never_reaches_the_money_totals(strategy_dir, monkeypatch):
    """Listed, counted, and still not folded into PnL."""

    async def _no_client(strategy_dir, default_config):
        return None, ""

    monkeypatch.setattr(agents_routes, "_get_client_for_strategy", _no_client)
    (strategy_dir / "sessions" / "session_1").mkdir(parents=True)

    sessions, totals = _compute(strategy_dir)

    kinds = sorted(s.kind for s in sessions)
    assert "experiment" in kinds
    # `real_sessions` — what the totals sum — excludes experiments, so a dry run
    # can be shown beside the money without ever being counted as money.
    assert totals["volume"] == 0
    assert totals["open_positions"] == 0


def test_an_errored_dry_run_says_so(strategy_dir, monkeypatch):
    async def _no_client(strategy_dir, default_config):
        return None, ""

    monkeypatch.setattr(agents_routes, "_get_client_for_strategy", _no_client)
    (strategy_dir / "dry_runs" / "experiment_2.md").write_text(
        _experiment_md(2, "(error: status_code: 404, model not found)")
    )

    sessions, _ = _compute(strategy_dir)

    by_num = {s.session_num: s for s in sessions if s.kind == "experiment"}
    assert by_num[2].error is True
    assert by_num[1].error is False
