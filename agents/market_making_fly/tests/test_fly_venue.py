"""Spot or perp, and what a round trip costs there."""

import asyncio

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


def _seed_hyperliquid_fees():
    """The two Hyperliquid payloads the fee comes from, as the venue returns
    them. Seeding the cache keeps the arithmetic under test and the network
    out of it."""
    venue._hl_cache.clear()
    venue._hl_cache[
        repr(
            sorted({"type": "userFees", "user": venue._SCHEDULE_PROBE_ADDRESS}.items())
        )
    ] = {"feeSchedule": {"add": "0.00015", "cross": "0.00045", "spotAdd": "0.0004"}}
    venue._hl_cache[repr(sorted({"type": "meta", "dex": "xyz"}.items()))] = {
        "universe": [
            {"name": "xyz:ORCL", "deployerFeeScale": "1.0", "growthMode": "enabled"},
            {"name": "xyz:NOGROWTH", "deployerFeeScale": "1.0"},
            {"name": "xyz:HALFSCALE", "deployerFeeScale": "0.5"},
        ]
    }


def test_one_hyperliquid_connector_charges_two_different_fees():
    """A core perp and a HIP-3 market differ by about 2x, and the floor built
    on the wrong one either loses money or never fills. Measured against the
    run of 2026-09-13: 173 maker fills on XYZ:ORCL-USD paid 1.29 bp all-in."""
    _seed_hyperliquid_fees()
    core = asyncio.run(venue.hyperliquid_maker_fee_bps("BTC-USD", "perp"))
    hip3 = asyncio.run(venue.hyperliquid_maker_fee_bps("XYZ:ORCL-USD", "perp"))
    assert core == pytest.approx(1.5 + venue.HUMMINGBOT_BUILDER_FEE_BPS)
    # scale 1.0 doubles the venue rate, growth mode takes a tenth of that
    assert hip3 == pytest.approx(1.5 * 2 * 0.1 + venue.HUMMINGBOT_BUILDER_FEE_BPS)
    assert abs(hip3 - 1.29) < 0.02, "computed fee has drifted from what was paid"


def test_the_deployers_own_setting_moves_the_fee_both_ways():
    """Hyperliquid's rule is not a discount: without growth mode a HIP-3
    market costs more than the core venue, not less."""
    _seed_hyperliquid_fees()
    dear = asyncio.run(venue.hyperliquid_maker_fee_bps("XYZ:NOGROWTH-USD", "perp"))
    half = asyncio.run(venue.hyperliquid_maker_fee_bps("XYZ:HALFSCALE-USD", "perp"))
    assert dear == pytest.approx(1.5 * 2 + venue.HUMMINGBOT_BUILDER_FEE_BPS)
    assert half == pytest.approx(1.5 * 1.5 + venue.HUMMINGBOT_BUILDER_FEE_BPS)
    assert dear > asyncio.run(venue.hyperliquid_maker_fee_bps("BTC-USD", "perp"))


def test_an_unlisted_hip3_market_is_refused_not_guessed():
    _seed_hyperliquid_fees()
    with pytest.raises(ValueError, match="not listed"):
        asyncio.run(venue.hyperliquid_maker_fee_bps("XYZ:NOTREAL-USD", "perp"))


def test_the_fly_says_what_to_install_rather_than_failing_deep():
    """pyarrow is 122 MB that only this agent uses, so it is an extra. An
    operator who skipped it should get the command, not an ImportError three
    frames inside a vendored connectome loader."""
    import builtins

    from flybrain.deps import INSTALL, require_pyarrow

    real_import = builtins.__import__

    def without_pyarrow(name, *args, **kwargs):
        if name == "pyarrow" or name.startswith("pyarrow."):
            raise ImportError("No module named 'pyarrow'")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = without_pyarrow
    try:
        with pytest.raises(RuntimeError, match=INSTALL):
            require_pyarrow()
    finally:
        builtins.__import__ = real_import

    require_pyarrow()  # installed here, so it must stay silent
