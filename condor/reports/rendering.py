"""HTML rendering utilities and bundled browser assets for reports."""

from __future__ import annotations

import html
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
{plotly_script}
<style>
  :root {{
    color-scheme: dark;
    --bg: #080c18; --surface: #0f1525; --surface-raised: #161e34;
    --border: #1c2541; --border-strong: #2a3558;
    --text: #e8e6e3; --text-muted: #7b8ba4;
    --green: #22c55e; --red: #ef4444; --blue: #d4a845;
  }}
  :root.light {{
    color-scheme: light;
    --bg: #f5f6fa; --surface: #ffffff; --surface-raised: #eef0f7;
    --border: #d8dce8; --border-strong: #b8c0d8;
    --text: #141727; --text-muted: #6b7280;
    --green: #16a34a; --red: #dc2626; --blue: #b8860b;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    font-size: 14px; line-height: 1.6; padding: 28px; max-width: 1480px; margin: 0 auto;
  }}
  .report-header {{
    display: flex; justify-content: space-between; align-items: baseline;
    border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 24px;
  }}
  .report-header h1 {{ font-size: 26px; line-height: 1.25; letter-spacing: -0.02em; }}
  .report-header .meta {{ color: var(--text-muted); font-size: 12px; }}
  .report-header .meta span {{ margin-left: 16px; }}
  .report-actions {{ display: none; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 16px; }}
  .report-actions.active {{ display: flex; }}
  .report-actions button, .report-control button, .table-toolbar button {{
    background: var(--surface-raised); color: var(--text); border: 1px solid var(--border-strong);
    border-radius: 6px; padding: 6px 10px; cursor: pointer;
  }}
  .report-actions button:hover, .report-control button:hover, .table-toolbar button:hover {{ border-color: var(--blue); }}
  .report-status {{ color: var(--text-muted); font-size: 12px; }}
  .live-controls {{ display: flex; align-items: center; gap: 8px; margin-left: auto; }}
  .live-badge {{
    display: inline-flex; align-items: center; gap: 6px; color: var(--green);
    font-size: 11px; font-weight: 700; letter-spacing: 0.5px; text-transform: uppercase;
  }}
  .live-badge::before {{ content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor; }}
  .live-badge.offline {{ color: var(--text-muted); }}
  .report-grid {{ display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 16px; align-items: start; }}
  .report-grid > .section, .report-grid > .kpi-bar {{ grid-column: 1 / -1; margin-bottom: 16px; }}
  .report-component {{ grid-column: span var(--component-span, 12); min-width: 0; }}
  .kpi-bar {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(230px, 100%), 1fr)); gap: 16px; }}
  .kpi-card {{
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px 20px; min-width: 0;
  }}
  .kpi-card .label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); }}
  .kpi-card .value {{ font-size: clamp(20px, 2vw, 24px); font-weight: 700; margin: 4px 0; white-space: normal; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }}
  .kpi-card .delta {{ font-size: 12px; }}
  .kpi-card .delta.up {{ color: var(--green); }}
  .kpi-card .delta.down {{ color: var(--red); }}
  .section {{ margin-bottom: 32px; }}
  .report-section-heading {{
    border-top: 2px solid var(--border-strong);
    padding: 24px 4px 0; margin: 34px 0 8px;
  }}
  .report-section-heading h2 {{ font-size: 22px; line-height: 1.3; letter-spacing: -0.015em; }}
  .report-section-description {{ color: var(--text-muted); margin-top: 5px; max-width: 920px; }}
  .report-grid > .report-section-heading:first-child {{ margin-top: 0; }}
  .section-md {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 20px 22px; overflow-x: auto; }}
  .section-md h1, .section-md h2, .section-md h3 {{ color: var(--text); line-height: 1.35; }}
  .section-md h1 {{ font-size: 20px; margin: 2px 0 10px; }}
  .section-md h2 {{ font-size: 18px; margin: 18px 0 8px; }}
  .section-md h3 {{ font-size: 16px; margin: 18px 0 8px; border-left: 3px solid var(--blue); padding-left: 10px; }}
  .section-md h1:first-child, .section-md h2:first-child, .section-md h3:first-child {{ margin-top: 0; }}
  .section-md p {{ margin: 8px 0; }}
  .section-md pre {{ background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 12px; overflow-x: auto; font-size: 13px; }}
  .section-md code {{ background: var(--bg); padding: 2px 6px; border-radius: 3px; font-size: 13px; }}
  .section-md pre code {{ background: none; padding: 0; }}
  .section-md ul, .section-md ol {{ padding-left: 20px; }}
  .section-md a {{ color: var(--blue); }}
  .section-md table {{ width: 100%; border-collapse: collapse; margin: 14px 0; font-size: 13px; }}
  .section-md th, .section-md td {{ border: 1px solid var(--border-strong); padding: 9px 11px; text-align: left; vertical-align: top; }}
  .section-md th {{ background: var(--surface-raised); font-weight: 650; }}
  .section-md tr:nth-child(even) td {{ background: rgba(123,139,164,0.06); }}
  .section-table {{ overflow-x: auto; }}
  .section-table table {{
    width: 100%; border-collapse: collapse; font-size: 13px;
    background: var(--surface); border: 1px solid var(--border-strong); border-radius: 8px; overflow: hidden;
  }}
  .section-table th {{
    background: var(--surface-raised); text-align: left; padding: 8px 12px;
    font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;
    color: var(--text-muted); border: 1px solid var(--border-strong);
  }}
  .section-table td {{ padding: 8px 12px; border: 1px solid var(--border-strong); }}
  .section-table tr:nth-child(even) td {{ background: rgba(123,139,164,0.06); }}
  .section-table tr:last-child td {{ border-bottom: none; }}
  .plotly-chart {{ min-height: 400px; margin-bottom: 24px; width: 100%; overflow: hidden; }}
  .plotly-chart .js-plotly-plot, .plotly-chart .plot-container, .plotly-chart .plotly {{ width: 100% !important; }}
  .report-panel {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; min-width: 0; }}
  .report-panel h2 {{ font-size: 15px; margin-bottom: 10px; }}
  .report-controls {{ display: flex; gap: 12px; flex-wrap: wrap; }}
  .report-control {{ min-width: 180px; flex: 1; }}
  .report-control > label, .report-control .control-label {{ display: block; color: var(--text-muted); font-size: 11px; text-transform: uppercase; margin-bottom: 5px; }}
  .report-control select, .report-control input, .table-toolbar input {{
    width: 100%; background: var(--surface-raised); color: var(--text); border: 1px solid var(--border-strong);
    border-radius: 6px; padding: 8px 10px; color-scheme: inherit;
  }}
  .report-control input[type="date"], .report-control input[type="datetime-local"] {{ min-height: 39px; }}
  .report-control input::-webkit-calendar-picker-indicator {{ cursor: pointer; opacity: 0.9; }}
  .multi-select {{ position: relative; }}
  .multi-select-toggle {{ width: 100%; display: flex; justify-content: space-between; align-items: center; gap: 10px; text-align: left; }}
  .multi-select-toggle::after {{ content: ">"; color: var(--text-muted); transform: rotate(90deg); }}
  .multi-select-toggle[aria-expanded="true"]::after {{ transform: rotate(-90deg); }}
  .multi-select-menu {{
    position: absolute; z-index: 20; left: 0; right: 0; top: calc(100% + 6px);
    background: var(--surface-raised); border: 1px solid var(--border-strong);
    border-radius: 7px; padding: 8px; box-shadow: 0 12px 30px rgba(0,0,0,0.28);
    max-height: 240px; overflow-y: auto;
  }}
  .multi-select-menu[hidden] {{ display: none; }}
  .multi-select-option {{ display: flex; align-items: center; gap: 9px; padding: 7px 8px; border-radius: 5px; cursor: pointer; }}
  .multi-select-option:hover {{ background: rgba(123,139,164,0.12); }}
  .multi-select-option input {{ width: auto; margin: 0; accent-color: var(--blue); }}
  .multi-select-clear {{ width: 100%; margin-top: 6px; }}
  .range-inputs {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
  .range-field span {{ display: block; color: var(--text-muted); font-size: 11px; margin-bottom: 4px; }}
  .report-helper {{ color: var(--text-muted); font-size: 12px; margin-top: 7px; }}
  .metric-card {{ height: 100%; }}
  .metric-card .label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); }}
  .metric-card .value {{ font-size: clamp(20px, 2vw, 24px); font-weight: 700; margin-top: 4px; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }}
  .metric-card .context {{ color: var(--text-muted); font-size: 11px; margin-top: 3px; }}
  .report-chart {{ min-height: 320px; }}
  .selection-status {{
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border);
  }}
  .selection-summary {{ display: flex; align-items: center; flex-wrap: wrap; gap: 6px; color: var(--text-muted); font-size: 12px; }}
  .selection-chip {{ background: var(--surface-raised); border: 1px solid var(--border-strong); border-radius: 999px; color: var(--text); padding: 2px 8px; }}
  .selection-clear {{ flex: 0 0 auto; }}
  .table-toolbar {{ display: flex; gap: 8px; align-items: center; margin-bottom: 10px; }}
  .table-toolbar input {{ max-width: 320px; }}
  .table-wrap {{ overflow: auto; max-height: 540px; }}
  .data-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  .data-table th {{ position: sticky; top: 0; z-index: 1; background: var(--surface-raised); cursor: pointer; user-select: none; }}
  .data-table th, .data-table td {{ text-align: left; padding: 7px 10px; border: 1px solid var(--border); white-space: nowrap; }}
  .data-table tr:hover td {{ background: rgba(88,166,255,0.06); }}
  .table-footer {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 10px; color: var(--text-muted); font-size: 11px; }}
  .empty-state {{ color: var(--text-muted); padding: 20px 4px; text-align: center; }}
  @media (max-width: 800px) {{
    body {{ padding: 14px; }}
    .report-header h1 {{ font-size: 22px; }}
    .report-section-heading h2 {{ font-size: 19px; }}
    .report-header {{ display: block; }}
    .report-header .meta span {{ margin: 0 12px 0 0; }}
    .report-component {{ grid-column: 1 / -1; }}
    .range-inputs {{ grid-template-columns: 1fr; }}
    .report-actions, .table-toolbar, .table-footer {{ flex-direction: column; align-items: stretch; }}
    .selection-status {{ align-items: stretch; flex-direction: column; }}
    .live-controls {{ margin-left: 0; flex-wrap: wrap; }}
    .table-toolbar input {{ max-width: none; }}
  }}
