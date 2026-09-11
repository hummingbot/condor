"""search_history must honour, or refuse, the filters its signature promises (CORR-563).

The tool advertises one filter set for three data types, but the perp branch calls
``client.trading.get_positions``, whose backend route (POST /trading/positions) takes
only account_names/connector_names/limit. So
``search_history("perp_positions", status="CLOSED", start_time=...)`` used to return
today's OPEN book under a "Perpetual Positions History" header, with no signal that
the time window had been dropped — the worst shape for a PnL or tax report.

These tests drive the real branch through the server-level tool (decorator and the
bare ``except Exception`` rewrap included) and assert on the request that actually
reaches the client, not on a helper's return value.

The repo has no async test setup, so the coroutines are driven with asyncio.run().
"""

import asyncio

import pytest

from mcp_servers.hummingbot_api import server as hb_server
from mcp_servers.hummingbot_api.exceptions import ToolError
from mcp_servers.hummingbot_api.tools import history as history_tools


class RecordingTrading:
    """Records every outgoing request instead of hitting the backend."""

    def __init__(self):
        self.position_calls = []
        self.order_calls = []

    async def get_positions(self, **kwargs):
        self.position_calls.append(kwargs)
        return {
            "data": [
                {
                    "account_name": "master",
                    "connector_name": "binance_perpetual",
                    "trading_pair": "SOL-USDT",
                    "side": "LONG",
                    "amount": 10,
                    "entry_price": 100,
                    "unrealized_pnl": 5,
                }
            ]
        }

    async def search_orders(self, **kwargs):
        self.order_calls.append(kwargs)
        return {"data": [], "pagination": {"has_more": False}}


class RecordingGatewayClmm:
    """Records every outgoing gateway_clmm.search_positions call."""

    def __init__(self):
        self.search_calls = []

    async def search_positions(self, **kwargs):
        self.search_calls.append(kwargs)
        return {"data": []}


class RecordingClient:
    def __init__(self):
        self.trading = RecordingTrading()
        self.gateway_clmm = RecordingGatewayClmm()


@pytest.fixture
def client_calls(monkeypatch):
    """Drive the server-level tool against a recording client."""
    client = RecordingClient()

    async def fake_get_client():
        return client

    monkeypatch.setattr(hb_server.hummingbot_client, "get_client", fake_get_client)
    return client.trading


@pytest.fixture
def clmm_calls(monkeypatch):
    """Drive the server-level tool against a recording client, gateway_clmm side."""
    client = RecordingClient()

    async def fake_get_client():
        return client

    monkeypatch.setattr(hb_server.hummingbot_client, "get_client", fake_get_client)
    return client.gateway_clmm


@pytest.mark.parametrize(
    "data_type, filters, expected_names",
    [
        ("perp_positions", {"status": "CLOSED"}, ["status"]),
        (
            "perp_positions",
            {"start_time": 1757000000, "end_time": 1757600000},
            ["start_time", "end_time"],
        ),
        ("perp_positions", {"trading_pairs": ["SOL-USDT"]}, ["trading_pairs"]),
        ("perp_positions", {"offset": 50}, ["offset"]),
        # CORR-618: gateway_clmm.search_positions has no time-window parameter at
        # any layer, so clmm_positions must refuse start_time/end_time rather than
        # silently returning the unfiltered newest-50 positions.
        (
            "clmm_positions",
            {"start_time": 1757000000, "end_time": 1757600000},
            ["start_time", "end_time"],
        ),
    ],
)
def test_positions_branches_refuse_filters_they_cannot_honour(
    client_calls, data_type, filters, expected_names
):
    """The tool raises naming the parameter, and no request is sent."""
    with pytest.raises(ToolError) as excinfo:
        asyncio.run(hb_server.search_history(data_type=data_type, **filters))

    message = str(excinfo.value)
    for name in expected_names:
        assert name in message, f"{name} not named in refusal: {message}"
    # The refusal must survive history.py's bare `except Exception` rewrap and the
    # handle_errors decorator with its parameter names intact, not be flattened
    # into "Failed to search history: ...".
    assert "silently ignored" in message

    # Acceptance criterion: neither branch reaches its backend endpoint.
    assert client_calls.position_calls == []


def test_perp_positions_still_works_with_supported_filters(client_calls):
    """Supported filters reach the client, and the header no longer says "History"."""
    output = asyncio.run(
        hb_server.search_history(
            data_type="perp_positions",
            account_names=["master"],
            connector_names=["binance_perpetual"],
            limit=25,
        )
    )

    assert client_calls.position_calls == [
        {
            "account_names": ["master"],
            "connector_names": ["binance_perpetual"],
            "limit": 25,
        }
    ]
    assert "History" not in output
    assert "current open book" in output


def test_clmm_positions_still_reaches_search_positions_with_supported_filters(
    clmm_calls,
):
    """CORR-618: refusing start_time/end_time must not touch the supported filters."""
    output = asyncio.run(
        hb_server.search_history(
            data_type="clmm_positions",
            network="mainnet-beta",
            connector_names=["raydium"],
            trading_pairs=["SOL-USDC"],
            status="OPEN",
            offset=10,
        )
    )

    assert clmm_calls.search_calls == [
        {
            "limit": 50,
            "offset": 10,
            "refresh": False,
            "network": "mainnet-beta",
            "connector": "raydium",
            "trading_pair": "SOL-USDC",
            "status": "OPEN",
        }
    ]
    assert "No CLMM positions found" in output


def test_orders_branch_still_forwards_every_filter(client_calls):
    """The guard is perp-only: orders genuinely sends its filters to the backend."""
    asyncio.run(
        hb_server.search_history(
            data_type="orders",
            account_names=["master"],
            connector_names=["binance"],
            trading_pairs=["SOL-USDC"],
            status="FILLED",
            start_time=1757000000,
            end_time=1757600000,
            limit=100,
        )
    )

    assert len(client_calls.order_calls) == 1
    sent = client_calls.order_calls[0]
    assert sent["trading_pairs"] == ["SOL-USDC"]
    assert sent["status"] == "FILLED"
    assert sent["start_time"] == 1757000000
    assert sent["end_time"] == 1757600000
    assert sent["limit"] == 100


def test_docstrings_no_longer_promise_common_filters():
    """The signature's promise and the perp reality have to match."""
    doc = hb_server.search_history.__doc__
    assert "Common Filters (apply to all data types)" not in doc
    assert 'search_history("perp_positions", status="CLOSED")' not in doc
    assert "both open and closed" not in doc.split("clmm_positions")[0]

    tool_doc = history_tools.search_history.__doc__
    assert "perp_positions: Perpetual positions (both open and closed)" not in tool_doc
