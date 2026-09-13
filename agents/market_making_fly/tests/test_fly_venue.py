"""Spot or perp, and what a round trip costs there."""

import pytest
from flybrain import venue


@pytest.mark.parametrize(
    "connector,expected",
    [
        ("binance_perpetual", "perp"),
        ("hyperliquid_perpetual", "perp"),
        ("gate_io_perpetual", "perp"),
        ("binance", "spot"),
        ("kucoin", "spot"),
        ("backpack", "spot"),
    ],
)
def test_market_type_comes_from_the_connector(connector, expected):
    assert venue.market_type_for(connector) == expected


def test_longest_prefix_wins():
    """binance_perpetual is not binance, and their fees differ by 3.75x."""
    assert venue.default_maker_fee_bps("binance_perpetual", "perp") == 2.0
    assert venue.default_maker_fee_bps("binance", "spot") == 7.5


def test_an_unknown_venue_is_quoted_wide_not_tight():
    """A fee floor set too low loses money silently; too high only costs fills."""
    assert venue.default_maker_fee_bps("brand_new_dex", "spot") == 10.0
    assert venue.default_maker_fee_bps("brand_new_dex_perpetual", "perp") == 2.5


def test_resolve_refuses_a_contradiction():
    assert venue.resolve("binance") == "spot"
    assert venue.resolve("binance_perpetual") == "perp"
    assert venue.resolve("binance", "spot") == "spot"
    with pytest.raises(ValueError):
        venue.resolve("binance", "perp")
    with pytest.raises(ValueError):
        venue.resolve("binance", "margin")
    with pytest.raises(ValueError):
        venue.market_type_for("")
