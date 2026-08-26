"""What each seat mounts (FEAT-066).

Tool allowlists are only enforced for pydantic-ai model keys; an ACP bridge
(claude-code, gemini, copilot) runs unrestricted. For those seats the surface a
session MOUNTS is the whole permission model, so every profile's tool set is
pinned here as a golden list: a tool added to the wrong ring fails a test rather
than quietly widening the seat that trades with real capital.
"""

import asyncio

import pytest
from mcp.server.fastmcp import FastMCP

from condor.memory.paths import CHAT_SLUG
from condor.runtime.toolsets import (
    _condor_mcp_args,
    _hummingbot_mcp_args,
    seat_profile,
)
from mcp_servers.condor import server as condor_server
from mcp_servers.hummingbot_api import server as hb_server

# ── golden lists ─────────────────────────────────────────────────────────────

HB_TRADING = {
    "get_portfolio_overview",
    "set_account_position_mode_and_leverage",
    "search_history",
    "get_prices",
    "get_candles",
    "get_funding_rate",
    "get_order_book",
    "manage_controllers",
    "manage_bots",
    "create_position_executor",
    "create_grid_executor",
    "create_dca_executor",
    "create_order_executor",
    "create_lp_executor",
    "list_executors",
    "get_executor",
    "stop_executor",
    "list_orphaned_positions",
    "resolve_orphaned_position",
    "list_positions_held",
    "clear_position_held",
    "get_performance_report",
    "executor_defaults",
    "explore_dex_pools",
    "quote_swap",
    "execute_swap",
    "get_swap_status",
    "search_swaps",
    "explore_geckoterminal",
}
HB_LIQUIDITY = {"manage_amm", "manage_clmm"}
HB_ADMIN = {"configure_server", "manage_gateway_config", "manage_gateway_container"}

HB_PROFILES = {
    "tick": HB_TRADING,
    "agent": HB_TRADING | HB_LIQUIDITY,
    "full": HB_TRADING | HB_LIQUIDITY | HB_ADMIN,
}

CONDOR_COMMON = {
    "consult",
    "delegate",
    "send_notification",
    "manage_routines",
    "run_code",
    "manage_servers",
    "manage_memory",
    "manage_skill",
    "trading_agent_journal_read",
    "trading_agent_journal_write",
}
CONDOR_ORCHESTRATION = {
    "manage_agents",
    "manage_strategies",
    "control_agent",
    "get_available_models",
}

CONDOR_PROFILES = {
    "tick": CONDOR_COMMON,
    "agent": CONDOR_COMMON | CONDOR_ORCHESTRATION,
    "full": CONDOR_COMMON | CONDOR_ORCHESTRATION,
}


def _registered(module, profile: str) -> set[str]:
    """The tool names ``profile`` puts on a fresh server."""
    server = FastMCP("profile-probe")
    module.register_tools(server, profile)
    return {tool.name for tool in asyncio.run(server.list_tools())}


# ── each profile registers exactly its golden list ───────────────────────────


@pytest.mark.parametrize("profile,expected", sorted(HB_PROFILES.items()))
def test_the_hummingbot_profile_registers_exactly_its_tools(profile, expected):
    assert _registered(hb_server, profile) == expected


@pytest.mark.parametrize("profile,expected", sorted(CONDOR_PROFILES.items()))
def test_the_condor_profile_registers_exactly_its_tools(profile, expected):
    assert _registered(condor_server, profile) == expected


@pytest.mark.parametrize("module", [hb_server, condor_server])
def test_the_profiles_nest(module):
    """tick ⊆ agent ⊆ full — a wider seat never *loses* a tool."""
    tick = _registered(module, "tick")
    agent = _registered(module, "agent")
    full = _registered(module, "full")
    assert tick <= agent <= full


@pytest.mark.parametrize("module", [hb_server, condor_server])
def test_every_tool_the_module_defines_lands_in_some_profile(module):
    """A newly written tool has to be sorted into a ring, or it is unreachable."""
    full = _registered(module, "full")
    for name in module.TOOL_PROFILES:
        assert _registered(module, name) <= full
    assert full == {fn.__name__ for fn in module.TOOL_PROFILES["full"]}


# ── the acceptance criterion: what a tick cannot name ────────────────────────


