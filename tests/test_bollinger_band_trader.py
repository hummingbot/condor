"""Tests for the bollinger_band_trader agent routines.

The routines carry the agent's decision logic — band math, regime classification, the
higher-timeframe veto, and position sizing — so they are tested directly. Modules are
loaded by file path, exactly the way ``discover_routines_from_path`` loads agent-local
routines at runtime.

Three of these tests are regressions for bugs found while building the agent:

* ``test_firing_coil_reads_as_expansion_not_squeeze`` — the squeeze check used to run
  before the expansion check, so the bar that fires the breakout still reported
  ``squeeze_pending``: the agent went flat exactly when it should have traded.
* ``test_adx_does_not_pin_at_100_in_a_range`` — the ADX started life as a raw
  single-window DX, which saturates near 100 on any directional push. Since reaching a
  Bollinger band *is* a directional push, ``reversion_range`` was unreachable.
* ``test_capped_size_is_reported_as_a_warn_row`` — a size reduced by the position cap
  was rendered as PASS, hiding the fact that the trade risks less than the target.
"""

import asyncio
import importlib.util
import math
import random
import statistics
from collections import Counter
from pathlib import Path

import pytest

ROUTINES_DIR = (
    Path(__file__).resolve().parents[1]
    / "agents"
    / "bollinger_band_trader"
    / "routines"
)


def _load(name):
    path = ROUTINES_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"agent_routine_bbt_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


band_state = _load("band_state")
squeeze_screener = _load("squeeze_screener")
band_trade_sizer = _load("band_trade_sizer")


# ── Fixtures: synthetic candles engineered to produce each regime ────────────


def _candles(prices, volumes=None):
    out = []
    for i, price in enumerate(prices):
        prev = prices[i - 1] if i else price
        out.append(
            {
                "timestamp": 1700000000 + i * 900,
                "open": prev,
                "high": max(price, prev) * 1.002,
                "low": min(price, prev) * 0.998,
                "close": price,
                "volume": volumes[i] if volumes else 100.0,
            }
        )
    return out


def _coil(n=200, base=100.0):
    """Wide swings, then a very tight coil at the end."""
    prices = [base + 8 * math.sin(i / 4.0) for i in range(n - 40)]
    return prices + [prices[-1] + 0.02 * math.sin(i / 3.0) for i in range(40)]


def _coil_then_breakout(n=200):
    prices = _coil(n - 3)
    last = prices[-1]
    return prices + [last * 1.02, last * 1.05, last * 1.09]


_BREAKOUT_VOLUMES = [100.0] * 197 + [300.0, 320.0, 350.0]


def _grind_up(n=200, base=100.0):
    """Steady advance so closes sit in the top decile of the band."""
    return [base * (1.004**i) for i in range(n)]


def _bounded_walk(seed, n=200, low=94.0, high=106.0, step=2.2):
    """Random walk reflecting off two levels — a realistic trading range."""
    rnd = random.Random(seed)
    price, out = 100.0, []
    for _ in range(n):
        price += rnd.uniform(-step, step)
        if price > high:
            price = high - (price - high)
        if price < low:
            price = low + (low - price)
        out.append(price)
    return out


# Seeds found by sweeping _bounded_walk: the series ends at a band extreme while the
# band itself is wide, flat and non-trending — a genuine reversion setup.
RANGE_AT_LOWER_BAND = 543
RANGE_AT_UPPER_BAND = 77

NEUTRAL_TREND = {"timeframe": "trend_4h", "verdict": "neutral"}


@pytest.fixture(scope="module")
def cfg():
    return band_state.Config()


@pytest.fixture(scope="module")
def coil_tf(cfg):
    return band_state._analyze_timeframe(_candles(_coil()), "entry_15m", cfg)


@pytest.fixture(scope="module")
def breakout_tf(cfg):
    return band_state._analyze_timeframe(
        _candles(_coil_then_breakout(), _BREAKOUT_VOLUMES), "entry_15m", cfg
    )


@pytest.fixture(scope="module")
def walk_tf(cfg):
    return band_state._analyze_timeframe(_candles(_grind_up()), "entry_15m", cfg)


