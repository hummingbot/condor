"""Small leftovers in the loop/agent tools and the model report (MC-15).

* ``manage_loops(update)`` saved name/instructions/skills and *then* validated
  ``config``, so an invalid config answered "error" with half the edit applied;
  and ``if description:`` could not clear a description.
* The hub actions do blocking HTTP and ran on the subprocess's event loop.
* ``get_available_models`` probed its three sources, and each custom endpoint,
  one after another — every dead endpoint added its full timeout.
"""

import asyncio
import threading
import time

import pytest

from condor.agents.agent import AgentStore
from condor.agents.strategy import StrategyStore
from mcp_servers.condor.tools import available_models
from mcp_servers.condor.tools import trading_agent as ta


@pytest.fixture
def loop(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    AgentStore().create(name="Scout", description="d", instructions="i")
    return StrategyStore().create(
        agent_slug="scout",
        name="Grid",
        description="the original description",
        instructions="the original playbook",
    )


# ── update validates before it saves ──


def test_an_invalid_config_saves_nothing_else(loop):
    result = ta.manage_loops(
        "update",
        loop_id=loop.key,
        name="Renamed",
        instructions="a rewritten playbook",
        skills=["x"],
        config={"frequency_sec": "not a number"},
    )

    assert "Invalid config" in result["error"]
    stored = StrategyStore().get_by_key(loop.key)
    assert stored.name == "Grid"
    assert stored.instructions.strip() == "the original playbook"
    assert stored.skills == []


def test_a_valid_update_still_lands_whole(loop):
    from condor.agents.config import load_full_config

    result = ta.manage_loops(
        "update",
        loop_id=loop.key,
        instructions="a rewritten playbook",
        config={"frequency_sec": 300},
    )

    assert result["updated"] is True
    stored = StrategyStore().get_by_key(loop.key)
    assert stored.instructions.strip() == "a rewritten playbook"
    assert load_full_config(stored.home, stored.default_config)["frequency_sec"] == 300


def test_a_description_can_be_cleared(loop):
    assert ta.manage_loops("update", loop_id=loop.key, description="")["updated"]

    assert StrategyStore().get_by_key(loop.key).description == ""


def test_an_omitted_description_is_left_alone(loop):
    ta.manage_loops("update", loop_id=loop.key, instructions="v2")

    stored = StrategyStore().get_by_key(loop.key)
    assert stored.description == "the original description"
    assert stored.instructions.strip() == "v2"


# ── hub calls leave the event loop ──


@pytest.mark.parametrize(
    "action,kwargs",
    [
        ("hub_browse", {}),
        ("hub_get", {"path": "listed"}),
        ("publish_hub", {"agent_slug": "scout"}),
    ],
)
def test_hub_actions_run_off_the_event_loop(monkeypatch, action, kwargs):
    seen: dict = {}

    def fake_manage_agents(called_action, **kw):
        seen["thread"] = threading.current_thread()
        seen["call"] = (called_action, kw)
        return {"ok": True}

    monkeypatch.setattr(ta, "manage_agents", fake_manage_agents)

    result = asyncio.run(ta.manage_agents_tool(action, **kwargs))

    assert result == {"ok": True}
    assert seen["call"] == (action, kwargs)
    assert seen["thread"] is not threading.main_thread()


def test_local_agent_actions_stay_inline(monkeypatch):
    seen: dict = {}

    def fake_manage_agents(called_action, **kw):
        seen["thread"] = threading.current_thread()
        return {"agents": []}

    monkeypatch.setattr(ta, "manage_agents", fake_manage_agents)

    asyncio.run(ta.manage_agents_tool("list"))

    assert seen["thread"] is threading.main_thread()


# ── the model probes run together ──


def test_the_three_probes_overlap(monkeypatch):
    async def slow(result):
        await asyncio.sleep(0.2)
        return result

    monkeypatch.setattr(available_models, "local_servers", lambda: slow({}))
    monkeypatch.setattr(available_models, "_custom_endpoints", lambda: slow([]))
    monkeypatch.setattr(
        available_models, "_openrouter", lambda q, n: slow({"models": []})
    )
    monkeypatch.setattr(available_models, "acp_bridges", lambda: [])

    started = time.monotonic()
    report = asyncio.run(available_models.get_available_models())
    elapsed = time.monotonic() - started

    assert report["custom_endpoints"] == [] and report["local"] == {}
    assert elapsed < 0.5  # 0.6s in sequence


def test_custom_endpoints_are_probed_together_and_keep_their_order(monkeypatch):
    from condor import preferences
    from condor.llm import custom_models

    providers = [
        {"name": "dead", "base_url": "http://dead"},
        {"name": "live", "base_url": "http://live", "api_key": "k"},
        {"name": "also_dead", "base_url": "http://dead2"},
    ]
    monkeypatch.setattr(preferences, "load_user_data_for", lambda uid: {})
    monkeypatch.setattr(preferences, "get_custom_providers", lambda data: providers)

    async def fake_fetch(base_url, api_key=""):
        await asyncio.sleep(0.2)
        if "dead" in base_url:
            raise custom_models.CustomProviderError("down")
        return base_url, ["m1"]

    monkeypatch.setattr(custom_models, "fetch_models", fake_fetch)

    started = time.monotonic()
    entries = asyncio.run(available_models._custom_endpoints())
    elapsed = time.monotonic() - started

    assert [e["name"] for e in entries] == ["dead", "live", "also_dead"]
    assert [e["reachable"] for e in entries] == [False, True, False]
    assert entries[1]["models"] == [{"agent_key": "custom@live:m1", "model": "m1"}]
    assert elapsed < 0.5  # 0.6s in sequence
