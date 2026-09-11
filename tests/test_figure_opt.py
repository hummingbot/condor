"""The report figure optimizer must shrink big charts without redrawing them."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

from condor.reports.figure_opt import GL_THRESHOLD, optimize_figure

BIG = GL_THRESHOLD + 100


def _minutes(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2026-07-28T04:57:00", periods=n, freq="1min")


def _traces(spec: dict) -> list[dict]:
    return spec["data"]


def test_small_figure_is_left_alone():
    fig = go.Figure(go.Scatter(x=_minutes(50), y=np.arange(50), mode="lines"))
    trace = _traces(optimize_figure(fig))[0]
    assert trace["type"] == "scatter"
    assert "x0" not in trace
    assert len(trace["x"]) == 50


def test_regular_timestamps_collapse_to_x0_dx():
    fig = go.Figure(go.Scatter(x=_minutes(BIG), y=np.arange(BIG), mode="lines"))
    trace = _traces(optimize_figure(fig))[0]
    assert "x" not in trace
    assert trace["dx"] == 60_000  # one minute, in milliseconds
    assert trace["x0"].startswith("2026-07-28T04:57:00")


def test_irregular_timestamps_keep_their_x_array():
    stamps = _minutes(BIG).delete(10)  # punch a hole in the series
    fig = go.Figure(go.Scatter(x=stamps, y=np.arange(len(stamps)), mode="lines"))
    trace = _traces(optimize_figure(fig))[0]
    assert "x0" not in trace
    assert len(trace["x"]) == len(stamps)


def test_large_line_moves_to_webgl():
    fig = go.Figure(go.Scatter(x=_minutes(BIG), y=np.arange(BIG), mode="lines"))
    assert _traces(optimize_figure(fig))[0]["type"] == "scattergl"


def test_filled_trace_stays_on_svg():
    """scattergl draws fills differently; a tozeroy area must keep its renderer."""
    fig = go.Figure(
        go.Scatter(x=_minutes(BIG), y=np.arange(BIG), mode="lines", fill="tozeroy")
    )
    trace = _traces(optimize_figure(fig))[0]
    assert trace["type"] == "scatter"
    assert "x0" in trace  # still gets the byte saving


def test_band_partner_is_pinned_to_svg():
    """A tonexty band fills against the previous trace, so both must match type.

    Promoting only the upper band leaves Plotly unable to pair them, and it
    silently fills to zero instead -- which drags the axis down to the origin.
    """
    x = _minutes(BIG)
    fig = go.Figure(
        [
            go.Scatter(x=x, y=np.full(BIG, 2.0), mode="lines", name="upper"),
            go.Scatter(x=x, y=np.full(BIG, 1.0), mode="lines", fill="tonexty"),
        ]
    )
    types = [t["type"] for t in _traces(optimize_figure(fig))]
    assert types == ["scatter", "scatter"]


def test_two_colour_bar_becomes_two_filled_areas():
    y = np.where(np.arange(BIG) % 2, 1.0, -1.0)
    colors = np.where(y >= 0, "#26a69a", "#ef5350")
    fig = go.Figure(go.Bar(x=_minutes(BIG), y=y, marker_color=colors, name="Hist"))
    out = _traces(optimize_figure(fig))
    assert len(out) == 2
    assert {t["type"] for t in out} == {"scatter"}
    assert all(t["fill"] == "tozeroy" for t in out)
    # Exactly one legend entry survives the split.
    assert sum(bool(t["showlegend"]) for t in out) == 1
    # Each area masks the other colour's points to the zero line, so the two
    # together reproduce the original series.
    assert np.allclose(out[0]["y"] + out[1]["y"], y)


def test_many_colour_bar_is_left_as_bars():
    """Per-point colours that carry real information must not be flattened."""
    colors = [f"#{i % 500:06x}" for i in range(BIG)]
    fig = go.Figure(go.Bar(x=_minutes(BIG), y=np.arange(BIG), marker_color=colors))
    assert _traces(optimize_figure(fig))[0]["type"] == "bar"


def test_small_bar_is_left_as_bars():
    fig = go.Figure(go.Bar(x=_minutes(50), y=np.arange(50)))
    assert _traces(optimize_figure(fig))[0]["type"] == "bar"


def test_input_figure_is_not_mutated():
    """Callers also export the original through kaleido, which needs plain SVG."""
    fig = go.Figure(go.Scatter(x=_minutes(BIG), y=np.arange(BIG), mode="lines"))
    optimize_figure(fig)
    assert fig.data[0].type == "scatter"
    assert len(fig.data[0].x) == BIG


def test_a_broken_figure_falls_back_instead_of_raising():
    """This runs for every report, so it must never be why one fails to render."""

    class Exploding:
        def to_dict(self):
            return {"data": [{"type": "scatter", "x": object(), "y": object()}]}

    assert optimize_figure(Exploding())["data"]


@pytest.mark.parametrize("mode", ["text", "lines+text"])
def test_text_modes_stay_on_svg(mode):
    fig = go.Figure(
        go.Scatter(x=_minutes(BIG), y=np.arange(BIG), mode=mode, text=["a"] * BIG)
    )
    assert _traces(optimize_figure(fig))[0]["type"] == "scatter"
