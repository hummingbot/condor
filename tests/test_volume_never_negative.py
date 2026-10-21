"""CORR-731: traded volume is never reported negative.

GitHub issue #280 -- an agent's journal printed a negative volume column during
a race in which volume was a scored criterion. Three independent claims are
pinned here, one per source the issue traced:

*Cumulative resets.* ``slice_history`` differences each instance's cumulative
across the window, which is only the volume traded while that counter keeps
counting. A restart drops it back to zero, and the difference goes negative.
The fix treats the drop as a new segment, so the window stays non-negative
**and** keeps the volume traded after the reset -- a plain ``max(0, ...)``
clamp would have been non-negative and wrong.

*Signed fields.* ``get_executor_volume`` reads a magnitude from an allow-list
of traded-volume fields, so neither a signed backend figure nor a position
quantity can be totalled as volume.

*Display.* A negative volume that still reaches the journal or the session
report is flagged as a data error rather than formatted, because a clamp at the
point of display hides the next instance of this bug.

The fourth claim is the one that constrains the first: a monotonic history must
slice to exactly what it sliced before the change.
"""

import pytest

from condor.agents.journal import JournalManager, _parse_snapshot_line
from condor.agents.session_report import SessionReport
from condor.agents.volume_render import VOLUME_DATA_ERROR, format_volume_usd
from condor.fetchers.bot_performance import (
    _cum_at,
    slice_history,
    slice_history_series,
)
from condor.fetchers.executors import get_executor_volume

# One instance that trades 100 of volume, restarts, then trades 50 more. Rows
# are (epoch, realized, volume, trades, fees) cumulatives.
RESET_HIST = [
    (10.0, 1.0, 100.0, 2.0, 0.1),
    (20.0, 2.0, 300.0, 6.0, 0.3),
    (30.0, 0.5, 50.0, 1.0, 0.05),  # restart: every counter back to a fresh start
    (40.0, 1.5, 180.0, 4.0, 0.18),
]

MONOTONIC_HIST = [
    (10.0, 1.0, 100.0, 1.0, 0.1),
    (20.0, 3.0, 300.0, 4.0, 0.3),
    (30.0, 5.0, 500.0, 9.0, 0.5),
]


def _old_slice(histories, start, end):
    """The unclamped differencing this change replaced, for parity checks."""
    realized = volume = trades = fees = 0.0
    for h in histories:
        r_e, v_e, t_e, f_e = _cum_at(h, end)
        r_s, v_s, t_s, f_s = _cum_at(h, start)
        realized += r_e - r_s
        volume += v_e - v_s
        trades += t_e - t_s
        fees += f_e - f_s
    return realized, volume, trades, fees


# ── Cumulative resets ──


def test_a_reset_mid_window_does_not_produce_negative_volume():
    # Before the fix: cum_at(50)=180 - cum_at(0)=0 is 180, which is not even
    # the bug's worst shape. Over [20, 50] it was 180 - 300 = -120.
    assert _old_slice([RESET_HIST], 20.0, 50.0)[1] == -120.0

    _, volume, trades, fees = slice_history([RESET_HIST], 20.0, 50.0)
    assert volume >= 0.0
    # The segment treatment keeps the trading done after the reset: the counter
    # restarted and reached 180, all of it inside this window.
    assert volume == pytest.approx(180.0)
    assert trades == pytest.approx(4.0)
    assert fees == pytest.approx(0.18)


def test_the_whole_span_counts_both_segments():
    _, volume, trades, fees = slice_history([RESET_HIST], 0.0, 50.0)
    # 300 traded before the restart, 180 after it.
    assert volume == pytest.approx(480.0)
    assert trades == pytest.approx(10.0)
    assert fees == pytest.approx(0.48)


def test_realized_pnl_is_not_offset_as_a_reset():
    """A falling realized PnL is a loss, not a counter reset."""
    losing = [
        (10.0, 5.0, 100.0, 1.0, 0.1),
        (20.0, -3.0, 200.0, 2.0, 0.2),  # closed at a loss; volume still grew
    ]
    realized, volume, _, _ = slice_history([losing], 0.0, 30.0)
    assert realized == pytest.approx(-3.0)
    assert volume == pytest.approx(200.0)


def test_series_over_a_reset_has_no_negative_point_or_step():
    stamps = [10.0, 20.0, 30.0, 40.0, 50.0]
    rows = slice_history_series([RESET_HIST], 0.0, stamps)

    volumes = [v for _, _, v, _, _ in rows]
    assert all(v >= 0.0 for v in volumes), volumes
    assert all(b >= a for a, b in zip(volumes, volumes[1:])), volumes
    for idx in (3, 4):  # trades and fees are cumulative counters too
        col = [row[idx] for row in rows]
        assert all(v >= 0.0 for v in col), col
        assert all(b >= a for a, b in zip(col, col[1:])), col


