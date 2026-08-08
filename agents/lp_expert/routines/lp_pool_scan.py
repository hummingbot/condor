"""One read-only Orca pool scan with a fixed model transport capacity."""

from __future__ import annotations

import asyncio
import json
import re
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from agents.lp_expert.core import orca
from agents.lp_expert.core.reporting import TraceRecorder, attach_report, safe_error
from agents.lp_expert.core.runtime import (
    get_hummingbot_client,
    integer_config,
    pool_tvl_policy,
    resolve_runtime,
)

CATEGORY = "Orca LP Candidate Evidence"
VERSION = "3"
_CONTROLLER = re.compile(r"^lp_expert\.orca_(?:e)?[1-9]\d*$")
_TRANSPORT_MAX_CHARS = 1_900
# Four representative full Solana identities serialize below 1,900 characters;
# five exceed it. The regression fixture locks this transport-only ceiling.
RETURNED_CANDIDATE_LIMIT = 4


def _transport_number(value: Any) -> int | float:
    number = Decimal(str(value))
    return int(number) if number == number.to_integral_value() else float(number)


class Config(BaseModel):
    """Scan the configured Orca universe for the exact current engine tick."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    controller_id: StrictStr = Field(min_length=1, max_length=128)
    tick: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def identity(self) -> "Config":
        if not _CONTROLLER.fullmatch(self.controller_id):
            raise ValueError("controller_id must be an lp_expert.orca controller")
        return self


# Agent-local routines are loaded from file without module registration.
Config.model_rebuild(
    _types_namespace={
        "StrictStr": StrictStr,
        "StrictInt": StrictInt,
    }
)


def _compact_result(payload: dict[str, Any]) -> dict[str, Any]:
    scan = payload.get("candidate_scan")
    coverage = scan.get("source_coverage") if isinstance(scan, dict) else None
    universe = scan.get("universe") if isinstance(scan, dict) else None
    candidates = payload.get("candidates", [])
    tvl_policy = payload.get("tvl_policy")
    returned = candidates[:RETURNED_CANDIDATE_LIMIT]
    result = {
        "schema": "lp_pool_scan.v1",
        "status": payload.get("status"),
        "controller_id": payload.get("controller_id"),
        "tick": payload.get("tick"),
        "coverage": (
            [
                coverage.get("completed_requests"),
                coverage.get("required_requests"),
            ]
            if isinstance(coverage, dict)
            else None
        ),
        "scanned_candidates": (
            universe.get("returned_candidates") if isinstance(universe, dict) else 0
        ),
        "eligible_candidates": len(candidates),
        "returned_candidates": len(returned),
        "omitted_candidates": len(candidates) - len(returned),
        "transport_limited": len(candidates) > len(returned),
        "deployable": payload.get("status") == "complete" and bool(returned),
        "tvl_policy": (
            [
                tvl_policy["default_risk_posture"],
                _transport_number(tvl_policy["minimum_tvl_usd"]),
                _transport_number(tvl_policy["profile_target_tvl_usd"]),
            ]
            if isinstance(tvl_policy, dict)
            else None
        ),
        "candidates": [orca.compact_candidate(item) for item in returned],
        "report_id": payload.get("report_id"),
        "report_error": payload.get("report_error"),
    }
    if payload.get("reason"):
        result["reason"] = safe_error(payload["reason"])
    return result


def _model_result(payload: dict[str, Any]) -> str:
    result = _compact_result(payload)
    encoded = json.dumps(result, default=str, separators=(",", ":"))
    if len(encoded) <= _TRANSPORT_MAX_CHARS:
        return encoded
    fallback = {
        "schema": "lp_pool_scan.v1",
        "status": "incomplete",
        "controller_id": payload.get("controller_id"),
        "tick": payload.get("tick"),
        "coverage": result["coverage"],
        "scanned_candidates": result["scanned_candidates"],
        "eligible_candidates": result["eligible_candidates"],
        "returned_candidates": 0,
        "omitted_candidates": result["eligible_candidates"],
        "transport_limited": True,
        "deployable": False,
        "tvl_policy": result["tvl_policy"],
        "candidates": [],
        "reason": (
            "pool scan exceeded the validated model transport shape; "
            "HOLD and review the complete report"
        ),
        "report_id": payload.get("report_id"),
        "report_error": (
            safe_error(payload["report_error"], limit=200)
            if payload.get("report_error")
            else None
        ),
    }
    return json.dumps(fallback, default=str, separators=(",", ":"))


async def run(config: Config, context: Any) -> str:
    trace = TraceRecorder()
    scope = None
    payload: dict[str, Any]
    try:
        with trace.stage("runtime_authority") as facts:
            scope = resolve_runtime(config.controller_id)
            if config.tick != scope.current_tick:
                raise ValueError("requested tick is not the current engine tick")
            candidate_limit = integer_config(scope.config, "candidate_scan_limit")
            tvl_policy = pool_tvl_policy(scope.config)
            facts.update(
                {
                    "controller_id": scope.controller_id,
                    "execution_mode": scope.execution_mode,
                    "tick": scope.current_tick,
                    "candidate_scan_limit": candidate_limit,
                    "returned_candidate_limit": RETURNED_CANDIDATE_LIMIT,
                    "minimum_tvl_usd": tvl_policy["minimum_tvl_usd"],
                    "default_risk_posture": tvl_policy["default_risk_posture"],
                    "profile_target_tvl_usd": tvl_policy["profile_target_tvl_usd"],
                }
            )
        with trace.stage("orca_candidate_scan") as facts:
            scan = await orca.scan_pools(
                candidate_limit,
                tvl_policy["minimum_tvl_usd"],
            )
            facts.update(scan["source_coverage"])
        registration_required: list[dict[str, Any]] = []
        token_conflicts: list[dict[str, Any]] = []
        accepted: list[dict[str, Any]] = []
        with trace.stage("gateway_token_classification") as facts:
            if scan["status"] != "complete":
                facts.update(
                    {"_outcome": "incomplete", "reason": "Orca coverage incomplete"}
                )
            else:
                client = await get_hummingbot_client(scope)
                registry = await client.gateway.get_network_tokens(scope.network)
                registered, registry_observations = orca.filter_registered_tokens(
                    scan["candidates"], registry
                )
                registered_pools = {
                    candidate["pool_address"] for candidate in registered
                }
                observations = {
                    observation["pool_address"]: observation
                    for observation in registry_observations
                }
                for candidate in scan["candidates"]:
                    tvl = Decimal(str(candidate["tvl_usd"]))
                    candidate = {
                        **candidate,
                        "tvl_floor_multiple": round(
                            float(tvl / tvl_policy["minimum_tvl_usd"]),
                            6,
                        ),
                        "meets_profile_target": (
                            tvl >= tvl_policy["profile_target_tvl_usd"]
                        ),
                    }
                    pool_address = candidate["pool_address"]
                    observation = observations.get(pool_address)
                    if (
                        observation is not None
                        and observation["reason"]
                        == "registered_token_identity_conflict"
                    ):
                        token_conflicts.append(observation)
                        continue
                    gateway_registered = pool_address in registered_pools
                    accepted.append(
                        {
                            **candidate,
                            "gateway_registered": gateway_registered,
                        }
                    )
                    if not gateway_registered:
                        registration_required.append(
                            {
                                "pool_address": pool_address,
                                "base_mint": candidate["token_a"]["mint"],
                                "base_symbol": candidate["token_a"]["symbol"],
                                "base_decimals": candidate["token_a"]["decimals"],
                            }
                        )
                facts.update(
                    {
                        "eligible": len(accepted),
                        "registered": len(registered),
                        "registration_required": len(registration_required),
                        "conflicts": len(token_conflicts),
                    }
                )
        payload = {
            "status": scan["status"],
            "controller_id": scope.controller_id,
            "tick": scope.current_tick,
            "observed_at": trace.finished_at.isoformat(),
            "mutation": False,
            "retry_allowed": False,
            "candidate_scan": scan,
            "tvl_policy": tvl_policy,
            "token_registration_required": registration_required,
            "token_conflicts": token_conflicts,
            "candidates": accepted,
            "deployable": scan["status"] == "complete" and bool(accepted),
        }
    except asyncio.CancelledError:
        payload = {
            "status": "cancelled",
            "controller_id": config.controller_id,
            "tick": config.tick,
            "observed_at": trace.finished_at.isoformat(),
            "reason": "CancelledError: pool scan invocation was cancelled",
            "mutation": False,
            "retry_allowed": False,
            "candidate_scan": None,
            "candidates": [],
            "deployable": False,
        }
    except Exception as exc:
        payload = {
            "status": "rejected",
            "controller_id": config.controller_id,
            "tick": config.tick,
            "observed_at": trace.finished_at.isoformat(),
            "reason": f"{type(exc).__name__}: {exc}",
            "mutation": False,
            "retry_allowed": False,
            "candidate_scan": None,
            "candidates": [],
            "deployable": False,
        }
    reported_payload = await attach_report(
        payload,
        title="Orca LP Pool Scan",
        source="lp_pool_scan",
        version=VERSION,
        routine_input=config.model_dump(mode="json"),
        trace=trace,
        scope=scope,
        links={},
    )
    return _model_result(reported_payload)
