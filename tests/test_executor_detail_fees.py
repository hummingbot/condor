"""The executor detail view must report LP fee *income*, not the tx fee.

An LP executor fills ``cum_fees_quote`` with what the open/close transactions
cost on-chain, and puts the fee income it actually earned in
``custom_info.fees_earned_quote``. Reading the first as "Cumulative Fees" showed
a live DJT-USDC band as $0.000023 when it had earned $0.074624 — three orders of
magnitude off, on the one number that says whether a band is worth keeping.

The detail formatter prefers ``fees_earned_quote`` when the executor carries it
and falls back to ``cum_fees_quote`` otherwise, since a CEX executor's
``cum_fees_quote`` is its real cumulative fee and has no custom_info twin.
"""

from mcp_servers.hummingbot_api.formatters.executors import format_executor_detail

_TX_FEE = 0.000022815
_EARNED = 0.0746235531801429


def _lp(custom_info):
    return {
        "id": "DvpnJsPK",
        "type": "lp_executor",
        "status": "RUNNING",
        "connector_name": "solana-mainnet-beta",
        "trading_pair": "DJT-USDC",
        "cum_fees_quote": _TX_FEE,
        "custom_info": custom_info,
    }


def test_lp_detail_shows_earned_fees_not_the_transaction_fee():
    out = format_executor_detail(_lp({"fees_earned_quote": _EARNED, "tx_fee": _TX_FEE}))
    assert "Cumulative Fees: $0.074624" in out
    assert "$0.000023" not in out


def test_a_band_that_earned_nothing_shows_zero_not_its_tx_fee():
    out = format_executor_detail(_lp({"fees_earned_quote": 0.0, "tx_fee": _TX_FEE}))
    assert "Cumulative Fees: $0.000000" in out
    assert "$0.000023" not in out


def test_an_executor_without_the_earned_field_falls_back_to_cum_fees_quote():
    out = format_executor_detail(
        {
            "id": "y",
            "type": "position_executor",
            "status": "RUNNING",
            "cum_fees_quote": 1.23,
        }
    )
    assert "Cumulative Fees: $1.23" in out


def test_an_executor_with_neither_field_prints_no_fee_line():
    out = format_executor_detail(
        {"id": "z", "type": "position_executor", "status": "RUNNING"}
    )
    assert "Cumulative Fees" not in out
