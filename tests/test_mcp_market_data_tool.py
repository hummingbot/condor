"""``get_market_data``: the candle read that survives a dry run (CORR-625).

ARCH-308 deleted the candle, order book and funding tools because they answered
in a rendered table, leaving ``client.market_data.*`` inside ``run_code`` as the
only structured read. SEC-616 then refused ``run_code`` in dry-run for holding
the unrestricted API client, and SEC-626 refused ``manage_routines`` one door
over for the same reason — so a rehearsal could not read a candle at all.

This tool is the way back, and the two properties it has to keep are pinned
here: it answers in **rows** (or ARCH-308's objection returns), and it reaches
for nothing but the client's candle readers (or dry-run's promise is worth
nothing). The gate side of that — which actions a dry run may call, and what
happens to one nobody classified — lives in test_risk_gate.py.

The repo has no async test setup, so the coroutines are driven with
asyncio.run().
"""

import asyncio

import pytest

from mcp_servers.hummingbot_api.exceptions import ToolError
from mcp_servers.hummingbot_api.tools.market_data import (
    MARKET_DATA_ACTIONS,
    MAX_CANDLE_RECORDS,
    get_market_data,
)


class FakeMarketData:
    """Only the three readers the tool is allowed to reach.

    Anything else — an order, a swap, a gateway call — is an ``AttributeError``,
    which is the assertion this class exists to make.
    """

    def __init__(self, rows=None, connectors=None):
        self._rows = rows if rows is not None else []
        self._connectors = connectors or []
        self.calls = []

    async def get_candles(self, connector_name, trading_pair, interval, limit):
        self.calls.append(
            ("get_candles", connector_name, trading_pair, interval, limit)
        )
        return self._rows

    async def get_historical_candles(
        self, connector_name, trading_pair, interval, start_time=None, end_time=None
    ):
        self.calls.append(
            (
                "get_historical_candles",
                connector_name,
                trading_pair,
                interval,
                start_time,
                end_time,
            )
        )
        return {"data": self._rows}

    async def get_available_candle_connectors(self):
        self.calls.append(("get_available_candle_connectors",))
        return self._connectors


class FakeClient:
    """A client with a market data reader and nothing else at all."""

    def __init__(self, market_data):
        self.market_data = market_data

    def __getattr__(self, name):
        raise AssertionError(
            f"get_market_data reached client.{name} — it may only read candles"
        )


ROWS = [
    {
        "timestamp": 1_757_000_000,
        "open": 1,
        "high": 2,
        "low": 0.5,
        "close": 1.5,
        "volume": 100,
    },
    [1_757_003_600, 1.5, 2.5, 1.0, 2.0, 200],
]


def _run(**kwargs):
    md = FakeMarketData(**kwargs.pop("fake", {}))
    result = asyncio.run(get_market_data(client=FakeClient(md), **kwargs))
    return result, md


def test_candles_come_back_as_rows_of_floats_and_not_a_table():
    """ARCH-308's objection was prose. Nothing read here is read twice."""
    result, _ = _run(
        action="candles",
        connector_name="binance",
        trading_pair="SOL-USDC",
        fake={"rows": ROWS},
    )

    assert result["count"] == 2
    assert result["candles"] == [
        {
            "timestamp": 1_757_000_000.0,
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 100.0,
        },
        {
            "timestamp": 1_757_003_600.0,
            "open": 1.5,
            "high": 2.5,
            "low": 1.0,
            "close": 2.0,
            "volume": 200.0,
        },
    ]
    assert all(isinstance(v, float) for v in result["candles"][0].values())


def test_a_plain_candle_read_asks_for_a_window_and_not_a_range():
    """No ``start_time`` means "the most recent N", which is the fetcher's
    fallback rung, not a ranged query with an invented start."""
    _, md = _run(
        action="candles",
        connector_name="binance",
        trading_pair="SOL-USDC",
        interval="1h",
        max_records=168,
        fake={"rows": ROWS},
    )

    assert md.calls == [("get_candles", "binance", "SOL-USDC", "1h", 168)]


def test_a_historical_read_passes_the_range_through():
    _, md = _run(
        action="historical_candles",
        connector_name="binance",
        trading_pair="SOL-USDC",
        interval="4h",
        start_time=1_757_000_000,
        end_time=1_757_600_000,
        fake={"rows": ROWS},
    )

    assert md.calls == [
        (
            "get_historical_candles",
            "binance",
            "SOL-USDC",
            "4h",
            1_757_000_000,
            1_757_600_000,
        )
    ]


def test_the_row_count_is_capped_so_one_read_cannot_eat_a_tick():
    _, md = _run(
        action="candles",
        connector_name="binance",
        trading_pair="SOL-USDC",
        max_records=50_000,
        fake={"rows": ROWS},
    )

    assert md.calls[0][-1] == MAX_CANDLE_RECORDS


def test_connectors_answers_which_venues_serve_ohlcv_at_all():
    """Most DEX connectors do not, and asking is cheaper than a failed read."""
    result, md = _run(action="connectors", fake={"connectors": ["binance", "kucoin"]})

    assert result == {"action": "connectors", "connectors": ["binance", "kucoin"]}
    assert md.calls == [("get_available_candle_connectors",)]


@pytest.mark.parametrize(
    "kwargs,missing",
    [
        ({"action": "candles", "trading_pair": "SOL-USDC"}, "connector_name"),
        ({"action": "candles", "connector_name": "binance"}, "trading_pair"),
        (
            {
                "action": "historical_candles",
                "connector_name": "binance",
                "trading_pair": "SOL-USDC",
            },
            "start_time",
        ),
    ],
)
def test_a_read_missing_what_it_needs_says_which_argument(kwargs, missing):
    with pytest.raises(ToolError, match=missing):
        _run(**kwargs)


def test_an_action_the_tool_does_not_have_is_an_error_not_an_empty_read():
    """The gate refuses an unclassified action in dry-run; in every other mode
    the tool itself has to say so rather than answer with nothing."""
    with pytest.raises(ToolError, match="unknown action"):
        _run(action="subscribe", connector_name="binance", trading_pair="SOL-USDC")


def test_every_action_the_tool_advertises_actually_works():
    """``MARKET_DATA_ACTIONS`` is what ``danger.py``'s read-only set is written
    against, so an entry in it that no branch handles would classify a call as a
    read that then fails."""
    assert set(MARKET_DATA_ACTIONS) == {"candles", "historical_candles", "connectors"}
    for action in MARKET_DATA_ACTIONS:
        result, _ = _run(
            action=action,
            connector_name="binance",
            trading_pair="SOL-USDC",
            start_time=1_757_000_000,
            fake={"rows": ROWS, "connectors": ["binance"]},
        )
        assert result["action"] == action
