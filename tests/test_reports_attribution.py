"""Reports stay in one flat store but are stamped with their producer (agent)."""

import asyncio

import pytest

import condor.reports as rep


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(rep, "CHARTS_DIR", tmp_path)
    monkeypatch.setattr(rep, "INDEX_FILE", tmp_path / "reports_index.json")
    return tmp_path


def _save(title: str) -> None:
    async def go():
        b = rep.ReportBuilder(title)
        b.source("routine", "r")
        b.markdown("body")
        await b.save()

    asyncio.run(go())


def test_default_attribution_is_condor(reports_dir):
    _save("Plain report")
    entries, _ = rep.list_reports()
    assert entries[0]["agent"] == "condor"


def test_attribute_to_stamps_and_filters(reports_dir):
    _save("From chat")  # default -> condor

    async def go():
        with rep.attribute_to("executor_manager"):
            b = rep.ReportBuilder("From expert")
            b.source("routine", "grid_check")
            b.markdown("y")
            await b.save()

    asyncio.run(go())

    assert sorted(e["agent"] for e in rep.list_reports()[0]) == [
        "condor",
        "executor_manager",
    ]
    expert, _ = rep.list_reports(agent="executor_manager")
    assert [e["title"] for e in expert] == ["From expert"]
    chat, _ = rep.list_reports(agent="condor")
    assert [e["title"] for e in chat] == ["From chat"]


def test_attribution_resets_after_block(reports_dir):
    async def go():
        with rep.attribute_to("executor_manager"):
            pass
        b = rep.ReportBuilder("After block")
        b.source("routine", "r")
        b.markdown("z")
        await b.save()

    asyncio.run(go())
    assert rep.list_reports()[0][0]["agent"] == "condor"


def test_mcp_runner_stamps_bare_agent_slug_for_strategy(
    reports_dir, tmp_path, monkeypatch
):
    """The MCP routine runner must attribute a strategy's report to the bare
    owning-agent slug, not the composite ``"{agent}.{strategy}"`` key — so it
    lands in the same ``list_reports(agent=...)`` bucket as the web/Telegram
    runner (which uses the bare slug). Regression test for CORR-027."""
    import condor.agents.strategy as strategy_mod
    import routines.base as routines_base
    from mcp_servers.condor.tools import routines as mcp_routines

    # Point both data roots (strategies live under agents/, routines are
    # resolved relative to the project root) at an isolated tmp tree.
    monkeypatch.setattr(strategy_mod, "_DATA_ROOT", tmp_path / "agents")
    monkeypatch.setattr(routines_base, "_PROJECT_ROOT", tmp_path)

    # A real strategy on disk so StrategyStore().get_by_key(...) resolves.
    strategy = strategy_mod.StrategyStore().create(
        agent_slug="market_making_expert",
        name="Scalp v2",
    )
    assert strategy.key == "market_making_expert.scalp_v2"  # composite key

    # A one-shot routine in the owning agent's routines dir, producing a report.
    routines_dir = routines_base.assistant_routines_dir("market_making_expert")
    routines_dir.mkdir(parents=True, exist_ok=True)
    (routines_dir / "probe.py").write_text(
        "from pydantic import BaseModel\n"
        "import condor.reports as rep\n"
        "class Config(BaseModel):\n"
        '    """probe"""\n'
        "async def run(config, context):\n"
        "    b = rep.ReportBuilder('Probe report')\n"
        "    b.source('routine', 'probe')\n"
        "    b.markdown('body')\n"
        "    await b.save()\n"
        "    return 'done'\n"
    )

    asyncio.run(mcp_routines.run_routine("probe", {}, strategy_id=strategy.key))

    # Report is bucketed under the bare slug, not the composite key.
    bare, _ = rep.list_reports(agent="market_making_expert")
    assert [e["title"] for e in bare] == ["Probe report"]
    assert rep.list_reports(agent=strategy.key)[0] == []
