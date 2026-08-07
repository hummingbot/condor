"""DEX/LP executor charts read candles from GeckoTerminal, not the CEX feed.

Hummingbot's ``CandlesFactory`` is CEX-only, so an LP executor on
``solana-mainnet-beta`` charted as "Failed to load candles" (502). These cover
the translation between what a chart asks for (a *window* on a trading pair) and
what GeckoTerminal answers (a *count* of candles walking back from a timestamp,
for one pool) — the place where the two models disagree is where the chart lies.
"""

import asyncio

import pytest

from condor import dex_candles
from condor.web.routes import market
from handlers.dex import pool_data


def run(coro):
    """Drive a coroutine to completion (the suite has no async plugin)."""
    return asyncio.run(coro)


# ── Interval → GeckoTerminal timeframe ──


@pytest.mark.parametrize("timeframe", pool_data.GECKO_TIMEFRAMES)
def test_supported_timeframes_pass_through(timeframe):
    assert pool_data.normalize_timeframe(timeframe) == timeframe


@pytest.mark.parametrize(
    "requested,expected",
    [
        ("3m", "1m"),  # between 1m and 5m — keep the finer resolution
        ("30m", "15m"),
        ("2h", "1h"),
        ("1s", "1m"),  # finer than anything offered — floor at 1m
        ("7d", "1d"),
        ("garbage", "1m"),
        ("", "1m"),
    ],
)
def test_unsupported_interval_snaps_to_a_supported_one(requested, expected):
    """The client raises ValueError on an unlisted timeframe, which would surface
    as an empty chart. Snap down instead, never up: a chart asking for 3m must not
    be answered with coarser 5m candles."""
    assert pool_data.normalize_timeframe(requested) == expected


# ── Window → candle count ──


def test_short_window_asks_for_few_candles():
    """The regression this guards: a ten-minute position charted at 1m used to
    request the max 1000 candles — ~16h of history that fitContent() then squeezed
    the actual position into a sliver of."""
    count = pool_data.candles_needed(1_700_000_000, 1_700_000_600, "1m")
    assert count == 12  # 10 candles + 2 of edge headroom


def test_window_count_scales_with_timeframe():
    day = 86400
    assert pool_data.candles_needed(0, day, "1h") == 26
    assert pool_data.candles_needed(0, day, "1d") == 3


def test_window_count_is_capped_at_the_api_limit():
    year = 365 * 86400
    assert pool_data.candles_needed(0, year, "1m") == pool_data.GECKO_OHLCV_MAX


def test_open_ended_window_asks_for_the_maximum():
    assert pool_data.candles_needed(None, None, "1m") == pool_data.GECKO_OHLCV_MAX
    assert pool_data.candles_needed(100, 100, "1m") == pool_data.GECKO_OHLCV_MAX


# ── Address validation ──


@pytest.mark.parametrize(
    "address",
    [
        "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # Solana mint
        "So11111111111111111111111111111111111111112",
        "0x" + "a" * 40,  # EVM contract
        "0x" + "F" * 40,
    ],
)
def test_real_addresses_are_accepted(address):
    assert market._ADDRESS_RE.match(address)


@pytest.mark.parametrize(
    "value",
    [
        "BTC",  # a plain ticker must not be mistaken for an address
        "../../../etc/passwd",
        "abc/def",
        "0x" + "a" * 39,  # one nibble short of an EVM address
        "0OIl" * 10,  # base58 excludes 0, O, I and l
        "short",
        "",
    ],
)
def test_non_addresses_are_rejected(value):
    """These reach GeckoTerminal as a URL path segment, so anything that is not an
    address is refused before it gets there."""
    assert not market._ADDRESS_RE.match(value)


# ── Pair splitting ──


@pytest.mark.parametrize(
    "pair,base,quote",
    [
        ("BTC-USDT", "BTC", "USDT"),
        (
            "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263-SOL",
            "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
            "SOL",
        ),
        # Perp pairs carry a suffix; the quote is still the last segment.
        ("BTC-USDT-240927", "BTC-USDT", "240927"),
        ("NOQUOTE", "NOQUOTE", ""),
    ],
)
def test_split_pair(pair, base, quote):
    assert dex_candles.split_pair(pair) == (base, quote)


# ── Quote matching on the fallback pool ──


class _FakePools:
    def __init__(self, rows):
        self._rows = rows

    def to_dict(self, _orient):
        return self._rows


def _pools(*names_and_addresses):
    return _FakePools([{"name": n, "address": a} for n, a in names_and_addresses])


@pytest.fixture(autouse=True)
def _clear_token_caches():
    caches = (
        pool_data._token_pool_cache,
        pool_data._token_symbol_cache,
        pool_data._pair_pool_cache,
    )
    for cache in caches:
        cache.clear()
    yield
    for cache in caches:
        cache.clear()