</style>
</head>
<body>
<div class="report-header">
  <h1>{title}</h1>
  <div class="meta">
    <span>{created_at}</span>
    {meta_badges}
  </div>
</div>
<div id="report-actions" class="report-actions"></div>
<div class="report-grid">{sections_html}</div>
<script id="condor-report-spec" type="application/json">{report_spec}</script>
<script>
window.addEventListener('message', function(e) {{
  if (e.source !== window.parent) return;
  if (e.data && e.data.type === 'set-theme') {{
    document.documentElement.classList.toggle('light', e.data.theme === 'light');
    if (window.CondorReportRuntime) window.CondorReportRuntime.setTheme(e.data.theme);
    themeCustomPlots(e.data.theme);
  }}
}});
function themeCustomPlots(theme) {{
  if (!window.Plotly) return Promise.resolve();
  var light = theme === 'light';
  var updates = [];
  window.__condorProgrammaticPlotUpdate = true;
  document.querySelectorAll('.plotly-chart .js-plotly-plot').forEach(function(el) {{
    var update = {{
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
      'font.color': light ? '#141727' : '#e8e6e3',
      'legend.font.color': light ? '#141727' : '#e8e6e3'
    }};
    Object.keys(el.layout || {{}}).forEach(function(key) {{
      if (/^[xy]axis\\d*$/.test(key)) update[key + '.gridcolor'] = light ? '#e5e7eb' : '#1c2541';
      if (/^scene\\d*$/.test(key)) {{
        update[key + '.bgcolor'] = 'rgba(0,0,0,0)';
        ['xaxis', 'yaxis', 'zaxis'].forEach(function(axis) {{
          update[key + '.' + axis + '.gridcolor'] = light ? '#e5e7eb' : '#1c2541';
          update[key + '.' + axis + '.color'] = light ? '#141727' : '#e8e6e3';
        }});
      }}
      if (/^polar\\d*$/.test(key)) {{
        update[key + '.bgcolor'] = 'rgba(0,0,0,0)';
        update[key + '.radialaxis.gridcolor'] = light ? '#e5e7eb' : '#1c2541';
        update[key + '.angularaxis.gridcolor'] = light ? '#e5e7eb' : '#1c2541';
      }}
      if (/^ternary\\d*$/.test(key)) update[key + '.bgcolor'] = 'rgba(0,0,0,0)';
      if (/^geo\\d*$/.test(key)) update[key + '.bgcolor'] = 'rgba(0,0,0,0)';
    }});
    updates.push(Plotly.relayout(el, update));
  }});
  return Promise.all(updates).finally(function() {{
    window.__condorProgrammaticPlotUpdate = false;
  }});
}}
function bindCustomPlotInteractions() {{
  document.querySelectorAll('.plotly-chart .js-plotly-plot').forEach(function(el) {{
    if (el.__condorInteractionsBound || typeof el.on !== 'function') return;
    var pause = function() {{
      if (window.__condorProgrammaticPlotUpdate) return;
      if (window.CondorReportRuntime) window.CondorReportRuntime.pauseAutoRefresh();
    }};
    el.on('plotly_relayouting', pause);
    el.on('plotly_relayout', pause);
    el.on('plotly_legendclick', pause);
    el.__condorInteractionsBound = true;
  }});
}}
window.addEventListener('load', function() {{
  window.__condorProgrammaticPlotUpdate = true;
  var initialUpdates = [];
  document.querySelectorAll('.plotly-chart .js-plotly-plot').forEach(function(el) {{
    if (window.Plotly) initialUpdates.push(Plotly.relayout(el, {{ autosize: true, width: undefined }}));
  }});
  Promise.all(initialUpdates)
    .catch(function() {{}})
    .then(function() {{
      return themeCustomPlots(document.documentElement.classList.contains('light') ? 'light' : 'dark');
    }})
    .finally(bindCustomPlotInteractions);
}});
window.addEventListener('resize', function() {{
  document.querySelectorAll('.plotly-chart .js-plotly-plot').forEach(function(el) {{
    if (window.Plotly) Plotly.Plots.resize(el);
  }});
}});
</script>
{report_runtime}
</body>
</html>
"""


@lru_cache(maxsize=1)
def report_runtime() -> str:
    return Path(__file__).with_name("report_runtime.js").read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def plotly_script() -> str:
    """Return Plotly's installed browser bundle for fully offline reports."""
    from plotly.offline import get_plotlyjs

    return f"<script>{get_plotlyjs()}</script>"


