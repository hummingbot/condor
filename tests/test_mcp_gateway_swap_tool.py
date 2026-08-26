"""The swap implementation must be REGISTERED as an MCP tool, not just importable.

`tools/gateway_swap.py`, `schemas.GatewaySwapRequest`, `formatters.format_gateway_swap_result`,
the agent danger-gate entry (`DANGEROUS_TOOLS`) and the ACP preload list all referenced
`manage_gateway_swaps` — but `server.py` imported the implementation and never wrapped it in
`@mcp.tool()`. Nothing on the wire exposed it, so agent playbooks routed swaps through
`manage_amm(quote_swap / execute_swap)` instead — actions that raise ToolError.

Gateway folded pool-scoped AMM swaps into the unified swap route, so this is now the ONLY swap
surface: its docstring has to carry the "name/type" connector form and the pool-resolution
caveat that makes "No pool found" readable as "unknown token", not "honeypot".

The repo has no async test setup, so coroutines are driven with asyncio.run().
"""

import asyncio
import inspect

import pytest

from mcp_servers.hummingbot_api.exceptions import ToolError
from mcp_servers.hummingbot_api.schemas import GatewaySwapRequest
from mcp_servers.hummingbot_api.tools.gateway_amm import manage_amm_impl


def test_the_swap_tool_is_registered_on_the_mcp_server():
    import mcp_servers.hummingbot_api.server as server

    assert hasattr(server, "manage_gateway_swaps"), (
        "the swap implementation exists but no @mcp.tool() exposes it — "
        "agents have no swap surface at all"
    )


def test_the_registered_tool_covers_every_action_the_implementation_supports():
    import mcp_servers.hummingbot_api.server as server

    params = inspect.signature(server.manage_gateway_swaps).parameters
    action = params["action"].annotation
    assert set(action.__args__) == {"quote", "execute", "search", "get_status"}


def test_the_tool_documents_the_name_slash_type_connector_form():
    """A bare 'meteora' is not routable — the unified route takes 'meteora/amm'."""
    import mcp_servers.hummingbot_api.server as server

    doc = server.manage_gateway_swaps.__doc__
    assert "meteora/amm" in doc
    assert "jupiter/router" in doc


def test_the_tool_warns_that_an_unresolvable_pool_is_not_a_bad_token():
    """Gateway matches its configured pool list by SYMBOL; a fresh mint is simply unknown."""
    import mcp_servers.hummingbot_api.server as server

    doc = server.manage_gateway_swaps.__doc__
    assert "No pool found" in doc
    assert "honeypot" in doc


def test_omitting_slippage_is_documented_as_the_connectors_own_setting():
    import mcp_servers.hummingbot_api.server as server

    doc = " ".join(server.manage_gateway_swaps.__doc__.split())
    assert "OMIT to use the connector's configured slippage" in doc
    assert "'0' is a real value" in doc


def test_manage_amm_has_no_swap_actions_left():
    """Gateway deleted /trading/amm/{quote,execute}-swap; manage_amm is LP-only now."""
    from mcp_servers.hummingbot_api.schemas import AMMRequest

    with pytest.raises(Exception):
        AMMRequest(action="quote_swap")

    request = AMMRequest.model_construct(
        action="execute_swap", connector="meteora", network="solana-mainnet-beta"
    )
    with pytest.raises(ToolError, match="[Uu]nknown action"):
        asyncio.run(manage_amm_impl(None, request))


def test_an_unset_slippage_stays_unset_on_the_request_model():
    """None is the wire signal for 'omit slippage_pct'; '0' is a real value."""
    assert GatewaySwapRequest(action="quote").slippage_pct is None
    assert GatewaySwapRequest(action="quote", slippage_pct="0").slippage_pct == "0"