def test_fallback_pool_must_match_the_quote_token(monkeypatch):
    """Candles are requested with currency="token", so prices come back in the
    pool's quote. A token/USDC pool charted under a token/SOL position draws the
    right shape on the wrong scale — with nothing on screen to say so. No matching
    quote must mean no pool, not the deepest one."""

    class _Client:
        async def get_top_pools_by_network_token(self, *_a):
            return _pools(("BONK / USDC", "usdc_pool"), ("BONK / USDT", "usdt_pool"))

    monkeypatch.setattr(pool_data, "_gecko_client", lambda: _Client())
    assert run(pool_data.fetch_token_top_pool("mint", "solana", "SOL")) == ""


def test_fallback_pool_picks_the_first_matching_quote(monkeypatch):
    """Rows arrive volume-sorted, so the first quote match is the deepest pool."""

    class _Client:
        async def get_top_pools_by_network_token(self, *_a):
            return _pools(
                ("BONK / USDC", "usdc_pool"),
                ("BONK / SOL", "deep_sol_pool"),
                ("BONK / SOL", "thin_sol_pool"),
            )

    monkeypatch.setattr(pool_data, "_gecko_client", lambda: _Client())
    got = run(pool_data.fetch_token_top_pool("mint", "solana", "SOL"))
    assert got == "deep_sol_pool"


def test_a_failed_pool_lookup_is_not_cached(monkeypatch):
    """A blip must not pin an empty answer for the cache's full hour."""
    calls = []

    class _Failing:
        async def get_top_pools_by_network_token(self, *_a):
            calls.append(1)
            raise RuntimeError("429 Too Many Requests")

    monkeypatch.setattr(pool_data, "_gecko_client", lambda: _Failing())
    assert run(pool_data.fetch_token_top_pool("mint", "solana", "SOL")) == ""
    assert run(pool_data.fetch_token_top_pool("mint", "solana", "SOL")) == ""
    assert len(calls) == 2


def test_a_successful_empty_symbol_is_cached(monkeypatch):
    """An unlisted mint is a real answer — re-asking on every table render is not."""
    calls = []

    class _Client:
        async def get_specific_token_on_network(self, *_a):
            calls.append(1)
            return {"attributes": {}}

    monkeypatch.setattr(pool_data, "_gecko_client", lambda: _Client())
    assert run(pool_data.fetch_token_symbol("mint", "solana")) == ""
    assert run(pool_data.fetch_token_symbol("mint", "solana")) == ""
    assert len(calls) == 1


def test_a_failed_symbol_lookup_is_not_cached(monkeypatch):
    calls = []

    class _Failing:
        async def get_specific_token_on_network(self, *_a):
            calls.append(1)
            raise RuntimeError("timeout")

    monkeypatch.setattr(pool_data, "_gecko_client", lambda: _Failing())
    assert run(pool_data.fetch_token_symbol("mint", "solana")) == ""
    assert run(pool_data.fetch_token_symbol("mint", "solana")) == ""
    assert len(calls) == 2


def test_symbol_resolves_from_the_token_attributes(monkeypatch):
    class _Client:
        async def get_specific_token_on_network(self, *_a):
            return {"attributes": {"symbol": "Bonk"}}

    monkeypatch.setattr(pool_data, "_gecko_client", lambda: _Client())
    assert run(pool_data.fetch_token_symbol("mint", "solana-mainnet-beta")) == "Bonk"


# ── Route assembly ──

MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"


def _row(ts, close=1.0):
    return [ts, close, close, close, close, 100.0]


def test_candles_outside_the_requested_window_are_dropped(monkeypatch):
    """GeckoTerminal answers with a count, not a window, so it can overshoot the
    start. The chart must not be handed candles from before the position existed."""
    start, end = 1_700_000_000, 1_700_000_600
    rows = [_row(start - 7200), _row(start + 60), _row(end + 7200)]

    async def _fake(*_a, **_k):
        return rows

    monkeypatch.setattr(dex_candles, "_pool_ohlcv", _fake)
    candles = run(
        market._fetch_dex_candles(
            "solana-mainnet-beta", "pool", f"{MINT}-SOL", "1m", start, end
        )
    )
    assert [c.timestamp for c in candles] == [start + 60]


def test_the_candle_containing_the_start_is_kept(monkeypatch):
    """A position opening mid-candle still needs the candle it opened in."""
    start, end = 1_700_000_000, 1_700_000_600

    async def _fake(*_a, **_k):
        return [_row(start - 30)]

    monkeypatch.setattr(dex_candles, "_pool_ohlcv", _fake)
    candles = run(
        market._fetch_dex_candles(
            "solana-mainnet-beta", "pool", f"{MINT}-SOL", "1m", start, end
        )
    )
    assert [c.timestamp for c in candles] == [start - 30]


