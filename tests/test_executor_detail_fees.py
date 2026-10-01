"""The executor detail view must report LP fee *income*, not the tx fee.

An LP executor fills ``cum_fees_quote`` with what the open/close transactions
cost on-chain, and puts the fee income it actually earned in
``custom_info.fees_earned_quote``. Reading the first as "Cumulative Fees" showed
a live DJT-USDC band as $0.000023 when it had earned $0.074624 — three orders of
magnitude off, on the one number that says whether a band is worth keeping.

An LP executor therefore reads ``fees_earned_quote`` and nothing else: its
``cum_fees_quote`` is a cost, so it never backs the earned figure — when the
field is absent the income is simply unknown, not the tx fee. A non-LP executor
(CEX order, position, grid…) has no ``custom_info`` twin and its
``cum_fees_quote`` *is* its fee, so it reports that as "Fees Paid". The two
labels stay distinct so a client cannot read a paid fee as income.
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
    assert "Fees Earned: $0.074624" in out
    assert "$0.000023" not in out


def test_a_band_that_earned_nothing_shows_zero_not_its_tx_fee():
    out = format_executor_detail(_lp({"fees_earned_quote": 0.0, "tx_fee": _TX_FEE}))
    assert "Fees Earned: $0.000000" in out
    assert "$0.000023" not in out


def test_an_lp_executor_without_the_earned_field_prints_no_fee_line():
    out = format_executor_detail(_lp({"tx_fee": _TX_FEE}))
    assert "Fees Earned" not in out
    assert "Fees Paid" not in out
    assert "$0.000023" not in out


def test_a_non_lp_executor_reports_its_cum_fees_as_fees_paid():
    out = format_executor_detail(
        {
            "id": "y",
            "type": "position_executor",
            "status": "RUNNING",
            "cum_fees_quote": 1.23,
        }
    )
    assert "Fees Paid: $1.23" in out
    assert "Fees Earned" not in out


def test_an_executor_with_neither_field_prints_no_fee_line():
    out = format_executor_detail(
        {"id": "z", "type": "position_executor", "status": "RUNNING"}
    )
    assert "Fees Earned" not in out
    assert "Fees Paid" not in out
