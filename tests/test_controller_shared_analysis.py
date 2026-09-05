"""
Guards the single home of the generic OHLCV helpers used by controllers.

`calculate_natr` / `calculate_price_stats` used to be byte-identical copies in
both `grid_strike/grid_analysis.py` and `pmm_mister/pmm_analysis.py`, so a fix
to one silently left the other wrong. They now live in
`handlers/bots/controllers/_analysis.py`; the per-package modules only
re-export them.
"""

from pathlib import Path

from handlers.bots.controllers import _analysis, grid_strike, pmm_mister
from handlers.bots.controllers.grid_strike import grid_analysis
from handlers.bots.controllers.pmm_mister import pmm_analysis

CONTROLLERS_DIR = Path(_analysis.__file__).parent


def test_helpers_are_defined_exactly_once():
    """Only the shared module may contain the `def` for these helpers."""
    for name in ("calculate_natr", "calculate_price_stats"):
        definers = [
            path.relative_to(CONTROLLERS_DIR).as_posix()
            for path in CONTROLLERS_DIR.rglob("*.py")
            if f"def {name}(" in path.read_text()
        ]
        assert definers == ["_analysis.py"], f"{name} is defined in {definers}"


def test_both_packages_reexport_the_shared_objects():
    """Every public path must resolve to the one shared implementation."""
    for name in ("calculate_natr", "calculate_price_stats"):
        shared = getattr(_analysis, name)
        for module in (grid_analysis, pmm_analysis, grid_strike, pmm_mister):
            assert getattr(module, name) is shared, f"{module.__name__}.{name} drifted"


def test_package_all_entries_still_resolve():
    """Keeping the `__init__.py` re-exports means no external caller changes."""
    for package in (grid_strike, pmm_mister):
        for name in ("calculate_natr", "calculate_price_stats"):
            assert name in package.__all__
            assert getattr(package, name, None) is not None


def _candles(closes):
    """Build candles with a fixed 2-wide high/low band around each close."""
    return [{"high": c + 1, "low": c - 1, "close": c} for c in closes]


def test_calculate_natr_matches_atr_over_close():
    # 16 candles -> 15 true ranges; each TR is max(2, |diff|+1) = 2 here.
    natr = _analysis.calculate_natr(_candles([100.0] * 16), period=14)
    assert natr == 2.0 / 100.0


def test_calculate_natr_returns_none_on_insufficient_data():
    assert _analysis.calculate_natr(_candles([100.0] * 10), period=14) is None
    assert _analysis.calculate_natr([], period=14) is None


def test_calculate_price_stats_reports_range_and_natr():
    stats = _analysis.calculate_price_stats(_candles([100.0] * 16))

    assert stats["current_price"] == 100.0
    assert stats["high_price"] == 101.0
    assert stats["low_price"] == 99.0
    assert stats["range_pct"] == (101.0 - 99.0) / 100.0
    assert stats["natr_14"] == 2.0 / 100.0
    # 16 candles is below the 51 needed for the 50-period NATR.
    assert stats["natr_50"] is None


def test_calculate_price_stats_is_empty_without_candles():
    assert _analysis.calculate_price_stats([]) == {}
