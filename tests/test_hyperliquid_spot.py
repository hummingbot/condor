"""HyperliquidSpotClient: pair resolution + swap-interface mapping (SDK faked)."""

import asyncio
from decimal import Decimal

import pytest

from condor.executors.hyperliquid import HyperliquidClient, HyperliquidError
from condor.executors.hyperliquid_spot import HyperliquidSpotClient

SPOT_META = {
    "tokens": [
        {"name": "USDC", "index": 0, "szDecimals": 8},
        {"name": "HYPE", "index": 150, "szDecimals": 2},
    ],
    "universe": [
        {"name": "@107", "tokens": [150, 0], "index": 107},  # HYPE/USDC
    ],
}
BOOK = {"levels": [[{"px": "65.606", "sz": "38"}], [{"px": "65.624", "sz": "1"}]]}


class FakeInfo:
    def __init__(self, balances=None):
        self._balances = balances or []

    def spot_meta(self):
        return SPOT_META

    def post(self, path, payload):
        assert payload["type"] == "l2Book"
        return BOOK

    def spot_user_state(self, addr):
        return {"balances": self._balances}


class FakeExchange:
    def __init__(self):
        self.orders = []

    def order(self, name, is_buy, sz, px, order_type, reduce_only=False, cloid=None, builder=None):
        self.orders.append((name, is_buy, sz, px, order_type["limit"]["tif"], builder))
        # fills the full size at the limit price
        return {"status": "ok", "response": {"data": {"statuses": [
            {"filled": {"oid": 42, "avgPx": str(px), "totalSz": str(sz)}}]}}}


def _spot(balances=None):
    hl = HyperliquidClient.__new__(HyperliquidClient)
    hl.builder_supported = False  # -> _builder() returns None, no network
    hl.network = "mainnet"
    hl._builder_fee_tenths_bps = 0
    c = HyperliquidSpotClient.__new__(HyperliquidSpotClient)
    c._hl = hl
    c.info = FakeInfo(balances)
    c.exchange = FakeExchange()
    c.account_address = "0xacct"
    c.network = "mainnet"
    c._pairs = None
    return c


def test_load_pairs_maps_token_to_pair():
    c = _spot()
    pairs = asyncio.run(c._load_pairs())
    assert pairs["HYPE"] == ("@107", 2)


def test_resolve_direction_and_errors():
    c = _spot()
    # base=USDC -> buying the token
    pair, buying, dec = asyncio.run(c._resolve("USDC", "HYPE"))
    assert pair == "@107" and buying is True and dec == 2
    # base=HYPE -> selling the token
    _, buying2, _ = asyncio.run(c._resolve("HYPE", "USDC"))
    assert buying2 is False
    with pytest.raises(HyperliquidError):  # neither side is USDC
        asyncio.run(c._resolve("HYPE", "ETH"))
    with pytest.raises(HyperliquidError):  # unknown pair
        asyncio.run(c._resolve("NOPE", "USDC"))


def test_quote_price_uses_ask_when_buying_bid_when_selling():
    c = _spot()
    buy_px = asyncio.run(c.quote_swap("hl", "USDC", "HYPE", 10, "SELL"))["price"]
    sell_px = asyncio.run(c.quote_swap("hl", "HYPE", "USDC", 0.1, "SELL"))["price"]
    assert buy_px == pytest.approx(65.624)   # ask
    assert sell_px == pytest.approx(65.606)  # bid


def test_execute_buy_spends_usdc_for_token():
    c = _spot()
    # buy: sell 10 USDC for HYPE. size = 10/ask rounded to 2dp
    out = asyncio.run(c.execute_swap("hl", "W", "USDC", "HYPE", 10, "SELL", slippage_pct=1))
    assert out["status"] == 1 and out["signature"] == "42"
    name, is_buy, sz, px, tif, _ = c.exchange.orders[0]
    assert name == "@107" and is_buy is True and tif == "Ioc"
    assert sz == pytest.approx(0.15)  # 10/65.624 -> 0.15 (2dp)
    # amountOut = token filled; amountIn = sz*px (USDC)
    assert out["data"]["amountOut"] == pytest.approx(0.15)
    assert out["data"]["amountIn"] == pytest.approx(0.15 * px)


def test_execute_sell_token_for_usdc():
    c = _spot()
    out = asyncio.run(c.execute_swap("hl", "W", "HYPE", "USDC", 0.15, "SELL", slippage_pct=1))
    name, is_buy, sz, px, tif, _ = c.exchange.orders[0]
    assert is_buy is False and sz == pytest.approx(0.15)
    assert out["data"]["amountIn"] == pytest.approx(0.15)          # token sold
    assert out["data"]["amountOut"] == pytest.approx(0.15 * px)    # USDC received


def test_get_balances_from_spot_state():
    c = _spot(balances=[{"coin": "HYPE", "total": "1.5"}, {"coin": "USDC", "total": "35.5"}])
    bals = asyncio.run(c.get_balances("hl", "mainnet", "0x", ["HYPE", "USDC"]))
    assert bals == {"HYPE": 1.5, "USDC": 35.5}


def test_poll_tx_confirmed():
    assert asyncio.run(_spot().poll_tx("hl", "mainnet", "42")) == {"txStatus": 1}


def test_runtime_routes_type_and_venue():
    from condor.executors.runtime import ExecutorRuntime

    rt = ExecutorRuntime.__new__(ExecutorRuntime)
    rt._jupiter = "JUP"
    rt._polymarket = "PM"
    rt._hl_clients = {"perp": "PERP", "spot": "SPOT", "outcome": "OUT"}
    # (instrument-from-type, venue) selects the connector
    assert rt.connector_for_spec("order_spot", "solana") == "JUP"
    assert rt.connector_for_spec("position_spot", "solana") == "JUP"
    assert rt.connector_for_spec("position_spot", "hyperliquid") == "SPOT"
    assert rt.connector_for_spec("order_spot", "hyperliquid") == "SPOT"
    assert rt.connector_for_spec("position_perp", "hyperliquid") == "PERP"
    assert rt.connector_for_spec("order_perp", "hyperliquid") == "PERP"
    assert rt.connector_for_spec("position_pred", "hyperliquid") == "OUT"
    assert rt.connector_for_spec("position_pred", "polymarket") == "PM"
    assert rt.connector_for_spec("order_spot", None) == "JUP"  # default venue = solana
    with pytest.raises(ValueError):
        rt.connector_for_spec("order_spot", "bogus")
