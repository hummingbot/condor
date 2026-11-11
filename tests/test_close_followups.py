"""Two follow-ups from the review of the review.

* `close_position` had to become dangerous to be confirmed -- and that routed it
  through the shutdown refusal, which only allows stops and liquidity removals.
  The one tool that can flatten a leftover position was refused exactly when a
  winddown needs it.
* a HEDGE-mode account can hold a LONG and a SHORT on one pair, so closing by
  pair alone could take the wrong leg and leave the intended one open.
"""

import asyncio

from condor.agents.risk import _shutdown_refusal
from mcp_servers.hummingbot_api import server as hb_server


class _Trading:
    def __init__(self, positions):
        self._positions = positions

    async def get_positions(self, account_names=None, connector_names=None):
        return {"data": self._positions}

    async def place_order(self, **kwargs):
        return {"order_id": "t-TEST", "status": "submitted"}


class _Executors:
    async def search_executors(self, **kwargs):
        return {"data": []}


class _Client:
    def __init__(self, positions):
        self.trading = _Trading(positions)
        self.executors = _Executors()


def _patch(monkeypatch, positions):
    client = _Client(positions)

    async def _get():
        return client

    monkeypatch.setattr(hb_server.hummingbot_client, "get_client", _get)


def _close(**kwargs):
    return asyncio.run(
        hb_server.close_position(
            account_name="master_account",
            connector_name="gate_io_perpetual",
            trading_pair="CL-USDT",
            **kwargs,
        )
    )


def test_shutdown_allows_the_flatten():
    """A winddown is where a leftover position has to be closable."""
    assert _shutdown_refusal("close_position", {}) == ""


def test_shutdown_still_refuses_a_create():
    assert "not a winddown action" in _shutdown_refusal(
        "create_position_executor", {"trading_pair": "CL-USDT"}
    )


HEDGED = [
    {
        "trading_pair": "CL-USDT",
        "connector_name": "gate_io_perpetual",
        "amount": 1.0,
        "side": "LONG",
    },
    {
        "trading_pair": "CL-USDT",
        "connector_name": "gate_io_perpetual",
        "amount": 1.0,
        "side": "SHORT",
    },
]


def test_a_hedged_pair_refuses_to_guess(monkeypatch):
    _patch(monkeypatch, HEDGED)
    out = _close()
    assert "more than one position" in out
    assert "side=" in out


def test_a_hedged_pair_closes_the_leg_that_was_named(monkeypatch):
    client = _patch(monkeypatch, HEDGED)
    out = _close(side="SHORT")
    assert "SHORT" in out
    assert "Closed" in out


def test_a_named_leg_that_is_not_open_says_so(monkeypatch):
    _patch(monkeypatch, HEDGED[:1])  # LONG only
    out = _close(side="SHORT")
    assert "No open SHORT" in out
