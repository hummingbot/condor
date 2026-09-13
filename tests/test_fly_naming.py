"""Derived names for a HIP-3 pair, and the pairs list rules."""

import pytest

from condor.fly.naming import pair_names, parse_pairs


def test_pair_names():
    n = pair_names("XYZ:DRAM-USD")
    assert (n.issuer, n.token, n.coin) == ("xyz", "DRAM", "xyz:DRAM")
    assert (n.bot_name, n.config_name) == ("dram-fly", "dram_fly_mm")


@pytest.mark.parametrize(
    "bad", ["xyz:dram-usd", "DRAM-USD", "XYZ:DRAM", "XYZ:-USD", ":DRAM-USD"]
)
def test_bad_pairs(bad):
    with pytest.raises(ValueError):
        pair_names(bad)


def test_parse_pairs():
    assert parse_pairs(" XYZ:A-USD, XYZ:B-USD ") == ["XYZ:A-USD", "XYZ:B-USD"]
    with pytest.raises(ValueError):
        parse_pairs("")
    with pytest.raises(ValueError):
        parse_pairs("XYZ:A-USD,XYZ:A-USD")
    with pytest.raises(ValueError):
        parse_pairs("XYZ:A-USD,XYZ:B-USD,XYZ:C-USD,XYZ:D-USD")
