"""Characterization tests for condor.archived_pnl.calculate_pnl_from_trades.

These pin the EXACT current numeric output of both PnL modes (average-cost /
NIL and OPEN/CLOSE position tracking) over a representative fixture set,
including the edge cases where the two modes deliberately disagree:

* an unmatched SELL in average-cost mode charges its fee to ``total_pnl`` but
  NOT to ``pnl_by_pair`` (so ``total_pnl != sum(pnl_by_pair.values())``),
* an unmatched CLOSE in OPEN/CLOSE mode charges nothing at all,
* average-cost clamps a sell to the inventory on hand, OPEN/CLOSE realizes the
  full trade amount even when it exceeds the open position.

The goldens below were captured from the implementation and must not be edited
to make a refactor pass: they are the contract.
"""

import pytest

from condor.archived_pnl import (
    _calculate_pnl_average_cost,
    _calculate_pnl_open_close,
    _timestamp_sort_key,
    calculate_pnl_from_trades,
    parse_timestamp,
)


def _trade(ts, pair, side, position, price, amount, fee):
    return {
        "timestamp": ts,
        "trading_pair": pair,
        "trade_type": side,
        "position": position,
        "price": price,
        "amount": amount,
        "trade_fee_in_quote": fee,
    }


SCENARIOS: dict[str, list[dict]] = {
    "empty": [],
    # ---- average-cost / NIL mode -------------------------------------
    "nil_buy_sell_full": [
        _trade("2026-01-01T00:00:00", "SOL-USDC", "BUY", "NIL", 100.0, 10.0, 0.5),
        _trade("2026-01-01T00:01:00", "SOL-USDC", "SELL", "NIL", 110.0, 10.0, 0.55),
    ],
    "nil_partial_and_oversell": [
        _trade("2026-01-01T00:00:00", "SOL-USDC", "BUY", "NIL", 100.0, 10.0, 0.1),
        _trade("2026-01-01T00:01:00", "SOL-USDC", "BUY", "NIL", 110.0, 10.0, 0.1),
        _trade("2026-01-01T00:02:00", "SOL-USDC", "SELL", "NIL", 120.0, 5.0, 0.2),
        _trade("2026-01-01T00:03:00", "SOL-USDC", "SELL", "NIL", 90.0, 20.0, 0.3),
        _trade("2026-01-01T00:04:00", "SOL-USDC", "SELL", "NIL", 100.0, 1.0, 0.05),
    ],
    "nil_unmatched_sell_first": [
        _trade("2026-01-01T00:00:00", "SOL-USDC", "SELL", "NIL", 100.0, 5.0, 0.25),
        _trade("2026-01-01T00:01:00", "SOL-USDC", "BUY", "NIL", 100.0, 5.0, 0.1),
    ],
    "nil_multi_pair_unsorted": [
        _trade("2026-01-01T00:03:00", "ETH-USDC", "SELL", "NIL", 2100.0, 1.0, 0.9),
        _trade("2026-01-01T00:00:00", "SOL-USDC", "BUY", "NIL", 100.0, 10.0, 0.4),
        _trade("2026-01-01T00:02:00", "ETH-USDC", "BUY", "NIL", 2000.0, 2.0, 1.2),
        _trade("2026-01-01T00:01:00", "SOL-USDC", "SELL", "NIL", 105.0, 4.0, 0.2),
    ],
    "nil_zero_amount_and_price": [
        _trade("2026-01-01T00:00:00", "SOL-USDC", "BUY", "NIL", 0.0, 0.0, 0.01),
        _trade("2026-01-01T00:01:00", "SOL-USDC", "SELL", "NIL", 0.0, 0.0, 0.02),
    ],
    "nil_missing_fields": [
        {"position": "NIL", "trade_type": "BUY"},
        {"position": "NIL", "trade_type": "SELL"},
        {"position": "NIL", "trade_type": "SELL"},
    ],
    "nil_none_timestamp": [
        {
            "timestamp": None,
            "trading_pair": "SOL-USDC",
            "trade_type": "BUY",
            "position": "NIL",
            "price": 100.0,
            "amount": 1.0,
            "trade_fee_in_quote": 0.1,
        },
    ],
    "nil_unparseable_timestamps": [
        _trade("2026-01-01T00:00:00", "SOL-USDC", "BUY", "NIL", 100.0, 4.0, 0.1),
        _trade("", "SOL-USDC", "SELL", "NIL", 110.0, 2.0, 0.1),
        _trade("garbage", "SOL-USDC", "SELL", "NIL", 120.0, 2.0, 0.1),
    ],
    "nil_unknown_trade_type": [
        _trade("2026-01-01T00:00:00", "SOL-USDC", "", "NIL", 100.0, 3.0, 0.1),
        _trade("2026-01-01T00:01:00", "SOL-USDC", "UNKNOWN", "NIL", 100.0, 3.0, 0.1),
        _trade("2026-01-01T00:02:00", "SOL-USDC", "buy", "NIL", 100.0, 2.0, 0.1),
        _trade("2026-01-01T00:03:00", "SOL-USDC", "sell", "NIL", 120.0, 2.0, 0.1),
    ],
    "nil_epoch_timestamps": [
        _trade(1735689600, "SOL-USDC", "BUY", "NIL", 100.0, 1.0, 0.1),
        _trade(1735689660000, "SOL-USDC", "SELL", "NIL", 130.0, 1.0, 0.1),
    ],
    # CORR-171: an explicit null next to a number used to raise TypeError and
    # take the whole report down. Null and missing both sort as 0, ahead of
    # any real epoch, so the deliberately shuffled input reorders to BUY,
    # BUY, SELL.
    "nil_null_missing_and_epoch": [
        _trade(1735689660, "SOL-USDC", "SELL", "NIL", 130.0, 4.0, 0.2),
        _trade(None, "SOL-USDC", "BUY", "NIL", 100.0, 2.0, 0.1),
        {
            "trading_pair": "SOL-USDC",
            "trade_type": "BUY",
            "position": "NIL",
            "price": 110.0,
            "amount": 2.0,
            "trade_fee_in_quote": 0.1,
        },
    ],
    # ---- OPEN/CLOSE mode ---------------------------------------------
    "oc_long_full": [
        _trade("2026-01-01T00:00:00", "SOL-USDT", "BUY", "OPEN", 100.0, 10.0, 0.5),
        _trade("2026-01-01T00:01:00", "SOL-USDT", "SELL", "CLOSE", 110.0, 10.0, 0.55),
    ],
    "oc_short_full": [
        _trade("2026-01-01T00:00:00", "SOL-USDT", "SELL", "OPEN", 100.0, 10.0, 0.5),
        _trade("2026-01-01T00:01:00", "SOL-USDT", "BUY", "CLOSE", 90.0, 10.0, 0.45),
    ],
    "oc_partial_then_over_close": [
        _trade("2026-01-01T00:00:00", "SOL-USDT", "BUY", "OPEN", 100.0, 10.0, 0.5),
        _trade("2026-01-01T00:01:00", "SOL-USDT", "SELL", "CLOSE", 110.0, 4.0, 0.2),
        _trade("2026-01-01T00:02:00", "SOL-USDT", "SELL", "CLOSE", 120.0, 20.0, 0.9),
        _trade("2026-01-01T00:03:00", "SOL-USDT", "SELL", "CLOSE", 130.0, 1.0, 0.05),
    ],
    "oc_close_no_position": [
        _trade("2026-01-01T00:00:00", "SOL-USDT", "SELL", "CLOSE", 100.0, 5.0, 0.25),
        _trade("2026-01-01T00:01:00", "SOL-USDT", "BUY", "OPEN", 100.0, 5.0, 0.1),
    ],
    "oc_reopen_after_full_close": [
        _trade("2026-01-01T00:00:00", "SOL-USDT", "BUY", "OPEN", 100.0, 5.0, 0.1),
        _trade("2026-01-01T00:01:00", "SOL-USDT", "SELL", "CLOSE", 105.0, 5.0, 0.1),
        _trade("2026-01-01T00:02:00", "SOL-USDT", "SELL", "OPEN", 110.0, 5.0, 0.1),
        _trade("2026-01-01T00:03:00", "SOL-USDT", "BUY", "CLOSE", 100.0, 5.0, 0.1),
    ],
    "oc_zero_amount_open": [
        _trade("2026-01-01T00:00:00", "SOL-USDT", "BUY", "OPEN", 100.0, 0.0, 0.1),
        _trade("2026-01-01T00:01:00", "SOL-USDT", "SELL", "CLOSE", 110.0, 3.0, 0.2),
    ],
    "oc_mixed_with_nil_rows": [
        _trade("2026-01-01T00:00:00", "SOL-USDT", "BUY", "NIL", 100.0, 2.0, 0.1),
        _trade("2026-01-01T00:01:00", "SOL-USDT", "BUY", "OPEN", 100.0, 10.0, 0.5),
        _trade("2026-01-01T00:02:00", "SOL-USDT", "SELL", "CLOSE", 110.0, 10.0, 0.55),
    ],
    "oc_missing_position_field": [
        {
            "timestamp": "2026-01-01T00:00:00",
            "trading_pair": "SOL-USDT",
            "trade_type": "BUY",
            "price": 100.0,
            "amount": 2.0,
            "trade_fee_in_quote": 0.1,
        },
        {
            "timestamp": "2026-01-01T00:01:00",
            "trading_pair": "SOL-USDT",
            "trade_type": "SELL",
            "price": 110.0,
            "amount": 2.0,
            "trade_fee_in_quote": 0.11,
        },
    ],
    "oc_multi_pair_unsorted": [
        _trade("2026-01-01T00:03:00", "ETH-USDT", "BUY", "CLOSE", 1900.0, 1.0, 0.8),
        _trade("2026-01-01T00:00:00", "SOL-USDT", "BUY", "OPEN", 100.0, 10.0, 0.4),
        _trade("2026-01-01T00:02:00", "ETH-USDT", "SELL", "OPEN", 2000.0, 1.0, 0.7),
        _trade("2026-01-01T00:01:00", "SOL-USDT", "SELL", "CLOSE", 105.0, 4.0, 0.2),
    ],
}

