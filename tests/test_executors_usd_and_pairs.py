"""Executor money is quote-denominated, and a DEX pair is a mint address.

Two symptoms on the chat surfaces, one root each.

PnL, volume and fees arrive from the API in the market's *quote* asset. Printing
them behind a ``$`` (or adding a SOL figure to a USDC one) is wrong by the whole
quote/USD rate, so every surface now restates through
:func:`~condor.quote_conversion.format_quote_money` and falls back to the
quote's own ticker when it cannot price it.

An LP executor's ``trading_pair`` is the mint it was opened on, which is what the
API resolves by but unreadable in a list. Gateway's token list already maps those
mints to tickers, so the row is labelled from it and left raw when the list does
not know the mint.
"""

import asyncio

import pytest

import handlers.executors._shared as shared
from condor.quote_conversion import QuoteRates
from handlers.executors._shared import (
    executor_quote_of,
    executor_raw_pair,
    executor_usd_value,
    format_executor_money,
    resolve_executor_pair_labels,
    resolve_executor_usd_rates,
)
from mcp_servers.hummingbot_api.formatters.executors import (
    format_executor_detail,
    format_executors_table,
)

MINT = "9QFfgxdSqH5zT7j6rZb1y6SZhw2aFtcQu2r6BuYpump"
NETWORK = "solana-mainnet-beta"
SOL_RATES = QuoteRates({"SOL": 150.0}, True)


class FakeGateway:
    def __init__(self, tokens):
        self.tokens = tokens
        self.calls = 0

    async def get_network_tokens(self, network_id, search=None):
        self.calls += 1
        return {"tokens": self.tokens}


class FakeClient:
    def __init__(self, gateway):
        self.gateway = gateway


@pytest.fixture(autouse=True)
def _clear_pair_labels_cache():
    shared._pair_labels_cache.clear()
    yield
    shared._pair_labels_cache.clear()


# ── Reading the pair and its quote out of an executor ──


def test_pair_and_quote_read_through_a_nested_config():
    ex = {"trading_pair": "IGNORED-SOL", "config": {"trading_pair": f"{MINT}-SOL"}}

    assert executor_raw_pair(ex) == f"{MINT}-SOL"
    assert executor_quote_of(ex) == "SOL"


def test_quote_falls_back_to_usdt_without_a_quote_segment():
    assert executor_quote_of({"trading_pair": "JUSTABASE"}) == "USDT"


# ── Money restatement ──


def test_usd_value_is_none_without_a_rate():
    assert executor_usd_value(2.0, "SOL", SOL_RATES) == 300.0
    assert executor_usd_value(2.0, "BONK", SOL_RATES) is None


def test_money_formats_as_usd_when_priced_and_as_quote_otherwise():
    assert format_executor_money(2.0, "SOL", SOL_RATES) == "$300.00"
    assert format_executor_money(-2.0, "SOL", SOL_RATES, signed=True) == "-$300.00"

    unpriced = QuoteRates({}, False)
    assert format_executor_money(0.0115, "SOL", unpriced) == "0.0115 SOL"


# ── Pair labels from Gateway's token list ──


def test_a_listed_mint_becomes_its_ticker():
    client = FakeClient(FakeGateway([{"address": MINT, "symbol": "PEPE"}]))

    labels = asyncio.run(
        resolve_executor_pair_labels(
            client,
            [{"connector_name": NETWORK, "trading_pair": f"{MINT}-SOL"}],
        )
    )

    assert labels == {f"{MINT}-SOL": "PEPE-SOL"}


def test_an_unlisted_mint_keeps_its_raw_pair():
    """The caller renders ``labels.get(raw, raw)``; nothing must vanish."""
    client = FakeClient(FakeGateway([]))

    labels = asyncio.run(
        resolve_executor_pair_labels(
            client,
            [{"connector_name": NETWORK, "trading_pair": f"{MINT}-SOL"}],
        )
    )

    assert labels == {}


def test_a_cex_connector_is_never_sent_to_the_token_list():
    client = FakeClient(FakeGateway([]))

    labels = asyncio.run(
        resolve_executor_pair_labels(
            client, [{"connector_name": "binance", "trading_pair": f"{MINT}-USDT"}]
        )
    )

    assert labels == {}
    assert client.gateway.calls == 0


def test_the_token_list_is_cached_across_renders():
    client = FakeClient(FakeGateway([{"address": MINT, "symbol": "PEPE"}]))
    ex = [{"connector_name": NETWORK, "trading_pair": f"{MINT}-SOL"}]

    asyncio.run(resolve_executor_pair_labels(client, ex))
    asyncio.run(resolve_executor_pair_labels(client, ex))

    assert client.gateway.calls == 1


# ── Rates for a batch ──


def test_usd_rates_resolve_every_quote_in_the_batch(monkeypatch):
    seen = {}

    async def _fake(client, quotes):
        seen["quotes"] = quotes
        return SOL_RATES

    monkeypatch.setattr("condor.quote_conversion.resolve_client_usd_rates", _fake)

    rates = asyncio.run(
        resolve_executor_usd_rates(
            None, [{"trading_pair": "A-SOL"}, {"trading_pair": "B-USDC"}]
        )
    )

    assert seen["quotes"] == {"SOL", "USDC"}
    assert rates.rates == {"SOL": 150.0}


def test_usd_rates_degrade_to_face_value_instead_of_raising(monkeypatch):
    async def _boom(client, quotes):
        raise RuntimeError("no server")

    monkeypatch.setattr("condor.quote_conversion.resolve_client_usd_rates", _boom)

    rates = asyncio.run(resolve_executor_usd_rates(None, [{"trading_pair": "A-SOL"}]))

    assert rates == QuoteRates({}, False)


# ── The MCP formatter reads the same rates ──


def _executor():
    return {
        "id": "e1",
        "type": "position",
        "connector_name": "binance",
        "trading_pair": "TOKEN-SOL",
        "status": "RUNNING",
        "side": "BUY",
        "filled_amount_quote": 2.0,
        "net_pnl_quote": 1.0,
        "timestamp": 1_700_000_000,
    }


def test_executor_table_states_money_in_usd_when_priced():
    table = format_executors_table([_executor()], SOL_RATES)

    assert "$300.00" in table
    assert "+$150.00" in table


def test_executor_table_keeps_the_ticker_when_unpriced():
    table = format_executors_table([_executor()], None)

    assert "2.0000 SOL" in table
    assert "+1.0000 SOL" in table
    assert "$" not in table


def test_executor_detail_restates_net_pnl():
    detail = format_executor_detail(_executor(), SOL_RATES)

    assert "Net PnL: +$150.00" in detail