@pytest.fixture(scope="module")
def range_low_tf(cfg):
    return band_state._analyze_timeframe(
        _candles(_bounded_walk(RANGE_AT_LOWER_BAND)), "entry_15m", cfg
    )


@pytest.fixture(scope="module")
def range_high_tf(cfg):
    return band_state._analyze_timeframe(
        _candles(_bounded_walk(RANGE_AT_UPPER_BAND)), "entry_15m", cfg
    )


# ── Band math ────────────────────────────────────────────────────────────────


def test_bands_match_the_textbook_definition():
    closes = [float(x) for x in range(1, 21)]
    series = band_state._bollinger_series(closes, 20, 2.0)

    assert all(
        point is None for point in series[:19]
    ), "band is undefined until the window fills"
    band = series[19]

    mid = sum(closes) / 20
    sigma = statistics.pstdev(
        closes
    )  # population, not sample — the Bollinger definition
    assert band["mid"] == pytest.approx(mid)
    assert band["upper"] == pytest.approx(mid + 2 * sigma)
    assert band["lower"] == pytest.approx(mid - 2 * sigma)
    assert band["pct_b"] == pytest.approx(
        (closes[-1] - band["lower"]) / (band["upper"] - band["lower"])
    )
    assert band["bandwidth_pct"] == pytest.approx(
        (band["upper"] - band["lower"]) / band["mid"] * 100
    )
    # Guard the pstdev/stdev choice: they differ enough here that a swap would show up.
    assert abs(sigma - statistics.stdev(closes)) > 1e-3


def test_flat_series_does_not_divide_by_zero():
    band = band_state._bollinger_series([50.0] * 25, 20, 2.0)[-1]
    assert band["bandwidth_pct"] == 0.0
    assert band["pct_b"] == 0.5


@pytest.mark.parametrize(
    "history,value,expected",
    [
        ([], 1.0, 50.0),
        ([1, 2, 3, 4], 0.5, 0.0),
        ([1, 2, 3, 4], 9.0, 100.0),
        ([1, 2, 2, 3], 2.0, 50.0),  # 1 below + 2 ties at half weight
    ],
)
def test_percentile_rank(history, value, expected):
    assert band_state._percentile_rank(history, value) == expected


def test_true_range_uses_the_previous_close_on_a_gap():
    candles = [
        {"high": 10, "low": 8, "close": 9},
        {"high": 12, "low": 11, "close": 11.5},
    ]
    assert band_state._true_range_series(candles) == [2, 3]


def test_ema_seeds_on_the_sma_then_smooths():
    assert band_state._ema_series([1.0, 2.0, 3.0, 4.0, 5.0], 3)[2:] == [2.0, 3.0, 4.0]


# ── ADX ──────────────────────────────────────────────────────────────────────


def test_adx_separates_trend_from_range():
    trending = band_state._calc_adx(_candles(_grind_up()))
    ranging = band_state._calc_adx(_candles(_bounded_walk(RANGE_AT_LOWER_BAND)))
    assert trending > 50
    assert ranging < band_state._TREND_ADX
    assert 0 <= ranging <= 100 and 0 <= trending <= 100


def test_adx_returns_zero_rather_than_raising_on_short_input():
    assert band_state._calc_adx(_candles([100.0] * 10)) == 0.0


def test_adx_does_not_pin_at_100_in_a_range():
    """Regression: the raw single-window DX saturated, making reversion unreachable."""
    worst = max(
        band_state._calc_adx(_candles(_bounded_walk(seed))) for seed in range(60)
    )
    assert (
        worst < 70
    ), f"ADX reached {worst:.1f} on a bounded range — smoothing is not being applied"


# ── Classification ───────────────────────────────────────────────────────────


def test_coil_classifies_as_squeeze(coil_tf):
    assert coil_tf["verdict"] == "squeeze"
    assert coil_tf["bw_rank"] <= 10
    assert coil_tf["squeeze_on"] is True


def test_firing_coil_reads_as_expansion_not_squeeze(breakout_tf):
    """Regression: bandwidth rank is still low on the bar that breaks the band.

    Checking the squeeze first meant the breakout bar reported `squeeze_pending`, i.e.
    the agent stood aside on the one candle the whole setup exists to trade.
    """
    assert breakout_tf["verdict"] == "expansion_up"
    assert breakout_tf["pct_b"] > 1.0
    assert breakout_tf["bw_rank"] <= band_state.Config().squeeze_rank


