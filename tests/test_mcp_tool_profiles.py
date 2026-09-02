"""What each seat mounts (FEAT-066).

Tool allowlists are only enforced for pydantic-ai model keys; an ACP bridge
(claude-code, gemini, copilot) runs unrestricted. For those seats the surface a
session MOUNTS is the whole permission model, so every profile's tool set is
pinned here as a golden list: a tool added to the wrong ring fails a test rather
than quietly widening the seat that trades with real capital.
"""

import asyncio
import re

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


@pytest.mark.parametrize("is_dry_run", [True, False])
@pytest.mark.parametrize("is_experiment", [True, False])
@pytest.mark.parametrize("is_controller_mode", [True, False])
def test_the_tick_preload_only_names_tools_a_tick_mounts(
    is_dry_run, is_experiment, is_controller_mode
):
    """The tick keeps its own preload list, and it stays hand-written on purpose
    — it is *narrower* than the ring (dry-run withholds the create/stop names so
    the agent does not spend a tick reaching for one), so it cannot be derived
    the way the chat seat's list now is (ARCH-292).

    What can be pinned is the direction: every name it preloads must be a tool
    the tick profile actually registers. It holds today; this is the guard that
    keeps it holding, since a tick preloading an unmounted name would burn the
    tick discovering that it cannot call it.
    """
    from condor.agents.prompts import _build_tool_preload

    line = _build_tool_preload(
        is_dry_run=is_dry_run,
        is_experiment=is_experiment,
        is_controller_mode=is_controller_mode,
    )
    names = re.search(r'select:([^"]+)"', line).group(1).split(",")
    mounted = {f"mcp__condor__{n}" for n in _registered(condor_server, "tick")} | {
        f"mcp__mcp-hummingbot__{n}" for n in _registered(hb_server, "tick")
    }

    assert names, "the tick preload named nothing at all"
    for name in names:
        assert name in mounted, f"the tick preloads {name!r}, which it cannot call"


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


# ── the admin ring is the server owner's (SEC-252) ───────────────────────────


def _session_profiles(monkeypatch, permission, **kwargs) -> dict[str, str]:
    """``{mcp server name: profile}`` for a chat seat on a server the caller
    holds at ``permission``."""
    import condor.runtime.toolsets as toolsets

    class _CM:
        def get_server(self, name):
            return {"host": "h", "port": 1, "username": "u", "password": "p"}

        def has_server_access(self, user_id, name, *a, **kw):
            return permission is not None

        def get_server_permission(self, user_id, name):
            return permission

        def get_accessible_servers(self, user_id):
            return ["local"] if permission is not None else []

    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: _CM(), raising=False
    )
    monkeypatch.setattr(
        "config_manager.get_effective_server", lambda *a, **k: "local", raising=False
    )
    servers = toolsets.build_mcp_servers_for_session(42, 42, **kwargs)
    return {s["name"]: s["args"][s["args"].index("--profile") + 1] for s in servers}


def test_a_shared_trader_chat_does_not_mount_the_admin_ring(monkeypatch):
    """The defect: the seat is `full` on attendance alone, so a user shared into
    someone else's server could stop their Gateway container from chat while the
    dashboard and Telegram both answer "Owner access required"."""
    from config_manager import ServerPermission

    profiles = _session_profiles(monkeypatch, ServerPermission.TRADER)
    assert profiles["mcp-hummingbot"] == "agent"
    assert profiles["condor"] == "agent"
    assert HB_ADMIN.isdisjoint(_registered(hb_server, profiles["mcp-hummingbot"]))


def test_a_shared_trader_keeps_every_tool_they_were_permitted(monkeypatch):
    """The downgrade lands on `agent`, not `tick`: a trader loses the operator
    surface and nothing else."""
    from config_manager import ServerPermission

    profiles = _session_profiles(monkeypatch, ServerPermission.TRADER)
    assert (
        _registered(hb_server, profiles["mcp-hummingbot"]) == HB_TRADING | HB_LIQUIDITY
    )
    assert _registered(condor_server, profiles["condor"]) == (
        CONDOR_COMMON | CONDOR_ORCHESTRATION
    )


