"""A delete from the tool goes through the main process, never the store (MC-02).

``StrategyStore.delete`` removes a loop's whole home — sessions, journal,
learnings, config, state — and asks nothing. Whether the loop is *running* is
known only to the main process, whose route refuses the delete; the tool wrote
the store from its subprocess and so deleted a live loop's directory out from
under an engine that kept trading. Pinned: both deletes cross, a refusal comes
back as the route worded it, and nothing on disk moved.
"""

import asyncio

import pytest

from condor.agents.agent import AgentStore
from condor.agents.strategy import StrategyStore
from mcp_servers.condor.exceptions import APIHTTPError
from mcp_servers.condor.tools import trading_agent as ta


@pytest.fixture
def loop(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    AgentStore().create(name="Scout", description="d", instructions="i")
    return StrategyStore().create(agent_slug="scout", name="Grid", instructions="x")


def _api(monkeypatch, outcome):
    called: list = []

    async def fake_api(method, path, body=None, timeout=None):
        called.append((method, path))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(ta, "call_main_api", fake_api)
    return called


def test_a_running_loop_is_not_deleted(loop, monkeypatch):
    refusal = "Cannot delete a running strategy. Stop all instances first."
    called = _api(monkeypatch, APIHTTPError(400, refusal))

    result = asyncio.run(ta.manage_loops_tool("delete", loop_id=loop.key))

    assert called == [("DELETE", "/agents/scout/strategies/grid")]
    assert refusal in result["error"]
    assert loop.home.is_dir()
    assert StrategyStore().get_by_key(loop.key) is not None


def test_the_legacy_spelling_crosses_too(loop, monkeypatch):
    called = _api(monkeypatch, {"deleted": True})

    result = asyncio.run(ta.manage_loops_tool("delete_strategy", loop_id=loop.key))

    assert result == {"deleted": True}
    assert called == [("DELETE", "/agents/scout/strategies/grid")]
    # The fake route removed nothing, and neither did the tool.
    assert loop.home.is_dir()


def test_the_local_half_can_no_longer_delete_a_loop(loop):
    result = ta.manage_loops("delete", loop_id=loop.key)

    assert "main process" in result["error"]
    assert loop.home.is_dir()


@pytest.mark.parametrize("loop_id,needle", [(None, "required"), ("nodot", "Invalid")])
def test_a_bad_loop_id_never_reaches_the_route(monkeypatch, loop_id, needle):
    called = _api(monkeypatch, {"deleted": True})

    result = asyncio.run(ta.manage_loops_tool("delete", loop_id=loop_id))

    assert needle in result["error"]
    assert called == []


def test_an_agent_with_loops_is_refused_before_the_route(loop, monkeypatch):
    called = _api(monkeypatch, {"deleted": True})

    result = asyncio.run(ta.manage_agents_tool("delete", agent_slug="scout"))

    assert "still owns 1 loop" in result["error"]
    assert called == []


def test_an_agent_delete_crosses_and_relays_the_refusal(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    AgentStore().create(name="Scout", description="d", instructions="i")
    refusal = "Cannot delete an Agent with running strategies. Stop them first."
    called = _api(monkeypatch, APIHTTPError(400, refusal))

    result = asyncio.run(ta.manage_agents_tool("delete", agent_slug="scout"))

    assert called == [("DELETE", "/agents/scout")]
    assert refusal in result["error"]
    assert AgentStore().get("scout") is not None
    assert "main process" in ta.manage_agents("delete", agent_slug="scout")["error"]


def test_the_other_actions_stay_local(loop, monkeypatch):
    called = _api(monkeypatch, {"deleted": True})

    listed = asyncio.run(ta.manage_loops_tool("list"))
    got = asyncio.run(ta.manage_agents_tool("get", agent_slug="scout"))

    assert [entry["id"] for entry in listed["loops"]] == [loop.key]
    assert got["slug"] == "scout"
    assert called == []


def test_a_misrouted_action_is_still_explained():
    result = asyncio.run(ta.manage_loops_tool("start_agent"))

    assert "control_agent" in result["error"]
