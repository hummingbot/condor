"""Composable HTML reports and persistent report storage."""

from .builder import LiveReport, ReportBuilder
from .store import (
    CHARTS_DIR,
    INDEX_FILE,
    MAX_REPORTS,
    attribute_to,
    delete_report,
    get_last_report_id,
    get_report,
    get_report_raw_html,
    list_reports,
    list_reports_grouped,
    reset_last_report_id,
)

__all__ = [
    "ReportBuilder",
    "LiveReport",
    "CHARTS_DIR",
    "INDEX_FILE",
    "MAX_REPORTS",
    "attribute_to",
    "reset_last_report_id",
    "get_last_report_id",
    "get_report_raw_html",
    "list_reports",
    "list_reports_grouped",
    "get_report",
    "delete_report",
]
