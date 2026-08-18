"""Tests for ARCH-146: one historical-candles ladder behind `fetch_historical_candles`.

The sequence "ask for a time range → unwrap a list-or-``{"data": [...]}`` payload →
fall back to ``get_candles`` → normalize dict-or-tuple rows to float OHLCV" used to
be inlined three times (the REST candles route, the WS backfill, the WS REST poll)
with subtly different edges. It now lives in ``condor.fetchers.market_data``, with
the three differences the call sites actually relied on expressed as arguments:
``limit=None`` (no fallback), ``fallback_on_error`` (swallow a failing historical
call) and ``strict`` (surface a malformed row instead of dropping it).

These tests pin the unified ladder — both payload shapes, both row shapes, the
fallback branch — and that each call site still gets its own edge behaviour.
"""

import asyncio

import pytest

import condor.web.routes.market as market
from condor.fetchers.market_data import fetch_historical_candles, normalize_candle
from condor.web.models import WebUser
from condor.web.ws_manager import WebSocketManager, _CandleBuffer

DICT_ROWS = [
    {
        "timestamp": 100,
        "open": 1,
        "high": 2,
        "low": 0.5,
        "close": 1.5,
        "volume": 10,
    },
    {
        "timestamp": 160,
        "open": 1.5,
        "high": 3,
        "low": 1.4,
        "close": 2.0,
        "volume": 20,
    },
]

# The other shape the API answers with: positional rows, extra fields trailing.
TUPLE_ROWS = [
    [100, 1, 2, 0.5, 1.5, 10, "ignored"],
    (160, 1.5, 3, 1.4, 2.0, 20),
]

EXPECTED = [
    {
        "timestamp": 100.0,
        "open": 1.0,
        "high": 2.0,
        "low": 0.5,
        "close": 1.5,
        "volume": 10.0,
    },
    {
        "timestamp": 160.0,
        "open": 1.5,
        "high": 3.0,
        "low": 1.4,
        "close": 2.0,
        "volume": 20.0,
    },
]


class FakeMarketData:
    """Records both candle calls and answers with canned payloads."""

    def __init__(self, historical=None, candles=None, historical_exc=None):
        self._historical = historical
        self._candles = candles
        self._historical_exc = historical_exc
        self.historical_calls = []
        self.candles_calls = []

    async def get_historical_candles(
        self, connector, pair, interval, *, start_time=None, end_time=None
    ):
        self.historical_calls.append((connector, pair, interval, start_time, end_time))
        if self._historical_exc is not None:
            raise self._historical_exc
        return self._historical

    async def get_candles(self, connector, pair, interval, limit):
        self.candles_calls.append((connector, pair, interval, limit))
        return self._candles


class FakeClient:
    def __init__(self, **kwargs):
        self.market_data = FakeMarketData(**kwargs)


def _fetch(client, **kwargs):
    return asyncio.run(
        fetch_historical_candles(client, "binance", "BTC-USDT", "1m", **kwargs)
    )


# --- Payload shapes and row shapes ---


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(DICT_ROWS, id="bare-list-of-dicts"),
        pytest.param({"data": DICT_ROWS}, id="data-envelope-of-dicts"),
        pytest.param(TUPLE_ROWS, id="bare-list-of-tuples"),
        pytest.param({"data": TUPLE_ROWS}, id="data-envelope-of-tuples"),
    ],
)
def test_both_payload_shapes_and_both_row_shapes_normalize_identically(payload):
    client = FakeClient(historical=payload)
    assert _fetch(client, start_time=100, end_time=200, limit=500) == EXPECTED
    assert client.market_data.candles_calls == [], "no fallback when the range answers"


def test_unexpected_payload_shape_yields_no_candles():
    client = FakeClient(historical="not a payload", candles="also not")
    assert _fetch(client, start_time=100, end_time=200, limit=500) == []


# --- The get_candles fallback branch ---


@pytest.mark.parametrize(
    "empty",
    [pytest.param([], id="empty-list"), pytest.param({"data": []}, id="empty-data")],
)
def test_an_empty_range_falls_back_to_get_candles(empty):
    client = FakeClient(historical=empty, candles={"data": TUPLE_ROWS})
    assert _fetch(client, start_time=100, end_time=200, limit=42) == EXPECTED
    assert client.market_data.candles_calls == [("binance", "BTC-USDT", "1m", 42)]


def test_no_limit_disables_the_fallback():
    """The WS REST poll wants the fresh tail or nothing — never a full window."""
    client = FakeClient(historical=[], candles=DICT_ROWS)
    assert _fetch(client, start_time=100, end_time=200) == []
    assert client.market_data.candles_calls == []


def test_without_start_time_the_range_call_is_skipped_entirely():
    client = FakeClient(historical=DICT_ROWS, candles=TUPLE_ROWS)
    assert _fetch(client, limit=7) == EXPECTED
    assert client.market_data.historical_calls == []
    assert client.market_data.candles_calls == [("binance", "BTC-USDT", "1m", 7)]


# --- Error handling per call site ---


