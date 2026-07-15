"""Composable report builders backed by report storage and rendering modules."""

from __future__ import annotations

import html
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from . import rendering, store

logger = logging.getLogger(__name__)

_SECTION_PRIORITY = {
    "section_header": -1,
    "filter": 0,
    "kpi": 1,
    "metric": 1,
    "plotly": 2,
    "chart": 2,
    "table": 3,
    "data_table": 3,
    "drilldown": 3,
    "markdown": 4,
}

_INTERACTIVE_SECTION_TYPES = {
    "filter",
    "metric",
    "chart",
    "data_table",
    "drilldown",
}


class ReportBuilder:
    def __init__(self, title: str = "Report"):
        self._title = title
        self._source_type: str = ""
        self._source_name: str = ""
        self._tags: list[str] = []
        self._sections: list[dict] = []
        self._datasets: dict[str, list[dict]] = {}
        self._manual_order = False
        self._auto_refresh_seconds: int | None = None

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

    def auto_refresh(self, seconds: int | None) -> ReportBuilder:
        if seconds is None:
            self._auto_refresh_seconds = None
            return self
        if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 1:
            raise ValueError("Auto-refresh interval must be at least 1 second")
        self._auto_refresh_seconds = seconds
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

    def section(self, title: str, description: str | None = None) -> ReportBuilder:
        """Add a semantic full-width report section heading."""
        if not title or not isinstance(title, str):
            raise ValueError("Section title must be a non-empty string")
        self._sections.append(
            {
                "type": "section_header",
                "title": title,
                "description": description,
            }
        )
        return self

    def plotly(self, fig: Any) -> ReportBuilder:
        content = fig.to_html(full_html=False, include_plotlyjs=False)
        self._sections.append({"type": "plotly", "content": content})
        return self

    def table(
        self, rows: list[dict], columns: list[str] | None = None
    ) -> ReportBuilder:
        if not columns and rows:
            columns = list(rows[0].keys())
        self._sections.append({"type": "table", "columns": columns or [], "rows": rows})
        return self

    def dataset(self, name: str, rows: list[dict]) -> ReportBuilder:
        """Register a named row dataset for interactive report components."""
        if not name or not isinstance(name, str):
            raise ValueError("Dataset name must be a non-empty string")
        if name in self._datasets:
            raise ValueError(f"Dataset '{name}' already exists")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise TypeError("Dataset rows must be a list of dictionaries")
        self._datasets[name] = list(rows)
        return self

    def select_filter(
        self,
        name: str,
        source: str,
        field: str,
        label: str | None = None,
        multiple: bool = True,
        width: int = 4,
        help_text: str | None = None,
    ) -> ReportBuilder:
        """Add a categorical filter shared by components using ``source``."""
        return self._add_component(
            "filter",
            name,
            width,
            filter_type="select",
            source=source,
            field=field,
            label=label or field.replace("_", " ").title(),
            multiple=multiple,
            help_text=help_text,
        )

    def range_filter(
        self,
        name: str,
        source: str,
        field: str,
        label: str | None = None,
        value_type: str = "auto",
        width: int = 6,
        help_text: str | None = None,
    ) -> ReportBuilder:
        """Add a numeric or date range filter shared by a dataset."""
        if value_type not in {"auto", "number", "date", "datetime"}:
            raise ValueError(
                "value_type must be 'auto', 'number', 'date', or 'datetime'"
            )
        return self._add_component(
            "filter",
            name,
            width,
            filter_type="range",
            source=source,
            field=field,
            label=label or field.replace("_", " ").title(),
            value_type=value_type,
            help_text=help_text,
        )

    def metric(
        self,
        label: str,
        source: str,
        field: str | None = None,
        aggregate: str = "first",
        format: str | None = None,
        prefix: str = "",
        suffix: str = "",
        width: int = 3,
        component_id: str | None = None,
    ) -> ReportBuilder:
        """Add a KPI whose value is recalculated from filtered dataset rows."""
        allowed = {
            "count",
            "sum",
            "mean",
            "min",
            "max",
            "median",
            "first",
            "last",
            "change_pct",
        }
        if aggregate not in allowed:
            raise ValueError(f"Unsupported metric aggregate: {aggregate}")
        if aggregate != "count" and not field:
            raise ValueError("field is required unless aggregate='count'")
        return self._add_component(
            "metric",
            component_id,
            width,
            label=label,
            source=source,
            field=field,
            aggregate=aggregate,
            format=format,
            prefix=prefix,
            suffix=suffix,
        )

    def chart(
        self,
        chart_type: str,
        title: str,
        source: str,
        x: str,
        y: str | None = None,
        *,
        color: str | None = None,
        color_map: dict[str, str] | None = None,
        aggregate: str | None = None,
        bins: int = 30,
        cross_filter: bool = True,
        x_label: str | None = None,
        y_label: str | None = None,
        selection_help: str | None = None,
        selection_mode: str = "filter",
        selection_label: str | None = None,
        selection_field: str | None = None,
        category_order: list[Any] | None = None,
        width: int = 12,
        height: int = 420,
        component_id: str | None = None,
        **encodings: Any,
    ) -> ReportBuilder:
        """Add a data-bound Plotly chart that can filter sibling components."""
        allowed = {
            "line",
            "area",
            "bar",
            "horizontal_bar",
            "scatter",
            "treemap",
            "box",
            "histogram",
            "candlestick",
        }
        if chart_type not in allowed:
            raise ValueError(f"Unsupported chart type: {chart_type}")
        aggregates = {"count", "sum", "mean", "min", "max", "median", "first", "last"}
        if aggregate is not None and aggregate not in aggregates:
            raise ValueError(f"Unsupported chart aggregate: {aggregate}")
        if selection_mode not in {"filter", "drilldown"}:
            raise ValueError("selection_mode must be 'filter' or 'drilldown'")
        if (
            chart_type
            in {
                "line",
                "area",
                "bar",
                "horizontal_bar",
                "scatter",
                "treemap",
                "box",
            }
            and not y
        ):
            raise ValueError(f"y is required for {chart_type} charts")
        if chart_type == "treemap" and aggregate is not None:
            raise ValueError("treemap charts do not support aggregate")
        if chart_type == "candlestick":
            missing = {"open", "high", "low", "close"} - encodings.keys()
            if missing:
                raise ValueError(
                    "candlestick charts require " + ", ".join(sorted(missing))
                )
        return self._add_component(
            "chart",
            component_id,
            width,
            chart_type=chart_type,
            title=title,
            source=source,
            x=x,
            y=y,
            color=color,
            color_map=color_map or {},
            aggregate=aggregate,
            bins=max(1, int(bins)),
            cross_filter=cross_filter,
            x_label=x_label,
            y_label=y_label,
            selection_help=selection_help,
            selection_mode=selection_mode,
            selection_label=selection_label,
            selection_field=selection_field,
            category_order=category_order or [],
            height=max(240, int(height)),
            encodings=encodings,
        )

    def data_table(
        self,
        source: str,
        columns: list[str | dict] | None = None,
        *,
        title: str = "",
        searchable: bool = True,
        sortable: bool = True,
        page_size: int = 25,
        width: int = 12,
        component_id: str | None = None,
    ) -> ReportBuilder:
        """Add a filter-aware table with search, sorting, paging, and CSV export."""
        return self._add_component(
            "data_table",
            component_id,
            width,
            source=source,
            title=title,
            columns=columns,
            searchable=searchable,
            sortable=sortable,
            page_size=max(1, int(page_size)),
        )

    def drilldown(
        self,
        source: str,
        columns: list[str | dict] | None = None,
        *,
        title: str = "Selection Details",
        page_size: int = 25,
        width: int = 12,
        component_id: str | None = None,
    ) -> ReportBuilder:
        """Add a table that displays rows selected in a linked chart."""
        return self._add_component(
            "drilldown",
            component_id,
            width,
            source=source,
            title=title,
            columns=columns,
            searchable=True,
            sortable=True,
            page_size=max(1, int(page_size)),
        )

    def _add_component(
        self,
        section_type: str,
        component_id: str | None,
        width: int,
        **config: Any,
    ) -> ReportBuilder:
        source = config.get("source")
        if source not in self._datasets:
            raise ValueError(f"Unknown dataset: {source}")
        component_id = component_id or f"{section_type}-{len(self._sections) + 1}"
        if any(section.get("id") == component_id for section in self._sections):
            raise ValueError(f"Component '{component_id}' already exists")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", component_id):
            raise ValueError(
                "Component IDs must start with a letter and contain only letters, numbers, '_' or '-'"
            )
        self._sections.append(
            {
                "type": section_type,
                "id": component_id,
                "width": max(1, min(12, int(width))),
                **config,
            }
        )
        return self

    async def save(self, report_id: str | None = None) -> str:
        """Save a new report or update an existing report in place."""
        charts_dir = store._charts_dir()
        charts_dir.mkdir(exist_ok=True)
        now = datetime.now(timezone.utc)
        sections_html = self._render_sections()
        interactive_sections = [
            section
            for section in self._sections
            if section["type"] in _INTERACTIVE_SECTION_TYPES
        ]
        report_spec = rendering.json_for_html(
            {
                "datasets": self._datasets,
                "components": interactive_sections,
                "auto_refresh_seconds": self._auto_refresh_seconds,
            }
        )
        needs_plotly = any(
            section["type"] in {"plotly", "chart"} for section in self._sections
        )
        meta_badges = ""
        if self._source_type:
            meta_badges += (
                f"<span>{html.escape(str(self._source_type))}: "
                f"{html.escape(str(self._source_name))}</span>"
            )
        for tag in self._tags:
            meta_badges += f"<span>#{html.escape(str(tag))}</span>"

        html_content = rendering.HTML_TEMPLATE.format(
            title=html.escape(str(self._title)),
            created_at=now.strftime("%Y-%m-%d %H:%M UTC"),
            meta_badges=meta_badges,
            sections_html=sections_html,
            report_spec=report_spec,
            plotly_script=rendering.plotly_script() if needs_plotly else "",
            report_runtime=(
                f"<script>{rendering.report_runtime()}</script>"
                if interactive_sections or self._auto_refresh_seconds is not None
                else ""
            ),
        )

        async with store._index_lock:
            if report_id is not None:
                entries = store._read_index()
                entry = next(
                    (item for item in entries if item["id"] == report_id), None
                )
                if entry is None:
                    raise ValueError(f"Report '{report_id}' not found in index")
                store._write_report_html(charts_dir / entry["filename"], html_content)
                entry["updated_at"] = now.isoformat()
                entry["title"] = self._title
                entry["tags"] = self._tags
                store._write_index(entries)
                store._last_report_id.set(report_id)
                logger.info(f"Report updated: {entry['filename']}")
                return report_id

            new_id = uuid.uuid4().hex[:6]
            timestamp = now.strftime("%Y%m%d_%H%M%S")
            slug = rendering.slugify(self._title)
            filename = f"{timestamp}_{slug}_{new_id}.html"
            store._write_report_html(charts_dir / filename, html_content)
            entry = {
                "id": new_id,
                "title": self._title,
                "filename": filename,
                "created_at": now.isoformat(),
                "source_type": self._source_type,
                "source_name": self._source_name,
                "tags": self._tags,
                "agent": store._report_agent.get() or "condor",
            }
            entries = store._read_index()
            entries.append(entry)
            store._write_index(entries)
            store._cleanup_locked()

        store._last_report_id.set(new_id)
        logger.info(f"Report saved: {filename}")
        return new_id

    def _render_sections(self) -> str:
        sections = list(self._sections)
        if not self._manual_order and not any(
            section["type"] == "section_header" for section in sections
        ):
            sections = sorted(
                sections, key=lambda section: _SECTION_PRIORITY.get(section["type"], 99)
            )

        parts = []
        index = 0
        while index < len(sections):
            section = sections[index]
            if section["type"] == "section_header":
                description = ""
                if section["description"]:
                    description = (
                        '<p class="report-section-description">'
                        f'{html.escape(str(section["description"]))}</p>'
                    )
                parts.append(
                    '<div class="section report-section-heading">'
                    f'<h2>{html.escape(str(section["title"]))}</h2>'
                    f"{description}</div>"
                )
                index += 1
            elif section["type"] == "kpi":
                kpis = []
                while index < len(sections) and sections[index]["type"] == "kpi":
                    kpis.append(sections[index])
                    index += 1
                cards = []
                for kpi in kpis:
                    delta_html = ""
                    if kpi["delta"]:
                        css_class = (
                            f' {kpi["trend"]}' if kpi["trend"] in ("up", "down") else ""
                        )
                        delta = html.escape(str(kpi["delta"]))
                        delta_html = f'<div class="delta{css_class}">{delta}</div>'
                    cards.append(
                        '<div class="kpi-card">'
                        f'<div class="label">{html.escape(str(kpi["label"]))}</div>'
                        f'<div class="value">{html.escape(str(kpi["value"]))}</div>'
                        f"{delta_html}</div>"
                    )
                parts.append(f'<div class="kpi-bar">{"".join(cards)}</div>')
            elif section["type"] == "markdown":
                parts.append(
                    '<div class="section section-md">'
                    f'{rendering.markdown_to_html(section["content"])}</div>'
                )
                index += 1
            elif section["type"] == "plotly":
                parts.append(
                    f'<div class="section plotly-chart report-panel">{section["content"]}</div>'
                )
                index += 1
            elif section["type"] == "table":
                parts.append(self._render_table(section["columns"], section["rows"]))
                index += 1
            elif section["type"] in _INTERACTIVE_SECTION_TYPES:
                component_id = html.escape(section["id"], quote=True)
                parts.append(
                    f'<div id="report-component-{component_id}" '
                    f'class="report-component" style="--component-span:{section["width"]}"></div>'
                )
                index += 1
            else:
                index += 1
        return "\n".join(parts)

    @staticmethod
    def _render_table(columns: list[str], rows: list[dict]) -> str:
        header = "".join(f"<th>{html.escape(str(column))}</th>" for column in columns)
        body_rows = []
        for row in rows:
            cells = "".join(
                f"<td>{html.escape(str(row.get(column, '')))}</td>"
                for column in columns
            )
            body_rows.append(f"<tr>{cells}</tr>")
        body = "\n".join(body_rows)
        return (
            '<div class="section section-table"><table><thead><tr>'
            f"{header}</tr></thead><tbody>{body}</tbody></table></div>"
        )


