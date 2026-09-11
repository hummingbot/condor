"""The swap implementation must be REGISTERED as MCP tools, not just importable.

`tools/gateway_swap.py`, `schemas.GatewaySwapRequest`, `formatters.format_gateway_swap_result`,
the agent danger-gate entry (`DANGEROUS_TOOLS`) and the ACP preload list all referenced a swap
tool — but `server.py` imported the implementation and never wrapped it in `@mcp.tool()`.
Nothing on the wire exposed it, so agent playbooks routed swaps through
`manage_amm(quote_swap / execute_swap)` instead — actions that raise ToolError.

The one multiplexed tool is now four (FEAT-064): `quote_swap`, `execute_swap`,
`get_swap_status`, `search_swaps`. The free read and the capital-committing write no longer
share a name, so the danger gate reads a name instead of sniffing an `action` string.

Gateway folded pool-scoped AMM swaps into the unified swap route, so this is still the ONLY
swap surface: `quote_swap` carries the "name/type" connector form and the pool-resolution
caveat that makes "No pool found" readable as "unknown token", not "honeypot".

The repo has no async test setup, so coroutines are driven with asyncio.run().
"""

import asyncio
import inspect

import pytest

from mcp_servers.hummingbot_api.exceptions import ToolError
from mcp_servers.hummingbot_api.schemas import GatewaySwapRequest
from mcp_servers.hummingbot_api.tools.gateway_amm import manage_amm_impl


def test_the_swap_tools_are_registered_on_the_mcp_server():
    import mcp_servers.hummingbot_api.server as server

    for name in ("quote_swap", "execute_swap", "get_swap_status", "search_swaps"):
        assert hasattr(server, name), (
            f"the swap implementation exists but no @mcp.tool() exposes {name} — "
            "agents have no swap surface at all"
        )


def test_the_multiplexed_swap_tool_is_gone():
    """`execute` behind the same name as a free quote is what FEAT-064 removed."""
    import mcp_servers.hummingbot_api.server as server

    assert not hasattr(server, "manage_gateway_swaps")


def test_each_swap_tool_carries_only_its_own_parameters():
    """No `action`, and no `search_`-prefixed namespace inside one signature."""
    import mcp_servers.hummingbot_api.server as server

    expected = {
        "quote_swap": {
            "connector",
            "network",
            "trading_pair",
            "side",
            "amount",
            "slippage_pct",
            "extra_params",
        },
        "execute_swap": {
            "connector",
            "network",
            "trading_pair",
            "side",
            "amount",
            "slippage_pct",
            "wallet_address",
            "extra_params",
        },
        "get_swap_status": {"transaction_hash"},
        "search_swaps": {
            "connector",
            "network",
            "wallet_address",
            "trading_pair",
            "status",
            "start_time",
            "end_time",
            "limit",
            "offset",
        },
    }
    for name, params in expected.items():
        actual = set(inspect.signature(getattr(server, name)).parameters)
        assert actual == params, f"{name} takes {sorted(actual)}"
        assert not any(p.startswith("search_") for p in actual)
        assert "action" not in actual


def test_search_filters_reach_the_client():
    """The filters lost their `search_` prefix; they must still arrive.

    `GatewaySwapRequest` ignores unknown fields, so a filter passed under a name
    the model no longer declares vanishes without an error and `search_swaps`
    quietly returns unfiltered history.
    """
    import types
    from unittest.mock import patch

    import mcp_servers.hummingbot_api.server as server

    seen = {}

    class _Router:
        async def search_swaps(self, **kwargs):
            seen.update(kwargs)
            return {"data": []}

    client = types.SimpleNamespace(gateway_swap=_Router())

    async def _get_client():
        return client

    with patch.object(server.hummingbot_client, "get_client", _get_client):
        asyncio.run(
            server.search_swaps(
                connector="jupiter/router",
                network="solana-mainnet-beta",
                wallet_address="Wallet1111",
                trading_pair="SOL-USDC",
                status="CONFIRMED",
                limit=5,
            )
        )

    assert seen == {
        "connector": "jupiter/router",
        "network": "solana-mainnet-beta",
        "wallet_address": "Wallet1111",
        "trading_pair": "SOL-USDC",
        "status": "CONFIRMED",
        "limit": 5,
        "offset": 0,
    }


def test_the_tool_documents_the_name_slash_type_connector_form():
    """A bare 'meteora' is not routable — the unified route takes 'meteora/amm'."""
    import mcp_servers.hummingbot_api.server as server

    doc = server.quote_swap.__doc__
    assert "meteora/amm" in doc
    assert "jupiter/router" in doc


def test_the_tool_warns_that_an_unresolvable_pool_is_not_a_bad_token():
    """Gateway matches its configured pool list by SYMBOL; a fresh mint is simply unknown."""
    import mcp_servers.hummingbot_api.server as server

    doc = server.quote_swap.__doc__
    assert "No pool found" in doc
    assert "honeypot" in doc


def test_execute_still_routes_strategy_swaps_to_the_order_executor():
    """The reason strategy swaps get PnL attribution has to survive the split."""
    import mcp_servers.hummingbot_api.server as server

    doc = " ".join(server.execute_swap.__doc__.split())
    assert "order_executor" in doc
    assert "quote_swap" in doc


def test_omitting_slippage_is_documented_as_the_connectors_own_setting():
    import mcp_servers.hummingbot_api.server as server

    doc = " ".join(server.quote_swap.__doc__.split())
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
