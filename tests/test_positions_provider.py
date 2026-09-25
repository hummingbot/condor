"""Unit tests for the positions core-data provider ([[ARCH-682]]).

The provider reads through :func:`condor.fetchers.tracked_positions.fetch_tracked_positions`
like its sibling ``DriftProvider``, so the response-shape normalisation (and the
dropping of malformed rows) lives in one place.
"""

import asyncio

from condor.agents.providers import positions as positions_module
from condor.agents.providers.positions import PositionsProvider
from condor.fetchers.tracked_positions import fetch_tracked_positions


class _Executors:
    def __init__(self, page=None, raises=None):
        self.page = page
        self.raises = raises

    async def get_positions_summary(self, controller_id=None):
        if self.raises:
            raise self.raises
        return self.page


class _Client:
    def __init__(self, **kw):
        self.executors = _Executors(**kw)


def test_positions_provider_reads_through_the_tracked_fetcher(monkeypatch):
    calls = []

    async def spy(client, **kwargs):
        calls.append(kwargs)
        return await fetch_tracked_positions(client, **kwargs)

    monkeypatch.setattr(positions_module, "fetch_tracked_positions", spy)
    provider = PositionsProvider()

    # A non-dict row in the page is dropped instead of crashing the block.
    row = {
        "connector_name": "binance",
        "trading_pair": "SOL-USDC",
        "position_side": "LONG",
        "net_amount_base": 1.5,
        "buy_breakeven_price": 140.0,
    }
    client = _Client(page={"positions": [row, "garbage", None]})
    result = asyncio.run(provider.execute(client, {}, agent_id="acme.scalper_1"))

    assert calls == [{"controller_id": "acme.scalper_1", "strict": True}]
    assert result.data == {"positions": [row]}
    assert "Positions Summary (1)" in result.summary
    assert "binance SOL-USDC LONG" in result.summary

    # A failed request is the provider's error result, never "no open positions".
    failing = _Client(raises=RuntimeError("api down"))
    result = asyncio.run(provider.execute(failing, {}, agent_id="acme.scalper_1"))
    assert result.data == {"error": "RuntimeError"}
    assert "failed to fetch" in result.summary


def test_positions_provider_empty_book_reports_no_open_positions():
    result = asyncio.run(
        PositionsProvider().execute(_Client(page={"positions": []}), {}, agent_id="")
    )
    assert result.data == {"positions": []}
    assert result.summary == "Positions Summary: no open positions"