# Which mode each scenario must dispatch to (None = empty short-circuit).
MODES: dict[str, str] = {
    "empty": "none",
    "nil_buy_sell_full": "avg",
    "nil_partial_and_oversell": "avg",
    "nil_unmatched_sell_first": "avg",
    "nil_multi_pair_unsorted": "avg",
    "nil_zero_amount_and_price": "avg",
    "nil_missing_fields": "avg",
    "nil_none_timestamp": "avg",
    "nil_unparseable_timestamps": "avg",
    "nil_unknown_trade_type": "avg",
    "nil_epoch_timestamps": "avg",
    "nil_null_missing_and_epoch": "avg",
    "oc_long_full": "oc",
    "oc_short_full": "oc",
    "oc_partial_then_over_close": "oc",
    "oc_close_no_position": "oc",
    "oc_reopen_after_full_close": "oc",
    "oc_zero_amount_open": "oc",
    "oc_mixed_with_nil_rows": "oc",
    "oc_missing_position_field": "oc",
    "oc_multi_pair_unsorted": "oc",
}

GOLDEN: dict[str, dict] = {
    "empty": {
        "total_pnl": 0,
        "total_fees": 0,
        "total_volume": 0,
        "pnl_by_pair": {},
        "cumulative": [],
    },
    "nil_buy_sell_full": {
        "total_pnl": 99.45,
        "total_fees": 1.05,
        "total_volume": 2100.0,
        "pnl_by_pair": {
            "SOL-USDC": 99.45,
        },
        "cumulative": [
            (0.0, "SOL-USDC"),
            (99.45, "SOL-USDC"),
        ],
    },
    "nil_partial_and_oversell": {
        "total_pnl": -150.55,
        "total_fees": 0.75,
        "total_volume": 4600.0,
        "pnl_by_pair": {
            "SOL-USDC": -150.5,
        },
        "cumulative": [
            (0.0, "SOL-USDC"),
            (0.0, "SOL-USDC"),
            (74.8, "SOL-USDC"),
            (-150.5, "SOL-USDC"),
            (-150.55, "SOL-USDC"),
        ],
    },
    "nil_unmatched_sell_first": {
        "total_pnl": -0.25,
        "total_fees": 0.35,
        "total_volume": 1000.0,
        "pnl_by_pair": {},
        "cumulative": [
            (-0.25, "SOL-USDC"),
            (-0.25, "SOL-USDC"),
        ],
    },
    "nil_multi_pair_unsorted": {
        "total_pnl": 118.89999999999999,
        "total_fees": 2.7,
        "total_volume": 7520.0,
        "pnl_by_pair": {
            "SOL-USDC": 19.8,
            "ETH-USDC": 99.1,
        },
        "cumulative": [
            (0.0, "SOL-USDC"),
            (19.8, "SOL-USDC"),
            (19.8, "ETH-USDC"),
            (118.89999999999999, "ETH-USDC"),
        ],
    },
    "nil_zero_amount_and_price": {
        "total_pnl": -0.02,
        "total_fees": 0.03,
        "total_volume": 0.0,
        "pnl_by_pair": {},
        "cumulative": [
            (0.0, "SOL-USDC"),
            (-0.02, "SOL-USDC"),
        ],
    },
    "nil_missing_fields": {
        "total_pnl": 0.0,
        "total_fees": 0.0,
        "total_volume": 0.0,
        "pnl_by_pair": {},
        # CORR-181: a missing timestamp is an absent one, like an explicit
        # null - it charts nothing instead of a point at the Unix epoch
        "cumulative": [],
    },
    "nil_none_timestamp": {
        "total_pnl": 0.0,
        "total_fees": 0.1,
        "total_volume": 100.0,
        "pnl_by_pair": {},
        "cumulative": [],
    },
    "nil_unparseable_timestamps": {
        "total_pnl": 39.8,
        "total_fees": 0.30000000000000004,
        "total_volume": 860.0,
        "pnl_by_pair": {
            "SOL-USDC": 39.9,
        },
        "cumulative": [
            (-0.1, "SOL-USDC"),
        ],
    },
    "nil_unknown_trade_type": {
        "total_pnl": 39.9,
        "total_fees": 0.4,
        "total_volume": 1040.0,
        "pnl_by_pair": {
            "SOL-USDC": 39.9,
        },
        "cumulative": [
            (0.0, "SOL-USDC"),
            (0.0, "SOL-USDC"),
            (0.0, "SOL-USDC"),
            (39.9, "SOL-USDC"),
        ],
    },
    "nil_epoch_timestamps": {
        "total_pnl": 29.9,
        "total_fees": 0.2,
        "total_volume": 230.0,
        "pnl_by_pair": {
            "SOL-USDC": 29.9,
        },
        "cumulative": [
            (0.0, "SOL-USDC"),
            (29.9, "SOL-USDC"),
        ],
    },
    "nil_null_missing_and_epoch": {
        "total_pnl": 99.8,
        "total_fees": 0.4,
        "total_volume": 940.0,
        "pnl_by_pair": {
            "SOL-USDC": 99.8,
        },
        # CORR-181: neither the null-timestamp BUY nor the missing-timestamp
        # one charts a point; their PnL is carried by the next dated trade
        "cumulative": [
            (99.8, "SOL-USDC"),
        ],
    },
    "oc_long_full": {
        "total_pnl": 99.45,
        "total_fees": 1.05,
        "total_volume": 2100.0,
        "pnl_by_pair": {
            "SOL-USDT": 99.45,
        },
        "cumulative": [
            (0.0, "SOL-USDT"),
            (99.45, "SOL-USDT"),
        ],
    },
    "oc_short_full": {
        "total_pnl": 99.55,
        "total_fees": 0.95,
        "total_volume": 1900.0,
        "pnl_by_pair": {
            "SOL-USDT": 99.55,
        },
        "cumulative": [
            (0.0, "SOL-USDT"),
            (99.55, "SOL-USDT"),
        ],
    },
    "oc_partial_then_over_close": {
        "total_pnl": 438.90000000000003,
        "total_fees": 1.6500000000000001,
        "total_volume": 3970.0,
        "pnl_by_pair": {
            "SOL-USDT": 438.90000000000003,
        },
        "cumulative": [
            (0.0, "SOL-USDT"),
            (39.8, "SOL-USDT"),
            (438.90000000000003, "SOL-USDT"),
            (438.90000000000003, "SOL-USDT"),
        ],
    },
    "oc_close_no_position": {
        "total_pnl": 0.0,
        "total_fees": 0.35,
        "total_volume": 1000.0,
        "pnl_by_pair": {},
        "cumulative": [
            (0.0, "SOL-USDT"),
            (0.0, "SOL-USDT"),
        ],
    },
    "oc_reopen_after_full_close": {
        "total_pnl": 74.8,
        "total_fees": 0.4,
        "total_volume": 2075.0,
        "pnl_by_pair": {
            "SOL-USDT": 74.8,
        },
        "cumulative": [
            (0.0, "SOL-USDT"),
            (24.9, "SOL-USDT"),
            (24.9, "SOL-USDT"),
            (74.8, "SOL-USDT"),
        ],
    },
    "oc_zero_amount_open": {
        "total_pnl": 0.0,
        "total_fees": 0.30000000000000004,
        "total_volume": 330.0,
        "pnl_by_pair": {},
        "cumulative": [
            (0.0, "SOL-USDT"),
            (0.0, "SOL-USDT"),
        ],
    },
    "oc_mixed_with_nil_rows": {
        "total_pnl": 99.45,
        "total_fees": 1.15,
        "total_volume": 2300.0,
        "pnl_by_pair": {
            "SOL-USDT": 99.45,
        },
        "cumulative": [
            (0.0, "SOL-USDT"),
            (0.0, "SOL-USDT"),
            (99.45, "SOL-USDT"),
        ],
    },
    "oc_missing_position_field": {
        "total_pnl": 0.0,
        "total_fees": 0.21000000000000002,
        "total_volume": 420.0,
        "pnl_by_pair": {},
        "cumulative": [
            (0.0, "SOL-USDT"),
            (0.0, "SOL-USDT"),
        ],
    },
    "oc_multi_pair_unsorted": {
        "total_pnl": 119.0,
        "total_fees": 2.1,
        "total_volume": 5320.0,
        "pnl_by_pair": {
            "SOL-USDT": 19.8,
            "ETH-USDT": 99.2,
        },
        "cumulative": [
            (0.0, "SOL-USDT"),
            (19.8, "SOL-USDT"),
            (19.8, "ETH-USDT"),
            (119.0, "ETH-USDT"),
        ],
    },
}


