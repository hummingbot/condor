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


def test_mcp_runner_stamps_agent_slug(reports_dir, tmp_path, monkeypatch):
    """The MCP routine runner must attribute an agent-targeted run's report to
    the agent slug — the same ``list_reports(agent=...)`` bucket the
    web runner uses. Regression test for CORR-027 (originally about
    composite strategy keys leaking into attribution)."""
    import condor.agents.agent as agent_mod
    import routines.base as routines_base
    from mcp_servers.condor.tools import routines as mcp_routines

    # Point both data roots (agents/, routines resolved relative to the
    # project root) at an isolated tmp tree.
    monkeypatch.setattr(agent_mod, "_DATA_ROOT", tmp_path / "agents")
    monkeypatch.setattr(routines_base, "_PROJECT_ROOT", tmp_path)

    # A real agent on disk so AgentStore().get(...) resolves.
    agent_dir = tmp_path / "agents" / "market_making_expert"
    agent_dir.mkdir(parents=True)
    (agent_dir / "AGENT.md").write_text(
        "---\nname: market_making_expert\n---\n\nBody.\n"
    )

    # A one-shot routine in the agent's routines dir, producing a report.
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

    # The routine normally runs in a disposable worker subprocess (§7.2),
    # which cannot see this test's monkeypatched tmp roots — dispatch the
    # worker's own logic in-process instead (same code path minus the fork),
    # so the attribution the worker applies is what gets asserted.
    import condor.routines_worker as worker

    async def _inline_spawn(payload, timeout_s):
        return await worker._do_run(payload)

    monkeypatch.setattr(worker, "_spawn", _inline_spawn)

    asyncio.run(
        mcp_routines.run_routine("probe", {}, agent_slug="market_making_expert")
    )

    bare, _ = rep.list_reports(agent="market_making_expert")
    assert [e["title"] for e in bare] == ["Probe report"]
