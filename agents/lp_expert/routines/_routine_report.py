"""Small, non-authoritative diagnostic reports for LP Expert routines."""

from __future__ import annotations

import asyncio
import re
from typing import Any

_SENSITIVE_KEYS = {
    "account",
    "account_name",
    "api_key",
    "authorization",
    "headers",
    "password",
    "rpc_url",
    "secret",
    "wallet",
    "wallet_address",
}
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_CREDENTIAL = re.compile(
    r"(?i)\b(api[_-]?key|authorization|bearer|password|secret|rpc[_-]?url)"
    r"\b\s*[:=]\s*\S+"
)


def safe_text(value: Any, limit: int = 500) -> str:
    """Render bounded diagnostics without URLs or inline credentials."""
    text = str(value if value is not None else "-")
    text = _URL.sub("[redacted-url]", text)
    text = _CREDENTIAL.sub(r"\1=[redacted]", text)
    return text if len(text) <= limit else f"{text[:limit]}…"


def _safe_row(row: dict[str, Any]) -> dict[str, str]:
    return {
        str(key): (
            "[redacted]" if str(key).lower() in _SENSITIVE_KEYS else safe_text(value)
        )
        for key, value in row.items()
    }


async def save_trace(
    *,
    title: str,
    source: str,
    status: str,
    summary: dict[str, Any],
    evidence: list[dict[str, Any]] | None = None,
) -> str:
    """Save one compact report through Condor's shared report platform."""
    from condor.reports import ReportBuilder

    builder = ReportBuilder(title)
    builder.source("routine", source).tags(
        ["lp-expert", "routine-trace", source.replace("_", "-")]
    )
    builder.kpi("Status", safe_text(status))
    summary_rows = [
        {"field": key.replace("_", " ").title(), "value": value}
        for key, value in _safe_row(summary).items()
    ]
    if summary_rows:
        builder.section("Request and result")
        builder.table(summary_rows, columns=["field", "value"])
    safe_evidence = [_safe_row(row) for row in evidence or []]
    if safe_evidence:
        columns = list(dict.fromkeys(key for row in safe_evidence for key in row))
        builder.section("Observed evidence")
        builder.table(safe_evidence, columns=columns)
    return await builder.save()


async def attach_trace(
    payload: dict[str, Any],
    *,
    title: str,
    source: str,
    status: str,
    summary: dict[str, Any],
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Attach optional report metadata without changing the routine outcome."""
    output = dict(payload)
    output["report_id"] = None
    output["report_error"] = None
    try:
        output["report_id"] = await save_trace(
            title=title,
            source=source,
            status=status,
            summary=summary,
            evidence=evidence,
        )
    except asyncio.CancelledError:
        output["report_error"] = "CancelledError: report save was cancelled"
    except Exception as exc:
        output["report_error"] = safe_text(f"{type(exc).__name__}: {exc}")
    return output