def test_grind_classifies_as_band_walk(walk_tf):
    assert walk_tf["verdict"] == "band_walk_up"
    assert walk_tf["adx"] > band_state._TREND_ADX


def test_bounded_range_at_a_band_classifies_as_reversion(range_low_tf, range_high_tf):
    assert range_low_tf["verdict"] == "reversion_range"
    assert range_low_tf["pct_b"] <= 0.05
    assert range_high_tf["verdict"] == "reversion_range"
    assert range_high_tf["pct_b"] >= 0.95


def test_thin_data_returns_an_error_instead_of_raising(cfg):
    assert "error" in band_state._analyze_timeframe(
        _candles([100.0] * 5), "entry_15m", cfg
    )


@pytest.mark.parametrize(
    "pct_bs,expected",
    [
        ([-0.2, -0.1, 0.4], "bullish"),
        ([1.2, 1.1, 0.6], "bearish"),
        ([0.4, 0.5, 0.6], ""),
        ([1.2, 1.1, 1.3], ""),  # still outside — not a failure yet
    ],
)
def test_failed_breakout_detection(pct_bs, expected):
    assert band_state._failed_breakout([{"pct_b": v} for v in pct_bs]) == expected


def test_every_verdict_is_reachable(cfg, breakout_tf, walk_tf):
    """A classification branch that never fires on realistic data is dead code."""
    seen = Counter()
    for seed in range(400):
        frame = band_state._analyze_timeframe(_candles(_bounded_walk(seed)), "e", cfg)
        if "error" not in frame:
            seen[frame["verdict"]] += 1
    seen[breakout_tf["verdict"]] += 1
    seen[walk_tf["verdict"]] += 1

    assert {
        "squeeze",
        "expansion_up",
        "expansion_down",
        "band_walk_up",
        "band_walk_down",
        "reversion_range",
        "neutral",
    } <= set(seen), f"unreachable verdicts; saw {dict(seen)}"


# ── Setup derivation ─────────────────────────────────────────────────────────


def test_squeeze_reports_triggers_but_no_position(coil_tf, cfg):
    setup = band_state._derive_setup(coil_tf, NEUTRAL_TREND, cfg)
    assert setup["setup"] == "squeeze_pending"
    assert setup["bias"] == "none"
    assert setup["long_trigger"] > setup["short_trigger"]
    assert "entry" not in setup


def test_breakout_stops_at_the_middle_band_and_targets_2r(breakout_tf, cfg):
    setup = band_state._derive_setup(breakout_tf, NEUTRAL_TREND, cfg)
    assert setup["setup"] == "squeeze_breakout_long"
    assert setup["stop"] == pytest.approx(breakout_tf["mid"])
    assert setup["rr"] == pytest.approx(2.0)


def test_band_walk_buys_the_pullback_to_the_mean(walk_tf, cfg):
    setup = band_state._derive_setup(walk_tf, NEUTRAL_TREND, cfg)
    assert setup["setup"] == "band_walk_long"
    assert setup["entry"] == pytest.approx(walk_tf["mid"])
    assert setup["target"] == pytest.approx(walk_tf["upper"])


def test_reversion_fades_the_band_back_to_the_mean(range_low_tf, cfg):
    setup = band_state._derive_setup(range_low_tf, NEUTRAL_TREND, cfg)
    assert setup["setup"] == "reversion_long"
    assert setup["entry"] == pytest.approx(range_low_tf["lower"])
    assert setup["target"] == pytest.approx(range_low_tf["mid"])
    assert setup["stop"] < range_low_tf["lower"]


@pytest.mark.parametrize("trend_verdict", ["band_walk_down", "expansion_down"])
def test_reversion_long_is_vetoed_by_an_opposing_trend(
    range_low_tf, cfg, trend_verdict
):
    setup = band_state._derive_setup(
        range_low_tf, {"timeframe": "trend_4h", "verdict": trend_verdict}, cfg
    )
    assert setup["setup"] == "no_trade"
    assert "do not fade" in setup["reason"]


