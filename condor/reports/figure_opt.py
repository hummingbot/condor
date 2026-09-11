"""Re-encode large Plotly figures so reports stay small and stay interactive.

A backtest over a month of 1m candles hands ``ReportBuilder.plotly`` a figure
with a dozen traces of ~44k points each. Serialized naively that is a 20MB
payload -- two thirds of it the same x-axis timestamps repeated once per trace --
drawn as SVG, which the browser renders as half a million DOM nodes. The report
loads, but panning and hovering it is miserable.

This module rewrites such a figure into an equivalent one that draws the same
picture from far less data:

* regularly spaced x arrays collapse to ``x0``/``dx`` (the single biggest win --
  it deletes the duplicated timestamps entirely),
* big SVG scatters become ``scattergl`` so the GPU draws them,
* big bar traces become filled areas, since at these densities the bars are
  narrower than a pixel anyway -- and one SVG path is far cheaper than tens of
  thousands of ``<rect>`` nodes.

Everything is threshold-gated, so a figure with a few hundred points passes
through untouched. Optimization happens on a *copy* of the figure: callers that
also render the original through kaleido keep a plain SVG figure to export, as
static export of WebGL traces is unreliable.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Below this many points an SVG scatter is perfectly smooth, so leave it alone.
GL_THRESHOLD = 5_000

# Collapsing x to x0/dx saves bytes well before rendering becomes a problem.
X0DX_THRESHOLD = 1_000

# Scatter modes safe to hand to the WebGL renderer. Anything involving text
# labels stays on SVG, where glyph layout is reliable.
_GL_SAFE_MODES = {"lines", "markers", "lines+markers"}

_NS_PER_MS = 1_000_000


def _decode_bdata(value: dict) -> np.ndarray | None:
    """Decode Plotly's ``{"dtype": ..., "bdata": ...}`` binary array form.

    ``Figure.to_dict`` hands back numeric columns already base64-encoded, so a
    check that only understands lists and numpy arrays sees nothing at all and
    silently declines to optimize every real figure.
    """
    if "bdata" not in value:
        return None
    if value.get("shape") and "," in str(value["shape"]):
        return None  # 2-D payload; not something this module reasons about
    import base64

    try:
        return np.frombuffer(
            base64.b64decode(value["bdata"]), dtype=np.dtype(value.get("dtype", "f8"))
        )
    except Exception:
        return None


def _as_array(value: Any) -> np.ndarray | None:
    """Return ``value`` as a 1-D numpy array, or None if it is not array-like."""
    if value is None or isinstance(value, (str, bytes)):
        return None
    if isinstance(value, dict):
        arr = _decode_bdata(value)
        return arr if arr is not None and arr.size else None
    try:
        arr = np.asarray(value)
    except Exception:
        return None
    return arr if arr.ndim == 1 and arr.size else None


def _regular_step(arr: np.ndarray) -> tuple[Any, float] | None:
    """Return ``(x0, dx)`` when ``arr`` is evenly spaced, else None.

    ``dx`` is milliseconds for datetime axes -- the unit Plotly expects on a
    date axis -- and the raw numeric step otherwise.
    """
    if arr.size < 2:
        return None

    if np.issubdtype(arr.dtype, np.datetime64):
        ints = arr.astype("datetime64[ns]").astype(np.int64)
        diffs = np.diff(ints)
        if diffs[0] <= 0 or not np.all(diffs == diffs[0]):
            return None
        import pandas as pd

        return pd.Timestamp(arr[0]).isoformat(), float(diffs[0]) / _NS_PER_MS

    if np.issubdtype(arr.dtype, np.number):
        diffs = np.diff(arr.astype(np.float64))
        if diffs[0] <= 0 or not np.allclose(diffs, diffs[0], rtol=0, atol=0):
            return None
        return float(arr[0]), float(diffs[0])

    # Object arrays of ISO strings: let pandas parse, then re-check as datetimes.
    if arr.dtype == object and isinstance(arr[0], str):
        import pandas as pd

        try:
            parsed = pd.to_datetime(arr, errors="raise", utc=False)
        except Exception:
            return None
        return _regular_step(np.asarray(parsed.values))

    return None


def _collapse_x(trace: dict) -> bool:
    """Replace an evenly spaced ``x`` array with ``x0``/``dx``. True if applied."""
    if "x0" in trace or "dx" in trace:
        return False
    arr = _as_array(trace.get("x"))
    if arr is None or arr.size < X0DX_THRESHOLD:
        return False
    step = _regular_step(arr)
    if step is None:
        return False
    trace["x0"], trace["dx"] = step
    trace.pop("x", None)
    return True


def _point_count(trace: dict) -> int:
    for key in ("y", "x"):
        arr = _as_array(trace.get(key))
        if arr is not None:
            return int(arr.size)
    return 0


def _is_filled(trace: dict) -> bool:
    """True when the trace paints an area, not just a stroke."""
    fill = trace.get("fill")
    return bool(fill) and fill != "none"


def _fill_partner_indices(traces: list[dict]) -> set[int]:
    """Indices that must keep their renderer because a neighbour fills to them.

    ``fill="tonexty"`` paints the band between a trace and the one before it --
    that is how a Bollinger band is drawn. Plotly can only fill between two
    traces of the *same* type, and when they differ it silently falls back to
    filling to zero, which drags the whole axis down to the origin. So a
    ``tonext*`` trace pins its predecessor to SVG alongside itself.
    """
    pinned: set[int] = set()
    for i, trace in enumerate(traces):
        if str(trace.get("fill") or "").startswith("tonext") and i > 0:
            pinned.add(i - 1)
            pinned.add(i)
    return pinned


def _promote_scatter(trace: dict, pinned: bool = False) -> bool:
    """Move a large SVG scatter onto the WebGL renderer. True if applied."""
    if pinned:
        return False
    if trace.get("type") != "scatter":
        return False
    if _point_count(trace) < GL_THRESHOLD:
        return False
    mode = trace.get("mode")
    if mode not in _GL_SAFE_MODES:
        return False
    # Per-point text is laid out per glyph; scattergl handles it poorly.
    if trace.get("text") is not None or trace.get("texttemplate"):
        return False
    # Filled areas are where the WebGL renderer visibly parts ways with SVG:
    # ``tozeroy`` can close the polygon across the series instead of along the
    # zero line, and ``tonexty`` depends on neighbouring-trace order that the
    # GL path does not honour the same way. Filled traces keep their SVG
    # renderer -- they still get the x0/dx saving, which is the larger win.
    if _is_filled(trace):
        return False
    trace["type"] = "scattergl"
    return True


def _split_bar_colors(trace: dict) -> list[dict] | None:
    """Split a bar trace into one filled area per distinct marker colour.

    A MACD-style histogram carries a per-point colour array with exactly two
    values (up green, down red). Masking each colour to zero elsewhere and
    filling to the zero line reproduces the histogram's silhouette. Traces with
    many distinct colours carry information a two-tone fill would destroy, so
    they are left alone.
    """
    marker = trace.get("marker") or {}
    colors = marker.get("color")
    arr = _as_array(colors)

    y = _as_array(trace.get("y"))
    if y is None:
        return None
    y = y.astype(np.float64)

    base = {
        k: v
        for k, v in trace.items()
        if k
        in (
            "x",
            "x0",
            "dx",
            "name",
            "legendgroup",
            "hovertemplate",
            "xaxis",
            "yaxis",
            "opacity",
        )
    }

    def _area(values: np.ndarray, color: Any, name: str, show: bool) -> dict:
        return {
            **base,
            "type": "scatter",
            "mode": "lines",
            "y": values,
            "line": {"width": 0, "color": color},
            "fill": "tozeroy",
            "fillcolor": color,
            "name": name,
            "showlegend": show,
            "legendgroup": trace.get("legendgroup") or trace.get("name") or name,
        }

    if arr is None:
        # One flat colour for the whole series.
        color = colors if isinstance(colors, str) else marker.get("color")
        return [_area(y, color, trace.get("name") or "", trace.get("showlegend", True))]

    uniques = list(dict.fromkeys(arr.tolist()))
    if len(uniques) != 2:
        return None

    name = trace.get("name") or ""
    out = []
    for i, color in enumerate(uniques):
        masked = np.where(arr == color, y, 0.0)
        out.append(_area(masked, color, name, trace.get("showlegend", True) and i == 0))
    return out


def _rewrite_bar(trace: dict) -> list[dict] | None:
    """Turn a very large bar trace into filled areas. None to leave it as is."""
    if trace.get("type") != "bar":
        return None
    if _point_count(trace) < GL_THRESHOLD:
        return None
    return _split_bar_colors(trace)


def optimize_figure(fig: Any) -> dict:
    """Return a display-optimized figure spec.

    Takes a ``go.Figure`` (or a figure dict) and returns a plain dict suitable
    for ``plotly.io.to_html``. The input figure is never mutated. Any failure
    falls back to the unmodified spec -- this sits on the path of every report,
    so it must never be the reason one fails to render.
    """
    spec = fig.to_dict() if hasattr(fig, "to_dict") else dict(fig)
    try:
        traces_in = list(spec.get("data") or [])
        traces_out: list[dict] = []
        stats = {"x0dx": 0, "gl": 0, "bars": 0}
        pinned = _fill_partner_indices(traces_in)

        for index, original in enumerate(traces_in):
            trace = dict(original)
            if _collapse_x(trace):
                stats["x0dx"] += 1

            replacement = _rewrite_bar(trace)
            if replacement is not None:
                stats["bars"] += 1
                traces_out.extend(replacement)
                continue

            if _promote_scatter(trace, pinned=index in pinned):
                stats["gl"] += 1
            traces_out.append(trace)

        if any(stats.values()):
            logger.debug(
                "figure optimized: %d traces, x0/dx=%d gl=%d bars=%d",
                len(traces_in),
                stats["x0dx"],
                stats["gl"],
                stats["bars"],
            )
        spec["data"] = traces_out
        return spec
    except Exception:
        logger.warning("figure optimization failed; using original", exc_info=True)
        return spec
