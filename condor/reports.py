"""Report builder — composable HTML reports with Plotly charts, markdown, and tables."""

from __future__ import annotations

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

CHARTS_DIR = Path(__file__).resolve().parent.parent / "charts"
INDEX_FILE = CHARTS_DIR / "reports_index.json"
MAX_REPORTS = int(os.environ.get("CONDOR_MAX_REPORTS", "100"))

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
  .plotly-chart {{ min-height: 400px; margin-bottom: 24px; }}
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
</body>
</html>
"""


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
            e for e in entries
            if q in e.get("title", "").lower()
            or q in e.get("source_name", "").lower()
            or any(q in t.lower() for t in e.get("tags", []))
        ]

    total = len(entries)
    return entries[offset : offset + limit], total


def get_report(report_id: str) -> dict | None:
    for e in _read_index():
        if e["id"] == report_id:
            return e
    return None


def delete_report(report_id: str) -> bool:
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


def _cleanup(max_reports: int = MAX_REPORTS) -> None:
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

    def kpi(self, label: str, value: str, delta: str | None = None, trend: str = "neutral") -> ReportBuilder:
        self._sections.append({"type": "kpi", "label": label, "value": value, "delta": delta, "trend": trend})
        return self

    def markdown(self, text: str) -> ReportBuilder:
        self._sections.append({"type": "markdown", "content": text})
        return self

    def plotly(self, fig: Any) -> ReportBuilder:
        html = fig.to_html(full_html=False, include_plotlyjs=False)
        self._sections.append({"type": "plotly", "content": html})
        return self

    def table(self, rows: list[dict], columns: list[str] | None = None) -> ReportBuilder:
        if not columns and rows:
            columns = list(rows[0].keys())
        self._sections.append({"type": "table", "columns": columns or [], "rows": rows})
        return self

    def save(self) -> str:
        CHARTS_DIR.mkdir(exist_ok=True)
        report_id = uuid.uuid4().hex[:6]
        now = datetime.now(timezone.utc)
        ts_str = now.strftime("%Y%m%d_%H%M%S")
        slug = _slugify(self._title)
        filename = f"{ts_str}_{slug}_{report_id}.html"

        sections_html = self._render_sections()
        meta_badges = ""
        if self._source_type:
            meta_badges += f"<span>{self._source_type}: {self._source_name}</span>"
        for tag in self._tags:
            meta_badges += f"<span>#{tag}</span>"

        html = _HTML_TEMPLATE.format(
            title=self._title,
            created_at=now.strftime("%Y-%m-%d %H:%M UTC"),
            meta_badges=meta_badges,
            sections_html=sections_html,
        )

        (CHARTS_DIR / filename).write_text(html)

        entry = {
            "id": report_id,
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
        _cleanup()

        logger.info(f"Report saved: {filename}")
        return report_id

    def _render_sections(self) -> str:
        sections = list(self._sections)
        if not self._manual_order:
            # Stable sort: kpi first, then plotly, table, markdown
            sections = sorted(sections, key=lambda s: _SECTION_PRIORITY.get(s["type"], 99))

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
                        f'{delta_html}</div>'
                    )
                parts.append(f'<div class="kpi-bar">{"".join(cards)}</div>')
            elif sec["type"] == "markdown":
                parts.append(f'<div class="section section-md">{_md_to_html(sec["content"])}</div>')
                i += 1
            elif sec["type"] == "plotly":
                parts.append(f'<div class="section plotly-chart">{sec["content"]}</div>')
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
