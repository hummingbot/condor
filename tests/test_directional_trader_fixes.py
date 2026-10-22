"""The Directional Trader's backtest and development findings (W6).

**DT-3** — ``df.ta.supertrend()`` emits ``SUPERTl`` and ``SUPERTs`` as a
complementary pair: exactly one is NaN on every row, and in a one-directional
window one of them is NaN on *all* of them. The backtester ``dropna()``s the
feature frame as a whole, so publishing both deleted every row and the run
returned HTTP 500 with nothing to read.

**DT-4** — features are merged onto the backtesting-resolution candles with
``merge_asof(direction="backward")`` on the candle **open** timestamp, so a
resolution finer than the controller's interval lets every row inside a candle
see that candle's close. Every skill mandated ``1m`` regardless of interval —
while ``bollinger_v1``'s own CONTROLLER.md had documented the hazard and its fix
all along.

**DT-6/7/8** — the loop filtered for ``ema_`` (which nothing shipped matches),
ran up to 8 synchronous backtests inside a 60-second tick, and priced them at a
maker fee while the controllers trade as takers.

**DT-9/10/11/12/13/14** — documentation that described a system other than the
one that exists: a signal of 0 that closes nothing, Bollinger column names with
one std instead of two, gates on metrics the backtester never returns, the wrong
tool for uploading controller code, a kill switch that does more than it says,
and a 252-day year for a market with no weekends.
"""

import re
from pathlib import Path

import numpy as np
import pandas as pd
import pandas_ta  # noqa: F401  (registers the .ta accessor)
import pytest

DT = Path("agents/directional_trader")
LOOP = DT / "loops/ema_trend_loop/loop.md"
CONTROLLERS = ["supertrend_v1", "macd_bb_v1", "bollinger_v1"]


def _trending_candles(n: int = 120) -> pd.DataFrame:
    """A one-directional window — the shape that empties the frame."""
    close = np.linspace(100, 120, n)
    return pd.DataFrame({"high": close + 1, "low": close - 1, "close": close})


# ── DT-3: the feature frame survives dropna ─────────────────────────────────


def test_the_mutually_exclusive_bands_would_empty_the_frame():
    """The premise, so the fix below is not mistaken for superstition."""
    df = _trending_candles()
    df.ta.supertrend(length=20, multiplier=3.0, append=True)

    assert df["SUPERTs_20_3.0"].isna().all()
    assert df.dropna().empty


def test_the_controller_drops_the_bands_it_does_not_read():
    source = (DT / "controllers/supertrend_v1/supertrend_v1.py").read_text()

    assert "df.drop(columns=" in source
    assert "SUPERTl_" in source and "SUPERTs_" in source
    # ...before publishing, not after.
    assert source.index("df.drop(columns=") < source.index('"features"] = df')


def test_the_published_features_survive_a_whole_frame_dropna():
    """What the backtester does to the frame it is handed."""
    df = _trending_candles()
    df.ta.supertrend(length=20, multiplier=3.0, append=True)
    df["percentage_distance"] = abs(df["close"] - df["SUPERT_20_3.0"]) / df["close"]
    df["signal"] = 0

    published = df.drop(columns=["SUPERTl_20_3.0", "SUPERTs_20_3.0"])

    assert not published.dropna().empty
    # The columns the signal actually reads are still there.
    assert "SUPERTd_20_3.0" in published
    assert "SUPERT_20_3.0" in published


@pytest.mark.parametrize("controller", ["macd_bb_v1", "bollinger_v1"])
def test_the_other_controllers_have_no_such_column(controller):
    """bbands and macd emit nothing all-NaN, so only supertrend needed this."""
    df = _trending_candles()
    df.ta.bbands(length=20, lower_std=2.0, upper_std=2.0, append=True)
    if controller == "macd_bb_v1":
        df.ta.macd(fast=12, slow=26, signal=9, append=True)

    assert not any(df[c].isna().all() for c in df.columns)
    assert not df.dropna().empty


