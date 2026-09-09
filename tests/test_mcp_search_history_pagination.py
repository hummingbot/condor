"""search_history must paginate orders by the cursor the API actually uses (CORR-569).

POST /trading/orders/search is cursor-paginated and has no ``offset`` parameter at
all, but the orders branch passed ``cursor=None,  # We use offset instead`` and then
told the caller ``... and more (use offset=N to see more)``. A model that followed
that hint re-issued the identical request forever and — because the rows came back
identical while the tool said "more" — read page one over and over as if it were
walking history. For the stated use case (reporting, tax) that silently truncated
every result set at the first page.

These tests drive the real branch through the server-level tool and assert on what
actually crosses the wire (the cursor in the outgoing request) and on what the model
actually reads back (the next-cursor in the formatted output) — not on a helper's
return value.

The repo has no async test setup, so the coroutines are driven with asyncio.run().
"""

import asyncio
import re

import pytest

from mcp_servers.hummingbot_api import server as hb_server
from mcp_servers.hummingbot_api.exceptions import ToolError

PAGE_ONE = [
    {
        "trading_pair": "SOL-USDC",
        "trade_type": "BUY",
        "order_type": "LIMIT",
        "amount": 10,
        "price": 200,
        "status": "FILLED",
    }
]
PAGE_TWO = [
    {
        "trading_pair": "ETH-USDC",
        "trade_type": "SELL",
        "order_type": "LIMIT",
        "amount": 2,
        "price": 3000,
        "status": "FILLED",
    }
]

NEXT_CURSOR = "eyJvZmZzZXQiOjF9"


class PaginatingTrading:
    """A cursor-paginated /trading/orders/search, as the backend really behaves.

    The two pages hold different pairs, so a caller that re-fetches page one is
    visibly distinguishable from one that genuinely advanced.
    """

    def __init__(self):
        self.order_calls = []
        self.position_calls = []

    async def search_orders(self, **kwargs):
        self.order_calls.append(kwargs)
        cursor = kwargs.get("cursor")
        if cursor is None:
            return {
                "data": PAGE_ONE,
                "pagination": {"has_more": True, "next_cursor": NEXT_CURSOR},
            }
        if cursor == NEXT_CURSOR:
            return {
                "data": PAGE_TWO,
                "pagination": {"has_more": False, "next_cursor": None},
            }
        raise AssertionError(f"unknown cursor sent to the backend: {cursor!r}")

    async def get_positions(self, **kwargs):
        self.position_calls.append(kwargs)
        return {"data": []}


class PaginatingClient:
    def __init__(self):
        self.trading = PaginatingTrading()


@pytest.fixture
def client_calls(monkeypatch):
    """Drive the server-level tool against a cursor-paginated recording client."""
    client = PaginatingClient()

    async def fake_get_client():
        return client

    monkeypatch.setattr(hb_server.hummingbot_client, "get_client", fake_get_client)
    return client.trading


def test_first_page_of_orders_surfaces_the_backend_cursor_not_an_offset(client_calls):
    """The hint the model reads has to be something the backend can act on."""
    output = asyncio.run(hb_server.search_history(data_type="orders", limit=1))

    assert client_calls.order_calls[0]["cursor"] is None
    assert "SOL-USDC" in output
    assert NEXT_CURSOR in output, f"next cursor not surfaced to the model: {output}"
    # The old hint was `use offset=1`, which search_orders has no parameter for.
    assert "offset=" not in output


def test_passing_the_cursor_back_returns_a_different_page(client_calls):
    """The acceptance criterion: page two is genuinely page two, not page one again."""
    first = asyncio.run(hb_server.search_history(data_type="orders", limit=1))

    # Take the cursor the way a model would: read it out of the rendered hint.
    match = re.search(r'cursor="([^"]+)"', first)
    assert match, f"no reusable cursor in the output: {first}"

    second = asyncio.run(
        hb_server.search_history(data_type="orders", limit=1, cursor=match.group(1))
    )

    assert client_calls.order_calls[1]["cursor"] == NEXT_CURSOR
    assert "ETH-USDC" in second, "the second page repeated page one"
    assert "SOL-USDC" not in second
    # Last page: no cursor came back, so no hint is invented.
    assert "cursor=" not in second


def test_orders_refuses_an_offset_it_would_silently_drop(client_calls):
    """search_orders has no offset parameter; accepting one is the CORR-563 bug."""
    with pytest.raises(ToolError) as excinfo:
        asyncio.run(hb_server.search_history(data_type="orders", offset=50))

    message = str(excinfo.value)
    assert "offset" in message
    assert "silently ignored" in message
    assert "cursor" in message, "the refusal should name the pagination that works"
    assert client_calls.order_calls == [], "no request may go out"


@pytest.mark.parametrize("data_type", ["perp_positions", "clmm_positions"])
def test_branches_without_cursor_support_refuse_a_cursor(client_calls, data_type):
    """tools/trading.py:get_positions and gateway_clmm.search_positions take no cursor.

    The trading router does accept one, but our wrapper neither takes nor forwards
    it, so an accepted-but-ignored cursor here would be exactly the silent drop
    CORR-563 closed.
    """
    with pytest.raises(ToolError) as excinfo:
        asyncio.run(hb_server.search_history(data_type=data_type, cursor="abc"))

    message = str(excinfo.value)
    assert "cursor" in message
    assert "silently ignored" in message
    assert client_calls.position_calls == [], "no request may go out"


def test_the_tool_signature_offers_a_cursor():
    """The signature is the schema: a model cannot pass what is not declared."""
    import inspect

    params = inspect.signature(hb_server.search_history).parameters
    assert "cursor" in params
    assert params["cursor"].default is None
