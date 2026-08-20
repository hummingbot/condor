"""The two-step swap flow reaches the agent, or it may as well not exist.

Gateway has had /trading/router/execute-quote all along and no layer above it exposed
the route: not hummingbot-api, not the client SDK, not this tool. So every swap on
record went through the one-step execute, which re-prices at execution and throws away
the quote the caller was shown — the whole value of a held quote on dflow, titan and 0x.
"""
import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest

from mcp_servers.hummingbot_api.exceptions import ToolError
from mcp_servers.hummingbot_api.schemas import GatewaySwapRequest
from mcp_servers.hummingbot_api.tools.gateway_swap import manage_gateway_swaps


class _Recorder:
    def __init__(self):
        self.calls = []

    async def execute_quote(self, **kwargs):
        self.calls.append(kwargs)
        return {"transaction_hash": "sig", "status": "confirmed"}


def _client(recorder):
    return SimpleNamespace(gateway_swap=recorder)


def _request(**overrides):
    fields = dict(
        action="execute_quote",
        connector="jupiter",
        network="solana-mainnet-beta",
        quote_id="q-123",
        trading_pair="SOL-USDC",
        side="SELL",
        amount="0.01",
    )
    fields.update(overrides)
    return GatewaySwapRequest(**fields)


def test_the_action_exists_and_passes_the_quote_id_through():
    recorder = _Recorder()

    result = asyncio.run(manage_gateway_swaps(_client(recorder), _request()))

    assert recorder.calls[0]["quote_id"] == "q-123"
    assert recorder.calls[0]["amount"] == Decimal("0.01")
    assert result["action"] == "execute_quote"


def test_a_missing_quote_id_says_how_to_get_one():
    recorder = _Recorder()

    with pytest.raises(ToolError, match="take a quote first"):
        asyncio.run(manage_gateway_swaps(_client(recorder), _request(quote_id=None)))

    assert recorder.calls == []


def test_the_pair_and_size_are_required_because_the_record_needs_them():
    # Gateway identifies the swap by quote_id alone, but the stored trade is filed under
    # the pair and size — omitting them would write a row that says nothing.
    recorder = _Recorder()

    with pytest.raises(ToolError, match="filed under the pair"):
        asyncio.run(manage_gateway_swaps(_client(recorder), _request(trading_pair=None)))
