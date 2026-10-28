"""A failing tool still tells the model why (FEAT-130).

MCP SDK 2.x reports any exception that is not its own ``ToolError`` as a bare
``Error executing tool <name>``. Agents read that text and act on it — retry
with another connector, a smaller size, a different pool — so the message is
part of the tool's contract. ``register_tools`` restores it for every exception
type at the one seam both servers register through.
"""

import asyncio

import mcp
import pytest
from mcp.server.mcpserver import MCPServer

from mcp_servers import _profiles
from mcp_servers.condor import exceptions as condor_exceptions
from mcp_servers.condor import server as condor_server
from mcp_servers.hummingbot_api import exceptions as hb_exceptions
from mcp_servers.hummingbot_api import server as hb_server

SERVERS = [
    pytest.param(condor_server, condor_exceptions.ToolError, id="condor"),
    pytest.param(hb_server, hb_exceptions.ToolError, id="hummingbot_api"),
]


def _call(server: MCPServer, name: str, arguments: dict | None = None):
    async def run():
        async with mcp.Client(server) as client:
            return await client.call_tool(name, arguments or {})

    return asyncio.run(run())


def _text(result) -> str:
    return "".join(block.text for block in result.content)


@pytest.mark.parametrize("module,own_tool_error", SERVERS)
@pytest.mark.parametrize("kind", ["own", "value", "bare"])
def test_the_message_reaches_the_client(module, own_tool_error, kind, monkeypatch):
    """Through the server's own ``register_tools``, with one tool made to fail."""
    raised = {"own": own_tool_error, "value": ValueError, "bare": Exception}[kind]
    victim = module.TOOL_PROFILES["full"][0]

    async def failing():
        raise raised("unknown connector 'binanse'")

    failing.__name__ = victim.__name__
    monkeypatch.setitem(
        module.TOOL_PROFILES,
        "full",
        (failing, *module.TOOL_PROFILES["full"][1:]),
    )
    server = MCPServer("error-probe")
    module.register_tools(server, "full")

    result = _call(server, victim.__name__)

    assert result.is_error
    assert _text(result) == (
        f"Error executing tool {victim.__name__}: unknown connector 'binanse'"
    )


def test_a_bad_argument_still_says_which_one():
    """Argument validation happens before the wrapper and keeps its own text."""

    async def sized(amount: int) -> str:
        return str(amount)

    server = MCPServer("error-probe")
    _profiles.register_tools(server, {"full": (sized,)}, "full")

    result = _call(server, "sized", {"amount": "lots"})

    assert result.is_error
    assert "amount" in _text(result)


def test_a_result_is_untouched():
    async def fine() -> str:
        return "pong"

    server = MCPServer("error-probe")
    _profiles.register_tools(server, {"full": (fine,)}, "full")

    result = _call(server, "fine")

    assert not result.is_error
    assert _text(result) == "pong"


@pytest.mark.parametrize("muted", [(), ("blocking",)])
def test_a_sync_tool_is_refused_at_registration(muted):
    """It would run on a worker thread with no loop — fail at startup instead."""

    def blocking() -> str:
        return "pong"

    with pytest.raises(TypeError, match="blocking"):
        _profiles.register_tools(
            MCPServer("error-probe"), {"full": (blocking,)}, "full", muted
        )