def json_for_html(value: Any) -> str:
    """Serialize JSON safely inside an HTML script element."""
    try:
        from plotly.utils import PlotlyJSONEncoder

        raw = json.dumps(value, cls=PlotlyJSONEncoder, separators=(",", ":"))
    except ImportError:
        raw = json.dumps(value, default=str, separators=(",", ":"))
    return raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def markdown_to_html(text: str) -> str:
    """Convert and sanitize markdown, falling back to escaped preformatted text."""
    try:
        import bleach
        import markdown

        rendered = markdown.markdown(
            text, extensions=["fenced_code", "tables", "nl2br"]
        )
        return bleach.clean(
            rendered,
            tags={
                "a",
                "blockquote",
                "br",
                "code",
                "em",
                "h1",
                "h2",
                "h3",
                "h4",
                "h5",
                "h6",
                "hr",
                "li",
                "ol",
                "p",
                "pre",
                "strong",
                "table",
                "tbody",
                "td",
                "th",
                "thead",
                "tr",
                "ul",
            },
            attributes={"a": ["href", "title"]},
            protocols={"http", "https", "mailto"},
            strip=True,
        )
    except Exception:
        escaped = html.escape(text)
        return f"<pre style='white-space:pre-wrap'>{escaped}</pre>"


def slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s-]+", "_", slug).strip("_")[:40]
