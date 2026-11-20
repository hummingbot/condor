"""What a result table shows is what the model trades on (MH-03, MH-04).

The tables used to round, compact and truncate: an order of ``0.015`` at
``0.00002345`` on ``1000PEPE-USDT`` rendered as ``0.01`` at ``0.0000`` on
``1000PEPE-USD``, a perpetual position named no exchange, and addresses another
tool takes as input were cut to ``7xKXtg2C...osgAsU``. Any close or resize
computed from such a table is wrong.

Pinned here: the exact amount, the exact price and the whole identifier survive,
and the columns a follow-up call needs are present.
"""

import pytest

from mcp_servers.hummingbot_api.formatters import (
    format_exact,
    format_executor_detail,
    format_executors_table,
    format_lp_positions_table,
    format_orders_as_table,
    format_portfolio_as_table,
    format_positions_as_table,
    format_positions_held_table,
    format_usd,
)

POSITION_ADDRESS = "H4vD69DsraHjHyKvRwRPHVGe2aJkvAUaNK5tMif2CiNw"
POOL_ADDRESS = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"


# ---------------------------------------------------------------------------
# LP positions (portfolio overview)
# ---------------------------------------------------------------------------


def test_the_lp_table_carries_both_addresses_a_close_needs():
    """`manage_clmm(action="close")` takes the position AND the pool address, and
    the orphan listing sends the caller to this table to find them."""
    table = format_lp_positions_table(
        [
            {
                "connector": "pancakeswap-sol",
                "network": "solana-mainnet-beta",
                "trading_pair": "SOL-USDC",
                "lower_price": 130.5,
                "upper_price": 170.25,
                "position_address": POSITION_ADDRESS,
                "pool_address": POOL_ADDRESS,
            }
        ]
    )

    assert POSITION_ADDRESS in table
    assert POOL_ADDRESS in table
    assert "pancakeswap-sol" in table, "the connector is the close call's argument"
    assert "solana-mainnet-beta" in table
    assert "..." not in table


def test_lp_price_bounds_are_not_rounded_to_four_decimals():
    table = format_lp_positions_table(
        [
            {
                "connector": "meteora",
                "trading_pair": "BONK-SOL",
                "lower_price": 0.00000012345,
                "upper_price": "0.00000019",
                "position_address": POSITION_ADDRESS,
            }
        ]
    )

    assert "0.00000012345" in table
    assert "0.00000019" in table
    assert "0.0000 " not in table


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "rendered"),
    [
        (0.015, "0.015"),
        ("0.00002345", "0.00002345"),
        (0.00002345, "0.00002345"),
        (0.38421, "0.38421"),
        (12345678.9, "12345678.9"),
        (1234.5, "1234.5"),
        (60000.0, "60000"),
        (0.1 + 0.2, "0.3"),
        # Float noise on the low side still cleans up (0.7999999999999999).
        (0.7 + 0.1, "0.8"),
        # Never more than the value: a cut that would round up truncates.
        ("12345678.123456789", "12345678.1234"),
        (12345678.123456789, "12345678.1234"),
        ("-12345678.123456789", "-12345678.1234"),
        ("0.123456789012999", "0.123456789012"),
        # A whole number is not trimmed to twelve digits.
        (123456789012345, "123456789012345"),
        ("123456789012345", "123456789012345"),
        (-0.015, "-0.015"),
        (0, "0"),
        (1e-9, "0.000000001"),
        (None, "N/A"),
        ("n/a", "n/a"),
    ],
)
def test_an_amount_or_price_is_rendered_as_the_number_it_is(value, rendered):
    assert format_exact(value) == rendered


@pytest.mark.parametrize(
    ("value", "rendered"),
    [
        (12345.67, "12345.67"),
        (1234.5, "1234.50"),
        (2_549_843.14, "2549843.14"),
        (-0.0042, "-0.0042"),
        (0, "0.00"),
        (None, "N/A"),
    ],
)
def test_a_usd_value_is_never_compacted_to_k_or_m(value, rendered):
    assert format_usd(value) == rendered


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

ORDER = {
    "created_at": 1_700_000_000,
    "account_name": "master_account",
    "connector_name": "hyperliquid_perpetual",
    "trading_pair": "1000PEPE-USDT",
    "trade_type": "BUY",
    "order_type": "LIMIT_MAKER",
    "amount": 0.015,
    "price": 0.00002345,
    "filled_amount": 0.0075,
    "status": "PARTIALLY_FILLED",
    "client_order_id": "HBOT-B-1000PEPE-USDT-1700000000000001",
}


def _rows(table):
    """``[{column: cell}]`` — the table as a model would read it."""
    header, *lines = table.splitlines()
    columns = header.split(" | ")
    return [dict(zip(columns, line.split(" | "), strict=True)) for line in lines]


def test_an_order_row_keeps_its_exact_size_price_and_names():
    (row,) = _rows(format_orders_as_table([ORDER]))

    assert row["amount"] == "0.015"
    assert row["price"] == "0.00002345"
    assert row["filled"] == "0.0075"
    assert row["pair"] == "1000PEPE-USDT"
    assert row["type"] == "LIMIT_MAKER"
    assert row["status"] == "PARTIALLY_FILLED"


def test_an_order_row_names_its_venue_account_and_order_id():
    (row,) = _rows(format_orders_as_table([ORDER]))

    assert row["connector"] == "hyperliquid_perpetual"
    assert row["account"] == "master_account"
    assert row["order_id"] == "HBOT-B-1000PEPE-USDT-1700000000000001"


