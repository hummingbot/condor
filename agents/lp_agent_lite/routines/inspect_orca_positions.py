"""Reconcile one failed close from an exact wallet/position's Orca events."""

import asyncio
import json
import math
import re
import time
from typing import Any

import aiohttp
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from agents.lp_agent_lite.routines._reporting import DiagnosticTrace, report_result

CATEGORY = "Orca LP Evidence"
_BASE_URL = "https://stats-api.mainnet.orca.so"
_SUMMARY_PATH = "/api/pnl/summary"
_HISTORY_PATH = "/api/pnl/history"
_MAX_RESPONSE_BYTES = 1_000_000
_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_SIGNATURE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{64,88}$")
_ACTION = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


class Config(BaseModel):
    """Reconcile one failed close for an exact wallet/position/pool."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    wallet_address: StrictStr = Field(min_length=32, max_length=44)
    position_address: StrictStr = Field(min_length=32, max_length=44)
    expected_pool_address: StrictStr = Field(min_length=32, max_length=44)
    mutation_started_at: StrictInt = Field(gt=0)
    indexing_lag_seconds: StrictInt = Field(default=90, ge=30, le=600)
    timestamp_tolerance_seconds: StrictInt = Field(default=5, ge=0, le=30)
    history_limit: StrictInt = Field(default=20, ge=2, le=100)
    timeout_seconds: StrictInt = Field(default=12, ge=1, le=30)

    @model_validator(mode="after")
    def validate_identity_and_expectation(self):
        for value in (
            self.wallet_address,
            self.position_address,
            self.expected_pool_address,
        ):
            if not _ADDRESS.fullmatch(value):
                raise ValueError("wallet, position, and pool must be Solana addresses")
        return self


def _dump(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    if len(raw) >= 1_900:
        return json.dumps(
            {
                "schema": "lp_agent_lite.orca_position_index.v1",
                "status": "unavailable",
                "reason": "transport_limit",
                "close_outcome": "unavailable",
                "mutation": False,
            },
            separators=(",", ":"),
        )
    return raw


async def _get_json(path: str, params: dict[str, str], timeout_seconds: int) -> Any:
    if path not in {_SUMMARY_PATH, _HISTORY_PATH}:
        raise ValueError("untrusted_path")
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    headers = {
        "Accept": "application/json",
        "User-Agent": "Condor-lp-agent-lite/1",
    }
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(
            f"{_BASE_URL}{path}", params=params, allow_redirects=False
        ) as response:
            if response.status != 200:
                raise ValueError(f"http_{response.status}")
            raw = await response.content.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("response_too_large")
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_json") from exc


def _finite(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"invalid_{field}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid_{field}") from exc
    if not math.isfinite(number):
        raise ValueError(f"invalid_{field}")
    return number


def _usd(row: dict[str, Any], field: str) -> float | None:
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"invalid_{field}")
    return _finite(value.get("usd_value"), f"{field}_usd")


def _normalize_row(
    raw: Any, *, summary: bool, wallet: str, position: str
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("row_not_object")
    position_address = raw.get("position_address")
    pool = raw.get("whirlpool_address")
    action = raw.get("last_action_type" if summary else "action_type")
    timestamp = raw.get("last_action_timestamp" if summary else "timestamp")
    if position_address != position or not _ADDRESS.fullmatch(str(position_address)):
        raise ValueError("position_mismatch")
    if not isinstance(pool, str) or not _ADDRESS.fullmatch(pool):
        raise ValueError("invalid_pool")
    if not isinstance(action, str) or not _ACTION.fullmatch(action):
        raise ValueError("invalid_action")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0:
        raise ValueError("invalid_timestamp")
    if not summary and raw.get("user_wallet_address") != wallet:
        raise ValueError("wallet_mismatch")
    signature = raw.get("transaction_signature")
    if summary:
        signature = None
    elif not isinstance(signature, str) or not _SIGNATURE.fullmatch(signature):
        raise ValueError("invalid_signature")
    reliable = raw.get("pnl_reliable")
    if not isinstance(reliable, bool):
        raise ValueError("invalid_pnl_reliable")
    return {
        "action": action,
        "timestamp": timestamp,
        "position": position_address,
        "pool": pool,
        "signature": signature,
        "pnl_reliable": reliable,
        "cost_usd": _usd(raw, "cost_basis"),
        "realized_usd": _usd(raw, "realized_pnl"),
        "fees_usd": _usd(raw, "total_fees_collected"),
    }


def _rows(payload: Any, *, summary: bool, config: Config):
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("invalid_envelope")
    meta = payload.get("meta")
    cursor = meta.get("cursor") if isinstance(meta, dict) else None
    if not isinstance(cursor, dict) or set(cursor) != {"previous", "next"}:
        raise ValueError("invalid_cursor")
    rows = [
        _normalize_row(
            row,
            summary=summary,
            wallet=config.wallet_address,
            position=config.position_address,
        )
        for row in payload["data"]
    ]
    if summary and len(rows) > 1:
        raise ValueError("ambiguous_summary")
    if not summary and any(
        rows[index]["timestamp"] < rows[index + 1]["timestamp"]
        for index in range(len(rows) - 1)
    ):
        raise ValueError("history_not_newest_first")
    return rows, cursor["next"] is not None


def _error_kind(error: BaseException) -> str:
    if isinstance(error, asyncio.TimeoutError):
        return "timeout"
    if isinstance(error, aiohttp.ClientError):
        return "transport"
    text = str(error)
    return text if re.fullmatch(r"[a-z0-9_]+", text) else "invalid_response"


async def run(config: Config, context: Any) -> str:
    """Return compact indexed lifecycle evidence, never on-chain absence proof."""
    trace = DiagnosticTrace()
    trace.record(
        "requests_planned",
        sources=["summary", "history"],
        expected_action="close_position",
    )
    params = {
        "wallet": config.wallet_address,
        "position": config.position_address,
    }
    results = await asyncio.gather(
        _get_json(_SUMMARY_PATH, params, config.timeout_seconds),
        _get_json(
            _HISTORY_PATH,
            {**params, "limit": str(config.history_limit)},
            config.timeout_seconds,
        ),
        return_exceptions=True,
    )
    trace.record(
        "orca_index_fetched",
        summary="error" if isinstance(results[0], BaseException) else "received",
        history="error" if isinstance(results[1], BaseException) else "received",
    )

    parsed: dict[str, tuple[list[dict[str, Any]], bool]] = {}
    errors: dict[str, str] = {}
    for name, result, summary in zip(
        ("summary", "history"), results, (True, False), strict=True
    ):
        try:
            if isinstance(result, BaseException):
                raise result
            parsed[name] = _rows(result, summary=summary, config=config)
        except (asyncio.TimeoutError, aiohttp.ClientError, ValueError) as exc:
            errors[name] = _error_kind(exc)

    summary_row = parsed.get("summary", ([], False))[0]
    history_rows = parsed.get("history", ([], False))[0]
    latest_summary = summary_row[0] if summary_row else None
    latest_history = history_rows[0] if history_rows else None

    consensus = False
    event = latest_summary or latest_history
    if "summary" in parsed and "history" in parsed:
        if latest_summary is None and latest_history is None:
            consensus = True
            event = None
        elif latest_summary is not None and latest_history is not None:
            consensus = all(
                latest_summary[key] == latest_history[key]
                for key in ("action", "timestamp", "position", "pool")
            )
            event = latest_history if consensus else None

    pool_matches = event is None or event["pool"] == config.expected_pool_address
    if not pool_matches:
        consensus = False
        event = None

    if event is None:
        indexed_state = "not_indexed" if consensus else "uncertain"
    elif event["action"] == "close_position":
        indexed_state = "closed"
    else:
        indexed_state = "active"

    now = int(time.time())
    window_end = config.mutation_started_at + config.indexing_lag_seconds
    wait_remaining = max(0, window_end - now)
    window_elapsed = wait_remaining == 0
    caught_up = bool(
        consensus
        and event is not None
        and event["action"] == "close_position"
        and event["timestamp"]
        >= config.mutation_started_at - config.timestamp_tolerance_seconds
    )

    if not parsed:
        close_outcome = "unavailable"
        status = "unavailable"
    elif len(parsed) == 2 and consensus and pool_matches and caught_up:
        close_outcome = "closed"
        status = "complete"
    elif not window_elapsed:
        close_outcome = "pending_index"
        status = "degraded"
    elif (
        len(parsed) == 2
        and consensus
        and pool_matches
        and event is not None
        and indexed_state == "active"
    ):
        close_outcome = "still_active"
        status = "complete"
    else:
        close_outcome = "uncertain"
        status = "degraded"

    payload: dict[str, Any] = {
        "schema": "lp_agent_lite.orca_position_index.v1",
        "status": status,
        "source": "orca_stats_api",
        "consistency": "eventual",
        "wallet": config.wallet_address,
        "position": config.position_address,
        "indexed_state": indexed_state,
        "close_outcome": close_outcome,
        "consensus": consensus,
        "api": {
            "summary": "ok" if "summary" in parsed else "unavailable",
            "history": "ok" if "history" in parsed else "unavailable",
            "history_more": parsed.get("history", ([], False))[1],
        },
        "lag": {
            "assumed_seconds": config.indexing_lag_seconds,
            "window_elapsed": window_elapsed,
            "wait_remaining_seconds": wait_remaining,
            "caught_up": caught_up,
        },
        "mutation": False,
    }
    if event is not None:
        payload["event_cols"] = [
            "action",
            "timestamp",
            "signature",
            "pool",
            "pnl_reliable",
            "cost_usd",
            "realized_usd",
            "fees_usd",
        ]
        payload["event"] = [
            event[key]
            for key in (
                "action",
                "timestamp",
                "signature",
                "pool",
                "pnl_reliable",
                "cost_usd",
                "realized_usd",
                "fees_usd",
            )
        ]
    payload["expected"] = [
        "close_position",
        config.mutation_started_at,
        config.expected_pool_address,
    ]
    if errors:
        payload["errors"] = errors
    trace.record(
        "indexed_events_reconciled",
        status=status,
        indexed_state=indexed_state,
        close_outcome=close_outcome,
        consensus=consensus,
        caught_up=caught_up,
        errors=errors,
    )
    return await report_result(
        _dump(payload),
        routine_name="inspect_orca_positions",
        title="LP Agent Lite — Orca Position Evidence Diagnostic",
        config=config,
        trace=trace,
        context=context,
    )