def test_a_failing_range_call_propagates_by_default():
    """WS backfill/poll abort on failure rather than pulling a different window."""
    client = FakeClient(historical_exc=RuntimeError("boom"), candles=DICT_ROWS)
    with pytest.raises(RuntimeError):
        _fetch(client, start_time=100, end_time=200, limit=5)
    assert client.market_data.candles_calls == []


def test_fallback_on_error_swallows_the_range_failure():
    """The REST route degrades to the plain window instead of erroring out."""
    client = FakeClient(historical_exc=RuntimeError("boom"), candles=DICT_ROWS)
    got = _fetch(client, start_time=100, end_time=200, limit=5, fallback_on_error=True)
    assert got == EXPECTED
    assert client.market_data.candles_calls == [("binance", "BTC-USDT", "1m", 5)]


def test_a_failing_fallback_always_propagates():
    client = FakeClient(historical=[])

    async def _raise(*_a, **_kw):
        raise RuntimeError("fallback down")

    client.market_data.get_candles = _raise
    with pytest.raises(RuntimeError):
        _fetch(client, start_time=100, end_time=200, limit=5, fallback_on_error=True)


# --- Row normalization ---


@pytest.mark.parametrize(
    "row",
    [
        pytest.param([1, 2, 3], id="too-short-a-row"),
        pytest.param("candle", id="a-string"),
        pytest.param(None, id="none"),
    ],
)
def test_rows_in_neither_shape_are_dropped(row):
    assert normalize_candle(row) is None


def test_a_malformed_value_is_dropped_by_default_and_raised_when_strict():
    row = {
        "timestamp": 100,
        "open": "n/a",
        "high": 2,
        "low": 1,
        "close": 1,
        "volume": 1,
    }
    assert normalize_candle(row) is None
    with pytest.raises(ValueError):
        normalize_candle(row, strict=True)


def test_missing_fields_default_to_zero():
    assert normalize_candle({"timestamp": 100}) == {
        "timestamp": 100.0,
        "open": 0.0,
        "high": 0.0,
        "low": 0.0,
        "close": 0.0,
        "volume": 0.0,
    }


def test_unusable_rows_do_not_sink_the_rest_of_the_batch():
    client = FakeClient(historical=[DICT_ROWS[0], "junk", TUPLE_ROWS[1]])
    assert _fetch(client, start_time=100, end_time=200) == EXPECTED


# --- The call sites still behave as they did ---


class _FakeCM:
    def __init__(self, client):
        self._client = client

    def has_server_access(self, *_a, **_kw):
        return True

    async def get_client(self, _name):
        return self._client


_USER = WebUser(id=1, role="admin")


@pytest.fixture(autouse=True)
def _clean_candle_cache():
    market._candle_cache.clear()
    yield
    market._candle_cache.clear()


def _route(client, monkeypatch, *, start_time=None, end_time=None):
    """Call the route function directly, with every Query default made explicit."""
    monkeypatch.setattr(market, "get_config_manager", lambda: _FakeCM(client))
    return asyncio.run(
        market.get_candles(
            name="srv",
            connector="binance",
            trading_pair="BTC-USDT",
            interval="1m",
            limit=1000,
            start_time=start_time,
            end_time=end_time,
            pool_address=None,
            user=_USER,
        )
    )


def test_the_route_returns_the_same_payload_with_and_without_a_start_time(monkeypatch):
    """Same rows in, same CandleData out — one path via the range, one via the window."""
    ranged = _route(
        FakeClient(historical={"data": DICT_ROWS}),
        monkeypatch,
        start_time=100.0,
        end_time=200.0,
    )
    windowed = _route(FakeClient(candles=TUPLE_ROWS), monkeypatch)
    assert [c.model_dump() for c in ranged] == EXPECTED
    assert [c.model_dump() for c in windowed] == EXPECTED


def test_the_route_still_falls_back_when_the_range_call_fails(monkeypatch):
    client = FakeClient(historical_exc=RuntimeError("boom"), candles=DICT_ROWS)
    got = _route(client, monkeypatch, start_time=100.0, end_time=200.0)
    assert [c.model_dump() for c in got] == EXPECTED


def test_ws_backfill_fills_the_buffer_through_the_fallback(monkeypatch):
    import config_manager

    client = FakeClient(historical=[], candles={"data": TUPLE_ROWS})
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _FakeCM(client))

    mgr = WebSocketManager()
    channel = "candles:srv:binance:BTC-USDT:1m"
    buf = _CandleBuffer("1m")
    mgr._candle_buffers[channel] = buf

    asyncio.run(mgr._backfill_candles(channel))

    assert buf.get_sorted() == EXPECTED
    # The backfill sizes its fallback off the buffer, capped at 5000.
    assert client.market_data.candles_calls == [
        ("binance", "BTC-USDT", "1m", min(buf._max_size, 5000))
    ]


def test_ws_normalize_candle_alias_shares_the_one_implementation():
    assert WebSocketManager._normalize_candle(TUPLE_ROWS[0]) == EXPECTED[0]
    assert WebSocketManager._normalize_candle(DICT_ROWS[0]) == EXPECTED[0]
    assert WebSocketManager._normalize_candle("junk") is None