@pytest.mark.parametrize("trend_verdict", ["band_walk_up", "expansion_up"])
def test_reversion_short_is_vetoed_by_an_opposing_trend(
    range_high_tf, cfg, trend_verdict
):
    setup = band_state._derive_setup(
        range_high_tf, {"timeframe": "trend_4h", "verdict": trend_verdict}, cfg
    )
    assert setup["setup"] == "no_trade"
    assert "do not fade" in setup["reason"]


def test_breakout_is_vetoed_by_an_opposing_trend(breakout_tf, cfg):
    setup = band_state._derive_setup(
        breakout_tf, {"timeframe": "trend_4h", "verdict": "band_walk_down"}, cfg
    )
    assert setup["setup"] == "no_trade"


def test_breakout_on_thin_volume_waits_for_the_retest(breakout_tf, cfg):
    setup = band_state._derive_setup(
        dict(breakout_tf, vol_ratio=0.4), NEUTRAL_TREND, cfg
    )
    assert setup["setup"] == "no_trade"
    assert "volume" in setup["reason"]


def test_reward_risk_floor_rejects_a_thin_setup(range_low_tf):
    setup = band_state._derive_setup(
        range_low_tf, NEUTRAL_TREND, band_state.Config(min_rr_reversion=99.0)
    )
    assert setup["setup"] == "no_trade"
    assert "R:R" in setup["reason"]


def test_errored_timeframe_yields_no_trade(cfg):
    setup = band_state._derive_setup(
        {"timeframe": "entry_15m", "error": "insufficient candles"}, NEUTRAL_TREND, cfg
    )
    assert setup["setup"] == "no_trade"


def test_levels_are_always_ordered_correctly(cfg):
    """Fuzz every regime against every trend verdict: no inverted stop may escape."""
    violations = []
    for seed in range(400):
        frame = band_state._analyze_timeframe(
            _candles(_bounded_walk(seed)), "entry_15m", cfg
        )
        if "error" in frame:
            continue
        for trend in (
            "neutral",
            "band_walk_up",
            "band_walk_down",
            "expansion_up",
            "expansion_down",
            "squeeze",
        ):
            setup = band_state._derive_setup(
                frame, {"timeframe": "trend_4h", "verdict": trend}, cfg
            )
            if setup["setup"] in ("no_trade", "squeeze_pending"):
                continue
            ordered = (
                setup["stop"] < setup["entry"] < setup["target"]
                if setup["bias"] == "long"
                else setup["stop"] > setup["entry"] > setup["target"]
            )
            if not ordered:
                violations.append((seed, trend, setup))
    assert not violations, f"{len(violations)} setups had an inverted stop/target"


# ── Fake Hummingbot client for the run() entrypoints ─────────────────────────


class _MarketData:
    def __init__(self, by_interval=None, by_pair=None, prices=None):
        self.by_interval = by_interval or {}
        self.by_pair = by_pair or {}
        self.prices = prices or {}

    async def get_candles(self, connector, pair, interval="1m", max_records=100):
        if self.by_pair:
            return self.by_pair.get(pair, [])[-max_records:]
        return self.by_interval.get(interval, [])[-max_records:]

    async def get_prices(self, connector, trading_pairs):
        return {p: self.prices.get(p, 0.0) for p in trading_pairs}


class _Portfolio:
    def __init__(self, total):
        self.total = total

    async def get_total_value(self):
        return self.total


class _Client:
    def __init__(self, market_data, portfolio=10000.0):
        self.market_data = market_data
        self.portfolio = _Portfolio(portfolio)


class _Context:
    _chat_id = 1


class _NullReportBuilder:
    """Swallows report calls so tests never write into reports/."""

    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, _name):
        return lambda *a, **k: self

    async def save(self, report_id=None):
        return "test-report"


@pytest.fixture(autouse=True)
def _no_report_writes(monkeypatch):
    monkeypatch.setattr("condor.reports.ReportBuilder", _NullReportBuilder)


def _with_client(module, client):
    async def _get_client(*_a, **_k):
        return client

    module.get_client = _get_client


# ── band_state.run() ─────────────────────────────────────────────────────────


