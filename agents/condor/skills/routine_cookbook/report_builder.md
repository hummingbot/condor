# ReportBuilder & RoutineResult

How to produce reports (KPIs, tables, charts) and rich inline output.

## ReportBuilder (mandatory in every routine)

Every routine MUST generate a report. Wrap in try/except so report failure never breaks the routine.

```python
try:
    from condor.reports import ReportBuilder
    builder = ReportBuilder("Report Title")
    builder.source("routine", "routine_name").tags(["tag1", "tag2"])
    builder.section("01 / OVERVIEW", "What this section explains")
    builder.kpi("Total Volume", "$1,250,000")
    builder.kpi("Best Spread", "0.12%")
    builder.table(table_data, ["Column A", "Column B", "Column C"])
    builder.plotly(fig)
    builder.markdown("## Summary\nSome **markdown** text.")
    builder.manual_order()
    report_id = await builder.save()  # MUST await — async!
except Exception as e:
    logger.warning(f"Report generation failed: {e}")
```

### Core methods
- `source(type, name)` — declare the routine source (always first)
- `tags([...])` — categorization tags
- `kpi(label, value)` — one KPI card per call (NOT a list of dicts)
- `section(title, description=None)` — full-width semantic section separator
- `markdown(text)` — markdown text block
- `table(data, columns)` — tabular data (`data` = list of dicts, `columns` = list of str)
- `plotly(fig)` — Plotly figure object
- `manual_order()` — lock in the order methods were called (call before save)
- `auto_refresh(seconds)` — reload an updating report in the browser
- `save()` — **async**, returns `report_id` (str)

Methods that DO NOT exist: `heading()`, `text()`, `html()` — use `section()` for
major report boundaries and `markdown()` for narrative and subsection headings.

`section()` preserves insertion order because section boundaries are meaningful.
Use it instead of styling a Markdown heading as a page-level separator. Reports
render Markdown tables with bordered cells and synchronize native controls and
Plotly figures with the Condor dark or light report theme.

### Interactive offline dashboards

Reports can embed named datasets and data-bound components. The generated HTML
contains its data, report runtime, and Plotly bundle, so it works both in the
Condor dashboard and as an offline downloaded file.

```python
builder = ReportBuilder("Market Dashboard")
builder.source("routine", "market_dashboard").tags(["market"])
builder.manual_order()

builder.dataset("candles", candle_rows)
builder.select_filter("pair-filter", "candles", "pair", label="Pair")
builder.range_filter(
    "period-filter",
    "candles",
    "timestamp",
    label="Period",
    value_type="date",
    help_text="Choose the UTC dates included in the analysis.",
)
builder.metric(
    "Average Volume", "candles", "volume", aggregate="mean", format=",.2f"
)
builder.chart(
    "line",
    "Price",
    "candles",
    "timestamp",
    "close",
    color="pair",
    x_label="Time (UTC)",
    y_label="Price",
    selection_mode="drilldown",
    selection_help="Select points to inspect their source candles.",
)
builder.data_table(
    "candles",
    title="Candles",
    columns=["timestamp", "pair", "close", "volume"],
)
builder.drilldown(
    "candles",
    title="Selected Candles",
    columns=["timestamp", "pair", "close", "volume"],
)
await builder.save()
```

Available interactive methods:

- `dataset(name, rows)` — register a `list[dict]` once for linked components
- `select_filter(name, source, field, ...)` — categorical single/multi-select
- `range_filter(name, source, field, ...)` — numeric or date range
- `metric(label, source, field, aggregate=...)` — filtered KPI calculation
- `chart(type, title, source, x, y, ...)` — linked Plotly chart
- `data_table(source, ...)` — searchable, sortable, paginated, CSV-exportable table
- `drilldown(source, ...)` — rows selected by a chart using the same dataset