class LiveReport:
    """An updatable report for continuous routines."""

    def __init__(
        self,
        title: str,
        source_name: str = "",
        tags: list[str] | None = None,
        auto_refresh_seconds: int | None = None,
    ):
        self._title = title
        self._source_name = source_name
        self._tags = tags or []
        self._auto_refresh_seconds = auto_refresh_seconds
        self._report_id: str | None = None
        self._builder = self._fresh_builder()

    def _fresh_builder(self) -> ReportBuilder:
        builder = ReportBuilder(self._title)
        builder.source("routine", self._source_name)
        builder.tags(self._tags)
        if self._auto_refresh_seconds is not None:
            builder.auto_refresh(self._auto_refresh_seconds)
        return builder

    @property
    def report_id(self) -> str | None:
        return self._report_id

    @property
    def builder(self) -> ReportBuilder:
        return self._builder

    def clear(self) -> None:
        """Reset the builder for a fresh render cycle."""
        self._builder = self._fresh_builder()

    async def update(self) -> str:
        """Save or update the report and return its ID."""
        previous_id = self._report_id
        try:
            self._report_id = await self._builder.save(report_id=previous_id)
        except ValueError:
            if previous_id is None or store.get_report(previous_id) is not None:
                raise
            logger.info(
                "Live report %s was removed; creating a replacement", previous_id
            )
            self._report_id = await self._builder.save()
        return self._report_id
