"""Tests for local cross-rate resolution over the cached ticker pool."""

import pytest

from condor.fetchers.market_data import _normalize_tickers, _usd_rate
from condor.rates import find_rate, merge_price_pool, resolve_rates, unresolved

# Mirrors the docstring example in hummingbot's `find_rate`.
PRICES = {
    "HBOT-USDT": 100.0,
    "AAVE-USDT": 50.0,
    "USDT-GBP": 0.75,
    "BTC-USDT": 64000.0,
    "USDT-BRL": 5.0,
    "BTC-JPY": 9_600_000.0,
}


def test_direct_pair():
    assert find_rate(PRICES, "HBOT-USDT") == 100.0


def test_reverse_pair():
    assert find_rate(PRICES, "USDT-HBOT") == pytest.approx(1 / 100)


def test_same_token_is_one():
    assert find_rate(PRICES, "USDT-USDT") == 1.0


def test_usd_is_equivalent_to_usdt():
    assert find_rate(PRICES, "USD-USDT") == 1.0
    assert find_rate(PRICES, "BTC-USD") == 64000.0


def test_bridged_through_shared_quote():
    # HBOT-USDT / AAVE-USDT
    assert find_rate(PRICES, "HBOT-AAVE") == pytest.approx(100 / 50)
    assert find_rate(PRICES, "AAVE-HBOT") == pytest.approx(50 / 100)


def test_bridged_through_link_pair():
    # HBOT-USDT * USDT-GBP
    assert find_rate(PRICES, "HBOT-GBP") == pytest.approx(100 * 0.75)


def test_fiat_quote_resolves_by_inversion():
    # 1 BRL in USDT via the reversed USDT-BRL market
    assert find_rate(PRICES, "BRL-USDT") == pytest.approx(1 / 5.0)


def test_unresolvable_pairs_are_none():
    rates = resolve_rates(PRICES, ["BTC-USDT", "NOSUCH-USDT", "malformed"])
    assert rates["BTC-USDT"] == 64000.0
    assert rates["NOSUCH-USDT"] is None
    assert rates["malformed"] is None
    assert unresolved(rates) == ["NOSUCH-USDT", "malformed"]


def test_zero_prices_never_divide():
    assert find_rate({"USDT-HBOT": 0.0}, "HBOT-USDT") is None


def test_merge_prefers_the_more_liquid_market():
    merged = merge_price_pool(
        {
            "thin": {"BTC-USDT": {"price": 64100.0, "quote_volume": 1_000.0}},
            "deep": {"BTC-USDT": {"price": 64000.0, "quote_volume": 900_000.0}},
            "broken": {"ETH-USDT": {"price": 0.0, "quote_volume": 5.0}},
        }
    )
    assert merged["BTC-USDT"] == 64000.0
    # A zero price is not a usable rate source.
    assert "ETH-USDT" not in merged


def test_usd_rate_prices_quote_only_fiat():
    # JPY is never a base, so only the inverted BTC-JPY market can price it.
    assert _usd_rate("JPY", PRICES) == pytest.approx(64000.0 / 9_600_000.0)
    assert _usd_rate("BRL", PRICES) == pytest.approx(1 / 5.0)
    assert _usd_rate("USDC", PRICES) == 1.0  # stablecoin fallback, no market needed
    assert _usd_rate("NOSUCH", PRICES) is None


def test_normalize_tickers_adds_usd_volume():
    tickers, latest_ts = _normalize_tickers(
        {
            "BTC-USDT": {
                "price": 64000.0,
                "base_volume": 10.0,
                "quote_volume": 640_000.0,
                "timestamp": 100.0,
            },
            # Older API shape: single quote-denominated `volume`, no base volume.
            "ETH-BTC": {"price": 0.03, "volume": 30.0, "timestamp": 200.0},
            "SOL-NOSUCH": {"price": 5.0, "quote_volume": 100.0, "timestamp": 50.0},
        },
        PRICES,
    )

    assert tickers["BTC-USDT"]["usd_volume"] == pytest.approx(640_000.0)
    # 30 BTC of volume, priced through BTC-USDT
    assert tickers["ETH-BTC"]["usd_volume"] == pytest.approx(30 * 64000.0)
    assert tickers["ETH-BTC"]["base_volume"] == pytest.approx(30 / 0.03)
    # Unpriceable quote: the volume stays quote-denominated rather than faked
    assert tickers["SOL-NOSUCH"]["usd_volume"] is None
    assert latest_ts == 200.0
