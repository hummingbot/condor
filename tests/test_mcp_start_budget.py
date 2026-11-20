"""``control_agent(start)`` waits long enough, and looks before it reports (MC-01).

The start route answers only after the first tick's bounded wait
(``FIRST_TICK_WAIT_SEC``), and the tool gave the call the 15 seconds of a plain
question. Any ordinary LLM tick therefore read as "failed to reach the main
process" while the loop ran on behind it, and the retry that invites is a second
live session. Pinned here: the budget is above the wait, the start crossing asks
for it, and a call that times out anyway checks what is running before
answering.
"""

import asyncio
from types import SimpleNamespace

import pytest

from condor.agents.engine import FIRST_TICK_WAIT_SEC
from condor.runtime.timeouts import TIMEOUTS
from mcp_servers.condor.exceptions import APITimeout, APIUnreachable
from mcp_servers.condor.tools import trading_agent

LOOP = SimpleNamespace(agent_slug="scout", slug="grid", home=None, default_config={})


def test_the_start_budget_outlasts_the_first_tick_wait():
    """With headroom for the model check and the trading preflight before it."""
    assert TIMEOUTS.mcp_start >= FIRST_TICK_WAIT_SEC + TIMEOUTS.mcp_call


def _start(monkeypatch, api):
    import condor.agents.agent as agent_module
    import condor.agents.config as config_module
    import condor.agents.strategy as strategy_module

    monkeypatch.setattr(
        strategy_module,
        "StrategyStore",
        lambda: SimpleNamespace(resolve_for_loop=lambda key: LOOP),
    )
    monkeypatch.setattr(config_module, "load_full_config", lambda home, default: {})
    monkeypatch.setattr(
        agent_module, "AgentStore", lambda: SimpleNamespace(get=lambda slug: None)
    )
    monkeypatch.setattr(trading_agent.settings, "active_server", "main", raising=False)
    monkeypatch.setattr(trading_agent, "call_main_api", api)
    return asyncio.run(trading_agent.control_agent("start", loop_id="scout.grid"))


def test_the_start_crossing_asks_for_that_budget(monkeypatch):
    seen: list = []

    async def api(method, path, body=None, timeout=None):
        seen.append((method, path, timeout))
        return {"started": True, "agent_id": "scout.grid_1"}

    result = _start(monkeypatch, api)

    assert result["started"] is True
    assert seen == [("POST", "/agents/scout/strategies/grid/start", TIMEOUTS.mcp_start)]


def _summaries(*instances):
    return [
        {
            "slug": "scout",
            "strategies": [
                {
                    "slug": "other",
                    "instances": [{"agent_id": "x", "status": "running"}],
                },
                {"slug": "grid", "instances": list(instances)},
            ],
        }
    ]


def _timing_out(listing):
    async def api(method, path, body=None, timeout=None):
        if method == "POST":
            raise APITimeout("The main process did not answer within 60s.")
        if isinstance(listing, Exception):
            raise listing
        return listing

    return api


def test_a_timed_out_start_that_came_up_is_not_reported_as_a_failure(monkeypatch):
    listing = _summaries({"agent_id": "scout.grid_3", "status": "running"})

    result = _start(monkeypatch, _timing_out(listing))

    assert "error" not in result
    assert result["running_instances"] == ["scout.grid_3"]
    assert "do NOT start it again" in result["note"]


def test_a_timed_out_start_with_nothing_running_says_check_first(monkeypatch):
    result = _start(monkeypatch, _timing_out(_summaries()))

    assert "did not answer" in result["error"]
    assert "No running instance" in result["error"]
    assert 'control_agent(action="list")' in result["error"]


def test_a_timed_out_start_that_cannot_be_checked_forbids_the_retry(monkeypatch):
    result = _start(monkeypatch, _timing_out(APIUnreachable("down")))

    assert "could not be confirmed" in result["error"]
    assert "do NOT start it again" in result["error"]


@pytest.mark.parametrize("verb", ["stop", "pause", "resume"])
def test_the_other_verbs_keep_the_ordinary_budget(monkeypatch, verb):
    seen: list = []

    async def api(method, path, body=None, timeout=None):
        seen.append(timeout)
        return {}

    monkeypatch.setattr(trading_agent, "call_main_api", api)
    asyncio.run(trading_agent.control_agent(verb, agent_id="scout.grid_1"))

    assert seen == [None]