def test_malformed_rows_are_skipped_not_fatal(monkeypatch):
    async def _fake(*_a, **_k):
        return [_row(1000), ["short"], None, [2000, "x", 1, 1, 1, 1], _row(3000)]

    monkeypatch.setattr(dex_candles, "_pool_ohlcv", _fake)
    candles = run(
        market._fetch_dex_candles(
            "solana-mainnet-beta", "pool", f"{MINT}-SOL", "1m", None, None
        )
    )
    assert [c.timestamp for c in candles] == [1000, 3000]


def test_a_live_window_asks_for_latest_candles(monkeypatch):
    """before_timestamp is what makes a live chart's cache key rotate every minute
    as its end drifts, re-hitting GeckoTerminal each time. A window that has not
    closed yet asks for "latest" instead, which is both correct and cacheable."""
    import time as _time

    seen = {}

    async def _fake(pool, connector, timeframe, limit, before_timestamp, **_k):
        seen["before"] = before_timestamp
        return []

    monkeypatch.setattr(dex_candles, "_pool_ohlcv", _fake)
    now = _time.time()
    run(
        market._fetch_dex_candles(
            "solana-mainnet-beta", "pool", f"{MINT}-SOL", "1m", now - 600, now
        )
    )
    assert seen["before"] is None


def test_a_closed_window_pins_before_timestamp(monkeypatch):
    """An archived executor must chart against the candles it actually traded in,
    not the newest ones."""
    seen = {}

    async def _fake(pool, connector, timeframe, limit, before_timestamp, **_k):
        seen["before"] = before_timestamp
        return []

    monkeypatch.setattr(dex_candles, "_pool_ohlcv", _fake)
    run(
        market._fetch_dex_candles(
            "solana-mainnet-beta",
            "pool",
            f"{MINT}-SOL",
            "1m",
            1_600_000_000,
            1_600_003_600,
        )
    )
    assert seen["before"] == 1_600_003_600


def test_top_pool_fallback_when_the_executors_pool_is_empty(monkeypatch):
    """A closed slot, or an executor that never recorded a pool_address, still
    charts against the token's live main pool."""
    queried = []

    async def _fake_ohlcv(pool, *_a, **_k):
        queried.append(pool)
        return [] if pool == "dead_pool" else [_row(1000)]

    async def _fake_top(mint, network, quote):
        assert (mint, quote) == (MINT, "SOL")
        return "live_pool"

    monkeypatch.setattr(dex_candles, "_pool_ohlcv", _fake_ohlcv)
    monkeypatch.setattr(pool_data, "fetch_token_top_pool", _fake_top)
    candles = run(
        market._fetch_dex_candles(
            "solana-mainnet-beta", "dead_pool", f"{MINT}-SOL", "1m", None, None
        )
    )
    assert queried == ["dead_pool", "live_pool"]
    assert len(candles) == 1


def test_no_fallback_for_a_ticker_base(monkeypatch):
    """GeckoTerminal resolves tokens by address. A pair like BTC-USDT has nothing
    to look up, so it must not spend a request finding out."""
    called = []

    async def _fake_ohlcv(*_a, **_k):
        return []

    async def _fake_top(*_a, **_k):
        called.append(1)
        return ""

    monkeypatch.setattr(dex_candles, "_pool_ohlcv", _fake_ohlcv)
    monkeypatch.setattr(pool_data, "fetch_token_top_pool", _fake_top)
    assert (
        run(
            market._fetch_dex_candles(
                "solana-mainnet-beta", None, "BTC-USDT", "1m", None, None
            )
        )
        == []
    )
    assert called == []


def test_dex_connectors_route_away_from_the_cex_feed():
    assert dex_candles.uses_gecko_candles("solana-mainnet-beta")
    assert dex_candles.uses_gecko_candles("base")
    assert not dex_candles.uses_gecko_candles("binance")
    assert not dex_candles.uses_gecko_candles("hyperliquid_perpetual")


# ── XRPL: a Hummingbot venue with no candle feed ──


def test_xrpl_routes_to_geckoterminal():
    """xrpl trades through Hummingbot like a CEX but is absent from
    available-candle-connectors, so its charts must take the GeckoTerminal path
    instead of 502-ing on a feed that does not exist."""
    assert dex_candles.uses_gecko_candles("xrpl")
    assert pool_data.uses_symbol_pairs("xrpl")
    # Address-based networks keep resolving pools by mint, not by ticker.
    assert not pool_data.uses_symbol_pairs("solana-mainnet-beta")


