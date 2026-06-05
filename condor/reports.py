"""Report builder — composable HTML reports with Plotly charts, markdown, and tables."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Module-level variable to capture the last saved report ID.
# Safe in asyncio (single-threaded); reset before each routine execution.
_last_report_id: str | None = None

CHARTS_DIR = Path(__file__).resolve().parent.parent / "reports"
INDEX_FILE = CHARTS_DIR / "reports_index.json"
MAX_REPORTS = int(os.environ.get("CONDOR_MAX_REPORTS", "100"))
_index_lock = asyncio.Lock()

# ── HTML Template ──

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  :root {{
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #e6edf3; --text-muted: #8b949e;
    --green: #3fb950; --red: #f85149; --blue: #58a6ff;
  }}
  :root.light {{
    --bg: #ffffff; --surface: #f6f8fa; --border: #d0d7de;
    --text: #1f2328; --text-muted: #656d76;
    --green: #1a7f37; --red: #cf222e; --blue: #0969da;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    font-size: 14px; line-height: 1.6; padding: 24px; max-width: 1400px; margin: 0 auto;
  }}
  .report-header {{
    display: flex; justify-content: space-between; align-items: baseline;
    border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 24px;
  }}
  .report-header h1 {{ font-size: 20px; }}
  .report-header .meta {{ color: var(--text-muted); font-size: 12px; }}
  .report-header .meta span {{ margin-left: 16px; }}
  .kpi-bar {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }}
  .kpi-card {{
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px 20px; min-width: 150px; flex: 1;
  }}
  .kpi-card .label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); }}
  .kpi-card .value {{ font-size: 24px; font-weight: 700; margin: 4px 0; }}
  .kpi-card .delta {{ font-size: 12px; }}
  .kpi-card .delta.up {{ color: var(--green); }}
  .kpi-card .delta.down {{ color: var(--red); }}
  .section {{ margin-bottom: 32px; }}
  .section-md {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px 20px; }}
  .section-md h1, .section-md h2, .section-md h3 {{ color: var(--text); margin: 12px 0 6px; }}
  .section-md h1 {{ font-size: 18px; }} .section-md h2 {{ font-size: 16px; }} .section-md h3 {{ font-size: 14px; }}
  .section-md p {{ margin: 6px 0; }}
  .section-md pre {{ background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 12px; overflow-x: auto; font-size: 13px; }}
  .section-md code {{ background: var(--bg); padding: 2px 6px; border-radius: 3px; font-size: 13px; }}
  .section-md pre code {{ background: none; padding: 0; }}
  .section-md ul, .section-md ol {{ padding-left: 20px; }}
  .section-md a {{ color: var(--blue); }}
  .section-table {{ overflow-x: auto; }}
  .section-table table {{
    width: 100%; border-collapse: collapse; font-size: 13px;
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden;
  }}
  .section-table th {{
    background: var(--bg); text-align: left; padding: 8px 12px;
    font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;
    color: var(--text-muted); border-bottom: 1px solid var(--border);
  }}
  .section-table td {{ padding: 8px 12px; border-bottom: 1px solid var(--border); }}
  .section-table tr:nth-child(even) td {{ background: rgba(255,255,255,0.02); }}
  .section-table tr:last-child td {{ border-bottom: none; }}
  .plotly-chart {{ min-height: 400px; margin-bottom: 24px; width: 100%; overflow: hidden; }}
  .plotly-chart .js-plotly-plot, .plotly-chart .plot-container, .plotly-chart .plotly {{ width: 100% !important; }}
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
{sections_html}
<script>
window.addEventListener('message', function(e) {{
  if (e.data && e.data.type === 'set-theme') {{
    document.documentElement.classList.toggle('light', e.data.theme === 'light');
  }}
}});
window.addEventListener('load', function() {{
  document.querySelectorAll('.plotly-chart .js-plotly-plot').forEach(function(el) {{
    if (window.Plotly) Plotly.relayout(el, {{ autosize: true, width: undefined }});
  }});
}});
window.addEventListener('resize', function() {{
  document.querySelectorAll('.plotly-chart .js-plotly-plot').forEach(function(el) {{
    if (window.Plotly) Plotly.Plots.resize(el);
  }});
}});
</script>
</body>
</html>
"""

