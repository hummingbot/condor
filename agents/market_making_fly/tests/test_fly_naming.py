"""Derived names for any CLOB pair, and the pairs-list rules."""

import pytest
from flybrain.naming import pair_names, parse_pairs


def test_hip3_pair():
    n = pair_names("XYZ:ORCL-USD")
    assert (n.base, n.quote, n.issuer) == ("ORCL", "USD", "xyz")
    assert n.hl_coin == "xyz:ORCL"
    assert (n.slug, n.bot_name, n.config_name) == (
        "xyz-orcl-usd",
        "xyz-orcl-usd-fly",
        "xyz_orcl_usd_fly_mm",
    )


def test_plain_pair():
    n = pair_names("SOL-USDT")
    assert (n.base, n.quote, n.issuer) == ("SOL", "USDT", "")
    assert n.hl_coin == ""  # nothing to ask Hyperliquid for
    assert (n.slug, n.bot_name, n.config_name) == (
        "sol-usdt",
        "sol-usdt-fly",
        "sol_usdt_fly_mm",
    )


def test_the_whole_pair_names_the_bot():
    """One token on two quotes is two markets. Naming both after the base
    would point the fly at one book\'s P&L while updating the other\'s config."""
    assert pair_names("BTC-USDT").bot_name != pair_names("BTC-USDC").bot_name
    assert pair_names("XYZ:ORCL-USD").bot_name != pair_names("ABC:ORCL-USD").bot_name


@pytest.mark.parametrize(
    "bad", ["sol-usdt", "SOLUSDT", "XYZ:-USD", ":SOL-USDT", "SOL-", "-USDT", ""]
)
def test_bad_pairs(bad):
    with pytest.raises(ValueError):
        pair_names(bad)


def test_parse_pairs():
    assert parse_pairs(" SOL-USDT, BTC-USDT ") == ["SOL-USDT", "BTC-USDT"]
    with pytest.raises(ValueError):
        parse_pairs("")
    with pytest.raises(ValueError):
        parse_pairs("SOL-USDT,SOL-USDT")
    with pytest.raises(ValueError):
        parse_pairs("A-USDT,B-USDT,C-USDT,D-USDT")
