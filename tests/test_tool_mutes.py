"""FEAT-091: a muted tool is never mounted.

The Tools tab stops being a read-only echo of the AGENT.md allowlist — which
only binds pydantic-ai seats, and is decoration on an ACP bridge — and becomes
the real mounted surface with a switch per row. Switching one off means the next
session's MCP subprocess never registers it, so the model is never told it
exists, on every backend alike.

Four things are asserted here, in the order the feature builds them:

1. the name tables — every name in every profile resolves to a function in its
   server, and the registered set per profile is *unchanged* from before the
   tables moved out of ``server.py``;
2. the subtraction — ``register_tools`` drops exactly the muted name off a bare
   ``FastMCP`` and leaves every other tool alone;
3. the spawn — ``seat_tools`` describes the seat, no ``--mute-tools`` reaches
   argv when nothing is muted (byte-identical to before this feature), and the
   exact csv reaches both subprocesses when something is;
4. the route — the panel writes a tool mute, the brain reads it back, and a name
   no seat mounts is refused rather than accumulating in the file.
"""

import asyncio

import pytest
from mcp.server.fastmcp import FastMCP

from condor.memory.mutes import load_mutes, set_muted
from condor.runtime import toolsets
from condor.runtime.toolsets import (
    _condor_mcp_args,
    _hummingbot_mcp_args,
    seat_tools,
)
from mcp_servers.condor import profiles as condor_profiles
from mcp_servers.condor import server as condor_server
from mcp_servers.hummingbot_api import profiles as hummingbot_profiles
from mcp_servers.hummingbot_api import server as hummingbot_server

MODULES = [
    pytest.param(condor_server, condor_profiles, id="condor"),
    pytest.param(hummingbot_server, hummingbot_profiles, id="hummingbot"),
]


def _registered(module, profile: str = "full", muted=()) -> set[str]:
    """The tool names ``profile`` puts on a fresh server, minus ``muted``."""
    server = FastMCP("mute-probe")
    module.register_tools(server, profile, muted)
    return {tool.name for tool in asyncio.run(server.list_tools())}


# ── 1. the name tables cannot drift from the functions ──


@pytest.mark.parametrize("module,profiles", MODULES)
def test_every_name_in_every_profile_resolves_to_a_function(module, profiles):
    """The whole reason ``server.py`` resolves the table at import."""
    for profile, names in profiles.PROFILE_TOOLS.items():
        for name in names:
            assert callable(
                getattr(module, name, None)
            ), f"{profile!r} names {name!r}, which {module.__name__} does not define"


@pytest.mark.parametrize("module,profiles", MODULES)
def test_a_name_with_no_function_behind_it_fails_loudly(module, profiles):
    with pytest.raises(RuntimeError, match="does not define"):
        module._resolve("a_tool_nobody_ever_wrote")


@pytest.mark.parametrize("module,profiles", MODULES)
def test_the_registered_set_is_exactly_the_name_table(module, profiles):
    """Step 1 is behaviour-preserving: the mount is what the table says, and the
    golden lists in ``test_mcp_tool_profiles`` pin the table itself."""
    for profile, names in profiles.PROFILE_TOOLS.items():
        assert _registered(module, profile) == set(names)


@pytest.mark.parametrize("module,profiles", MODULES)
def test_every_mounted_tool_has_a_line_for_the_panel(module, profiles):
    """A switch with no description is a row the operator cannot read."""
    for name in profiles.PROFILE_TOOLS["full"]:
        assert profiles.TOOL_DESCRIPTIONS.get(name), f"no description for {name!r}"


