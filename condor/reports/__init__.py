"""Composable HTML reports and persistent report storage."""

from .builder import LiveReport, ReportBuilder
from .rendering import hydrate
from .store import (
    CHARTS_DIR,
    INDEX_FILE,
    MAX_REPORTS,
    attribute_owner,
    attribute_to,
    current_agent,
    default_source,
    delete_report,
    get_last_report_id,
    get_report,
    get_report_raw_html,
    list_reports,
    list_reports_grouped,
    reset_last_report_id,
    resolve_report_asset,
    run_scope,
)

__all__ = [
    "ReportBuilder",
    "LiveReport",
    "CHARTS_DIR",
    "INDEX_FILE",
    "MAX_REPORTS",
    "attribute_owner",
    "attribute_to",
    "default_source",
    "run_scope",
    "current_agent",
    "reset_last_report_id",
    "get_last_report_id",
    "get_report_raw_html",
    "hydrate",
    "resolve_report_asset",
    "list_reports",
    "list_reports_grouped",
    "get_report",
    "delete_report",
]
