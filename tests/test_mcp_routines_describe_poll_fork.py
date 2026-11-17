"""Three small faults in ``manage_routines`` (MC-14).

* ``describe`` ignored ``agent=``: the chat could list and run an agent's
  routine and was told "not found" when it asked what config it takes.
* The blocking ``run`` slept a full second before its first look, so a 50 ms
  routine cost a second on every tick that followed a skill's routine link.
* ``edit_routine`` forked the shipped routine down before checking the edit,
  so a rejected edit left a local copy shadowing the shipped one for good.
"""

import asyncio

import pytest

from condor.agents.agent import AgentStore
from condor.memory.paths import agent_home, stock_agent_home
from mcp_servers.condor.settings import settings
from mcp_servers.condor.tools import routines as tool

AGENT_MD = "---\nname: Scout\ndescription: shipped\n---\n\nShip.\n"


def _src(desc: str) -> str:
    return (
        "from pydantic import BaseModel, Field\n\n\n"
        "class Config(BaseModel):\n"
        f'    """{desc}"""\n\n'
        '    pair: str = Field(default="SOL-USDC", description="Pair")\n\n\n'
        "async def run(config, context):\n"
        "    return 'ok'\n"
    )


@pytest.fixture
def shipped(monkeypatch):
    """A shipped agent with one shipped routine and no local files."""
    monkeypatch.setattr(settings, "agent_slug", "condor")
    home = stock_agent_home("scout")
    (home / "routines").mkdir(parents=True)
    (home / "AGENT.md").write_text(AGENT_MD)
    (home / "routines" / "recon_sweep.py").write_text(_src("SHIPPED"))
    assert AgentStore().get("scout") is not None
    return home / "routines" / "recon_sweep.py"


def _local():
    return agent_home("scout") / "routines" / "recon_sweep.py"


# ── describe ──


def test_describe_resolves_an_agents_routine_from_the_chat(shipped):
    described = asyncio.run(
        tool.manage_routines("describe", name="recon_sweep", agent="scout")
    )

    assert described["description"] == "SHIPPED"
    assert "pair" in str(described["fields"])


def test_describe_without_an_agent_keeps_the_callers_own_scope(shipped):
    described = asyncio.run(tool.manage_routines("describe", name="recon_sweep"))

    assert described == {"error": "Routine 'recon_sweep' not found"}


# ── the poll ──


def test_the_first_looks_come_well_before_the_steady_interval(monkeypatch):
    slept: list[float] = []
    answers = iter(["running"] * 6 + ["completed"])

    async def fake_sleep(delay):
        slept.append(delay)

    async def fake_api(method, path, body=None, timeout=None):
        return {"status": next(answers)}

    monkeypatch.setattr(tool.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(tool, "call_main_api", fake_api)

    inst = asyncio.run(tool._poll_until_done("inst-1"))

    assert inst == {"status": "completed"}
    assert slept == [0.05, 0.1, 0.2, 0.5, 1.0, 1.0, 1.0]


def test_a_fast_routine_is_read_back_after_the_first_short_wait(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(delay):
        slept.append(delay)

    async def fake_api(method, path, body=None, timeout=None):
        return {"status": "completed"}

    monkeypatch.setattr(tool.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(tool, "call_main_api", fake_api)

    asyncio.run(tool._poll_until_done("inst-1"))

    assert slept == [0.05]


# ── the fork ──


def test_an_edit_with_no_code_does_not_fork_the_shipped_routine(shipped):
    result = tool.edit_routine("recon_sweep", "", "scout")

    assert result == {"error": "code is required"}
    assert not _local().exists()


def test_a_rejected_edit_leaves_no_fork_behind(shipped):
    result = tool.edit_routine("recon_sweep", "def broken(:\n", "scout")

    assert "failed to load" in result["error"]
    assert "shipped routine is unchanged" in result["error"]
    assert not _local().exists()
    assert "SHIPPED" in shipped.read_text()


def test_a_good_edit_still_forks_and_lands(shipped):
    result = tool.edit_routine("recon_sweep", _src("MINE"), "scout")

    assert result.get("updated") is True
    assert "MINE" in _local().read_text()
    assert "SHIPPED" in shipped.read_text()


def test_a_rejected_edit_of_a_local_routine_still_reverts(shipped):
    assert tool.edit_routine("recon_sweep", _src("MINE"), "scout").get("updated")

    result = tool.edit_routine("recon_sweep", "def broken(:\n", "scout")

    assert "Reverted to previous version" in result["error"]
    assert "MINE" in _local().read_text()