def test_the_leaf_modules_do_not_wake_a_server():
    """The web process imports these to draw the switches. Importing a
    ``server.py`` parses argv and builds a ``FastMCP`` singleton — neither of
    which a web request has any business doing — so the tables have to be
    reachable without it."""
    import subprocess
    import sys
    from pathlib import Path

    probe = (
        "import sys\n"
        "import mcp_servers.condor.profiles\n"
        "import mcp_servers.hummingbot_api.profiles\n"
        "assert 'mcp_servers.condor.server' not in sys.modules\n"
        "assert 'mcp_servers.hummingbot_api.server' not in sys.modules\n"
        "assert not [m for m in sys.modules if m.startswith('mcp.')]\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr


def test_the_shared_profile_helpers_are_a_leaf_too():
    """ARCH-289 moved the mechanics both servers share into
    ``mcp_servers/_profiles.py``. It annotates ``FastMCP`` but must not import
    it, and must never reach for a ``server`` module: anything it drags in, both
    servers drag in at import, and the leafness of the name tables above is only
    worth as much as the module they are resolved by."""
    import subprocess
    import sys
    from pathlib import Path

    probe = (
        "import sys\n"
        "import mcp_servers._profiles\n"
        "assert 'mcp_servers.condor.server' not in sys.modules\n"
        "assert 'mcp_servers.hummingbot_api.server' not in sys.modules\n"
        "assert not [m for m in sys.modules if m.startswith('mcp.')]\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr


# ── 2. the subtraction ──


@pytest.mark.parametrize("module,profiles", MODULES)
def test_muting_removes_exactly_that_tool(module, profiles):
    everything = _registered(module, "full")
    victim = profiles.PROFILE_TOOLS["full"][0]

    left = _registered(module, "full", [victim])
    assert victim not in left
    assert left == everything - {victim}


@pytest.mark.parametrize("module,profiles", MODULES)
def test_muting_nothing_registers_everything(module, profiles):
    assert _registered(module, "full", []) == _registered(module, "full")
    assert _registered(module, "full", ()) == _registered(module, "full")


@pytest.mark.parametrize("module,profiles", MODULES)
def test_a_mute_for_a_tool_this_seat_never_mounts_is_a_no_op(module, profiles):
    """Seats mount different rings, so "off here, never mounted there" is an
    ordinary difference between seats and not an error."""
    tick = _registered(module, "tick")
    assert _registered(module, "tick", ["explore_geckoterminal_but_wrong"]) == tick
    # …including a real name that belongs to the *other* server, since both are
    # handed the same list.
    assert _registered(condor_server, "tick", ["get_prices"]) == _registered(
        condor_server, "tick"
    )


@pytest.mark.parametrize("module,profiles", MODULES)
def test_muting_still_refuses_an_unknown_profile(module, profiles):
    with pytest.raises(ValueError, match="Unknown tool profile"):
        module.register_tools(FastMCP("probe"), "trading", ["consult"])


def test_muting_cannot_widen_a_seat():
    """``mute ⊆ profile``, always — it only ever subtracts."""
    tick = _registered(condor_server, "tick")
    assert _registered(condor_server, "tick", ["manage_agents"]) <= tick


# ── 3. what the seat mounts, and what reaches argv ──


def test_seat_tools_describes_both_servers(tmp_path):
    rows = seat_tools("perps")
    by_name = {row["name"]: row for row in rows}

    assert by_name["consult"]["server"] == "condor"
    assert by_name["get_prices"]["server"] == "hummingbot"
    assert by_name["consult"]["description"]
    assert all(row["muted"] is False for row in rows)
    # An attended specialist is the ``agent`` ring on both servers.
    assert set(by_name) == set(condor_profiles.PROFILE_TOOLS["agent"]) | set(
        hummingbot_profiles.PROFILE_TOOLS["agent"]
    )


def test_seat_tools_narrows_for_a_tick(tmp_path):
    tick = {row["name"] for row in seat_tools("perps", tick=True)}
    assert "manage_agents" not in tick and "manage_clmm" not in tick
    assert "get_prices" in tick and "consult" in tick


def test_seat_tools_marks_what_the_operator_switched_off(tmp_path):
    set_muted("perps", "tool", "manage_clmm", True)
    rows = {row["name"]: row for row in seat_tools("perps")}
    assert rows["manage_clmm"]["muted"] is True
    assert rows["get_prices"]["muted"] is False
    # Rendered, not filtered — a switch you cannot see is one you cannot undo.
    assert "manage_clmm" in rows


def test_nothing_muted_means_no_flag_on_the_line():
    """The acceptance criterion: an untouched install spawns the argv it always
    did, so the flag can never be blamed for a session behaving differently."""
    condor = _condor_mcp_args(1, 1, "perps", profile="agent")
    hb = _hummingbot_mcp_args({"host": "h", "port": 1}, "srv", "agent")
    assert "--mute-tools" not in condor
    assert "--mute-tools" not in hb
    assert condor == _condor_mcp_args(1, 1, "perps", profile="agent", muted_tools=())
    assert hb == _hummingbot_mcp_args({"host": "h", "port": 1}, "srv", "agent", ())


def test_the_flag_carries_exactly_the_muted_names():
    muted = ["manage_clmm", "run_code"]
    condor = _condor_mcp_args(1, 1, "perps", profile="agent", muted_tools=muted)
    hb = _hummingbot_mcp_args({"host": "h", "port": 1}, "srv", "agent", muted)

    for args in (condor, hb):
        assert args[args.index("--mute-tools") + 1] == "manage_clmm,run_code"


def _session_args(monkeypatch, **kwargs) -> dict[str, list[str]]:
    """``{mcp server name: argv}`` for a session the spawner actually builds."""

    class _CM:
        def get_server(self, name):
            return {"host": "h", "port": 1, "username": "u", "password": "p"}

        def has_server_access(self, user_id, name, *a, **kw):
            return True

        def get_accessible_servers(self, user_id):
            return ["local"]

    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: _CM(), raising=False
    )
    monkeypatch.setattr(
        "config_manager.get_effective_server", lambda *a, **k: "local", raising=False
    )
    servers = toolsets.build_mcp_servers_for_session(42, 42, **kwargs)
    return {s["name"]: s["args"] for s in servers}


def test_an_uncurated_agent_spawns_the_argv_it_always_did(monkeypatch):
    args = _session_args(monkeypatch, agent_slug="perps")
    assert args  # both servers resolved
    for argv in args.values():
        assert "--mute-tools" not in argv


def test_both_subprocesses_are_told_the_mute(monkeypatch):
    set_muted("perps", "tool", "manage_clmm", True)
    set_muted("perps", "tool", "run_code", True)

    args = _session_args(monkeypatch, agent_slug="perps")
    assert set(args) == {"condor", "mcp-hummingbot"}
    for argv in args.values():
        # Sorted, so the spawn line is stable between restarts.
        assert argv[argv.index("--mute-tools") + 1] == "manage_clmm,run_code"


def test_end_to_end_the_muted_tool_is_not_registered(monkeypatch):
    """The acceptance criterion, through the builder the engine actually calls:
    the names on the line are the ones the subprocess subtracts at import."""
    set_muted("perps", "tool", "manage_clmm", True)
    args = _session_args(monkeypatch, agent_slug="perps")

    argv = args["mcp-hummingbot"]
    profile = argv[argv.index("--profile") + 1]
    muted = argv[argv.index("--mute-tools") + 1].split(",")

    mounted = _registered(hummingbot_server, profile, muted)
    assert "manage_clmm" not in mounted
    assert "manage_amm" in mounted  # its ring-mate is untouched


def test_a_mute_belongs_to_one_agent(tmp_path):
    set_muted("perps", "tool", "manage_clmm", True)
    assert load_mutes("spot")["tools"] == set()
    assert {r["name"] for r in seat_tools("spot") if r["muted"]} == set()


# ── 4. through the route ──


@pytest.fixture
def web_env(tmp_path, monkeypatch):
    """One real Agent on disk, reachable through the agents router."""
    from condor.agents import agent as agent_module
    from condor.agents.agent import AgentStore

    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    AgentStore().create(name="Brigado", description="BRL market making")
    return tmp_path


def _client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from condor.web.auth import get_current_user
    from condor.web.models import WebUser
    from condor.web.routes import agents as routes

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(
        id=555, username="u", first_name="U", role="user"
    )
    return TestClient(app)


def test_the_brain_lists_the_real_mounted_surface(web_env):
    tools = _client().get("/agents/brigado/brain").json()["tools"]
    by_name = {t["name"]: t for t in tools}

    assert set(by_name) == set(condor_profiles.PROFILE_TOOLS["agent"]) | set(
        hummingbot_profiles.PROFILE_TOOLS["agent"]
    )
    assert by_name["get_prices"]["server"] == "hummingbot"
    assert by_name["get_prices"]["description"]
    assert all(t["muted"] is False for t in tools)
    # This Agent's AGENT.md names no allowlist, so nothing is allowlisted and the
    # panel still says the allowlist is empty — two different statements.
    assert all(t["allowlisted"] is False for t in tools)


def test_the_allowlist_stays_visible_beside_the_mute(web_env):
    from condor.agents.agent import AgentStore

    store = AgentStore()
    agent = store.get("brigado")
    agent.tools = ["get_prices"]
    store.update(agent)

    brain = _client().get("/agents/brigado/brain").json()
    by_name = {t["name"]: t for t in brain["tools"]}
    assert by_name["get_prices"]["allowlisted"] is True
    assert by_name["get_portfolio_overview"]["allowlisted"] is False
    assert brain["tools_unrestricted"] is False
    # …and it is still the whole mounted surface, not the allowlist.
    assert len(brain["tools"]) > 1


def test_the_panel_mutes_a_tool_and_still_shows_it(web_env):
    client = _client()

    put = client.put(
        "/agents/brigado/mutes",
        json={"kind": "tool", "name": "manage_clmm", "muted": True},
    )
    assert put.status_code == 200, put.text

    by_name = {
        t["name"]: t for t in client.get("/agents/brigado/brain").json()["tools"]
    }
    assert by_name["manage_clmm"]["muted"] is True
    assert by_name["manage_amm"]["muted"] is False

    client.put(
        "/agents/brigado/mutes",
        json={"kind": "tool", "name": "manage_clmm", "muted": False},
    )
    tools = client.get("/agents/brigado/brain").json()["tools"]
    assert all(t["muted"] is False for t in tools)


def test_the_route_refuses_a_tool_this_seat_never_mounts(web_env):
    bad = _client().put(
        "/agents/brigado/mutes",
        json={"kind": "tool", "name": "not_a_tool", "muted": True},
    )
    assert bad.status_code == 400
    assert load_mutes("brigado")["tools"] == set()
