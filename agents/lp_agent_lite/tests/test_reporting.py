from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import condor.reports as reports
from routines.base import discover_routines_from_path

from agents.lp_agent_lite.routines import _reporting

_REAL_REPORT_SAVE = _reporting.ReportBuilder.save
_PUBLIC_ROUTINES = {
    "scan_orca_pools",
    "calculate_lp_requirements",
    "inspect_orca_positions",
    "snapshot_lp_metrics",
    "register_gateway_token",
}


@dataclass
class Config:
    wallet_address: str = "Cs8TbwfyEN1paECV8aarY4oi9FMevWDBdEdb2afxpWgP"
    api_key: str = "hunter2"

    def model_dump(self, mode):
        assert mode == "json"
        return {
            "wallet_address": self.wallet_address,
            "api_key": self.api_key,
            "limit": 4,
        }


class Builder:
    latest = None

    def __init__(self, title):
        self.title = title
        self.calls = []
        Builder.latest = self

    def _call(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        return self

    def source(self, *args, **kwargs):
        return self._call("source", *args, **kwargs)

    def tags(self, *args, **kwargs):
        return self._call("tags", *args, **kwargs)

    def kpi(self, *args, **kwargs):
        return self._call("kpi", *args, **kwargs)

    def section(self, *args, **kwargs):
        return self._call("section", *args, **kwargs)

    def table(self, *args, **kwargs):
        return self._call("table", *args, **kwargs)

    def markdown(self, *args, **kwargs):
        return self._call("markdown", *args, **kwargs)

    async def save(self):
        return "abc123"


def _run(raw, trace=None):
    return json.loads(
        asyncio.run(
            _reporting.report_result(
                json.dumps(raw, separators=(",", ":")),
                routine_name="example_routine",
                title="Example diagnostic",
                config=Config(),
                trace=trace or _reporting.DiagnosticTrace(),
                context=None,
            )
        )
    )


def test_builtin_report_contains_sanitized_input_output_and_trace(monkeypatch):
    monkeypatch.setattr(_reporting, "ReportBuilder", Builder)
    trace = _reporting.DiagnosticTrace()
    trace.record("fetch", endpoint="https://secret.invalid?q=token")

    result = _run({"status": "complete", "mutation": False}, trace)

    assert result == {
        "status": "complete",
        "mutation": False,
        "report_id": "abc123",
        "report_error": None,
    }
    builder = Builder.latest
    assert builder.title == "Example diagnostic"
    assert ("source", ("routine", "example_routine"), {}) in builder.calls
    sections = [call[1][0] for call in builder.calls if call[0] == "section"]
    assert sections == [
        "Diagnostic metadata",
        "Sanitized routine input",
        "Structured routine output",
        "Ordered debug trace",
    ]
    rendered = repr(builder.calls)
    assert "hunter2" not in rendered
    assert "secret.invalid" not in rendered
    assert "[redacted]" in rendered
    assert "[redacted-url]" in rendered
    assert Config.wallet_address in rendered
    assert "diagnostic_only" in rendered


def test_report_failure_preserves_authoritative_result_and_sanitizes_error(
    monkeypatch,
):
    async def fail(_builder, report_id=None):
        raise OSError("password=hunter2 https://report.invalid?token=abc")

    monkeypatch.setattr(_reporting.ReportBuilder, "save", fail)
    result = _run({"status": "uncertain", "mutation": True, "present": None})

    assert result["status"] == "uncertain"
    assert result["mutation"] is True
    assert result["present"] is None
    assert result["report_id"] is None
    assert result["report_error"].startswith("OSError:")
    assert "hunter2" not in result["report_error"]
    assert "report.invalid" not in result["report_error"]


def test_report_cancellation_is_not_a_trading_cancellation(monkeypatch):
    async def cancel(_builder, report_id=None):
        raise asyncio.CancelledError

    monkeypatch.setattr(_reporting.ReportBuilder, "save", cancel)
    result = _run({"status": "complete", "mutation": False})

    assert result["status"] == "complete"
    assert result["mutation"] is False
    assert result["report_id"] is None
    assert result["report_error"] == "CancelledError: report save was cancelled"


def test_report_input_serialization_failure_preserves_result():
    class BrokenConfig:
        def model_dump(self, mode):
            raise RuntimeError("password=hunter2")

    trace = _reporting.DiagnosticTrace()
    raw = asyncio.run(
        _reporting.report_result(
            '{"status":"complete","mutation":false}',
            routine_name="example_routine",
            title="Example diagnostic",
            config=BrokenConfig(),
            trace=trace,
            context=None,
        )
    )
    result = json.loads(raw)

    assert result["status"] == "complete"
    assert result["mutation"] is False
    assert result["report_id"] is None
    assert result["report_error"].startswith("RuntimeError:")
    assert "hunter2" not in result["report_error"]


def test_sanitizer_bounds_nested_diagnostics_and_keeps_public_identity():
    sanitized = _reporting.sanitize(
        {
            "wallet_address": Config.wallet_address,
            "private_key": "never-store-this",
            "rows": list(range(120)),
        }
    )

    assert sanitized["wallet_address"] == Config.wallet_address
    assert sanitized["private_key"] == "[redacted]"
    assert len(sanitized["rows"]) == 101
    assert sanitized["rows"][-1] == "[20 items omitted]"


def test_builtin_report_builder_smoke_saves_reviewable_html(monkeypatch, tmp_path):
    reports_dir = tmp_path / "reports"
    monkeypatch.setattr(reports, "CHARTS_DIR", reports_dir)
    monkeypatch.setattr(reports, "INDEX_FILE", reports_dir / "reports_index.json")
    monkeypatch.setattr(_reporting.ReportBuilder, "save", _REAL_REPORT_SAVE)
    trace = _reporting.DiagnosticTrace()
    trace.record("observe", rows=2, coverage="complete")

    result = _run({"status": "complete", "mutation": False}, trace)

    assert result["report_error"] is None
    assert len(result["report_id"]) == 6
    index = json.loads((reports_dir / "reports_index.json").read_text())
    assert index[0]["id"] == result["report_id"]
    html = (reports_dir / index[0]["filename"]).read_text()
    assert "Sanitized routine input" in html
    assert "Structured routine output" in html
    assert "Ordered debug trace" in html
    assert "Diagnostic artifact only" in html


def test_discovery_keeps_reporting_adapter_private():
    routines_dir = Path(__file__).resolve().parents[1] / "routines"

    discovered = discover_routines_from_path(
        routines_dir, agent_slug="lp_agent_lite", force_reload=True
    )

    assert set(discovered) == _PUBLIC_ROUTINES
