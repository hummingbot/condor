"""A session row carries when the session really started (CORR-388).

The equity curve used to plot sessions one hour apart ending at ``Date.now()``
because the performance rows said nothing about time. Each ``kind="session"``
row now carries ``started_at`` from ``session_start_epoch`` (config.yml mtime);
an experiment carries 0.0, so the chart can never place one on the axis.
"""

import asyncio
import os
from collections import OrderedDict

from condor.agents.attribution import session_start_epoch
from condor.runtime import loops as loops_module
from condor.web.routes import agents as agents_routes

RUN_KEY = "my_agent.my_strategy"


class _FakeExecutorsApi:
    async def search_executors(self, **kwargs):
        return {
            "executors": [
                {
                    "id": "x",
                    "status": "TERMINATED",
                    "net_pnl_quote": 1.0,
                    "filled_amount_quote": 10.0,
                    "cum_fees_quote": 0.1,
                    "config": {"type": "position_executor", "entry_price": 1.0},
                }
            ]
        }


class _FakeClient:
    executors = _FakeExecutorsApi()


def test_session_rows_carry_their_real_start_and_experiments_carry_zero(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(agents_routes, "_PERF_CACHE", {})
    monkeypatch.setattr(agents_routes, "_CLOSED_PERF_CACHE", OrderedDict())
    monkeypatch.setattr(loops_module.get_supervisor(), "_engines", {})

    async def _fake_get_client(strategy_dir, default_config, principal):
        return _FakeClient(), "srv"

    monkeypatch.setattr(agents_routes, "_get_client_for_strategy", _fake_get_client)

    # Two sessions started weeks apart, and one dry run.
    starts = {1: 1_750_000_000.0, 2: 1_752_000_000.0}
    for num, start in starts.items():
        sd = tmp_path / "sessions" / f"session_{num}"
        sd.mkdir(parents=True)
        (sd / "config.yml").write_text("agent_key: x\n")
        os.utime(sd / "config.yml", (start, start))
    (tmp_path / "dry_runs").mkdir()
    (tmp_path / "dry_runs" / "experiment_1.md").write_text(
        "# Experiment #1\nMode: dry_run\n\n## Agent Response\n\nok\n"
    )

    sessions, _ = asyncio.run(
        agents_routes._compute_strategy_performance(RUN_KEY, tmp_path, None, 1)
    )

    by_kind = {(s.kind, s.session_num): s for s in sessions}
    for num, start in starts.items():
        row = by_kind[("session", num)]
        assert row.started_at == session_start_epoch(tmp_path, num)
        assert row.started_at == start
    assert by_kind[("experiment", 1)].started_at == 0.0
