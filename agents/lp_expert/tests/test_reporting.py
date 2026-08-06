from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from agents.lp_expert.core import reporting


def test_report_sanitization_redacts_secrets_paths_wallets_and_urls(tmp_path):
    value = reporting.sanitize(
        {
            "api_key": "secret-value",
            "jwt": "signed-token",
            "private_key": "private-material",
            "signed_transaction": "raw-bytes",
            "wallet_address": "wallet-value",
            "nested": {
                "authorization": "Bearer token",
                "path": tmp_path / "receipt.json",
                "error": "failed at https://rpc.example/?key=secret",
            },
            "amount": Decimal("1.2300"),
        }
    )

    assert value["api_key"] == "[redacted]"
    assert value["jwt"] == "[redacted]"
    assert value["private_key"] == "[redacted]"
    assert value["signed_transaction"] == "[redacted]"
    assert value["wallet_address"] == "[redacted]"
    assert value["nested"]["authorization"] == "[redacted]"
    assert value["nested"]["path"] == "[redacted-path]"
    assert "rpc.example" not in value["nested"]["error"]
    assert value["amount"] == "1.2300"


def test_trace_records_ordered_input_output_debug_timing_and_failure():
    trace = reporting.TraceRecorder()
    with trace.stage("input_validation") as facts:
        facts["input"] = {"amount": "1"}
        facts["output"] = {"valid": True}
    with pytest.raises(RuntimeError):
        with trace.stage("mutation") as facts:
            facts["debug"] = "before-submit"
            raise RuntimeError("authorization=Bearer-secret https://private.invalid")

    assert [event["sequence"] for event in trace.events] == [1, 2]
    assert [event["stage"] for event in trace.events] == [
        "input_validation",
        "mutation",
    ]
    assert trace.events[0]["outcome"] == "complete"
    assert trace.events[0]["facts"]["input"] == {"amount": "1"}
    assert trace.events[0]["facts"]["output"] == {"valid": True}
    assert trace.events[1]["outcome"] == "error"
    assert "Bearer-secret" not in trace.events[1]["error"]
    assert "private.invalid" not in trace.events[1]["error"]
    assert all(event["duration_ms"] >= 0 for event in trace.events)
    assert trace.duration_ms >= 0


def test_attach_report_success_preserves_result_and_adds_metadata(monkeypatch):
    captured = {}

    async def save(**kwargs):
        captured.update(kwargs)
        return "report-123"

    monkeypatch.setattr(reporting, "_save_report", save)
    trace = reporting.TraceRecorder()
    with trace.stage("done"):
        pass
    payload = {
        "status": "confirmed",
        "mutation": True,
        "retry_allowed": False,
        "receipt": {"transaction_hash": "tx-1"},
    }

    result = asyncio.run(
        reporting.attach_report(
            payload,
            title="LP swap",
            routine_name="lp_swap",
            normalized_input={"operation_id": "operation-1"},
            trace=trace,
            links={"operation_id": "operation-1"},
        )
    )

    assert result == {
        **payload,
        "report_id": "report-123",
        "report_error": None,
    }
    assert captured["routine_name"] == "lp_swap"
    assert captured["normalized_input"] == {"operation_id": "operation-1"}
    assert captured["output"]["status"] == "confirmed"
    assert captured["links"] == {"operation_id": "operation-1"}


def test_report_failure_never_changes_authoritative_outcome(monkeypatch):
    async def fail(**_):
        raise OSError("password=hunter2 https://report.invalid")

    monkeypatch.setattr(reporting, "_save_report", fail)
    trace = reporting.TraceRecorder()
    payload = {
        "status": "uncertain",
        "mutation": True,
        "retry_allowed": False,
        "reason": "submission outcome unavailable",
    }

    result = asyncio.run(
        reporting.attach_report(
            payload,
            title="LP create",
            routine_name="lp_create",
            normalized_input={"operation_id": "create-1"},
            trace=trace,
        )
    )

    assert {key: result[key] for key in payload} == payload
    assert result["report_id"] is None
    assert result["report_error"].startswith("OSError:")
    assert "hunter2" not in result["report_error"]
    assert "report.invalid" not in result["report_error"]


@pytest.mark.parametrize(
    ("status", "mutation"),
    [
        ("complete", False),
        ("rejected", False),
        ("uncertain", True),
        ("cancelled", False),
    ],
)
def test_report_envelope_preserves_every_outcome_class(monkeypatch, status, mutation):
    async def save(**_):
        return f"report-{status}"

    monkeypatch.setattr(reporting, "_save_report", save)
    trace = reporting.TraceRecorder()
    if status == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            with trace.stage("work"):
                raise asyncio.CancelledError
        assert trace.events[0]["outcome"] == "cancelled"
    else:
        with trace.stage("work"):
            pass

    payload = {
        "status": status,
        "mutation": mutation,
        "retry_allowed": False,
    }
    result = asyncio.run(
        reporting.attach_report(
            payload,
            title="LP outcome",
            routine_name="lp_snapshot",
            normalized_input={"controller_id": "lp_expert.orca_1"},
            trace=trace,
        )
    )

    assert {key: result[key] for key in payload} == payload
    assert result["report_id"] == f"report-{status}"
    assert result["report_error"] is None


def test_saved_report_has_metadata_input_output_and_ordered_trace(monkeypatch):
    captured = {"sections": [], "tables": []}

    class Builder:
        def __init__(self, title):
            captured["title"] = title

        def source(self, kind, value):
            captured["source"] = (kind, value)
            return self

        def tags(self, value):
            captured["tags"] = value
            return self

        def kpi(self, name, value):
            captured.setdefault("kpis", []).append((name, value))
            return self

        def section(self, name):
            captured["sections"].append(name)
            return self

        def table(self, rows, columns):
            captured["tables"].append((rows, columns))
            return self

        async def save(self):
            return "report-envelope"

    monkeypatch.setattr("condor.reports.ReportBuilder", Builder)
    trace = reporting.TraceRecorder()
    with trace.stage("validate") as facts:
        facts["outcome_classification"] = "confirmed"

    report_id = asyncio.run(
        reporting._save_report(
            title="LP routine",
            routine_name="lp_swap",
            version="1",
            normalized_input={"operation_id": "op-1", "wallet": "secret"},
            output={
                "status": "confirmed",
                "mutation": True,
                "retry_allowed": False,
            },
            trace=trace,
            scope=None,
            links={"operation_id": "op-1"},
        )
    )

    assert report_id == "report-envelope"
    assert captured["sections"] == [
        "Report metadata",
        "Sanitized input",
        "Structured output",
        "Ordered debug trace",
    ]
    assert len(captured["tables"]) == 4
    input_rows = captured["tables"][1][0]
    assert {"field": "wallet", "value": "[redacted]"} in input_rows
    trace_rows = captured["tables"][3][0]
    assert trace_rows[0]["sequence"] == 1
    assert trace_rows[0]["stage"] == "validate"
    assert trace_rows[0]["outcome"] == "complete"