def _expected_timestamps(trades):
    """Timestamps the cumulative series must carry, derived independently."""
    stamps = [
        parse_timestamp(t.get("timestamp"))
        for t in sorted(trades, key=_timestamp_sort_key)
    ]
    return [ts for ts in stamps if ts]


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_totals_match_golden(name):
    """The money numbers are byte-identical to the captured contract."""
    result = calculate_pnl_from_trades(SCENARIOS[name])
    expected = GOLDEN[name]

    assert result["total_pnl"] == expected["total_pnl"]
    assert result["total_fees"] == expected["total_fees"]
    assert result["total_volume"] == expected["total_volume"]
    assert result["pnl_by_pair"] == expected["pnl_by_pair"]
    # exact key set too - a spurious zero entry must not creep in
    assert list(result["pnl_by_pair"]) == list(expected["pnl_by_pair"])


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_cumulative_series_matches_golden(name):
    """The chart series keeps its exact length, order, values and pairs."""
    result = calculate_pnl_from_trades(SCENARIOS[name])
    expected = GOLDEN[name]["cumulative"]

    assert [(p["pnl"], p["pair"]) for p in result["cumulative_pnl"]] == expected
    assert [p["timestamp"] for p in result["cumulative_pnl"]] == _expected_timestamps(
        SCENARIOS[name]
    )
    assert all(set(p) == {"timestamp", "pnl", "pair"} for p in result["cumulative_pnl"])


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_result_shape_and_mode_dispatch(name):
    """Every scenario returns the same five keys via the expected mode."""
    trades = SCENARIOS[name]
    result = calculate_pnl_from_trades(trades)
    assert set(result) == {
        "total_pnl",
        "total_fees",
        "pnl_by_pair",
        "cumulative_pnl",
        "total_volume",
    }

    mode = MODES[name]
    if mode == "avg":
        assert result == _calculate_pnl_average_cost(trades)
    elif mode == "oc":
        assert result == _calculate_pnl_open_close(trades)


