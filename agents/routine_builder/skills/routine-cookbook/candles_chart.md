# Candlestick Charts

How to build candlestick charts with overlays (indicators, volume profile) in
Plotly routines — including the shared-Y-axis footprint pattern and datetime
X-axis formatting.

## 1. Datetime X axis (CRITICAL — common pitfall)

Candle timestamps from the Hummingbot API arrive as raw integers (Unix ms or Unix s).
**Never** pass them directly to Plotly — it will display raw numbers on the X axis.

```python
from datetime import datetime, timezone

def _ts_to_dt(ts):
    """Convert raw timestamp (int ms or s) to UTC datetime. Returns None on failure."""
    if ts is None:
        return None
    try:
        ts_f = float(ts)
        if ts_f > 1_000_000_000_000:   # milliseconds (most crypto APIs)
            return datetime.fromtimestamp(ts_f / 1000, tz=timezone.utc)
        elif ts_f > 1_000_000_000:     # seconds
            return datetime.fromtimestamp(ts_f, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        pass
    return None

def _make_ts_list(candles):
    result = []
    for i, c in enumerate(candles):
        ts = c.get("timestamp", c.get("time", c.get("open_time")))
        dt = _ts_to_dt(ts)
        result.append(dt if dt is not None else i)   # fallback to index
    return result
```

Then in the chart:
```python
ts = _make_ts_list(candles)
fig.update_xaxes(type="date", row=1, col=1)   # enforce date type on candle axis
```

## 2. Simple candlestick + indicator overlay (single subplot)

```python
import plotly.graph_objects as go
from plotly.subplots import make_subplots

fig = go.Figure()
fig.add_trace(go.Candlestick(
    x=ts, open=opens, high=highs, low=lows, close=closes,
    name="Candles",
    increasing_line_color="#22c55e", decreasing_line_color="#ef4444",
))
# EMA / SMA overlays use the same x and y axes automatically
fig.add_trace(go.Scatter(x=ts, y=ema9_series, mode="lines", name="EMA 9",
                         line=dict(color="#f59e0b", width=1.5)))
fig.update_layout(
    xaxis_rangeslider_visible=False,
    xaxis_type="date",          # datetime labels
    template="plotly_dark", height=450,
)
```

## 3. Footprint chart — candlestick + volume profile sharing the SAME Y (price) axis

**Root cause of misalignment**: `go.Bar(orientation="h")` can silently coerce the shared Y
axis to "category" type when `shared_yaxes=True` is used in make_subplots. This breaks
price-level alignment. Use **`go.Scatter` with `fill="toself"`** instead — it always
keeps the Y axis numeric.

### Pattern

```python
from plotly.subplots import make_subplots
import plotly.graph_objects as go

price_lo = min(float(c["low"]) for c in candles)
price_hi = max(float(c["high"]) for c in candles)
n_buckets = 25
bucket_h = (price_hi - price_lo) / n_buckets
half = bucket_h / 2

# Build filled polygon lists (None = separator between buckets)
buy_x, buy_y = [], []
sell_x, sell_y = [], []
for p, b, s in zip(fp_prices, fp_buys, fp_sells):
    # Buy bar: rectangle from x=0 to x=b, at y=[p-half, p+half]
    buy_x.extend([0, b, b, 0, None])
    buy_y.extend([p - half, p - half, p + half, p + half, None])
    # Sell bar: rectangle from x=-s to x=0 (negative = left of zero)
    sell_x.extend([0, -s, -s, 0, None])
    sell_y.extend([p - half, p - half, p + half, p + half, None])

fig = make_subplots(
    rows=1, cols=2,
    column_widths=[0.65, 0.35],
    shared_yaxes=True,                         # link Y axes
    subplot_titles=["1m Candles", "Volume Profile"],
    horizontal_spacing=0.01,
)
fig.add_trace(go.Candlestick(
    x=ts_1m, open=opens, high=highs, low=lows, close=closes,
    name="1m",
    increasing_line_color="#22c55e", decreasing_line_color="#ef4444",
), row=1, col=1)
fig.add_trace(go.Scatter(
    x=buy_x, y=buy_y,
    fill="toself", fillcolor="rgba(34, 197, 94, 0.7)",
    line=dict(width=0), mode="lines", name="Buy Vol",
), row=1, col=2)
fig.add_trace(go.Scatter(
    x=sell_x, y=sell_y,
    fill="toself", fillcolor="rgba(239, 68, 68, 0.7)",
    line=dict(width=0), mode="lines", name="Sell Vol",
), row=1, col=2)

fig.update_layout(
    title="Footprint Chart",
    template="plotly_dark", height=600,
    xaxis_rangeslider_visible=False,
)
fig.update_xaxes(title_text="Time", type="date", row=1, col=1)
fig.update_xaxes(title_text="Volume (buy+ / sell−)", row=1, col=2)
# Force shared Y to linear and pin to the actual price range so both panels align
fig.update_yaxes(
    type="linear",
    range=[price_lo * 0.999, price_hi * 1.001],
    title_text="Price",
)
```

### Why Scatter polygons (not go.Bar)?

| Approach | Y axis type | Price alignment |
|---|---|---|
| `go.Bar(orientation="h", y=fp_prices)` | Sometimes silently "category" | **Breaks** with shared_yaxes |
| `go.Scatter(fill="toself", y=price_coords)` | Always "linear" | **Correct** |

The `go.Bar` approach works when the bar chart occupies its own subplot with an independent
Y axis, but fails when the Y must be a numeric price axis shared with a candlestick.

## 4. Reference implementation

See `market_analyzer` routine in `agents/market_making_expert/routines/market_analyzer.py`
for a full working example (footprint chart section).
