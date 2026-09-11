---
name: charting
description: When and how to render charts — dashboard ```chart fence vs ReportBuilder.plotly;
  pick the right path, format correctly, and downsample when needed.
when_to_use: 'Agent needs to show any visual: a time series, PNL curve, portfolio
  evolution, volume bar chart, price comparison, distribution — any chart or graph.
  Read this BEFORE reaching for ReportBuilder or writing a chart block. Triggers:
  "show as a chart", "plot the PNL", "give me a line chart", "chart the evolution",
  "visualize this", "show the distribution".'
created: '2026-09-04T10:07:23Z'
source: chat
---

## Charting — pick the right path first

There are **two mechanisms**. Pick before writing any code.

| | ` ```chart ` fence | `ReportBuilder.plotly(fig)` |
|---|---|---|
| Where it appears | Inline in the chat response | Saved as a named report in the dashboard |
| Requires `run_code`? | **No** — just output the fenced block in your reply | Yes |
| Max data points | **200** | Unlimited |
| Max series | **4** | Unlimited |
| Chart types | `line`, `area`, `bar` | Full Plotly (scatter, heatmap, etc.) |
| Persistent / shareable | No — lives in the chat turn | Yes — stays in the Reports panel |
| When to use | **Default** — use this unless you need persistence or > 200 points | Large datasets, saved reports, complex layouts |

**Default to the ` ```chart ` fence.** Only reach for ReportBuilder when the data exceeds 200 points or the user explicitly wants a saved report.

---

### Path 1 — ` ```chart ` fence (no run_code needed)

Emit this block anywhere in your reply. The dashboard renders it as an interactive chart.

~~~
```chart
{
  "type":  "line",
  "title": "My Chart Title",
  "x":     "time",
  "series": [
    {"key": "pnl",    "name": "Global PNL"},
    {"key": "volume", "name": "Volume", "color": "#888888"}
  ],
  "data": [
    {"time": "2026-09-04T08:00:00Z", "pnl": 120.5, "volume": 45000},
    {"time": "2026-09-04T09:00:00Z", "pnl": 134.2, "volume": 52000}
  ]
}
```
~~~

**Required fields:**
- `type`: `"line"` | `"area"` | `"bar"`
- `x`: field name in each data row used as the x-axis
- `series`: array of `{key, name}` — `key` must match a field in each data row
- `data`: array of objects, each object is one point

**Optional per series:**
- `color`: `"up"` (green) | `"down"` (red) | `"yellow"` | any hex string e.g. `"#3b82f6"`

**Constraints:**
- Max **200 data points** — downsample if you have more (see snippet below)
- Max **4 series**
- All data rows must have the same keys

**Time formatting:** ISO-8601 strings work well as x values (`"2026-09-04T08:00:00Z"`). Short labels also work (`"08:00"`, `"Mon"`, `"Sep 4"`).

---

### Downsampling to 200 points (inside run_code)

When your data exceeds 200 points, downsample before emitting the chart block:

```python
def downsample(rows, max_pts=200):
    if len(rows) <= max_pts:
        return rows
    step = len(rows) / max_pts
    return [rows[int(i * step)] for i in range(max_pts)]

# Example: rows is a list of {time, pnl, volume} dicts
chart_data = downsample(rows)
print("```chart")
import json
print(json.dumps({
    "type": "line",
    "title": "Controller PNL 24h",
    "x": "time",
    "series": [{"key": "pnl", "name": "PNL"}],
    "data": chart_data
}))
print("```")
```

For **multi-controller** data (many controllers × many timestamps), pick the top N controllers by absolute last PNL and emit one series per controller — max 4:

```python
from collections import defaultdict

# Group rows by controller
by_ctrl = defaultdict(list)
for r in rows:
    by_ctrl[r["controller"]].append(r)

# Pick top 4 by last PNL magnitude
top4 = sorted(by_ctrl.items(), key=lambda x: abs(x[1][-1]["pnl"]), reverse=True)[:4]

# Build wide-format rows: {time, ctrl_a, ctrl_b, ...}
times = sorted(set(r["time"] for r in rows))
wide = []
for t in times:
    row = {"time": t}
    for ctrl, ctrl_rows in top4:
        match = next((r for r in ctrl_rows if r["time"] == t), None)
        row[ctrl[:20]] = match["pnl"] if match else None
    wide.append(row)

wide = downsample(wide)
series = [{"key": ctrl[:20], "name": ctrl[:20]} for ctrl, _ in top4]
```

---

### Path 2 — `ReportBuilder.plotly(fig)` (saved report)

Use when: data > 200 points OR user explicitly wants a persistent/shareable report.

```python
# Inside run_code
import plotly.graph_objects as go
from condor.reports import ReportBuilder

fig = go.Figure()
for ctrl, ctrl_rows in by_ctrl.items():
    fig.add_trace(go.Scatter(
        x=[r["time"] for r in ctrl_rows],
        y=[r["pnl"]  for r in ctrl_rows],
        mode="lines",
        name=ctrl[:30],
    ))
fig.update_layout(title="Controller PNL 24h", xaxis_title="Time", yaxis_title="PNL (quote)")

rb = ReportBuilder(source="controller_pnl_24h")
rb.plotly(fig)
report = rb.build()
result = {"report_id": report.id}
print(f"Report saved: #{report.id}")
```

**ReportBuilder API (verified):**
- `rb = ReportBuilder(source="short-label")` — instantiate
- `rb.plotly(fig)` — attach a Plotly figure
- `rb.table(df)` — attach a DataFrame as a table
- `rb.text(md)` — attach markdown text
- `report = rb.build()` — save and return; `report.id` is the report number shown in the dashboard

---

### Chart + table combo (best practice)

For numerical series, show both — the chart for the shape, the table for exact values:

1. Emit the ` ```chart ` fence first (the visual)
2. Follow with a markdown table of key rows (first, last, min, max — or top N)

---

### Common traps

| Trap | Fix |
|---|---|
| Reaching for ReportBuilder when ≤ 200 points | Use the ` ```chart ` fence — simpler, no run_code needed |
| > 200 points in a fence block | Downsample first — the fence silently truncates or errors |
| > 4 series in a fence block | Pick top 4 by relevance; mention the rest in text |
| `series[].key` doesn't match a data row field | Keys must be exact — typo = blank chart |
| Multi-controller data in long format | Convert to wide format (one field per controller) before emitting |
| `rb.plotly(fig)` — calling `.build()` before adding content | Add all content first, then call `.build()` once |
| Saving a report for a simple one-off | Use the fence — ReportBuilder is for persistence, not convenience |
