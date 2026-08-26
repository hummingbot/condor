"""The condor MCP surface after FEAT-067 — what is registered, and what is not.

Four leftovers were dropped: ``manage_notes`` (a declared-dead alias over
``manage_memory``), ``get_user_context`` (folded into ``manage_servers``), the
``list_routines``/``run_routine`` actions on ``manage_trading_agent`` (strict
duplicates of ``manage_routines``), and the ``strategy_id`` alias. Every
docstring costs context on every session, so the surface is pinned here.
"""

import asyncio

import pytest

from mcp_servers.condor import server
from mcp_servers.condor.settings import settings
from mcp_servers.condor.tools import servers as servers_tool

EXPECTED_TOOLS = {
    "consult",
    "delegate",
    "get_available_models",
    "manage_memory",
    "manage_routines",
    "manage_servers",
    "manage_skill",
    "manage_trading_agent",
    "run_code",
    "send_notification",
    "trading_agent_journal_read",
    "trading_agent_journal_write",
}


def _registered() -> set[str]:
    return {tool.name for tool in asyncio.run(server.mcp.list_tools())}


def test_the_server_registers_exactly_the_twelve_tools():
    assert _registered() == EXPECTED_TOOLS


def test_the_dropped_tools_are_not_registered():
    """Unknown-tool at the host beats a live deprecated docstring in context."""
    assert not {"manage_notes", "get_user_context"} & _registered()


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
        result = asyncio.run(
            trading_agent.manage_trading_agent(action, strategy_id="some_agent")
        )
        assert "Unknown action" in result.get("error", "")


def test_strategy_id_is_gone_from_the_routine_and_skill_signatures():
    import inspect

    for fn in (server.manage_routines, server.manage_skill):
        assert "strategy_id" not in inspect.signature(fn).parameters
