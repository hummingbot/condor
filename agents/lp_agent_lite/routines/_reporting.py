"""Best-effort Condor reports for LP Agent Lite routine diagnostics."""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import PurePath
from typing import Any

from condor.reports import ReportBuilder

_TRANSPORT_MAX_CHARS = 1_900
_SENSITIVE_PARTS = {
    "api_key",
    "authorization",
    "credential",
    "headers",
    "jwt",
    "password",
    "private_key",
    "raw_transaction",
    "rpc_url",
    "secret",
    "seed",
    "signed_transaction",
    "transaction_bytes",
}
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_CREDENTIAL = re.compile(
    r"(?i)\b(api[_-]?key|authorization|bearer|jwt|password|private[_-]?key|"
    r"rpc[_-]?url|secret|seed)\b\s*[:=]\s*\S+"
)


def safe_error(value: Any, limit: int = 160) -> str:
    """Return a bounded diagnostic error without credentials or URLs."""

    text = " ".join(str(value if value is not None else "-").split())
    text = _URL.sub("[redacted-url]", text)
    text = _CREDENTIAL.sub(r"\1=[redacted]", text)
    return text if len(text) <= limit else f"{text[:limit]}…"


def _sensitive_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_PARTS)


def sanitize(value: Any, *, depth: int = 0) -> Any:
    """Bound and sanitize one value before it reaches persistent reports."""

    if depth > 8:
        return "[truncated-depth]"
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 100:
                output["[truncated]"] = f"{len(value) - index} fields omitted"
                break
            output[str(key)] = (
                "[redacted]" if _sensitive_key(key) else sanitize(item, depth=depth + 1)
            )
        return output
    if isinstance(value, (list, tuple, set)):
        rows = list(value)
        output = [sanitize(item, depth=depth + 1) for item in rows[:100]]
        if len(rows) > 100:
            output.append(f"[{len(rows) - 100} items omitted]")
        return output
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, PurePath):
        return "[redacted-path]"
    if isinstance(value, str):
        return safe_error(value, 2_000)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return safe_error(value, 1_000)


class DiagnosticTrace:
    """Collect a small ordered trace without participating in control flow."""

    def __init__(self) -> None:
        self.started_at = datetime.now(timezone.utc)
        self._started = time.perf_counter()
        self.events: list[dict[str, Any]] = []

    def record(self, stage: str, outcome: str = "complete", **facts: Any) -> None:
        self.events.append(
            {
                "sequence": len(self.events) + 1,
                "elapsed_ms": round((time.perf_counter() - self._started) * 1_000, 3),
                "stage": safe_error(stage, 80),
                "outcome": safe_error(outcome, 40),
                "facts": sanitize(facts),
            }
        )

    @property
    def duration_ms(self) -> float:
        return round((time.perf_counter() - self._started) * 1_000, 3)


def _rows(value: dict[str, Any]) -> list[dict[str, str]]:
    sanitized = sanitize(value)
    rows: list[dict[str, str]] = []
    for key, item in sanitized.items():
        rendered = (
            json.dumps(item, separators=(",", ":"), sort_keys=True)
            if isinstance(item, (dict, list))
            else str(item)
        )
        rows.append({"field": str(key), "value": safe_error(rendered, 2_000)})
    return rows


def _trace_rows(trace: DiagnosticTrace) -> list[dict[str, Any]]:
    rows = []
    for event in sanitize(trace.events):
        rows.append(
            {
                "sequence": event["sequence"],
                "elapsed_ms": event["elapsed_ms"],
                "stage": event["stage"],
                "outcome": event["outcome"],
                "facts": safe_error(
                    json.dumps(event["facts"], separators=(",", ":"), sort_keys=True),
                    2_000,
                ),
            }
        )
    return rows


async def _save_report(
    *,
    routine_name: str,
    title: str,
    routine_input: dict[str, Any],
    output: dict[str, Any],
    trace: DiagnosticTrace,
    context: Any,
) -> str:
    builder = ReportBuilder(title)
    builder.source("routine", routine_name).tags(
        ["lp-agent-lite", "diagnostic", routine_name.replace("_", "-")]
    )
    builder.kpi("Status", safe_error(output.get("status", "unknown"), 80))
    builder.kpi("Mutation", str(bool(output.get("mutation", False))).lower())
    builder.kpi("Duration", f"{trace.duration_ms:.3f} ms")
    builder.section("Diagnostic metadata")
    builder.table(
        _rows(
            {
                "routine": routine_name,
                "started_at": trace.started_at,
                "finished_at": datetime.now(timezone.utc),
                "duration_ms": trace.duration_ms,
                "chat_id": getattr(context, "_chat_id", None),
                "authority": "diagnostic_only",
            }
        ),
        columns=["field", "value"],
    )
    builder.section("Sanitized routine input")
    builder.table(_rows(routine_input), columns=["field", "value"])
    builder.section("Structured routine output")
    builder.table(_rows(output), columns=["field", "value"])
    builder.section("Ordered debug trace")
    builder.table(
        _trace_rows(trace),
        columns=["sequence", "elapsed_ms", "stage", "outcome", "facts"],
    )
    builder.markdown(
        "Diagnostic artifact only. This report is not trading authority, Agent state, "
        "or a retry signal."
    )
    return await builder.save()


def _encode_with_metadata(
    payload: dict[str, Any], report_id: str | None, report_error: str | None
) -> str:
    reported = {**payload, "report_id": report_id, "report_error": report_error}
    encoded = json.dumps(reported, separators=(",", ":"), ensure_ascii=True)
    if len(encoded) < _TRANSPORT_MAX_CHARS:
        return encoded

    # Diagnostic metadata must never replace or truncate authoritative facts.
    reported["report_error"] = "report_metadata_transport_limit"
    encoded = json.dumps(reported, separators=(",", ":"), ensure_ascii=True)
    if len(encoded) < _TRANSPORT_MAX_CHARS:
        return encoded
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


async def report_result(
    raw_result: str,
    *,
    routine_name: str,
    title: str,
    config: Any,
    trace: DiagnosticTrace,
    context: Any,
) -> str:
    """Save one best-effort report and attach only compact report metadata."""

    try:
        payload = json.loads(raw_result)
        if not isinstance(payload, dict):
            raise ValueError("routine result is not a JSON object")
    except (TypeError, ValueError, json.JSONDecodeError):
        return raw_result

    trace.record(
        "result_finalized",
        status=payload.get("status"),
        mutation=payload.get("mutation", False),
    )
    report_id = None
    report_error = None
    report_output = {**payload, "report_id": None, "report_error": None}
    try:
        routine_input = config.model_dump(mode="json")
        task = asyncio.create_task(
            _save_report(
                routine_name=routine_name,
                title=title,
                routine_input=routine_input,
                output=report_output,
                trace=trace,
                context=context,
            )
        )
        report_id = await asyncio.shield(task)
    except asyncio.CancelledError:
        report_error = "CancelledError: report save was cancelled"
    except Exception as exc:
        report_error = safe_error(f"{type(exc).__name__}: {exc}")
    return _encode_with_metadata(payload, report_id, report_error)