def test_a_missing_field_is_a_marked_cell_not_a_shifted_column():
    (row,) = _rows(format_orders_as_table([{"trading_pair": "BTC-USDT"}]))

    assert row["pair"] == "BTC-USDT"
    assert row["connector"] == "-"
    assert row["amount"] == "N/A"


# ---------------------------------------------------------------------------
# Perpetual positions
# ---------------------------------------------------------------------------


def test_a_perp_position_names_its_exchange_and_keeps_its_size():
    """With two perpetual venues, a row naming neither cannot be closed."""
    (row,) = _rows(
        format_positions_as_table(
            [
                {
                    "account_name": "master_account",
                    "connector_name": "binance_perpetual",
                    "trading_pair": "XRP-USDT",
                    "side": "LONG",
                    "amount": 0.015,
                    "entry_price": 0.38421,
                    "unrealized_pnl": -0.0042,
                    "leverage": 5,
                }
            ]
        )
    )

    assert row["connector"] == "binance_perpetual"
    assert row["account"] == "master_account"
    assert row["amount"] == "0.015"
    assert row["entry_price"] == "0.38421"
    assert row["unrealized_pnl"] == "-0.0042"


# ---------------------------------------------------------------------------
# Balances
# ---------------------------------------------------------------------------


def test_a_balance_row_is_not_compacted_and_names_the_whole_connector():
    (row,) = _rows(
        format_portfolio_as_table(
            {
                "master_account": {
                    "hyperliquid_perpetual": [
                        {
                            "token": "BONK",
                            "units": 12345678.9,
                            "available_units": 12000000.5,
                            "value": 12345.67,
                        }
                    ]
                }
            }
        )
    )

    assert row == {
        "account": "master_account",
        "connector": "hyperliquid_perpetual",
        "token": "BONK",
        "total": "12345678.9",
        "available": "12000000.5",
        "value_usd": "12345.67",
    }


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

EXECUTOR = {
    "id": "8mVfyZq3Yx2w1v9u7t5s3r1q9p7o5n3m1k9j7h5g3f1d",
    "type": "position_executor",
    "connector_name": "hyperliquid_perpetual",
    "trading_pair": "1000PEPE-USDT",
    "status": "RUNNING",
    "side": "BUY",
    "controller_id": "agent-7",
    "account_name": "master_account",
    "filled_amount_quote": 1234.5,
    "net_pnl_quote": -3.25,
    "timestamp": 1_700_000_000,
}


def test_an_executor_row_keeps_whole_names_and_says_who_owns_it():
    """The list tool's docstring promises "under which controller"."""
    (row,) = _rows(format_executors_table([EXECUTOR]))

    assert row["type"] == "position_executor"
    assert row["pair"] == "1000PEPE-USDT"
    assert row["connector"] == "hyperliquid_perpetual"
    assert row["controller_id"] == "agent-7"
    assert row["account"] == "master_account"
    assert row["volume"] == "1234.50"
    assert row["pnl"] == "-3.25"
    assert row["close_type"] == "-"


def test_an_executor_row_reads_pair_and_owner_from_its_config_when_not_top_level():
    executor = {
        "id": "exec-1",
        "type": "grid_executor",
        "status": "RUNNING",
        "config": {
            "connector_name": "binance",
            "trading_pair": "SOL-USDT",
            "controller_id": "main",
        },
    }

    (row,) = _rows(format_executors_table([executor]))

    assert (row["connector"], row["pair"], row["controller_id"]) == (
        "binance",
        "SOL-USDT",
        "main",
    )


def test_a_dca_ladder_is_readable_in_the_executor_detail():
    """``amounts_quote: [3 items]`` said nothing about what was deployed."""
    detail = format_executor_detail(
        {
            "id": "exec-1",
            "type": "dca_executor",
            "status": "RUNNING",
            "config": {
                "amounts_quote": [100, 100, 150],
                "prices": [50000.0, 48000.5, 0.00002345],
            },
        }
    )

    assert "amounts_quote: [100, 100, 150]" in detail
    assert "prices: [50000, 48000.5, 0.00002345]" in detail
    assert "items]" not in detail


def test_a_long_list_is_cut_with_its_true_length_stated():
    detail = format_executor_detail(
        {
            "id": "exec-1",
            "custom_info": {"grid_level_prices": [100 + i for i in range(250)]},
        }
    )

    line = next(row for row in detail.splitlines() if "grid_level_prices:" in row)
    assert "[100, 101, " in line
    assert "230 more (250 items)" in line


def test_a_position_held_keeps_its_exact_amount_and_connector():
    (row,) = _rows(
        format_positions_held_table(
            [
                {
                    "connector_name": "hyperliquid_perpetual",
                    "trading_pair": "1000PEPE-USDT",
                    "side": "BUY",
                    "net_amount_base": 0.115990048,
                    "buy_breakeven_price": 0.00002345,
                    "controller_id": "agent-7",
                }
            ]
        )
    )

    assert row["connector"] == "hyperliquid_perpetual"
    assert row["trading_pair"] == "1000PEPE-USDT"
    assert row["amount"] == "0.115990048"
    assert row["entry_price"] == "0.00002345"
    assert row["controller_id"] == "agent-7"


# ---------------------------------------------------------------------------
# The renderer
# ---------------------------------------------------------------------------


def test_no_table_spends_a_line_on_dashes():
    for table in (
        format_orders_as_table([ORDER]),
        format_executors_table([EXECUTOR]),
        format_positions_as_table([{"trading_pair": "BTC-USDT"}]),
    ):
        assert "----" not in table
        assert len(table.splitlines()) == 2
