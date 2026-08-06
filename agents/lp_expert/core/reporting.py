"""Bounded, non-authoritative human-review reports for LP Expert routines."""

from __future__ import annotations

import asyncio
import json
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import PurePath
from typing import Any, Iterator

_SENSITIVE_PARTS = {
    "account",
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
    "wallet",
}
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_CREDENTIAL = re.compile(
    r"(?i)\b(api[_-]?key|authorization|bearer|jwt|password|private[_-]?key|"
    r"rpc[_-]?url|secret|seed)\b\s*[:=]\s*\S+"
)


def _sensitive_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_PARTS)


def safe_error(value: Any, limit: int = 500) -> str:
    """Return one bounded error string without URLs or inline credentials."""

    text = str(value if value is not None else "-")
    text = _URL.sub("[redacted-url]", text)
    text = _CREDENTIAL.sub(r"\1=[redacted]", text)
    return text if len(text) <= limit else f"{text[:limit]}…"


def sanitize(value: Any, *, depth: int = 0) -> Any:
    """Recursively sanitize one report value and bound retained diagnostics."""

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


class TraceRecorder:
    """Collect deterministic, ordered, timed stage events for one invocation."""

    def __init__(self) -> None:
        self.started_at = datetime.now(timezone.utc)
        self._started = time.perf_counter()
        self.events: list[dict[str, Any]] = []

    @contextmanager
    def stage(self, name: str) -> Iterator[dict[str, Any]]:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("trace stage name must be non-empty")
        started_at = datetime.now(timezone.utc)
        started = time.perf_counter()
        facts: dict[str, Any] = {}
        event = {
            "sequence": len(self.events) + 1,
            "stage": name.strip(),
            "started_at": started_at.isoformat(),
            "outcome": "running",
            "facts": facts,
        }
        self.events.append(event)
        try:
            yield facts
        except asyncio.CancelledError:
            event["outcome"] = "cancelled"
            event["error"] = "CancelledError: invocation was cancelled"
            raise
        except BaseException as exc:
            event["outcome"] = "error"
            event["error"] = safe_error(f"{type(exc).__name__}: {exc}")
            raise
        else:
            event["outcome"] = str(facts.pop("_outcome", "complete"))
        finally:
            finished_at = datetime.now(timezone.utc)
            event["finished_at"] = finished_at.isoformat()
            event["duration_ms"] = round((time.perf_counter() - started) * 1_000, 3)

    @property
    def finished_at(self) -> datetime:
        return datetime.now(timezone.utc)

    @property
    def duration_ms(self) -> float:
        return round((time.perf_counter() - self._started) * 1_000, 3)


def _rows(value: dict[str, Any]) -> list[dict[str, str]]:
    sanitized = sanitize(value)
    rows = []
    for key, item in sanitized.items():
        rendered = (
            json.dumps(item, separators=(",", ":"), sort_keys=True)
            if isinstance(item, (dict, list))
            else str(item)
        )
        rows.append({"field": str(key), "value": safe_error(rendered, 2_000)})
    return rows


def _trace_rows(trace: TraceRecorder) -> list[dict[str, Any]]:
    rows = []
    for event in trace.events:
        safe = sanitize(event)
        rows.append(
            {
                "sequence": safe.get("sequence"),
                "stage": safe.get("stage"),
                "started_at": safe.get("started_at"),
                "finished_at": safe.get("finished_at"),
                "duration_ms": safe.get("duration_ms"),
                "outcome": safe.get("outcome"),
                "facts": safe_error(
                    json.dumps(
                        safe.get("facts", {}),
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    2_000,
                ),
                "error": safe.get("error", "-"),
            }
        )
    return rows


async def _save_report(
    *,
    title: str,
    routine_name: str,
    version: str,
    normalized_input: dict[str, Any],
    output: dict[str, Any],
    trace: TraceRecorder,
    scope: Any | None,
    links: dict[str, Any] | None,
) -> str:
    from condor.reports import ReportBuilder

    observed_at = datetime.now(timezone.utc).isoformat()
    builder = ReportBuilder(title)
    builder.source("routine", routine_name).tags(
        ["lp-expert", "routine-trace", routine_name.replace("_", "-")]
    )
    builder.kpi("Status", safe_error(output.get("status", "unknown")))
    builder.kpi("Mutation", safe_error(output.get("mutation", False)))
    builder.kpi("Retry allowed", safe_error(output.get("retry_allowed", False)))
    metadata = {
        "routine_name": routine_name,
        "version": version,
        "started_at": trace.started_at.isoformat(),
        "finished_at": observed_at,
        "duration_ms": trace.duration_ms,
        "controller_id": getattr(scope, "controller_id", None),
        "strategy": getattr(scope, "strategy_slug", None),
        "execution_mode": getattr(scope, "execution_mode", None),
        "server_name": getattr(scope, "server_name", None),
        "account_name": getattr(scope, "account_name", None),
        "network": getattr(scope, "network", None),
        "operation_id": normalized_input.get("operation_id"),
        "executor_id": normalized_input.get("executor_id"),
        "links": links or {},
    }
    builder.section("Report metadata")
    builder.table(_rows(metadata), columns=["field", "value"])
    builder.section("Sanitized input")
    builder.table(_rows(normalized_input), columns=["field", "value"])
    builder.section("Structured output")
    builder.table(_rows(output), columns=["field", "value"])
    builder.section("Ordered debug trace")
    builder.table(
        _trace_rows(trace),
        columns=[
            "sequence",
            "stage",
            "started_at",
            "finished_at",
            "duration_ms",
            "outcome",
            "facts",
            "error",
        ],
    )
    return await builder.save()


async def attach_report(
    payload: dict[str, Any],
    *,
    title: str,
    source: str | None = None,
    routine_name: str | None = None,
    version: str = "1",
    routine_input: dict[str, Any] | None = None,
    normalized_input: dict[str, Any] | None = None,
    trace: TraceRecorder,
    links: dict[str, Any] | None = None,
    scope: Any | None = None,
) -> dict[str, Any]:
    """Attach report metadata without changing the authoritative routine result."""

    output = dict(payload)
    output["report_id"] = None
    output["report_error"] = None
    report_output = {**output}
    selected_source = source or routine_name
    selected_input = routine_input if routine_input is not None else normalized_input
    if not selected_source:
        raise ValueError("report source is required")
    if not isinstance(selected_input, dict):
        raise ValueError("normalized routine input is required")
    try:
        task = asyncio.create_task(
            _save_report(
                title=title,
                routine_name=selected_source,
                version=version,
                normalized_input=selected_input,
                output=report_output,
                trace=trace,
                scope=scope,
                links=links,
            )
        )
        output["report_id"] = await asyncio.shield(task)
    except asyncio.CancelledError:
        output["report_error"] = "CancelledError: report save was cancelled"
    except Exception as exc:
        output["report_error"] = safe_error(f"{type(exc).__name__}: {exc}")
    return output