def test_series_still_matches_per_stamp_slices_across_a_reset():
    """The merge pass and the per-stamp call agree, resets included."""
    h2 = [(5.0, 0.5, 50.0, 1.0, 0.05), (25.0, 1.0, 90.0, 2.0, 0.09)]
    for histories, since in (
        ([RESET_HIST], 0.0),
        ([RESET_HIST], 20.0),
        ([RESET_HIST, h2], 0.0),
        ([RESET_HIST, h2], 11.0),
        ([RESET_HIST, MONOTONIC_HIST], 0.0),
    ):
        stamps = [10.0, 20.0, 25.0, 30.0, 40.0, 50.0]
        expected = [(t, *slice_history(histories, since, t)) for t in stamps]
        assert slice_history_series(histories, since, stamps) == expected


def test_a_monotonic_history_slices_exactly_as_before():
    """The fifth criterion: no reset, no change to any figure, bit for bit."""
    for start, end in ((0.0, 5.0), (0.0, 20.0), (20.0, 40.0), (0.0, 100.0)):
        assert slice_history([MONOTONIC_HIST], start, end) == _old_slice(
            [MONOTONIC_HIST], start, end
        )
    stamps = [10.0, 20.0, 30.0, 40.0]
    assert slice_history_series([MONOTONIC_HIST], 0.0, stamps) == [
        (t, *_old_slice([MONOTONIC_HIST], 0.0, t)) for t in stamps
    ]


# ── Signed fields ──


def test_a_signed_volume_field_yields_its_magnitude():
    assert get_executor_volume({"filled_amount_quote": -250.0}) == 250.0
    assert get_executor_volume({"volume_traded": -1.5}) == 1.5


def test_a_position_quantity_is_not_read_as_volume():
    """Grid's ``position_size_*`` is ``executed - close_executed`` -- signed."""
    ex = {
        "position_size_base": -2.5,
        "position_size_quote": -500.0,
        "net_pnl_quote": -7.0,
    }
    assert get_executor_volume(ex) == 0.0
    # And a real volume field alongside them still wins.
    assert get_executor_volume({**ex, "filled_amount_quote": 42.0}) == 42.0


def test_an_unparseable_volume_field_is_zero_not_a_crash():
    assert get_executor_volume({"filled_amount_quote": "n/a"}) == 0.0


# ── Display ──


def test_the_formatter_flags_a_negative_and_formats_a_positive():
    assert format_volume_usd(1234.5, source="t") == "$1,234"
    assert format_volume_usd(0.0, source="t") == "$0"
    assert VOLUME_DATA_ERROR in format_volume_usd(-1234.5, source="t")
    assert "-1,234" in format_volume_usd(-1234.5, source="t")


def test_a_negative_volume_is_flagged_in_the_journal_snapshot(tmp_path):
    journal = JournalManager("test-agent", session_dir=tmp_path)
    journal.record_snapshot(
        total_pnl=-1.0, total_volume=-500.0, open_count=1, position_size=100.0
    )
    line = [
        ln
        for ln in journal.read_full().splitlines()
        if ln.startswith("- ") and "volume=" in ln
    ][-1]
    assert f"volume={VOLUME_DATA_ERROR}" in line
    assert "volume=$-500" not in line

    # The flag must not break the parser the drawdown guard reads through.
    entry = _parse_snapshot_line(line)
    assert "volume" not in entry
    assert entry["pnl"] == -1.0
    assert entry["exposure"] == 100.0
    assert journal.get_total_volume() == 0.0
    assert journal.get_drawdown_pct_checked()[1] is True


def test_a_positive_volume_still_reads_back_from_the_journal(tmp_path):
    journal = JournalManager("test-agent", session_dir=tmp_path)
    journal.record_snapshot(
        total_pnl=1.0, total_volume=12345.0, open_count=1, position_size=100.0
    )
    assert journal.get_total_volume() == 12345.0


class _KpiSpy:
    def __init__(self) -> None:
        self.kpis: dict[str, str] = {}

    def kpi(self, label, value, **_kw) -> None:
        self.kpis[label] = value


def test_a_negative_volume_is_flagged_in_the_session_report_kpi():
    spy = _KpiSpy()
    SessionReport._kpis(spy, {"total_volume": -900.0})
    assert VOLUME_DATA_ERROR in spy.kpis["Volume"]

    spy = _KpiSpy()
    SessionReport._kpis(spy, {"total_volume": 900.0})
    assert spy.kpis["Volume"] == "$900"
