"""HIP-4 outcome client: asset-id encoding, discovery, price parsing, order wire."""

import asyncio
from decimal import Decimal

import pytest

from condor.executors.hyperliquid import OrderAck
from condor.executors.hyperliquid_outcome import (
    HyperliquidOutcomeClient,
    Outcome,
    _parse_ack,
)

C = HyperliquidOutcomeClient


def test_asset_id_encoding():
    # doc example: outcome 1, side 0 -> encoding 10 -> asset 100000010
    assert C.encoding(1, 0) == 10
    assert C.asset_id(1, 0) == 100_000_010
    assert C.coin(1, 0) == "#10"
    # Argentina outcome 173
    assert C.encoding(173, 0) == 1730 and C.coin(173, 0) == "#1730"
    assert C.asset_id(173, 0) == 100_001_730
    assert C.coin(173, 1) == "#1731"  # No side


def _client(fake_info):
    c = C.__new__(C)
    c.info = fake_info
    c.account_address = "0xacct"
    c.network = "mainnet"
    return c


class _Info:
    def __init__(self, meta=None, book=None, spot=None):
        self._meta = meta or {}
        self._book = book or {}
        self._spot = spot or {"balances": []}

    def post(self, path, payload):
        t = payload["type"]
        if t == "outcomeMeta":
            return self._meta
        if t == "l2Book":
            return self._book
        raise AssertionError(t)

    def spot_user_state(self, addr):
        return self._spot


META = {"outcomes": [
    {"outcome": 173, "name": "Argentina", "description": "wins WC",
     "sideSpecs": [{"name": "Yes"}, {"name": "No"}], "quoteToken": "USDC"},
]}


def test_find_outcome_by_id_and_name():
    c = _client(_Info(meta=META))
    o = asyncio.run(c.find_outcome(173))
    assert o.name == "Argentina" and o.sides == ["Yes", "No"]
    o2 = asyncio.run(c.find_outcome("argentina"))
    assert o2.id == 173
    assert C.side_index(o2, "No") == 1


def test_price_from_book():
    book = {"levels": [[{"px": "0.19319", "sz": "1552"}], [{"px": "0.19899", "sz": "365"}]]}
    c = _client(_Info(book=book))
    assert asyncio.run(c.price(173, 0, "bid")) == Decimal("0.19319")
    assert asyncio.run(c.price(173, 0, "ask")) == Decimal("0.19899")
    assert asyncio.run(c.price(173, 0, "mid")) == (Decimal("0.19319") + Decimal("0.19899")) / 2


def test_shares_and_usdc_from_spot_balances():
    # Spot balances key on the token name '+<encoding>', not the '#' coin symbol.
    spot = {"balances": [{"coin": "+1730", "total": "42.0"}, {"coin": "USDC", "total": "284.16"}]}
    c = _client(_Info(spot=spot))
    assert asyncio.run(c.shares(173, 0)) == Decimal("42.0")
    assert asyncio.run(c.usdc_balance()) == Decimal("284.16")
    assert C.token_name(173, 0) == "+1730" and C.coin(173, 0) == "#1730"


def test_parse_ack_variants():
    assert _parse_ack({"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": 9}}]}}}).oid == 9
    err = _parse_ack({"status": "ok", "response": {"data": {"statuses": [{"error": "bad"}]}}})
    assert err.status == "error" and err.detail == "bad"
    with pytest.raises(Exception):
        _parse_ack({"status": "err"})


def test_order_wire_uses_outcome_asset_id():
    # The order the client signs must carry asset 100_000_000 + 10*outcome + side.
    from hyperliquid.utils.signing import order_request_to_order_wire, order_wires_to_order_action
    asset = C.asset_id(173, 0)
    order = {"coin": C.coin(173, 0), "is_buy": True, "sz": 10.0, "limit_px": 0.2,
             "order_type": {"limit": {"tif": "Ioc"}}, "reduce_only": False}
    wire = order_request_to_order_wire(order, asset)
    action = order_wires_to_order_action([wire], None, "na")
    assert action["orders"][0]["a"] == 100_001_730
    assert action["orders"][0]["b"] is True  # buy