def test_run_reports_the_firing_breakout_end_to_end():
    _with_client(
        band_state,
        _Client(
            _MarketData(
                by_interval={
                    "15m": _candles(_coil_then_breakout(), _BREAKOUT_VOLUMES),
                    "1h": _candles(_coil()),
                    "4h": _candles(_grind_up()),
                }
            )
        ),
    )
    out = asyncio.run(
        band_state.run(band_state.Config(trading_pair="TEST-USDT"), _Context())
    )

    assert "setup: squeeze_breakout_long" in out
    assert "entry_tf:" in out and "context_tf:" in out and "trend_tf:" in out


def test_run_degrades_gracefully_without_data():
    _with_client(band_state, _Client(_MarketData()))
    out = asyncio.run(
        band_state.run(band_state.Config(trading_pair="TEST-USDT"), _Context())
    )
    assert "No candle data" in out


def test_run_survives_a_raising_connector():
    class _Broken(_MarketData):
        async def get_candles(self, *a, **k):
            raise RuntimeError("connector down")

    _with_client(band_state, _Client(_Broken()))
    out = asyncio.run(
        band_state.run(band_state.Config(trading_pair="TEST-USDT"), _Context())
    )
    assert "No candle data" in out


# ── squeeze_screener.run() ───────────────────────────────────────────────────


def test_screener_ranks_the_tightest_band_first():
    _with_client(
        squeeze_screener,
        _Client(
            _MarketData(
                by_pair={
                    "TIGHT-USDT": _candles(_coil()),
                    "WIDE-USDT": _candles(_bounded_walk(RANGE_AT_LOWER_BAND)),
                    "TREND-USDT": _candles(_grind_up()),
                    "THIN-USDT": _candles([100.0] * 5),
                }
            )
        ),
    )
    result = asyncio.run(
        squeeze_screener.run(
            squeeze_screener.Config(
                trading_pairs="TIGHT-USDT,WIDE-USDT,TREND-USDT,THIN-USDT"
            ),
            _Context(),
        )
    )

    pairs = [row["Pair"] for row in result.table_data]
    assert pairs[0] == "TIGHT-USDT"
    assert result.table_data[0]["State"] in ("squeeze", "firing")
    assert (
        "THIN-USDT" not in pairs
    ), "pairs without enough history must be skipped, not guessed"
    assert "THIN-USDT" in result.text, "and the skip must be reported"


def test_screener_distinguishes_a_firing_coil_from_a_resting_one():
    _with_client(
        squeeze_screener,
        _Client(
            _MarketData(
                by_pair={
                    "FIRING-USDT": _candles(_coil_then_breakout(), _BREAKOUT_VOLUMES)
                }
            )
        ),
    )
    result = asyncio.run(
        squeeze_screener.run(
            squeeze_screener.Config(trading_pairs="FIRING-USDT"), _Context()
        )
    )
    assert result.table_data[0]["State"] == "firing"


def test_screener_handles_an_empty_watchlist():
    _with_client(squeeze_screener, _Client(_MarketData()))
    result = asyncio.run(
        squeeze_screener.run(
            squeeze_screener.Config(trading_pairs="NOPE-USDT"), _Context()
        )
    )
    assert "No usable candle data" in result.text


# ── band_trade_sizer.run() ───────────────────────────────────────────────────


def _size(**overrides):
    _with_client(
        band_trade_sizer,
        _Client(
            _MarketData(
                by_interval={"15m": _candles(_bounded_walk(RANGE_AT_LOWER_BAND))},
                prices={"TEST-USDT": 100.0},
            ),
            portfolio=overrides.pop("portfolio", 10000.0),
        ),
    )
    config = dict(trading_pair="TEST-USDT", side="long", portfolio_value_quote=10000.0)
    config.update(overrides)
    return asyncio.run(
        band_trade_sizer.run(band_trade_sizer.Config(**config), _Context())
    )


def test_size_is_derived_from_the_stop_distance():
    # 0.5% of $10,000 = $50 risk over a 10% stop -> $500 notional, under every cap.
    result = _size(entry_price=100.0, stop_price=90.0)
    assert "verdict: PASS" in result.text
    assert "notional_quote: $500.00" in result.text
    assert "risk_if_stopped: $50.00" in result.text
    assert '"amount": 5.0' in result.text


