"""A held position carries the executors it came from, so a pool page can place it.

On a DEX pool page the connector *is* the network, so connector + trading pair
puts one `solana-mainnet-beta` SOL-USDC hold on every Solana SOL-USDC pool —
Orca's page and Meteora's alike — and each reads as if the position were its own.

The API's own attribution is `executor_ids`, and an LP executor records the pool
it opened in. Passing the ids through is what lets the pool workspace tell a hold
that belongs to the pool on screen from one held in another pool of the same pair
(dropped), and from a hold no executor ties to any pool at all — a swap, whose
route the aggregator picked, which stays on the chart labelled "any pool".
"""

from condor.web.routes.positions import _normalize_position


def test_the_executors_behind_a_position_survive_normalization():
    row = _normalize_position(
        {
            "trading_pair": "SOL-USDC",
            "connector_name": "solana-mainnet-beta",
            "position_side": "LONG",
            "net_amount_base": 0.115990048,
            "buy_breakeven_price": 86.4017516622,
            "executor_count": 3,
            "executor_ids": ["exec-a", "exec-b", "exec-c"],
        },
        "executor",
        "master_account",
    )

    assert row["executor_ids"] == ["exec-a", "exec-b", "exec-c"]
    assert row["executor_count"] == 3


def test_a_position_without_executors_normalizes_to_an_empty_list():
    # Bot positions come from a controller's `positions_summary`, which carries
    # no ids. An absent list must not become `None`: the browser reads it as
    # "nothing ties this to a pool", not as a missing field to guess about.
    row = _normalize_position(
        {"trading_pair": "BTC-USDT", "connector_name": "binance"},
        "bot",
        "bot/main",
    )

    assert row["executor_ids"] == []


def test_an_upstream_that_answers_something_else_is_not_trusted():
    row = _normalize_position(
        {"trading_pair": "BTC-USDT", "executor_ids": "exec-a"},
        "executor",
        "master_account",
    )

    assert row["executor_ids"] == []
