"""HyperliquidClient: builder-fee resolution + response parsing (no network).

The client is built with __new__ so no Info/Exchange (network) is constructed;
we exercise the pure logic.
"""

import asyncio
from decimal import Decimal

import pytest

from condor.executors.hyperliquid import (
    FOUNDATION_BUILDER_FEE_TENTHS_BPS,
    HyperliquidClient,
    HyperliquidError,
)


def _client(network="mainnet", info=None):
    c = HyperliquidClient.__new__(HyperliquidClient)
    c.network = network
    c.account_address = "0xabc"
    c.builder_supported = True
    c.info = info
    c._builder_fee_tenths_bps = None
    c._sz_decimals = {}
    return c


class _Info:
    def __init__(self, value=None, raises=False):
        self.value = value
        self.raises = raises

    def post(self, path, payload):
        if self.raises:
            raise RuntimeError("boom")
        return self.value


def test_builder_fee_capped_at_foundation_max():
    c = _client(info=_Info(value=25))  # approved 25 tenths-bps
    fee = asyncio.run(c.ensure_builder_fee())
    assert fee == FOUNDATION_BUILDER_FEE_TENTHS_BPS == 10
    assert asyncio.run(c._builder()) == {"b": "0x10ba451e6439efc6a17dc20d21121aa838100705", "f": 10}


def test_builder_fee_uses_lower_approval():
    c = _client(info=_Info(value=3))  # user approved only 3 tenths-bps
    assert asyncio.run(c.ensure_builder_fee()) == 3


def test_builder_fee_zero_when_not_approved():
    c = _client(info=_Info(value=0))
    assert asyncio.run(c.ensure_builder_fee()) == 0
    assert asyncio.run(c._builder()) is None


def test_builder_fee_zero_on_testnet():
    c = _client(network="testnet", info=_Info(value=10))
    assert asyncio.run(c.ensure_builder_fee()) == 0


def test_builder_fee_zero_on_lookup_failure():
    c = _client(info=_Info(raises=True))
    assert asyncio.run(c.ensure_builder_fee()) == 0


def test_parse_fill():
    c = _client()
    resp = {"status": "ok", "response": {"data": {"statuses": [
        {"filled": {"oid": 123, "avgPx": "2500.5", "totalSz": "0.01"}}]}}}
    fill = c._parse_fill(resp)
    assert fill.oid == 123 and fill.avg_px == Decimal("2500.5") and fill.size == Decimal("0.01")


def test_parse_fill_error_raises():
    c = _client()
    resp = {"status": "ok", "response": {"data": {"statuses": [{"error": "insufficient margin"}]}}}
    with pytest.raises(HyperliquidError):
        c._parse_fill(resp)


def test_parse_ack_resting():
    c = _client()
    resp = {"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": 77}}]}}}
    ack = c._parse_ack(resp)
    assert ack.oid == 77 and ack.status == "resting"


def test_statuses_rejects_non_ok():
    c = _client()
    with pytest.raises(HyperliquidError):
        c._statuses({"status": "err", "response": "bad"})


def test_round_sz_uses_cached_decimals():
    c = _client()
    c._sz_decimals = {"ETH": 4}
    assert asyncio.run(c._round_sz("ETH", Decimal("0.123456"))) == 0.1235


def test_round_px_5_sig_figs():
    # entry 1873.4 * 1.05 = 1967.07 -> 5 sig figs = 1967.1 (venue rejects more precision)
    assert HyperliquidClient._round_px(Decimal("1967.07")) == 1967.1
    assert HyperliquidClient._round_px(Decimal("1966.545")) == 1966.5
    assert HyperliquidClient._round_px(Decimal("0.0123456")) == 0.012346
