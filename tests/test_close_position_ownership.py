"""close_position must not be a way to flatten another strategy's book.

Greptile, PR #335: the tool closed by (account, connector, pair) alone, so on a
shared account one agent could close a position another session still owned —
``stop_executor`` is ownership-checked, this was not. It also shipped missing
from ``DANGEROUS_TOOLS``, so a dry run approved it and an attended caller skipped
confirmation: a rehearsal could submit a real market order.
"""

import asyncio

from condor.runtime.danger import DANGEROUS_TOOLS
from mcp_servers.hummingbot_api import server as hb_server


class _Trading:
    def __init__(self, positions):
        self._positions = positions

    async def get_positions(self, account_names=None, connector_names=None):
        return {"data": self._positions}

    async def place_order(self, **kwargs):
        return {"order_id": "t-TEST", "status": "submitted"}


class _Executors:
    def __init__(self, rows):
        self._rows = rows

    async def search_executors(self, **kwargs):
        return {"data": self._rows}


class _Client:
    def __init__(self, positions, executors):
        self.trading = _Trading(positions)
        self.executors = _Executors(executors)


def _patch(monkeypatch, positions, executors):
    client = _Client(positions, executors)

    async def _get():
        return client

    monkeypatch.setattr(hb_server.hummingbot_client, "get_client", _get)
    return client


POSITION = [
    {
        "trading_pair": "CL-USDT",
        "connector_name": "gate_io_perpetual",
        "amount": -0.12,
        "side": "SHORT",
    }
]


def _close(**kwargs):
    return asyncio.run(
        hb_server.close_position(
            account_name="master_account",
            connector_name="gate_io_perpetual",
            trading_pair="CL-USDT",
            **kwargs,
        )
    )


def test_it_is_a_dangerous_tool():
    """A dry run must not approve it and an attended caller must be asked."""
    assert "close_position" in DANGEROUS_TOOLS


def test_it_refuses_while_another_session_owns_the_position(monkeypatch):
    _patch(
        monkeypatch, POSITION, [{"controller_id": "otheragent", "status": "RUNNING"}]
    )
    out = _close()
    assert "owned by a live executor" in out
    assert "otheragent" in out


def test_it_allows_the_owner_to_close(monkeypatch):
    _patch(monkeypatch, POSITION, [{"controller_id": "mine", "status": "RUNNING"}])
    out = _close(controller_id="mine")
    assert "Closed" in out


def test_it_allows_a_leftover_with_nothing_running(monkeypatch):
    """The case the tool exists for: an executor stopped with keep_position."""
    _patch(
        monkeypatch, POSITION, [{"controller_id": "stopped", "status": "TERMINATED"}]
    )
    out = _close()
    assert "Closed" in out


def test_it_refuses_when_ownership_cannot_be_read(monkeypatch):
    class _Broken(_Client):
        def __init__(self, positions):
            super().__init__(positions, [])
            self.executors = None

    client = _Broken(POSITION)

    async def _get():
        return client

    monkeypatch.setattr(hb_server.hummingbot_client, "get_client", _get)
    out = _close()
    assert "Refusing to close" in out
