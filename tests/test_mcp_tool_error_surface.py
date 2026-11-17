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


# ---------------------------------------------------------------------------
# Error text a model can act on, with no backend address in it (MH-06)
# ---------------------------------------------------------------------------

import types  # noqa: E402

_BACKEND = "http://10.0.0.7:8000/executors/search"


def _response_error(status=429, message="Too Many Requests"):
    import aiohttp
    from multidict import CIMultiDict
    from yarl import URL

    return aiohttp.ClientResponseError(
        request_info=aiohttp.RequestInfo(
            url=URL(_BACKEND),
            method="POST",
            headers=CIMultiDict(),
            real_url=URL(_BACKEND),
        ),
        history=(),
        status=status,
        message=message,
    )


def test_a_failing_executor_read_does_not_leak_the_backend_address():
    """``str(exc)`` of the client's error carries ``url='http://10.0.0.7:8000/…'``."""
    from mcp_servers.hummingbot_api.tools import executors as executors_tools

    async def _search(**_kwargs):
        raise _response_error()

    client = types.SimpleNamespace(
        executors=types.SimpleNamespace(search_executors=_search)
    )

    result = asyncio.run(executors_tools.list_executors(client))

    for text in (result["formatted_output"], result["error"]):
        assert "10.0.0.7" not in text
        assert "http://" not in text
    assert result["formatted_output"] == (
        "Error listing executors: HTTP 429: Too Many Requests"
    )


def test_the_tool_wrapper_does_not_leak_the_backend_address_either():
    import aiohttp

    from mcp_servers.hummingbot_api.exceptions import ToolError
    from mcp_servers.hummingbot_api.middleware import describe_error, handle_errors

    @handle_errors("get prices")
    async def get_prices():
        raise _response_error(500, f"upstream {_BACKEND} exploded")

    with pytest.raises(ToolError) as caught:
        asyncio.run(get_prices())

    assert "10.0.0.7" not in str(caught.value)
    assert "HTTP 500" in str(caught.value)
    assert "/executors/search" in str(caught.value), "the route is safe and useful"

    unreachable = describe_error(
        aiohttp.ClientConnectorError(
            types.SimpleNamespace(host="10.0.0.7", port=8000, ssl=None),
            OSError(61, "Connect call failed ('10.0.0.7', 8000)"),
        )
    )
    assert "10.0.0.7" not in unreachable
    assert "unreachable" in unreachable


@pytest.mark.parametrize(
    ("amount", "says"),
    [
        ("$100", "currency symbol"),
        ("1,000", "'1000'"),
        ("abc", "plain decimal number"),
        ("NaN", "plain decimal number"),
    ],
)
def test_a_swap_amount_that_is_not_a_number_is_refused_by_name(amount, says):
    """It used to read ``[<class 'decimal.ConversionSyntax'>]`` plus a pointer at
    Gateway's logs, for a value that never left this process."""
    from mcp_servers.hummingbot_api.exceptions import ToolError
    from mcp_servers.hummingbot_api.schemas import GatewaySwapRequest
    from mcp_servers.hummingbot_api.tools.gateway_swap import manage_gateway_swaps

    sent = []

    class _Router:
        async def get_swap_quote(self, **kwargs):
            sent.append(kwargs)
            return {}

        async def execute_swap(self, **kwargs):
            sent.append(kwargs)
            return {}

    client = types.SimpleNamespace(gateway_swap=_Router())

    for action in ("quote", "execute"):
        request = GatewaySwapRequest(
            action=action,
            connector="jupiter/router",
            network="solana-mainnet-beta",
            trading_pair="SOL-USDC",
            side="BUY",
            amount=amount,
        )
        with pytest.raises(ToolError) as caught:
            asyncio.run(manage_gateway_swaps(client, request))

        message = str(caught.value)
        assert message.startswith("amount must be a plain decimal number")
        assert repr(amount) in message
        assert says in message
        assert "ConversionSyntax" not in message
    assert sent == [], "nothing may be quoted or signed"


def test_a_bad_slippage_names_slippage_not_amount():
    from mcp_servers.hummingbot_api.exceptions import ToolError
    from mcp_servers.hummingbot_api.schemas import CLMMRequest, GatewaySwapRequest
    from mcp_servers.hummingbot_api.tools.gateway_clmm import manage_clmm_impl
    from mcp_servers.hummingbot_api.tools.gateway_swap import manage_gateway_swaps

    swap = GatewaySwapRequest(
        action="quote",
        connector="jupiter/router",
        network="solana-mainnet-beta",
        trading_pair="SOL-USDC",
        side="SELL",
        amount="0.5",
        slippage_pct="1%",
    )

    class _Quotes:
        async def get_swap_quote(self, **_kwargs):
            raise AssertionError("must not be reached")

    with pytest.raises(ToolError, match="slippage_pct must be a plain decimal"):
        asyncio.run(
            manage_gateway_swaps(types.SimpleNamespace(gateway_swap=_Quotes()), swap)
        )

    # The optional CLMM parses were unguarded: a bad base amount raised a bare
    # decimal.InvalidOperation.
    clmm = CLMMRequest(
        action="open",
        connector="orca",
        network="solana-mainnet-beta",
        pool_address="Pool1",
        lower_price="130",
        upper_price="170",
        base_token_amount="1,000",
    )

    class _Clmm:
        async def open_position(self, **_kwargs):
            raise AssertionError("must not be reached")

    with pytest.raises(ToolError, match="base_token_amount must be a plain decimal"):
        asyncio.run(manage_clmm_impl(types.SimpleNamespace(gateway_clmm=_Clmm()), clmm))