def test_unmatched_sell_charges_fee_to_total_but_not_to_pair():
    """Average-cost mode: an unmatched SELL leaks its fee into total_pnl only.

    This asymmetry is load-bearing behaviour, not an accident of the loop, and
    the OPEN/CLOSE mode deliberately does NOT do it (see the test below).
    """
    result = calculate_pnl_from_trades(SCENARIOS["nil_unmatched_sell_first"])
    assert result["total_pnl"] == -0.25
    assert result["pnl_by_pair"] == {}
    assert result["total_pnl"] != sum(result["pnl_by_pair"].values())


def test_unmatched_close_charges_nothing():
    """OPEN/CLOSE mode: a CLOSE with no position leaves running PnL untouched."""
    result = calculate_pnl_from_trades(SCENARIOS["oc_close_no_position"])
    assert result["total_pnl"] == 0.0
    assert result["pnl_by_pair"] == {}


def test_average_cost_clamps_sell_to_inventory():
    """Average-cost realizes at most the inventory on hand..."""
    trades = [
        _trade("2026-01-01T00:00:00", "S-U", "BUY", "NIL", 100.0, 10.0, 0.0),
        _trade("2026-01-01T00:01:00", "S-U", "SELL", "NIL", 110.0, 25.0, 0.0),
    ]
    # 10 units realized, not 25
    assert calculate_pnl_from_trades(trades)["total_pnl"] == 100.0