def test_the_owner_chat_still_mounts_the_admin_ring(monkeypatch):
    """``get_server_permission`` answers OWNER for the owner *and* for an admin,
    the same free bypass ``require_owner`` grants — so both keep the ring."""
    from config_manager import ServerPermission

    profiles = _session_profiles(monkeypatch, ServerPermission.OWNER)
    assert profiles == {"mcp-hummingbot": "full", "condor": "full"}
    assert HB_ADMIN <= _registered(hb_server, "full")


def test_a_bound_specialist_and_a_tick_are_unaffected(monkeypatch):
    """They were never `full`; the gate only ever narrows that one seat."""
    from config_manager import ServerPermission

    for permission in (ServerPermission.OWNER, ServerPermission.TRADER):
        bound = _session_profiles(
            monkeypatch, permission, agent_slug="adaptive_grid_trader"
        )
        assert set(bound.values()) == {"agent"}
        tick = _session_profiles(
            monkeypatch, permission, agent_slug="adaptive_grid_trader", tick=True
        )
        assert set(tick.values()) == {"tick"}


def test_a_seat_with_no_server_never_reaches_the_admin_ring(monkeypatch):
    """No resolved server means no mcp-hummingbot at all, so no ring to gate;
    the condor seat is unchanged (`agent` and `full` register the same set)."""
    profiles = _session_profiles(monkeypatch, None)
    assert "mcp-hummingbot" not in profiles


# ── the identity doc names only tools that exist (READ-307) ──────────────────


def _agent_md_tool_names() -> set[str]:
    """Every tool ``agents/condor/AGENT.md`` presents as callable.

    The "## MCP Tools" section is a bullet per tool family, each written as
    ``- `name` / `name` — what it does``. Only the head of the bullet, before
    the em dash, names tools; the prose after it mentions actions
    (``manage_servers`` → ``list``), skills (``routine_cookbook``) and agent
    slugs (``executor_manager``) in backticks too, and none of those are tools.
    """
    from pathlib import Path

    text = (
        Path(__file__).resolve().parents[1] / "agents" / "condor" / "AGENT.md"
    ).read_text()
    section = text.split("## MCP Tools", 1)[1].split("\n## ", 1)[0]
    names: set[str] = set()
    for line in section.splitlines():
        if line.startswith("- "):
            names.update(re.findall(r"`([a-z_][a-z0-9_]*)`", line.split("—", 1)[0]))
    return names


def test_the_chat_identity_doc_names_only_tools_its_seat_mounts():
    """READ-307: a phantom tool in the identity prompt is a guaranteed failure.

    AGENT.md *is* the chat's system prompt, so a name listed here under "call
    directly" is a name the model will call. It advertised ``place_order`` — a
    tool no server registers and that ``condor.runtime.danger`` blocks by name —
    so every plain "buy me some SOL" spent a turn on an unknown tool. Nothing
    else pinned the list, so the prose drifted from the registry silently; this
    reconciles the two.
    """
    from condor.runtime.toolsets import seat_profile

    profile = seat_profile(CHAT_SLUG, tick=False)
    mounted = _registered(hb_server, profile) | _registered(condor_server, profile)

    advertised = _agent_md_tool_names()
    assert advertised, "the MCP Tools section parsed to nothing — did it move?"

    phantom = advertised - mounted
    assert not phantom, (
        f"agents/condor/AGENT.md advertises tool(s) no seat registers: "
        f"{sorted(phantom)}"
    )


def test_the_chat_identity_doc_does_not_resurrect_place_order():
    """The one name to keep out: the prompts, the gate and the risk engine all
    forbid it, so it may only appear as the negative it is worded as today."""
    from pathlib import Path

    text = (
        Path(__file__).resolve().parents[1] / "agents" / "condor" / "AGENT.md"
    ).read_text()
    for line in text.splitlines():
        if "place_order" in line:
            assert (
                "no `place_order`" in line
            ), f"AGENT.md presents place_order as usable: {line!r}"