# ── DT-4: the resolution matches the interval ───────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        LOOP,
        DT / "skills/backtesting/go_no_go.md",
        DT / "skills/backtesting/parameter_sweep.md",
        DT / "skills/deploy_and_monitor/SKILL.md",
        Path("agents/_shared/skills/backtest_flow/SKILL.md"),
    ],
)
def test_nothing_mandates_a_finer_resolution_than_the_interval(path):
    assert '"resolution": "1m"' not in path.read_text()


def test_the_hazard_is_explained_where_the_gate_is_decided():
    body = (DT / "skills/backtesting/go_no_go.md").read_text()

    assert "merge_asof" in body
    assert "open** timestamp" in body
    assert "upper bound" in body


def test_the_controller_doc_no_longer_recommends_finer():
    body = (DT / "controllers/macd_bb_v1/CONTROLLER.md").read_text()

    assert "at least as fine as" not in body
    assert "EQUAL to `interval`" in body


# ── DT-6/7/8: the loop's selection and economics ────────────────────────────


def test_the_loop_does_not_filter_on_a_name_nothing_ships():
    body = LOOP.read_text()

    assert "contains `ema_`" not in body
    assert "do not filter on a name substring" in body


def test_no_shipped_config_would_have_matched_that_filter():
    """The premise: a clean install has no ``ema_`` controller at all."""
    shipped = {p.name for p in (DT / "controllers").iterdir() if p.is_dir()}

    assert shipped == set(CONTROLLERS)
    assert not any(name.startswith("ema_") for name in shipped)


def test_the_tick_budget_bounds_the_backtests():
    body = LOOP.read_text()

    assert "At most TWO configs per tick" in body
    assert "up to 8" not in body


def test_the_frequency_fits_the_work():
    import yaml

    config = yaml.safe_load(LOOP.read_text().split("---")[1])["default_config"]

    assert config["frequency_sec"] >= 300


def test_backtests_are_priced_as_takers():
    body = LOOP.read_text()

    assert '"trade_cost": 0.0006' in body
    assert '"trade_cost": 0.0002' not in body


# ── DT-9/12: AGENT.md describes the system that exists ──────────────────────


def test_signal_zero_is_not_described_as_closing():
    body = (DT / "AGENT.md").read_text()

    assert "does NOT close anything" in body
    assert "no exit at all" in body


def test_controller_code_goes_through_the_tool_that_accepts_it():
    body = (DT / "AGENT.md").read_text()

    assert "manage_agent_controllers" in body
    assert (
        "Controller upload to the Hummingbot backend API (`manage_controllers`)"
        not in body
    )


# ── DT-13: the kill switch does what it says ────────────────────────────────


@pytest.mark.parametrize("controller", CONTROLLERS)
def test_the_kill_switch_row_names_the_executors(controller):
    """It stops the controller AND its executors, closing position executors by
    default — two of the three docs said only "stops new executors"."""
    body = (DT / f"controllers/{controller}/CONTROLLER.md").read_text()
    row = next(line for line in body.splitlines() if "`manual_kill_switch`" in line)

    assert "executors" in row
    assert "new executors" not in row


# ── DT-10/11: the checklists ask for things that exist ──────────────────────


def test_the_bollinger_column_pattern_carries_both_stds():
    """``bbands`` takes lower and upper separately and puts BOTH in the name, so
    a 20/2.0 band is ``BBL_20_2.0_2.0``."""
    df = _trending_candles()
    df.ta.bbands(length=20, lower_std=2.0, upper_std=2.0, append=True)
    assert "BBL_20_2.0_2.0" in df.columns

    body = (DT / "skills/controller_development/SKILL.md").read_text()
    assert "BBL_{length}_{lower_std}_{upper_std}" in body
    assert "BBL_20_2.0_2.0" in body


def test_the_checklist_warns_about_the_dropna_trap():
    body = (DT / "skills/controller_development/SKILL.md").read_text()

    assert "mutually exclusive feature columns" in body
    assert "SUPERTl" in body


def test_the_checklist_requires_declared_feeds():
    body = (DT / "skills/controller_development/SKILL.md").read_text()

    assert "get_candles_config()" in body


