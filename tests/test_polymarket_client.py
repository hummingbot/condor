"""PolymarketClient (CLOB V2): quotes, order placement/parse, balance, auth — SDK faked."""

import asyncio
from decimal import Decimal

import pytest

from condor.executors.polymarket import OrderAck, PolymarketClient, PolymarketError, _order_type, _side
from py_clob_client_v2.order_builder.constants import BUY, SELL
from py_clob_client_v2.clob_types import OrderType


class FakeClob:
    def __init__(self, price="0.52", post_resp=None, balance="284163584"):
        self._price = price
        self._post_resp = post_resp or {"success": True, "orderID": "0xabc", "status": "live"}
        self._balance = balance
        self.calls = []
        self.creds_set = False

    def get_address(self):
        return "0xWALLET"

    def create_or_derive_api_key(self):
        self.calls.append("derive")
        return {"api_key": "k", "api_secret": "s", "api_passphrase": "p"}

    def set_api_creds(self, creds):
        self.creds_set = True

    def get_price(self, token_id, side):
        self.calls.append(("get_price", token_id, side))
        return {"price": self._price}

    def get_midpoint(self, token_id):
        return {"mid": "0.5"}

    def get_tick_size(self, token_id):
        return "0.01"

    def get_neg_risk(self, token_id):
        self.calls.append(("get_neg_risk", token_id))
        return False

    def create_and_post_order(self, args, options=None, order_type="GTC"):
        self.calls.append(("limit", args.token_id, args.price, args.size, args.side, order_type,
                           options.tick_size, options.neg_risk))
        return self._post_resp

    def create_and_post_market_order(self, args, options=None, order_type="FOK"):
        self.calls.append(("market", args.token_id, args.amount, args.side, order_type))
        return self._post_resp

    def cancel_order(self, payload):
        self.calls.append(("cancel", payload.orderID))

    def get_balance_allowance(self, params):
        return {"balance": self._balance, "allowance": "0"}


def _client(fake, authed=False, sig_type=0):
    c = PolymarketClient.__new__(PolymarketClient)
    c.client = fake
    c.signature_type = sig_type
    c._authed = authed
    return c


def test_side_and_order_type_helpers():
    assert _side("buy") == BUY and _side("SELL") == SELL
    with pytest.raises(ValueError):
        _side("hold")
    assert _order_type("gtc") == OrderType.GTC and _order_type("FOK") == OrderType.FOK
    with pytest.raises(ValueError):
        _order_type("bogus")


def test_price_and_midpoint():
    c = _client(FakeClob(price="0.61"), authed=True)
    assert asyncio.run(c.price("tok", "BUY")) == Decimal("0.61")
    assert asyncio.run(c.midpoint("tok")) == Decimal("0.5")


def test_place_limit_derives_auth_and_posts():
    fake = FakeClob()
    c = _client(fake, authed=False)
    ack = asyncio.run(c.place_limit("tok", "BUY", Decimal("0.4"), Decimal("10"), "GTC"))
    assert ack.order_id == "0xabc" and ack.success and ack.status == "live"
    assert "derive" in fake.calls  # L2 creds derived on first authed call
    # create_and_post_order with tick_size + neg_risk options and GTC
    assert ("limit", "tok", 0.4, 10.0, BUY, OrderType.GTC, "0.01", False) in fake.calls


def test_place_market_uses_fok():
    fake = FakeClob(post_resp={"success": True, "orderID": "0xm", "status": "matched"})
    c = _client(fake, authed=True)
    ack = asyncio.run(c.place_market("tok", "BUY", Decimal("25")))
    assert ack.status == "matched"
    assert ("market", "tok", 25.0, BUY, OrderType.FOK) in fake.calls


def test_ack_error_parsed():
    fake = FakeClob(post_resp={"success": False, "errorMsg": "not enough balance"})
    c = _client(fake, authed=True)
    ack = asyncio.run(c.place_limit("tok", "BUY", Decimal("0.4"), Decimal("10")))
    assert not ack.success and ack.status == "error" and "balance" in ack.detail


def test_usdc_balance_scaled_from_base_units():
    c = _client(FakeClob(balance="284163584"), authed=True)
    assert asyncio.run(c.usdc_balance()) == Decimal("284.163584")


def test_auth_not_rederived_when_creds_present():
    fake = FakeClob()
    c = _client(fake, authed=True)
    asyncio.run(c.ensure_auth())
    assert "derive" not in fake.calls
