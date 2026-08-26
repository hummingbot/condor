"""The condor MCP surface — what is registered, and what is not.

FEAT-067 dropped four leftovers: ``manage_notes`` (a declared-dead alias over
``manage_memory``), ``get_user_context`` (folded into ``manage_servers``), the
``list_routines``/``run_routine`` actions on the agent tool (strict duplicates
of ``manage_routines``), and the ``strategy_id`` alias. FEAT-068 then split the
``manage_trading_agent`` funnel into ``manage_agents`` / ``manage_strategies`` /
``control_agent``. Every docstring costs context on every session, so the
surface is pinned here.
"""

import asyncio

import pytest

from mcp_servers.condor import server
from mcp_servers.condor.settings import settings
from mcp_servers.condor.tools import servers as servers_tool

EXPECTED_TOOLS = {
    "consult",
    "control_agent",
    "delegate",
    "get_available_models",
    "manage_agents",
    "manage_memory",
    "manage_routines",
    "manage_servers",
    "manage_skill",
    "manage_strategies",
    "run_code",
    "send_notification",
    "trading_agent_journal_read",
    "trading_agent_journal_write",
}


def _registered() -> set[str]:
    return {tool.name for tool in asyncio.run(server.mcp.list_tools())}


def test_the_server_registers_exactly_the_expected_tools():
    assert _registered() == EXPECTED_TOOLS


def test_the_dropped_tools_are_not_registered():
    """Unknown-tool at the host beats a live deprecated docstring in context."""
    assert (
        not {
            "manage_notes",
            "get_user_context",
            "manage_trading_agent",
        }
        & _registered()
    )


class _FakeRole:
    value = "admin"


class _FakeConfigManager:
    def get_accessible_servers(self, user_id):
        return ["local"]

    def get_server(self, name):
        return {"host": "127.0.0.1", "port": 8000}

    def get_server_permission(self, user_id, name):
        return _FakeRole()

    def get_chat_default_server(self, chat_id):
        return "local"

    def get_user_role(self, user_id):
        return _FakeRole()

    def is_admin(self, user_id):
        return True


@pytest.fixture
def fake_cm(monkeypatch):
    import config_manager

    monkeypatch.setattr(config_manager, "get_config_manager", _FakeConfigManager)
    monkeypatch.setattr(settings, "user_id", 1)
    monkeypatch.setattr(settings, "chat_id", 1)


def test_manage_servers_list_answers_who_and_where(fake_cm):
    """The context fields get_user_context used to carry now ride on `list`."""
    result = asyncio.run(servers_tool.manage_servers("list", None))

    assert result["active_server"] == "local"
    assert result["user_role"] == "admin"
    assert result["is_admin"] is True
    # The LLM identity half — a coordinator must never invent an agent_key.
    assert "active_agent_key" in result
    assert "custom_llm_endpoints" in result


def test_the_duplicate_routine_actions_are_gone():
    """manage_routines(agent=...) is the one way in; the alias no longer answers."""
    from mcp_servers.condor.tools import trading_agent

    for action in ("list_routines", "run_routine"):
        result = asyncio.run(trading_agent.control_agent(action))
        assert "Unknown action" in result.get("error", "")


def test_strategy_id_is_gone_from_the_routine_and_skill_signatures():
    import inspect

    for fn in (server.manage_routines, server.manage_skill):
        assert "strategy_id" not in inspect.signature(fn).parameters


# ── FEAT-068: the three families, and the routing help between them ──


@pytest.mark.parametrize(
    "action",
    ["list", "create", "get", "update", "delete"],
)
def test_every_agent_action_reaches_manage_agents(action):
    """Each family action answers on its own tool — no "unknown action"."""
    from mcp_servers.condor.tools import trading_agent

    result = trading_agent.manage_agents(action)
    assert "Unknown action" not in result.get("error", "")


@pytest.mark.parametrize("action", ["list", "get", "create", "update", "delete"])
def test_every_strategy_action_reaches_manage_strategies(action):
    from mcp_servers.condor.tools import trading_agent

    result = trading_agent.manage_strategies(action)
    assert "Unknown action" not in result.get("error", "")


@pytest.mark.parametrize(
    "action",
    ["stop", "pause", "resume", "shutdown", "get_state", "set_state", "start"],
)
def test_every_control_action_reaches_control_agent(action):
    """Called with no ids, each one asks for its id rather than rejecting the verb."""
    from mcp_servers.condor.tools import trading_agent

    result = asyncio.run(trading_agent.control_agent(action))
    assert "is required" in result.get("error", "")


@pytest.mark.parametrize(
    "action,sibling",
    [
        ("start_agent", "control_agent"),
        ("list_agents", "control_agent"),
        ("create_strategy", "manage_strategies"),
        ("agent_tracker", "trading_agent_journal_read"),
    ],
)
def test_a_misrouted_action_names_the_sibling_tool(action, sibling):
    """A funnel-era action gets the call that works, not a bare error."""
    from mcp_servers.condor.tools import trading_agent

    result = trading_agent.manage_agents(action)
    assert sibling in result["error"]


def test_the_legacy_prefixed_names_still_resolve_in_their_own_family():
    """Prompts in the wild say "create_agent"; the family that owns it answers."""
    from mcp_servers.condor.tools import trading_agent

    result = trading_agent.manage_agents("get_agent")
    assert "belongs to" not in result.get("error", "")
    assert "Unknown action" not in result.get("error", "")


def test_journal_read_tracker_section_replaces_agent_tracker(monkeypatch):
    """agent_tracker's payload now rides on the read tool as a section."""
    from mcp_servers.condor.tools import trading_agent

    class _JM:
        def read_full(self):
            return "# tracker"

        def get_summary_dict(self):
            return {"ticks": 3}

    monkeypatch.setattr(
        trading_agent, "_resolve_journal_manager", lambda agent_id: (_JM(), None)
    )
    result = trading_agent.journal_read("some_agent", section="tracker")
    assert result == {"tracker_md": "# tracker", "summary": {"ticks": 3}}


def test_set_state_carries_its_payload_outside_config(monkeypatch):
    """`config` means start-overrides only; the state value has its own param."""
    from mcp_servers.condor.tools import trading_agent

    calls = []

    async def _fake_call(method, path, body=None):
        calls.append((method, path, body))
        return {"ok": True}

    monkeypatch.setattr(trading_agent, "call_main_api", _fake_call)
    asyncio.run(
        trading_agent.control_agent(
            "set_state", agent_id="mm.grid_abc123", key="cursor", value=42
        )
    )

    method, path, body = calls[0]
    assert (method, path) == ("POST", "/agents/mm/strategies/grid_abc123/state")
    assert body == {"key": "cursor", "value": 42, "expires_in": None, "clear": False}