@pytest.mark.parametrize("controller", CONTROLLERS)
def test_every_shipped_controller_declares_its_feeds(controller):
    source = (DT / f"controllers/{controller}/{controller}.py").read_text()

    assert "def get_candles_config" in source


def test_no_checklist_gates_on_a_metric_the_backtester_lacks():
    """``avg_trade_duration_hours`` was required and is never returned."""
    for path in list((DT / "skills").rglob("*.md")):
        body = path.read_text()
        for line in body.splitlines():
            if "avg_trade_duration" in line:
                assert "does not return" in body, f"{path} still asks for it"


def test_the_circuit_breaker_is_the_real_argument():
    body = (DT / "skills/deploy_and_monitor/SKILL.md").read_text()

    assert "max_global_drawdown_quote" in body
    assert "- [ ] **`max_loss_quote`**" not in body


def test_leverage_is_not_gated_against_a_simulation_that_does_not_happen():
    body = (DT / "skills/deploy_and_monitor/SKILL.md").read_text()

    assert "does NOT simulate it" in body
    assert "never deploy at a different leverage than" not in body


# ── DT-14: a year with no weekends ──────────────────────────────────────────


def test_the_research_routine_annualises_on_calendar_days():
    source = (DT / "routines/ema_research_charts.py").read_text()

    assert "CALENDAR_DAYS = 365" in source
    assert "252" not in re.sub(r"#.*", "", source)


@pytest.mark.parametrize(
    "path",
    [DT / "AGENT.md", DT / "skills/research/SKILL.md"],
)
def test_the_documented_snippets_annualise_the_same_way(path):
    body = path.read_text()

    assert "(365 ** 0.5)" in body
    assert "(252 ** 0.5)" not in body


def test_the_two_routine_sites_share_one_definition():
    """They carried the number inline, which is how they would drift apart."""
    source = (DT / "routines/ema_research_charts.py").read_text()

    assert source.count("CALENDAR_DAYS * bars_per_day") == 2


# ── review follow-ups ───────────────────────────────────────────────────────


def test_an_opposite_signal_is_not_offered_as_an_exit():
    """``stop_actions_proposal`` returns nothing on a flip, so a -1 after a +1
    opens a second executor and leaves the first position open."""
    body = " ".join((DT / "AGENT.md").read_text().split())

    assert "does not close the existing position" in body or (
        "opens a second executor and leaves the first position open" in body
    )
    assert "or an explicit opposite signal" not in body


def test_leverage_is_set_where_it_is_actually_read():
    """``manage_bots(action="deploy")`` has no ``leverage`` argument and its risk
    check does not compare a controller's leverage against ``max_leverage``."""
    import inspect

    from mcp_servers.hummingbot_api import server as hb

    assert "leverage" not in inspect.signature(hb.manage_bots).parameters

    body = " ".join((DT / "skills/deploy_and_monitor/SKILL.md").read_text().split())
    assert "saved controller config" in body
    assert "has no `leverage` argument" in body


def test_non_grid_work_is_not_routed_to_the_grid_agent():
    """``adaptive_grid_trader`` excludes "position executors without grid
    structure" and DCA in as many words."""
    excluded = " ".join(
        Path("agents/adaptive_grid_trader/AGENT.md").read_text().split()
    )
    assert "Non-grid strategies" in excluded

    deploy = " ".join((DT / "skills/deploy_and_monitor/SKILL.md").read_text().split())
    assert "do **not** hand it to `adaptive_grid_trader`" in deploy


@pytest.mark.parametrize(
    "path",
    [
        Path("agents/_shared/skills/backtest_flow/SKILL.md"),
        DT / "skills/backtesting/windows_and_costs.md",
    ],
)
def test_no_guide_still_sends_the_winner_back_to_1m(path):
    """Both taught the rule backwards — 1m as the honest final check, when on a
    coarser controller it is the optimistic one."""
    body = " ".join(path.read_text().split())

    assert "re-run the winner at `1m`" not in body
    assert "merge_asof" in body
