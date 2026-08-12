"""Tests for CORR-124: ``validate_trading_pair`` must fail closed.

The validator is the single gate that stops a typo'd or non-existent pair from
reaching order placement. It used to return ``is_valid=True`` whenever the
trading rules came back empty — which is exactly what a backend outage produces
on this path, since ``fetch_trading_rules`` swallows errors into ``{}`` for
direct callers (CORR-108 only bound ``strict=True`` for the SDS read).

It now asks for the rules with ``strict=True`` and reports the pair as invalid
when they cannot be resolved, with a message that names the cause instead of
one that looks like the pair does not exist.
"""

import asyncio

from handlers.cex._shared import validate_trading_pair

RULES = {"BTC-USDT": {"min_order_size": 1}, "ETH-USDT": {"min_order_size": 1}}


class _Connectors:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.calls = 0

    async def get_trading_rules(self, connector_name=""):
        self.calls += 1
        if self.mode == "down":
            raise RuntimeError("502 Bad Gateway")
        if self.mode == "empty":
            return {}
        return RULES


class _FakeClient:
    def __init__(self, mode="ok"):
        self.connectors = _Connectors(mode)


def test_an_api_outage_blocks_the_pair_instead_of_allowing_it_through():
    """The whole point: a failed fetch must not read as a validated pair."""

    async def _drive():
        client = _FakeClient("down")
        return await validate_trading_pair({}, client, "binance_perpetual", "BTC-USDC")

    is_valid, error_msg, suggestions, correct_pair = asyncio.run(_drive())

    assert is_valid is False
    assert "could not reach" in error_msg.lower()
    assert "binance_perpetual" in error_msg
    assert suggestions == []
    assert correct_pair is None


def test_the_outage_message_differs_from_the_pair_not_found_message():
    """A user must be able to tell "try again" from "that pair is wrong"."""

    async def _drive():
        down = await validate_trading_pair(
            {}, _FakeClient("down"), "binance_perpetual", "DOGE-USDT"
        )
        missing = await validate_trading_pair(
            {}, _FakeClient("ok"), "binance_perpetual", "DOGE-USDT"
        )
        return down, missing

    (down_valid, down_msg, _, _), (missing_valid, missing_msg, _, _) = asyncio.run(
        _drive()
    )

    assert down_valid is False and missing_valid is False
    assert down_msg != missing_msg
    assert "not found" in missing_msg.lower()
    assert "not found" not in down_msg.lower()


def test_a_connector_with_no_rules_is_also_not_validated():
    """An answered-but-empty connector gives nothing to validate against."""

    async def _drive():
        return await validate_trading_pair(
            {}, _FakeClient("empty"), "binance_perpetual", "BTC-USDT"
        )

    is_valid, error_msg, suggestions, correct_pair = asyncio.run(_drive())

    assert is_valid is False
    assert "no trading rules" in error_msg.lower()
    assert suggestions == []
    assert correct_pair is None


def test_the_failure_is_not_cached_so_a_recovered_api_validates_again():
    """The raise happens before the cache write, so nothing poisons the entry."""

    async def _drive():
        client = _FakeClient("down")
        user_data = {}
        first = await validate_trading_pair(
            user_data, client, "binance_perpetual", "BTC-USDT"
        )
        client.connectors.mode = "ok"
        second = await validate_trading_pair(
            user_data, client, "binance_perpetual", "BTC-USDT"
        )
        return first, second, client.connectors.calls

    (first_valid, _, _, _), (second_valid, _, _, correct_pair), calls = asyncio.run(
        _drive()
    )

    assert first_valid is False
    assert second_valid is True
    assert correct_pair == "BTC-USDT"
    assert calls == 2


def test_a_listed_pair_still_validates():
    """The happy path is untouched: exact match returns the exchange's format."""

    async def _drive():
        return await validate_trading_pair(
            {}, _FakeClient("ok"), "binance_perpetual", "btc/usdt"
        )

    is_valid, error_msg, suggestions, correct_pair = asyncio.run(_drive())

    assert is_valid is True
    assert error_msg is None
    assert suggestions == []
    assert correct_pair == "BTC-USDT"