def test_capped_size_is_reported_as_a_warn_row():
    """Regression: a capped size rendered as PASS and hid the reduced risk."""
    # A 2% stop wants $2,500 of notional; max_position_pct caps it at $1,000.
    result = _size(entry_price=100.0, stop_price=98.0)
    assert "verdict: PASS" in result.text
    assert "notional_quote: $1,000.00" in result.text
    assert any(
        r["Check"] == "Size capped" and r["Result"] == "WARN" for r in result.table_data
    )


def test_uncapped_size_emits_no_cap_warning():
    result = _size(entry_price=100.0, stop_price=90.0)
    assert not any(r["Check"] == "Size capped" for r in result.table_data)


def test_payload_matches_the_position_executor_schema():
    result = _size(entry_price=100.0, stop_price=98.0, target_price=104.0)
    assert '"side": 1' in result.text
    assert (
        '"stop_loss": 0.02' in result.text
    ), "stop_loss is a decimal fraction, not a price"
    assert '"take_profit": 0.04' in result.text
    assert '"connector_name"' in result.text and '"trading_pair"' in result.text


def test_short_maps_to_executor_side_two():
    result = _size(side="short", entry_price=100.0, stop_price=102.0)
    assert "verdict: PASS" in result.text
    assert '"side": 2' in result.text


@pytest.mark.parametrize(
    "alias,expected_side", [("buy", 1), ("BUY", 1), ("sell", 2), ("2", 2)]
)
def test_side_aliases(alias, expected_side):
    stop = 98.0 if expected_side == 1 else 102.0
    result = _size(side=alias, entry_price=100.0, stop_price=stop)
    assert f'"side": {expected_side}' in result.text


def test_invalid_side_is_rejected_cleanly():
    result = _size(side="sideways")
    assert "Invalid side" in result.text


@pytest.mark.parametrize(
    "overrides,failing_check",
    [
        ({"entry_price": 100.0, "stop_price": 102.0}, "Stop on the protective side"),
        ({"entry_price": 100.0, "stop_price": 99.99}, "Stop distance sane"),
        (
            {"entry_price": 100.0, "stop_price": 98.0, "risk_pct": 5.0},
            "Risk per trade within policy",
        ),
        (
            {"entry_price": 100.0, "stop_price": 98.0, "leverage": 20},
            "Leverage within policy",
        ),
        (
            {"entry_price": 100.0, "stop_price": 98.0, "portfolio_value_quote": 20.0},
            "Meets exchange minimum notional",
        ),
    ],
)
def test_guardrails_block_the_trade(overrides, failing_check):
    result = _size(**overrides)
    assert "verdict: FAIL" in result.text
    assert any(
        r["Check"] == failing_check and r["Result"] == "FAIL" for r in result.table_data
    )
    assert (
        "position_executor payload" not in result.text
    ), "a failed check must not emit a payload"


def test_unknown_portfolio_value_fails_instead_of_sizing_on_zero():
    result = _size(
        entry_price=100.0, stop_price=98.0, portfolio_value_quote=0.0, portfolio=0.0
    )
    assert "verdict: FAIL" in result.text


def test_stop_is_derived_from_the_bands_when_none_is_given():
    result = _size()
    stop_line = result.text.split("stop:")[1].splitlines()[0]
    assert "supplied" not in stop_line
    assert "band" in stop_line, stop_line


# ── Round trip ───────────────────────────────────────────────────────────────


def test_band_state_levels_feed_straight_into_the_sizer():
    _with_client(
        band_state,
        _Client(
            _MarketData(
                by_interval={"15m": _candles(_bounded_walk(RANGE_AT_LOWER_BAND))}
            )
        ),
    )
    summary = asyncio.run(
        band_state.run(band_state.Config(trading_pair="TEST-USDT"), _Context())
    )
    fields = dict(
        line.split(": ", 1)
        for line in summary.splitlines()
        if ": " in line and not line.startswith("**")
    )

    result = _size(
        entry_price=float(fields["entry"].replace(",", "")),
        stop_price=float(fields["stop"].replace(",", "")),
        target_price=float(fields["target"].replace(",", "")),
    )
    assert "verdict: PASS" in result.text
    sizer_rr = float(result.text.split("rr: ")[1].splitlines()[0])
    assert sizer_rr == pytest.approx(float(fields["rr"]), abs=0.01)
