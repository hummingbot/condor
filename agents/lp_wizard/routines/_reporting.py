from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from agents.lp_wizard.routines import _shared
from routines.base import RoutineResult

log = logging.getLogger(__name__)
REPORT_TIMEOUT_SECONDS = 10
CANCELLATION_REPORT_TIMEOUT_SECONDS = 2

_REPORT_REDACTED_KEYS = {
    "defaultsolanawallet",
    "observeddefaultwallet",
    "owneraddress",
    "wallet",
    "walletaddress",
}
_CREDENTIAL_URL_RE = re.compile(r"(https?://)[^/\s:@]+:[^@\s/]+@", re.IGNORECASE)
_AUTHORIZATION_ASSIGNMENT_RE = re.compile(
    r"(?P<key>[\"']?authorization[\"']?)(?P<separator>\s*[:=]\s*)"
    r"(?P<value>[\"'][^\"']*[\"']|[^\r\n,;&}\]]+)",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?P<key_quote>[\"']?)(?P<key>x[_-]?api[_-]?key|api[_-]?key|api[_-]?secret|"
    r"access[_-]?token|refresh[_-]?token|auth[_-]?token|private[_-]?key|"
    r"client[_-]?secret|password|passphrase|secret)(?P=key_quote)"
    r"(?P<separator>\s*[:=]\s*)(?P<value>[\"'][^\"']*[\"']|[^\s,;&}]+)",
    re.IGNORECASE,
)
_ADDRESS_ASSIGNMENT_RE = re.compile(
    r"(?P<key_quote>[\"']?)(?P<key>wallet(?:[_-]?address)?|owner[_-]?address|"
    r"observed[_-]?default[_-]?wallet|default[_-]?solana[_-]?wallet)"
    r"(?P=key_quote)(?P<separator>\s*[:=]\s*)"
    r"(?P<value>[\"'][^\"']*[\"']|[^\s,;&}]+)",
    re.IGNORECASE,
)

_SAMPLE_FLOWS = {
    "state": [
        "Resolve the exact live or experiment controller.",
        "Read controller-bound slot state and immutable strategy configuration.",
        "For live sessions, reconcile executor, Gateway, balance, and ownership evidence.",
        "Return capacity, telemetry, contradictions, and session-stop guidance without mutating.",
    ],
    "open": [
        "Validate the exact fresh scanner candidate, range option, slot, and amount.",
        "Lock controller state and wallet ownership, then run live ownership and balance preflight.",
        "Rebalance only the proven base shortfall when required.",
        "Persist an intent before creating one LP executor, then reconcile external evidence.",
        "Leave uncertain submissions for recover; never submit an uncertain request again.",
    ],
    "close": [
        "Validate the exact occupied controller slot and persisted executor identity.",
        "Persist a close intent, stop the executor with keep_position enabled, and reconcile settlement.",
        "Record returned principal and fees, then restore only returned base inventory to USDC when above dust.",
        "Clear the slot only after terminal executor and restoration evidence are confirmed.",
    ],
    "recover": [
        "Load the exact persisted pending mutation for one controller slot.",
        "Search executor, Gateway, balance, order, or transaction evidence for that exact request.",
        "Reconcile a submitted or uncertain request without blind resubmission.",
        "Submit only a persisted intent that has never been attempted, then reconcile it.",
    ],
}

_SAMPLE_OUTCOMES = {
    "state": ["healthy", "blocked", "error", "stateless_read_only"],
    "open": ["opened", "restored", "failed"],
    "close": [
        "closed",
        "recoverable",
        "blocked",
        "pending",
        "manual_blocked",
        "error",
    ],
    "recover": [
        "recovered",
        "recoverable",
        "nothing_to_recover",
        "blocked",
        "pending",
        "manual_blocked",
        "closed",
        "error",
    ],
}

_SAMPLE_EXAMPLES = {
    "state": {"controller_id": "lp_wizard.orca_1"},
    "open": {
        "controller_id": "lp_wizard.orca_1",
        "slot_id": "slot-01",
        "candidate": "<exact candidate returned by the current scan>",
        "range_option": "<exact candidate range option name>",
        "amount_quote": "5",
    },
    "close": {
        "controller_id": "lp_wizard.orca_1",
        "slot_id": "slot-01",
        "reason": "operator-requested close",
    },
    "recover": {
        "controller_id": "lp_wizard.orca_1",
        "slot_id": "slot-01",
    },
}


def _normalized_key(value: Any) -> str:
    return "".join(character for character in str(value).lower() if character.isalnum())


def _scrub_text(value: str) -> str:
    value = _CREDENTIAL_URL_RE.sub(r"\1[redacted]@", value)
    value = _AUTHORIZATION_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group('key')}{match.group('separator')}[redacted]",
        value,
    )

    def replace_assignment(match: re.Match[str]) -> str:
        raw_value = match.group("value")
        quote = raw_value[0] if raw_value[:1] in {'"', "'"} else ""
        return (
            f"{match.group('key_quote')}{match.group('key')}"
            f"{match.group('key_quote')}{match.group('separator')}"
            f"{quote}[redacted]{quote}"
        )

    value = _SECRET_ASSIGNMENT_RE.sub(replace_assignment, value)
    return _ADDRESS_ASSIGNMENT_RE.sub(replace_assignment, value)


