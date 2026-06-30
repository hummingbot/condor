---
name: routine_report_builder
description: 'ReportBuilder and RoutineResult patterns: KPIs, tables, Plotly charts,
  rich output for routines'
when_to_use: When a routine needs to generate a persistent report with KPIs, tables,
  or charts — or return rich inline output with RoutineResult.
created: '2026-06-30T08:54:12Z'
source: chat
---

## ReportBuilder (mandatory in every routine)

Every routine MUST generate a report. Wrap in try/except so report failure never breaks the routine.

```python
try:
    from condor.reports import ReportBuilder
    builder = ReportBuilder("Report Title")
    builder.source("routine", "routine_name").tags(["tag1", "tag2"])
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

### Available methods (ONLY these exist)
- `source(type, name)` — declare the routine source (always first)
- `tags([...])` — categorization tags
- `kpi(label, value)` — one KPI card per call (NOT a list of dicts)
- `markdown(text)` — markdown text block
- `table(data, columns)` — tabular data (`data` = list of dicts, `columns` = list of str)
- `plotly(fig)` — Plotly figure object
- `manual_order()` — lock in the order methods were called (call before save)
- `save()` — **async**, returns `report_id` (str)

Methods that DO NOT exist: `heading()`, `text()`, `section()`, `html()` — use `markdown()`.

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
- `manual_order()` must be called AFTER all sections, BEFORE `save()`
- Always pass the actual routine filename to `builder.source("routine", "my_routine_name")`