@pytest.mark.parametrize(
    "forbidden",
    [
        "configure_server",
        "manage_gateway_config",
        "manage_gateway_container",
        "manage_amm",
        "manage_clmm",
    ],
)
def test_a_tick_cannot_reach_the_operator_surface(forbidden):
    assert forbidden not in _registered(hb_server, "tick")


@pytest.mark.parametrize(
    "forbidden",
    ["manage_agents", "manage_strategies", "control_agent", "get_available_models"],
)
def test_a_tick_cannot_reach_the_orchestration_family(forbidden):
    """It is running inside the loop these tools start and stop."""
    assert forbidden not in _registered(condor_server, "tick")


def test_the_split_dangerous_tools_stay_with_the_seat_that_trades():
    """A tick's job IS to create executors and swap; the gate, not the mount,
    is what stands in front of those (``condor.runtime.danger``)."""
    from condor.runtime.danger import CREATE_EXECUTOR_TOOLS

    tick = _registered(hb_server, "tick")
    assert CREATE_EXECUTOR_TOOLS <= tick
    assert "execute_swap" in tick
    # …and the free half of the swap family is never gated, so it must be there.
    assert {"quote_swap", "get_swap_status", "search_swaps"} <= tick


def test_the_manage_trading_agent_funnel_is_in_no_profile():
    """FEAT-068 split it; no ring may resurrect the name."""
    for name in CONDOR_PROFILES:
        assert "manage_trading_agent" not in _registered(condor_server, name)


# ── an unknown profile is a spawner bug, not a silent widening ───────────────


@pytest.mark.parametrize("module", [hb_server, condor_server])
def test_an_unknown_profile_raises_rather_than_falling_back_to_full(module):
    with pytest.raises(ValueError, match="Unknown tool profile"):
        module.register_tools(FastMCP("probe"), "trading")


# ── the default: a launch with no flag serves everything ─────────────────────


@pytest.mark.parametrize("module", [hb_server, condor_server])
def test_a_launch_with_no_flag_serves_the_full_surface(module):
    """External-host compat: uvx, the checked-in `.mcp.json`, a bare console run."""
    server = FastMCP("probe")
    module.register_tools(server)
    assert {tool.name for tool in asyncio.run(server.list_tools())} == _registered(
        module, "full"
    )


@pytest.mark.parametrize("module", [hb_server, condor_server])
def test_the_module_singleton_is_registered_at_import(module):
    """Anything that inspects the server before startup sees a complete one."""
    assert {tool.name for tool in asyncio.run(module.mcp.list_tools())}


# ── seat → profile ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "agent_slug,tick,expected",
    [
        ("adaptive_grid_trader", True, "tick"),
        (CHAT_SLUG, True, "tick"),
        (None, True, "tick"),
        ("adaptive_grid_trader", False, "agent"),
        (CHAT_SLUG, False, "full"),
        (None, False, "full"),
        ("", False, "full"),
    ],
)
def test_the_seat_picks_its_profile(agent_slug, tick, expected):
    assert seat_profile(agent_slug, tick) == expected


def test_both_subprocesses_are_told_the_profile():
    hb = _hummingbot_mcp_args({"host": "h", "port": 1}, "srv", "tick")
    assert hb[hb.index("--profile") + 1] == "tick"

    condor = _condor_mcp_args(1, 1, "some_agent", profile="tick")
    assert condor[condor.index("--profile") + 1] == "tick"


def test_a_tick_session_mounts_the_narrow_surface_on_both_servers(monkeypatch):
    """End to end through the builder the engine actually calls."""
    import condor.runtime.toolsets as toolsets

    class _CM:
        def get_server(self, name):
            return {"host": "h", "port": 1, "username": "u", "password": "p"}

        def has_server_access(self, user_id, name):
            return True

        def get_accessible_servers(self, user_id):
            return ["local"]

    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: _CM(), raising=False
    )
    monkeypatch.setattr(
        "config_manager.get_effective_server", lambda *a, **k: "local", raising=False
    )

    servers = toolsets.build_mcp_servers_for_session(
        42, 42, agent_slug="adaptive_grid_trader", tick=True
    )
    by_name = {s["name"]: s["args"] for s in servers}
    for args in by_name.values():
        assert args[args.index("--profile") + 1] == "tick"
