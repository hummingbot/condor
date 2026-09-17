"""The agents wire names its PnL roll-ups for what they are (READ-667).

``StrategySummary`` and ``AgentSummary`` carried a ``daily_pnl`` that was never a
calendar-day figure: it was the newest session's total PnL, handed to the model
and summed into the home page's Agents cell under a "daily" label. It is now
``latest_session_pnl`` beside ``total_pnl`` (the rollup across all sessions).
A ``RunningInstance`` is one session, so it carries only ``total_pnl``, which
keeps the engine-info fallback when no performance row exists.
"""

import asyncio

import pytest

from condor.agents.agent import AgentStore
from condor.agents.strategy import StrategyStore
from condor.web.models import WebUser
from condor.web.routes import agents as routes
from condor.web.routes.agents import (
    AgentPerformanceModel,
    AgentSummary,
    RunningInstance,
    StrategySummary,
)

USER = WebUser(id=555, username="u", first_name="U", role="user")


@pytest.mark.parametrize("model", [RunningInstance, StrategySummary, AgentSummary])
def test_no_wire_model_calls_a_pnl_daily(model):
    assert "daily_pnl" not in model.model_fields


def test_summaries_carry_the_latest_session_pnl_and_instances_do_not():
    assert "latest_session_pnl" in StrategySummary.model_fields
    assert "latest_session_pnl" in AgentSummary.model_fields
    assert "latest_session_pnl" not in RunningInstance.model_fields


def test_an_instance_without_a_perf_row_keeps_the_engine_figure(monkeypatch):
    import condor.agents.fleet_map as fleet_map

    monkeypatch.setattr(fleet_map, "read_last_action", lambda journal: "")
    monkeypatch.setattr(fleet_map, "read_last_did", lambda engine: None)

    info = {
        "agent_id": "demo.s_2",
        "session_num": 2,
        "status": "running",
        "tick_count": 4,
        "daily_pnl": -7.5,
    }

    class _Engine:
        journal = None

        def get_info(self):
            return dict(info)

    inst = routes._instance_from_engine(_Engine(), {})
    assert inst.total_pnl == info["daily_pnl"]


def test_strategy_summary_splits_latest_session_from_the_rollup(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    AgentStore().create(name="Brigado", description="BRL market making")
    strategy = StrategyStore().create(agent_slug="brigado", name="BRL MM")

    rows = [
        AgentPerformanceModel(agent_id="a_1", session_num=1, total_pnl=10.0),
        AgentPerformanceModel(agent_id="a_3", session_num=3, total_pnl=-4.0),
        AgentPerformanceModel(agent_id="a_2", session_num=2, total_pnl=30.0),
        # An experiment is never "the latest session", whatever its number.
        AgentPerformanceModel(
            agent_id="x_9", session_num=9, kind="experiment", total_pnl=99.0
        ),
    ]

    async def _fake_perf(run_key, strategy_dir, default_config, principal):
        return rows, {"total_pnl": 36.0, "volume": 0.0, "open_positions": 0}

    monkeypatch.setattr(routes, "_compute_strategy_performance", _fake_perf)
    monkeypatch.setattr(routes, "_strategy_principal", lambda s, u: USER.id)
    monkeypatch.setattr(routes, "_get_engines_for", lambda a, s: [])

    summary = asyncio.run(routes._build_strategy_summary(strategy, USER))
    assert summary.latest_session_pnl == -4.0
    assert summary.total_pnl == 36.0

    rolled = routes._aggregate_strategy_perf([summary, summary])
    assert rolled["latest_session_pnl"] == -8.0
    assert "daily_pnl" not in rolled