def test_open_close_realizes_full_amount_even_beyond_position():
    """...while OPEN/CLOSE realizes the whole trade amount. Deliberate divergence."""
    trades = [
        _trade("2026-01-01T00:00:00", "S-U", "BUY", "OPEN", 100.0, 10.0, 0.0),
        _trade("2026-01-01T00:01:00", "S-U", "SELL", "CLOSE", 110.0, 25.0, 0.0),
    ]
    # 25 units realized against a 10-unit position
    assert calculate_pnl_from_trades(trades)["total_pnl"] == 250.0


def test_empty_trades_short_circuits_with_integer_zeros():
    assert calculate_pnl_from_trades([]) == {
        "total_pnl": 0,
        "total_fees": 0,
        "pnl_by_pair": {},
        "cumulative_pnl": [],
        "total_volume": 0,
    }


def test_null_timestamp_next_to_a_number_does_not_raise():
    """CORR-171: one ``timestamp: None`` row must not kill the whole report."""
    trades = SCENARIOS["nil_null_missing_and_epoch"]
    result = calculate_pnl_from_trades(trades)  # used to raise TypeError
    assert result["total_pnl"] == 99.8

    # null and missing sort as 0, ahead of the epoch row, input order kept
    assert [t.get("price") for t in sorted(trades, key=_timestamp_sort_key)] == [
        100.0,
        110.0,
        130.0,
    ]


def test_sort_key_orders_each_shape_without_mixing_comparisons():
    """Homogeneous lists keep the ordering the naive sort gave them."""
    numeric = [{"timestamp": 3}, {"timestamp": 1}, {"timestamp": 2.5}]
    assert [t["timestamp"] for t in sorted(numeric, key=_timestamp_sort_key)] == [
        1,
        2.5,
        3,
    ]

    strings = [{"timestamp": "garbage"}, {"timestamp": ""}, {"timestamp": "2026-01-01"}]
    assert [t["timestamp"] for t in sorted(strings, key=_timestamp_sort_key)] == [
        "",
        "2026-01-01",
        "garbage",
    ]

    # every shape at once: numbers, then strings, then anything unorderable
    mixed = [
        {"timestamp": object()},
        {"timestamp": "2026-01-01"},
        {"timestamp": None},
        {"timestamp": 5},
        {},
    ]
    ordered = sorted(mixed, key=_timestamp_sort_key)
    assert [type(t.get("timestamp")).__name__ for t in ordered] == [
        "NoneType",  # explicit null -> 0
        "NoneType",  # missing -> 0
        "int",
        "str",
        "object",
    ]