def sanitize(value: Any) -> Any:
    value = _shared.json_value(value, redact=True)
    if isinstance(value, dict):
        return {
            str(key): (
                "[redacted]"
                if _normalized_key(key) in _REPORT_REDACTED_KEYS
                else sanitize(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return _scrub_text(value)
    return value


def compact(value: dict[str, Any]) -> str:
    return json.dumps(_shared.json_value(value), separators=(",", ":"), sort_keys=True)


def sample_payload(
    routine: str, config: Any, missing_runtime_fields: list[str]
) -> dict[str, Any]:
    controller_id = getattr(config, "controller_id", None)
    return {
        "status": "sample_only",
        "mode": "sample",
        "routine": routine,
        "controller_id": controller_id,
        "mutations_allowed": False,
        "mutations_attempted": False,
        "runtime_state_inspected": False,
        "missing_runtime_fields": missing_runtime_fields,
        "required_runtime_inputs": list(_SAMPLE_EXAMPLES[routine]),
        "example_input": _SAMPLE_EXAMPLES[routine],
        "expected_flow": _SAMPLE_FLOWS[routine],
        "possible_outcomes": _SAMPLE_OUTCOMES[routine],
        "warning": (
            "This is a sample report only. No session state, exchange API, executor, "
            "Gateway position, wallet, or transaction was inspected or changed."
        ),
    }


def _payload(output: str) -> dict[str, Any]:
    try:
        value = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        return {"status": "completed", "result": output}
    return (
        value if isinstance(value, dict) else {"status": "completed", "result": value}
    )


def _json_block(value: Any) -> str:
    rendered = json.dumps(sanitize(value), indent=2, sort_keys=True, ensure_ascii=True)
    return "```json\n" + rendered.replace("```", "\\u0060\\u0060\\u0060") + "\n```"


def _table_rows(routine: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    if routine == "scan":
        return [
            {
                "Rank": rank,
                "Pair": candidate.get("trading_pair"),
                "Pool": candidate.get("pool_address"),
                "Score": candidate.get("weighted_score"),
                "TVL USD": candidate.get("tvl_usd"),
                "24h Volume USD": candidate.get("volume_24h_usd"),
                "Range Options": ", ".join(
                    str(option.get("name"))
                    for option in candidate.get("range_options", [])
                    if isinstance(option, dict) and option.get("name")
                ),
            }
            for rank, candidate in enumerate(payload.get("ranked_candidates") or [], 1)
            if isinstance(candidate, dict)
        ]
    if routine == "state":
        rows = []
        for slot in payload.get("slots") or []:
            if not isinstance(slot, dict):
                continue
            position = slot.get("position") or {}
            pending = slot.get("pending_mutation") or {}
            telemetry = slot.get("telemetry") or {}
            rows.append(
                {
                    "Slot": slot.get("slot_id"),
                    "Pool": slot.get("pool_address"),
                    "Executor": position.get("executor_id"),
                    "Pending Step": pending.get("step"),
                    "Pending Status": pending.get("status"),
                    "Range": telemetry.get("range_state"),
                    "Net PnL": telemetry.get("net_pnl_quote"),
                }
            )
        return rows
    return []


async def _save_report(
    routine: str,
    config: Any,
    payload: dict[str, Any],
    debug: dict[str, Any],
) -> str:
    from condor.reports import ReportBuilder

    status = (
        payload.get("status") or payload.get("scan_status") or payload.get("health")
    )
    mode = payload.get("mode") or payload.get("controller_mode") or "runtime"
    builder = ReportBuilder(f"LP Wizard {routine.replace('_', ' ').title()}")
    builder.source("routine", f"lp_wizard/{routine}").tags(
        ["lp_wizard", "orca", "lp", routine, str(mode)]
    ).manual_order()
    builder.kpi("Routine", routine)
    builder.kpi("Status", str(status or "unknown"))
    builder.kpi("Mode", str(mode))
    controller_id = sanitize(payload.get("controller_id") or "not supplied")
    builder.kpi("Controller", str(controller_id))

    rows = sanitize(_table_rows(routine, payload))
    if rows:
        builder.markdown("## Result Summary")
        builder.table(rows)

    errors = payload.get("errors")
    if not errors and payload.get("error"):
        errors = [payload["error"]]
    if errors:
        builder.markdown("## Errors\n" + _json_block(errors))
    if payload.get("warning") or payload.get("warnings"):
        builder.markdown(
            "## Warnings\n"
            + _json_block(payload.get("warnings") or [payload.get("warning")])
        )

    builder.markdown("## Input\n" + _json_block(config.model_dump(mode="python")))
    builder.markdown("## Output\n" + _json_block(payload))
    builder.markdown("## Debug\n" + _json_block(debug))
    return await builder.save()


async def finish(
    routine: str,
    config: Any,
    output: str | dict[str, Any],
    *,
    started_at: str,
    started_monotonic: float,
    debug: dict[str, Any] | None = None,
    report_timeout_seconds: float | None = None,
) -> RoutineResult:
    original_text = output if isinstance(output, str) else compact(output)
    payload = _payload(original_text)
    completed_at = datetime.now(timezone.utc).isoformat()
    report_debug = {
        "routine": routine,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": round((time.monotonic() - started_monotonic) * 1000, 3),
        "report_kind": (
            "sample" if payload.get("status") == "sample_only" else "runtime"
        ),
        **(debug or {}),
    }
    report_id = None
    report_error = None
    timeout = (
        REPORT_TIMEOUT_SECONDS
        if report_timeout_seconds is None
        else report_timeout_seconds
    )
    try:
        report_id = await _shared.wait_bounded(
            _save_report(routine, config, payload, report_debug), timeout
        )
    except asyncio.TimeoutError:
        report_error = f"TimeoutError: report save exceeded {timeout} seconds"
        log.error("Timed out saving lp_wizard %s report", routine)
    except Exception as error:
        report_error = sanitize(f"{type(error).__name__}: {error}")
        log.exception("Failed to save lp_wizard %s report", routine)
    if report_id or report_error:
        result_payload = {**payload, "report_id": report_id}
        if report_error:
            result_payload["report_error"] = report_error
        text = compact(result_payload)
    else:
        text = original_text
    return RoutineResult(
        text=text,
        table_data=sanitize(_table_rows(routine, payload)) or None,
    )


async def finish_cancelled(
    routine: str,
    config: Any,
    *,
    started_at: str,
    started_monotonic: float,
    debug: dict[str, Any] | None = None,
) -> None:
    details = {
        **(debug or {}),
        "cancelled": True,
        "cancellation_advice": (
            "Inspect current state and recover any persisted pending mutation before "
            "retrying an external action."
        ),
    }
    await finish(
        routine,
        config,
        {
            "status": "cancelled",
            "controller_id": getattr(config, "controller_id", None),
            "slot_id": getattr(config, "slot_id", None),
            "recover_required": bool((debug or {}).get("mutation_claimed")),
            "error": "routine execution was cancelled",
        },
        started_at=started_at,
        started_monotonic=started_monotonic,
        debug=details,
        report_timeout_seconds=CANCELLATION_REPORT_TIMEOUT_SECONDS,
    )
