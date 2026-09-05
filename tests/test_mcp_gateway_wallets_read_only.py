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
