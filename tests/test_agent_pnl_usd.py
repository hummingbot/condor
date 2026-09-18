"""Agent PnL is restated in USD per controller, before anything is summed.

Controllers report in their market's quote currency. A BTC-BRL agent summed
those as-is and the portfolio strip showed R$9,412 as "+$9,412.54" next to the
bots card's correctly converted +$1,906. Each controller is converted at its own
quote's rate, so a bot or session mixing BRL and USDT markets still adds up.
"""

import asyncio
from types import SimpleNamespace

from condor.fetchers import bot_performance as bp
from condor.quote_conversion import QuoteRates

BRL = 0.2
RATES = {"BRL": BRL, "USDT": 1.0}


def _snap(bot, cid, pair, realized, unrealized, volume, fees=0.0):
    positions = (
        [
            {
                "trading_pair": pair,
                "realized_pnl_quote": realized,
                "unrealized_pnl_quote": unrealized,
                "volume_traded_quote": volume,
                "cum_fees_quote": fees,
            }
        ]
        if pair
        else []
    )
    return {
        "bot_name": bot,
        "controller_id": cid,
        "timestamp": "2026-09-18T00:00:00+00:00",
        "performance": {
            "realized_pnl_quote": realized,
            "unrealized_pnl_quote": unrealized,
            "volume_traded": volume,
            "positions_summary": positions,
        },
    }


def test_controllers_carry_the_pair_from_their_positions():
    agg = bp._aggregate_by_bot([_snap("b", "c", "BTC-BRL", 10, 0, 100)])
    (ctrl,) = agg["b"]["controllers"]
    assert ctrl["trading_pair"] == "BTC-BRL"
    assert ctrl["quote"] == "BRL"


def test_mixed_quotes_are_converted_before_the_bot_total():
    agg = bp._aggregate_by_bot(
        [
            _snap("b", "brl", "BTC-BRL", 100, 50, 1000, fees=5),
            _snap("b", "usdt", "BTC-USDT", 10, 5, 200, fees=1),
        ]
    )
    usd = bp.restate_universe_in_usd(agg, RATES)["b"]
    assert usd["realized_pnl_quote"] == 100 * BRL + 10
    assert usd["unrealized_pnl_quote"] == 50 * BRL + 5
    assert usd["global_pnl_quote"] == 150 * BRL + 15
    assert usd["volume_traded"] == 1000 * BRL + 200
    assert usd["cum_fees_quote"] == 5 * BRL + 1
    assert usd["controllers"][0]["realized_pnl_quote"] == 100 * BRL
    # Position rows stay in quote: the dashboard converts them by their pair.
    brl_pos = usd["controllers"][0]["positions_summary"][0]
    assert brl_pos["unrealized_pnl_quote"] == 50
    # The shared snapshot aggregate is not mutated.
    assert agg["b"]["realized_pnl_quote"] == 110


def test_a_flat_controller_takes_its_siblings_quote():
    agg = bp._aggregate_by_bot(
        [
            _snap("b", "open", "BTC-BRL", 10, 0, 0),
            _snap("b", "flat", "", 40, 0, 0),
        ]
    )
    usd = bp.restate_universe_in_usd(agg, RATES)["b"]
    assert usd["realized_pnl_quote"] == 50 * BRL


def test_history_merge_converts_each_controller_at_its_own_rate():
    series = {
        "brl": ("BRL", [(1.0, (100.0, 1000.0, 2.0, 10.0))]),
        "usdt": ("USDT", [(1.0, (10.0, 100.0, 1.0, 1.0))]),
        "flat": ("", [(1.0, (5.0, 0.0, 0.0, 0.0))]),
    }
    ((_, realized, volume, trades, fees),) = bp.merge_controller_series(series, RATES)
    # "flat" never named a pair and takes the first sibling's quote (BRL).
    assert realized == 100 * BRL + 10 + 5 * BRL
    assert volume == 1000 * BRL + 100
    assert trades == 3.0  # counts are not money
    assert fees == 10 * BRL + 1
    # No rates: quote units, as before.
    ((_, raw, *_),) = bp.merge_controller_series(series)
    assert raw == 115.0


def test_base_histories_come_back_in_usd(monkeypatch):
    rows = [
        {
            "timestamp": "2026-09-18T00:00:00+00:00",
            "controller_id": "c",
            "performance": {
                "realized_pnl_quote": 0.0,
                "volume_traded": 0.0,
                "positions_summary": [{"trading_pair": "BTC-BRL"}],
            },
        },
        {
            "timestamp": "2026-09-18T01:00:00+00:00",
            "controller_id": "c",
            "performance": {"realized_pnl_quote": 500.0, "volume_traded": 9000.0},
        },
    ]

    class _History:
        async def get_controller_performance_history(self, **kw):
            return {"data": rows if not kw.get("cursor") else []}

    asked: list[set] = []

    async def _rates(client, quotes):
        asked.append(set(quotes))
        return QuoteRates(RATES, True)

    monkeypatch.setattr(bp, "resolve_client_usd_rates", _rates)
    client = SimpleNamespace(bot_orchestration=_History())
    hist = asyncio.run(
        bp.fetch_base_histories(client, {}, ["bot"], 0.0, 1e10, extra_names=["bot"])
    )
    ((first, last),) = hist["bot"]
    assert asked == [{"BRL"}]
    assert last[1] == 500 * BRL
    assert last[2] == 9000 * BRL
    realized, *_ = bp.slice_history(hist["bot"], first[0], last[0] + 1)
    assert realized == 500 * BRL
