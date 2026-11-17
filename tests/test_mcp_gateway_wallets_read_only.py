"""No private key is a parameter of any MCP tool (FEAT-065).

`manage_gateway_config` used to take a `private_key` for `wallets` + `add`. A key typed
into a chat is persisted by the transport, by the bot's own state and by every transcript
the session writes; a confirmation gate stops the action, not the exposure, so the
parameter cannot exist at all. Exchange API keys were already kept off this surface for
the same reason — wallets now follow the identical rule: list over MCP, add and delete in
the Condor dashboard.

The repo has no async test setup, so coroutines are driven with asyncio.run().
"""

import asyncio
import inspect
from pathlib import Path

import pytest

from mcp_servers.hummingbot_api.exceptions import ToolError
from mcp_servers.hummingbot_api.schemas import GatewayConfigRequest
from mcp_servers.hummingbot_api.tools.gateway import manage_gateway_config

MCP_ROOT = Path(__file__).resolve().parent.parent / "mcp_servers"


class _FakeAccounts:
    """The API answers wallets as one group per chain, not a flat list."""

    def __init__(self, groups):
        self._groups = groups
        self.calls = 0

    async def list_gateway_wallets(self):
        self.calls += 1
        return self._groups

    async def add_gateway_wallet(self, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("MCP must never reach the wallet-add route")

    async def remove_gateway_wallet(self, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("MCP must never reach the wallet-remove route")


class _FakeClient:
    def __init__(self, groups):
        self.accounts = _FakeAccounts(groups)


def _wallets(action, **kwargs):
    client = _FakeClient(
        [
            {
                "chain": "solana",
                "walletAddresses": ["So1aaa", "So1bbb"],
                "default_address": "So1aaa",
            },
            {"chain": "ethereum", "walletAddresses": ["0xabc"]},
        ]
    )
    request = GatewayConfigRequest(resource_type="wallets", action=action, **kwargs)
    return client, asyncio.run(manage_gateway_config(client, request))


def test_no_mcp_tool_takes_a_private_key():
    """The acceptance criterion, checked over the whole server package."""
    hits = [
        f"{path.relative_to(MCP_ROOT)}:{n}: {line.strip()}"
        for path in MCP_ROOT.rglob("*.py")
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if "private_key" in line
    ]
    assert not hits, "a private key is still reachable over MCP:\n" + "\n".join(hits)


def test_listing_wallets_flattens_the_per_chain_groups():
    _, result = _wallets("list")
    assert result["resource_type"] == "wallets"
    assert result["action"] == "list"
    assert result["result"]["wallets"] == [
        {"chain": "solana", "address": "So1aaa"},
        {"chain": "solana", "address": "So1bbb"},
        {"chain": "ethereum", "address": "0xabc"},
    ]


def test_listing_wallets_can_be_filtered_by_chain():
    _, result = _wallets("list", chain="ethereum")
    assert result["result"]["wallets"] == [{"chain": "ethereum", "address": "0xabc"}]


def test_the_wallet_list_survives_an_empty_answer():
    client = _FakeClient(None)
    request = GatewayConfigRequest(resource_type="wallets", action="list")
    result = asyncio.run(manage_gateway_config(client, request))
    assert result["result"]["wallets"] == []


@pytest.mark.parametrize("action", ["add", "delete"])
def test_mutating_a_wallet_answers_with_the_dashboard_pointer(action):
    """A ToolError the agent can read, not an exception trace or a silent success."""
    with pytest.raises(ToolError) as excinfo:
        _wallets(action)
    message = str(excinfo.value)
    assert "dashboard" in message
    assert "private key must never be sent through chat" in message


def test_the_registered_tool_exposes_no_wallet_secret_parameters():
    import mcp_servers.hummingbot_api.server as server

    params = inspect.signature(server.manage_gateway_config).parameters
    assert "private_key" not in params
    assert "wallet_address" not in params
    assert "chain" in params, "listing wallets for one chain must stay possible"


def test_the_tool_docstring_sends_wallet_changes_to_the_dashboard():
    import mcp_servers.hummingbot_api.server as server

    doc = server.manage_gateway_config.__doc__
    assert "wallets: list only" in doc
    assert "dashboard" in doc


def test_the_request_model_carries_no_secret_field():
    fields = GatewayConfigRequest.model_fields
    assert "private_key" not in fields
    assert "wallet_address" not in fields


# ---------------------------------------------------------------------------
# An address another tool takes is printed whole (MH-04)
# ---------------------------------------------------------------------------

_WALLET = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"


def test_the_wallet_list_prints_the_address_a_wallet_address_parameter_takes():
    from mcp_servers.hummingbot_api.formatters import format_gateway_config_result

    text = format_gateway_config_result(
        {
            "resource_type": "wallets",
            "action": "list",
            "result": {"wallets": [{"chain": "solana", "address": _WALLET}]},
        }
    )

    assert _WALLET in text
    assert "..." not in text


def _tokens(count, search=None):
    from mcp_servers.hummingbot_api.formatters import format_gateway_config_result

    tokens = [
        {"symbol": f"T{i}", "decimals": 6, "address": f"{_MINT[:-4]}{i:04d}"}
        for i in range(count)
    ]
    return format_gateway_config_result(
        {
            "resource_type": "tokens",
            "action": "list",
            "search": search,
            "result": {"network_id": "solana-mainnet-beta", "tokens": tokens},
        }
    )


def test_the_token_list_prints_whole_mint_addresses():
    text = _tokens(1)

    assert f"{_MINT[:-4]}0000" in text
    assert "..." not in text


def test_an_unfiltered_token_list_says_it_is_a_page_and_how_to_search():
    """It used to stop at 20 rows with '... and N more tokens' and no way on."""
    text = _tokens(120)

    assert "Showing the first 50 of 120 tokens" in text
    assert "search=" in text
    assert "NOT missing from Gateway" in text
    assert f"{_MINT[:-4]}0049" in text
    assert f"{_MINT[:-4]}0050" not in text


def test_a_token_search_shows_every_match():
    text = _tokens(120, search="T")

    assert f"{_MINT[:-4]}0119" in text
    assert "Showing the first" not in text


def test_a_token_search_with_no_match_does_not_read_as_an_empty_registry():
    text = _tokens(0, search="UMBRA")

    assert "No token matches 'UMBRA'" in text
    assert "by its\naddress" in text or "by its address" in text