# Marker around each Plotly chart in saved HTML, so the email renderer can find
# and convert charts to static images in-process (no second file persisted).
_CHART_OPEN = "<!--CONDOR_CHART-->"
_CHART_CLOSE = "<!--/CONDOR_CHART-->"
_CHART_RE = re.compile(
    re.escape(_CHART_OPEN) + r".*?" + re.escape(_CHART_CLOSE), re.DOTALL
)
_CDN_SCRIPT = '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>'


def _scan_balanced(
    s: str, start: int, open_ch: str, close_ch: str
) -> tuple[str | None, int]:
    """Return the balanced substring starting at s[start] (an opener), JSON-aware."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    return s[start : i + 1], i + 1
    return None, len(s)


def _newplot_to_png_b64(block: str) -> str | None:
    """Reconstruct the figure from a Plotly.newPlot(...) call and render a PNG."""
    idx = block.find("Plotly.newPlot(")
    if idx == -1:
        return None
    s = block[idx:]
    db = s.find("[")
    if db == -1:
        return None
    data, end = _scan_balanced(s, db, "[", "]")
    if data is None:
        return None
    lb = s.find("{", end)
    if lb == -1:
        return None
    layout, _ = _scan_balanced(s, lb, "{", "}")
    if layout is None:
        return None
    try:
        import plotly.io as pio

        fig = pio.from_json(f'{{"data": {data}, "layout": {layout}}}')
        png = fig.to_image(format="png", scale=2)
        return base64.b64encode(png).decode("ascii")
    except Exception as e:  # noqa: BLE001
        logger.warning("Email chart render failed: %s", e)
        return None


def get_report_html_for_email(report_id: str) -> tuple[str, str] | None:
    """Return (html, attachment_filename) for emailing a report.

    Converts each interactive Plotly chart into a static <img> in-process and
    drops the Plotly CDN script, so the report renders in any email client.
    Done on demand at send time — nothing extra is persisted to disk.
    """
    entry = get_report(report_id)
    if not entry:
        return None
    filename = entry["filename"]
    path = CHARTS_DIR / filename
    if not path.exists():
        return None
    html = path.read_text()

    def _repl(m: re.Match) -> str:
        b64 = _newplot_to_png_b64(m.group(0))
        if not b64:
            return m.group(0)  # leave interactive markup if render fails
        return (
            '<div class="section plotly-chart">'
            f'<img src="data:image/png;base64,{b64}" '
            'style="max-width:100%;height:auto;border:1px solid var(--border);border-radius:8px;"/>'
            "</div>"
        )

    html = _CHART_RE.sub(_repl, html)
    html = html.replace(_CDN_SCRIPT, "")
    html = _inline_css_vars(html)
    return html, filename


def _inline_css_vars(html: str) -> str:
    """Replace CSS var() references with resolved dark-theme values for email clients."""
    _VAR_MAP = {
        "var(--bg)": "#0d1117",
        "var(--surface)": "#161b22",
        "var(--border)": "#30363d",
        "var(--text)": "#e6edf3",
        "var(--text-muted)": "#8b949e",
        "var(--green)": "#3fb950",
        "var(--red)": "#f85149",
        "var(--blue)": "#58a6ff",
    }
    for var, val in _VAR_MAP.items():
        html = html.replace(var, val)
    return html


def _md_to_html(text: str) -> str:
    """Convert markdown to HTML, falling back to <pre> if library fails."""
    try:
        import markdown

        return markdown.markdown(text, extensions=["fenced_code", "tables"])
    except Exception:
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f"<pre style='white-space:pre-wrap'>{escaped}</pre>"


def _slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s-]+", "_", s).strip("_")[:40]


# ── Index management ──


def _read_index() -> list[dict]:
    if not INDEX_FILE.exists():
        return []
    try:
        return json.loads(INDEX_FILE.read_text())
    except Exception:
        return []


def _write_index(entries: list[dict]) -> None:
    CHARTS_DIR.mkdir(exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", dir=str(CHARTS_DIR), suffix=".tmp", delete=False
    )
    try:
        json.dump(entries, tmp, indent=2)
        tmp.close()
        os.replace(tmp.name, str(INDEX_FILE))
    except Exception:
        tmp.close()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


def list_reports(
    source_type: str | None = None,
    tag: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    entries = _read_index()
    # newest first
    entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)

    if source_type:
        entries = [e for e in entries if e.get("source_type") == source_type]
    if tag:
        entries = [e for e in entries if tag in e.get("tags", [])]
    if search:
        q = search.lower()
        entries = [
            e
            for e in entries
            if q in e.get("title", "").lower()
            or q in e.get("source_name", "").lower()
            or any(q in t.lower() for t in e.get("tags", []))
        ]

    total = len(entries)
    return entries[offset : offset + limit], total


def list_reports_grouped() -> list[dict]:
    """Return latest report per source_name, with count."""
    entries = _read_index()
    entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    groups: dict[str, dict] = {}
    for e in entries:
        sn = e.get("source_name", "")
        if not sn:
            continue
        if sn not in groups:
            groups[sn] = {
                "source_name": sn,
                "source_type": e.get("source_type", ""),
                "latest_report": e,
                "total_count": 1,
                "all_tags": set(e.get("tags", [])),
            }
        else:
            groups[sn]["total_count"] += 1
            groups[sn]["all_tags"].update(e.get("tags", []))
    for g in groups.values():
        g["all_tags"] = sorted(g["all_tags"])
    return sorted(
        groups.values(), key=lambda g: g["latest_report"]["created_at"], reverse=True
    )


def get_report(report_id: str) -> dict | None:
    for e in _read_index():
        if e["id"] == report_id:
            return e
    return None


async def delete_report(report_id: str) -> bool:
    async with _index_lock:
        entries = _read_index()
        new_entries = []
        deleted = False
        for e in entries:
            if e["id"] == report_id:
                fpath = CHARTS_DIR / e["filename"]
                if fpath.exists():
                    fpath.unlink()
                deleted = True
            else:
                new_entries.append(e)
        if deleted:
            _write_index(new_entries)
    return deleted


def _cleanup_locked(max_reports: int = MAX_REPORTS) -> None:
    """Run cleanup while caller already holds _index_lock."""
    entries = _read_index()
    if len(entries) <= max_reports:
        return
    entries.sort(key=lambda e: e.get("created_at", ""))
    to_remove = entries[: len(entries) - max_reports]
    keep_ids = {e["id"] for e in entries[len(entries) - max_reports :]}
    for e in to_remove:
        fpath = CHARTS_DIR / e["filename"]
        if fpath.exists():
            fpath.unlink()
    _write_index([e for e in entries if e["id"] in keep_ids])


# ── ReportBuilder ──


_SECTION_PRIORITY = {"kpi": 0, "plotly": 1, "table": 2, "markdown": 3}


class ReportBuilder:
    def __init__(self, title: str = "Report"):
        self._title = title
        self._source_type: str = ""
        self._source_name: str = ""
        self._tags: list[str] = []
        self._sections: list[dict] = []
        self._manual_order = False

    def source(self, source_type: str, source_name: str) -> ReportBuilder:
        self._source_type = source_type
        self._source_name = source_name
        return self

    def tags(self, tags: list[str]) -> ReportBuilder:
        self._tags = tags
        return self

    def manual_order(self) -> ReportBuilder:
        self._manual_order = True
        return self

    def kpi(
        self, label: str, value: str, delta: str | None = None, trend: str = "neutral"
    ) -> ReportBuilder:
        self._sections.append(
            {
                "type": "kpi",
                "label": label,
                "value": value,
                "delta": delta,
                "trend": trend,
            }
        )
        return self

    def markdown(self, text: str) -> ReportBuilder:
        self._sections.append({"type": "markdown", "content": text})
        return self

    def plotly(self, fig: Any) -> ReportBuilder:
        html = fig.to_html(full_html=False, include_plotlyjs=False)
        self._sections.append({"type": "plotly", "content": html})
        return self

    def table(
        self, rows: list[dict], columns: list[str] | None = None
    ) -> ReportBuilder:
        if not columns and rows:
            columns = list(rows[0].keys())
        self._sections.append({"type": "table", "columns": columns or [], "rows": rows})
        return self

    async def save(self, report_id: str | None = None) -> str:
        """Save the report as an HTML file.

        Args:
            report_id: If provided, update an existing report in place.
                       If None (default), create a new report.
        """
        CHARTS_DIR.mkdir(exist_ok=True)
        now = datetime.now(timezone.utc)

        sections_html = self._render_sections()
        meta_badges = ""
        if self._source_type:
            meta_badges += f"<span>{self._source_type}: {self._source_name}</span>"
        for tag in self._tags:
            meta_badges += f"<span>#{tag}</span>"

        html_content = _HTML_TEMPLATE.format(
            title=self._title,
            created_at=now.strftime("%Y-%m-%d %H:%M UTC"),
            meta_badges=meta_badges,
            sections_html=sections_html,
        )

        global _last_report_id

        async with _index_lock:
            if report_id is not None:
                # Update existing report
                entries = _read_index()
                entry = next((e for e in entries if e["id"] == report_id), None)
                if entry is None:
                    raise ValueError(f"Report '{report_id}' not found in index")

                (CHARTS_DIR / entry["filename"]).write_text(html_content)

                entry["updated_at"] = now.isoformat()
                entry["title"] = self._title
                entry["tags"] = self._tags
                _write_index(entries)

                _last_report_id = report_id
                logger.info(f"Report updated: {entry['filename']}")
                return report_id

            # New report
            new_id = uuid.uuid4().hex[:6]
            ts_str = now.strftime("%Y%m%d_%H%M%S")
            slug = _slugify(self._title)
            filename = f"{ts_str}_{slug}_{new_id}.html"

            (CHARTS_DIR / filename).write_text(html_content)

            entry = {
                "id": new_id,
                "title": self._title,
                "filename": filename,
                "created_at": now.isoformat(),
                "source_type": self._source_type,
                "source_name": self._source_name,
                "tags": self._tags,
            }

            entries = _read_index()
            entries.append(entry)
            _write_index(entries)
            _cleanup_locked()

        _last_report_id = new_id
        logger.info(f"Report saved: {filename}")
        return new_id

    def _render_sections(self) -> str:
        sections = list(self._sections)
        if not self._manual_order:
            # Stable sort: kpi first, then plotly, table, markdown
            sections = sorted(
                sections, key=lambda s: _SECTION_PRIORITY.get(s["type"], 99)
            )

        parts = []
        i = 0
        while i < len(sections):
            sec = sections[i]
            if sec["type"] == "kpi":
                # Group consecutive KPIs into a single kpi-bar
                kpis = []
                while i < len(sections) and sections[i]["type"] == "kpi":
                    kpis.append(sections[i])
                    i += 1
                cards = []
                for k in kpis:
                    delta_html = ""
                    if k["delta"]:
                        cls = f' {k["trend"]}' if k["trend"] in ("up", "down") else ""
                        delta_html = f'<div class="delta{cls}">{k["delta"]}</div>'
                    cards.append(
                        f'<div class="kpi-card">'
                        f'<div class="label">{k["label"]}</div>'
                        f'<div class="value">{k["value"]}</div>'
                        f"{delta_html}</div>"
                    )
                parts.append(f'<div class="kpi-bar">{"".join(cards)}</div>')
            elif sec["type"] == "markdown":
                parts.append(
                    f'<div class="section section-md">{_md_to_html(sec["content"])}</div>'
                )
                i += 1
            elif sec["type"] == "plotly":
                # Wrap in markers so the email renderer can locate and convert
                # the chart to a static image in-process at send time.
                parts.append(
                    f'{_CHART_OPEN}<div class="section plotly-chart">{sec["content"]}</div>{_CHART_CLOSE}'
                )
                i += 1
            elif sec["type"] == "table":
                parts.append(self._render_table(sec["columns"], sec["rows"]))
                i += 1
            else:
                i += 1
        return "\n".join(parts)

    @staticmethod
    def _render_table(columns: list[str], rows: list[dict]) -> str:
        header = "".join(f"<th>{c}</th>" for c in columns)
        body_rows = []
        for row in rows:
            cells = "".join(f"<td>{row.get(c, '')}</td>" for c in columns)
            body_rows.append(f"<tr>{cells}</tr>")
        body = "\n".join(body_rows)
        return f'<div class="section section-table"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>'


# ── LiveReport ──


class LiveReport:
    """Updatable report for continuous routines.

    Creates a single report on first update, then overwrites it on each
    subsequent call. Ideal for continuous routines that accumulate data.
    """

    def __init__(
        self, title: str, source_name: str = "", tags: list[str] | None = None
    ):
        self._title = title
        self._source_name = source_name
        self._tags = tags or []
        self._report_id: str | None = None
        self._builder: ReportBuilder | None = None
        self.clear()

    @property
    def report_id(self) -> str | None:
        return self._report_id

    @property
    def builder(self) -> ReportBuilder:
        return self._builder

    def clear(self) -> None:
        """Reset builder for a fresh render cycle."""
        self._builder = ReportBuilder(self._title)
        self._builder.source("routine", self._source_name)
        self._builder.tags(self._tags)

    async def update(self) -> str:
        """Save or update the report. Returns report_id."""
        self._report_id = await self._builder.save(report_id=self._report_id)
        return self._report_id
