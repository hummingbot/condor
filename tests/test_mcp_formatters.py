"""What a result table shows is what the model trades on (MH-03, MH-04).

The tables used to round, compact and truncate: an order of ``0.015`` at
``0.00002345`` on ``1000PEPE-USDT`` rendered as ``0.01`` at ``0.0000`` on
``1000PEPE-USD``, a perpetual position named no exchange, and addresses another
tool takes as input were cut to ``7xKXtg2C...osgAsU``. Any close or resize
computed from such a table is wrong.

Pinned here: the exact amount, the exact price and the whole identifier survive,
and the columns a follow-up call needs are present.
"""

from mcp_servers.hummingbot_api.formatters import format_lp_positions_table

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