Supported chart types are `line`, `area`, `bar`, `horizontal_bar`, `scatter`,
`treemap`, `box`, `histogram`, and `candlestick`. For a treemap, `x` supplies
tile labels, `y` supplies positive tile area, and `color` supplies an optional
continuous value rendered on a symmetric red-to-green scale around zero. Use
`value_label`, `value_prefix`, `value_suffix`, `value_format`, `color_label`,
`color_prefix`, `color_suffix`, and `color_format` to format treemap labels and
hover details. Treemaps do not
support `aggregate`. Use `color_map={"BUY": "#22c55e"}`
when categories need fixed semantic colors. Pass `text="label_field"` for value
labels and, for scatter charts, `size="size_field"` and
`text_position="top center"` for marker sizing and label placement. Use
`category_order=[...]` for stable categorical axes and
`reference_lines=[{"axis": "y", "value": 0, "label": "Break-even"}]` for
labeled thresholds. Pass `x_range=[0, 90]` or `y_range=[...]` when a fixed view
is clearer, and `x_scale="log"` or `y_scale="log"` for logarithmic axes. Use `plotly(fig)`
for specialized charts that do not need shared filtering. Supported metric
aggregates are `count`, `sum`, `mean`, `min`, `max`, `median`, `first`, `last`,
and `change_pct`.

Use `value_type="date"` for native calendar-day controls and
`value_type="datetime"` when the user needs hour-and-minute precision. Date
ranges include the complete UTC end date. Add `help_text` when a filter's effect
is not obvious.

Charts select and cross-filter linked components by default. Set
`cross_filter=False` on charts intended only for viewing. For a deliberate
row-inspection interaction, use `selection_mode="drilldown"` with
`selection_help="..."`. Add `selection_label="trade"` and
`selection_field="trade_id"` to show selected IDs and a local clear button. A
drilldown selection populates tables without changing metrics or sibling charts.
Use `x_label` and `y_label` to state units explicitly.

For a report that is updated in place, call `auto_refresh(seconds)` before
`save()`. The generated report displays Live and Pause/Resume controls. Filtering,
chart selection, table search, sorting, or paging pauses refresh so the user's
interaction is not discarded. Resume loads the newest report immediately. Call
`auto_refresh(None)` before the final update to leave a stopped, fixed snapshot.
Downloaded reports remain fixed offline snapshots. In-place updates count as
recent activity when Condor applies its report-retention limit.

For cross-filtering, charts and target components must reference the same named
dataset. Keep embedded datasets below roughly 10,000 rows for responsive reports.
See `routines/report_component_gallery.py` for a deterministic Hummingbot trading
reference covering every component family and semantic API variant. See
`continuous.md` for the `LiveReport` lifecycle and automatic-refresh pattern.

### Update existing report (e.g. in continuous routines)
```python
report_id = await builder.save(report_id=existing_id)
```

## RoutineResult (rich inline output)

Use alongside ReportBuilder for richer in-chat display:

```python
from routines.base import RoutineResult

# With table
return RoutineResult(
    text="Summary line",
    table_data=[{"Pair": "BTC-USDT", "Price": 100000, "Change": "+2.1%"}],
    table_columns=["Pair", "Price", "Change"]
)

# With chart image (PNG bytes)
return RoutineResult(text=summary, chart_image=png_bytes)

# With KPI sections
return RoutineResult(text=summary, sections=[
    {"type": "kpi", "label": "Price", "value": "$100K", "delta": "+5%", "trend": "up"},
    {"type": "kpi", "label": "Volume", "value": "$2.5M", "delta": "-3%", "trend": "down"},
])
```

## Plotly Rules

Every figure must place the legend at the bottom:
```python
fig.update_layout(
    legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5)
)
```

Convert figure to PNG bytes for `chart_image`:
```python
import io
buf = io.BytesIO()
fig.write_image(buf, format="png", width=1200, height=600)
png_bytes = buf.getvalue()
```

## Common Mistakes

- `builder.kpi(label, value)` — two args, NOT `builder.kpi([{"label": ..., "value": ...}])`
- `await builder.save()` — forgetting `await` means no report is ever written (silently fails)
- Call `manual_order()` before `save()` when section call order should be preserved
- Always pass the actual routine filename to `builder.source("routine", "my_routine_name")`
- Register a dataset before adding components that reference it
- Use stable component IDs when another component or test needs to identify it