class _FakeSearch:
    """Stands in for GeckoTerminal's /search/pools."""

    def __init__(self, rows, on_request=None):
        self._rows = rows
        self._on_request = on_request

    async def api_request(self, _method, path, params=None):
        if self._on_request:
            self._on_request(path, params)
        return {
            "data": [
                {
                    "attributes": {
                        "name": name,
                        "address": address,
                        "volume_usd": {"h24": volume},
                    }
                }
                for name, address, volume in self._rows
            ]
        }


def test_symbol_pair_picks_the_deepest_exact_match(monkeypatch):
    """Any XRPL issuer can mint a "RLUSD", so the search returns several pools of
    the same name plus fuzzy neighbours. Only an exact symbol match counts, and
    among those the deepest wins — the thin ones are dead issuers."""
    monkeypatch.setattr(
        pool_data,
        "_gecko_client",
        lambda: _FakeSearch(
            [
                ("RLUSDM / XRP", "fuzzy_pool", 9_999_999.0),  # different token
                ("RLUSD / XRP", "thin_pool", 0.08),
                ("RLUSD / XRP", "deep_pool", 3_400_000.0),
            ]
        ),
    )
    assert run(pool_data.fetch_pair_top_pool("RLUSD", "XRP", "xrpl")) == (
        "deep_pool",
        False,
    )


def test_symbol_pair_reports_an_inverted_pool(monkeypatch):
    """A venue pair quoted the other way round from the pool (XRP-RLUSD against a
    RLUSD / XRP pool) must chart the quote token's series — reading the base's
    would draw the reciprocal price with nothing on screen to say so."""
    monkeypatch.setattr(
        pool_data,
        "_gecko_client",
        lambda: _FakeSearch([("RLUSD / XRP", "pool", 3_400_000.0)]),
    )
    assert run(pool_data.fetch_pair_top_pool("XRP", "RLUSD", "xrpl")) == ("pool", True)


def test_symbol_pair_with_no_exact_match_returns_nothing(monkeypatch):
    monkeypatch.setattr(
        pool_data,
        "_gecko_client",
        lambda: _FakeSearch([("SOLO / USDC", "other_quote", 1.0)]),
    )
    assert run(pool_data.fetch_pair_top_pool("SOLO", "XRP", "xrpl")) == ("", False)


def test_a_failed_pair_search_is_not_cached(monkeypatch):
    calls = []

    class _Failing:
        async def api_request(self, *_a, **_k):
            calls.append(1)
            raise RuntimeError("429 Too Many Requests")

    monkeypatch.setattr(pool_data, "_gecko_client", lambda: _Failing())
    assert run(pool_data.fetch_pair_top_pool("SOLO", "XRP", "xrpl")) == ("", False)
    assert run(pool_data.fetch_pair_top_pool("SOLO", "XRP", "xrpl")) == ("", False)
    assert len(calls) == 2


def test_xrpl_pair_charts_the_pool_found_by_symbol(monkeypatch):
    """End to end: SOLO-XRP carries no address at all, so the pool comes from the
    symbol search and its candles are priced in XRP like the venue quotes them."""
    seen = {}

    async def _fake_ohlcv(pool, connector, timeframe, limit, before, **kwargs):
        seen["pool"] = pool
        seen["token"] = kwargs.get("token", "base")
        return [_row(1000, close=0.0121)]

    async def _fake_search(base, quote, network):
        assert (base, quote, network) == ("SOLO", "XRP", "xrpl")
        return "solo_xrp_pool", False

    monkeypatch.setattr(dex_candles, "_pool_ohlcv", _fake_ohlcv)
    monkeypatch.setattr(pool_data, "fetch_pair_top_pool", _fake_search)
    candles = run(market._fetch_dex_candles("xrpl", None, "SOLO-XRP", "1m", None, None))
    assert seen == {"pool": "solo_xrp_pool", "token": "base"}
    assert [c.close for c in candles] == [0.0121]


def test_an_inverted_xrpl_pair_reads_the_quote_series(monkeypatch):
    seen = {}

    async def _fake_ohlcv(pool, connector, timeframe, limit, before, **kwargs):
        seen["token"] = kwargs.get("token", "base")
        return []

    async def _fake_search(*_a, **_k):
        return "rlusd_xrp_pool", True

    monkeypatch.setattr(dex_candles, "_pool_ohlcv", _fake_ohlcv)
    monkeypatch.setattr(pool_data, "fetch_pair_top_pool", _fake_search)
    run(market._fetch_dex_candles("xrpl", None, "XRP-RLUSD", "1m", None, None))
    assert seen["token"] == "quote"
