import asyncio
import json
import socket
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from agents.lp_wizard.routines import _pool_policy, _reporting, _shared

CATEGORY = "LP Wizard"
ORCA_POOL_URL = "https://api.orca.so/v2/solana/pools"
REQUEST_TIMEOUT_SECONDS = 15
USER_AGENT = "Mozilla/5.0 CondorLPWizard/3.1"
QUALIFICATION = (
    "This ranks only the union of the first 100 Orca API results for every "
    "profile category and each declared discovery lens. Orca categories are "
    "source classifications, not independent token-security verification; "
    "scanner eligibility is not an execution quote or a guarantee of LP returns."
)


class Config(BaseModel):
    """Scan the controller-bound Orca discovery universe without selecting or mutating."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    controller_id: StrictStr = "lp_wizard.orca_e1"
    risk_profile: Literal[
        "yield_focused",
        "yield_high_risk",
        "yield_extreme_risk",
        "yield_no_limit",
    ] = "yield_focused"
    limit: StrictInt = Field(default=5, ge=1, le=20)

    @field_validator("controller_id")
    @classmethod
    def _controller_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("controller_id must not be empty")
        return _shared.validate_controller_id(value)


def _validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "api.orca.so"
        or parsed.path != "/v2/solana/pools"
        or parsed.fragment
    ):
        raise ValueError("Orca request must use the trusted HTTPS pools endpoint")


class _TrustedRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _retry_delay(error: HTTPError) -> float:
    value = error.headers.get("Retry-After") if error.headers else None
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(
                    0.0,
                    (retry_at - datetime.now(timezone.utc)).total_seconds(),
                )
            except (TypeError, ValueError, OverflowError):
                pass
    return 0.5


class _RequestFailure(Exception):
    def __init__(self, message: str, diagnostic: dict[str, Any]):
        super().__init__(message)
        self.diagnostic = diagnostic


def _fetch_json(
    name: str, category: str, lens: str, url: str
) -> tuple[list[Any], dict[str, Any]]:
    _validate_url(url)
    request = Request(
        url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
    )
    opener = build_opener(_TrustedRedirectHandler())
    diagnostic: dict[str, Any] = {
        "name": name,
        "category": category,
        "discovery_lens": lens,
        "attempts": [],
    }
    for attempt in range(1, 3):
        try:
            with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                final_url = response.geturl()
                _validate_url(final_url)
                status = response.getcode()
                value = json.loads(response.read().decode("utf-8"))
            if not isinstance(value, dict) or not isinstance(value.get("data"), list):
                raise ValueError("response missing top-level data list")
            rows = value["data"]
            diagnostic["attempts"].append({"attempt": attempt, "status": status})
            diagnostic.update({"status": "success", "record_count": len(rows)})
            return rows, diagnostic
        except HTTPError as error:
            retryable = error.code == 429 or 500 <= error.code <= 599
            entry: dict[str, Any] = {"attempt": attempt, "http_status": error.code}
            if attempt == 1 and retryable:
                delay = _retry_delay(error)
                entry["retry_after_seconds"] = delay
                diagnostic["attempts"].append(entry)
                time.sleep(delay)
                continue
            diagnostic["attempts"].append(entry)
            diagnostic["status"] = "failed"
            raise _RequestFailure(f"{name}: HTTP {error.code}", diagnostic) from error
        except (TimeoutError, socket.timeout, URLError) as error:
            entry = {"attempt": attempt, "error": type(error).__name__}
            if attempt == 1:
                entry["retry_after_seconds"] = 0.5
                diagnostic["attempts"].append(entry)
                time.sleep(0.5)
                continue
            diagnostic["attempts"].append(entry)
            diagnostic["status"] = "failed"
            raise _RequestFailure(
                f"{name}: {type(error).__name__}: {error}", diagnostic
            ) from error
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            diagnostic["attempts"].append(
                {"attempt": attempt, "error": type(error).__name__}
            )
            diagnostic["status"] = "failed"
            raise _RequestFailure(
                f"{name}: invalid response: {error}", diagnostic
            ) from error
    raise RuntimeError("unreachable request retry state")


def _request_specs(profile: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    specs = []
    for category in profile["categories"]:
        for lens in _pool_policy.DISCOVERY_LENSES:
            query = urlencode(
                {
                    "categories": category,
                    "sortBy": lens,
                    "sortDirection": "desc",
                    "size": 100,
                    "stats": ",".join(_pool_policy.LIVE_STATS),
                }
            )
            url = f"{ORCA_POOL_URL}?{query}"
            _validate_url(url)
            specs.append((f"{category}:{lens}", category, lens, url))
    return specs


async def _fetch_all(
    specs: list[tuple[str, str, str, str]],
) -> tuple[list[tuple[str, str, list[Any]]], dict[str, Any], list[str]]:
    results = await asyncio.gather(
        *(asyncio.to_thread(_fetch_json, *spec) for spec in specs),
        return_exceptions=True,
    )
    fetched: list[tuple[str, str, list[Any]]] = []
    diagnostics: list[dict[str, Any]] = []
    errors: list[str] = []
    for (_, category, lens, _), result in zip(specs, results, strict=True):
        if isinstance(result, _RequestFailure):
            diagnostics.append(result.diagnostic)
            errors.append(str(result))
        elif isinstance(result, BaseException):
            diagnostics.append(
                {
                    "name": f"{category}:{lens}",
                    "category": category,
                    "discovery_lens": lens,
                    "status": "failed",
                    "attempts": [],
                }
            )
            errors.append(f"{category}:{lens}: {type(result).__name__}: {result}")
        else:
            rows, diagnostic = result
            diagnostics.append(diagnostic)
            fetched.append((category, lens, rows))
    return (
        fetched,
        {
            "required_count": len(specs),
            "successful_count": len(fetched),
            "failed_count": len(errors),
            "requests": diagnostics,
        },
        errors,
    )


def _resolve_policy(
    config: Config,
) -> tuple[_shared.Session, _shared.Config, str, str]:
    session = _shared.resolve_session(config.controller_id)
    strategy_config = _shared.load_config(session)
    return session, strategy_config, config.risk_profile, "routine_parameter"


def _compact(value: dict[str, Any]) -> str:
    return json.dumps(_shared.json_value(value), separators=(",", ":"), sort_keys=True)


async def _execute(config: Config, context: Any, debug: dict[str, Any]) -> str:
    del context
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        debug["stage"] = "resolve_policy"
        session, strategy_config, risk_profile, profile_source = _resolve_policy(config)
        profile = _pool_policy.get_profile(risk_profile)
        specs = _request_specs(profile)
        debug["stage"] = "fetch_orca_pools"
        fetched, request_diagnostics, request_errors = await _fetch_all(specs)
        scope = {
            "endpoint": ORCA_POOL_URL,
            "categories": profile["categories"],
            "discovery_lenses": list(_pool_policy.DISCOVERY_LENSES),
            "results_per_request": 100,
            "stats": list(_pool_policy.LIVE_STATS),
            "sort_direction": "desc",
            "scope_statement": (
                "Union of the first 100 descending Orca results for every listed "
                "category and discovery lens; not all Orca pools."
            ),
        }
        base = {
            "controller_id": session.controller_id,
            "controller_mode": "live" if session.live else "experiment",
            "observed_at": started_at,
            "risk_profile": risk_profile,
            "risk_profile_source": profile_source,
            "total_amount_quote": float(strategy_config.total_amount_quote),
            "total_amount_quote_source": (
                "immutable_session_config" if session.live else "strategy_root_config"
            ),
            "fetched_scope": scope,
            "qualification": QUALIFICATION,
            "request_diagnostics": request_diagnostics,
        }
        if request_errors:
            return _compact(
                {
                    **base,
                    "scan_status": "incomplete_fetch",
                    "eligible_count": 0,
                    "ranked_candidates": [],
                    "rejection_reasons": {
                        "normalization": {},
                        "eligibility": {},
                        "all": {},
                    },
                    "errors": request_errors,
                }
            )

        debug["stage"] = "normalize_and_rank"
        normalization_rejections: Counter[str] = Counter()
        normalized = []
        raw_count = 0
        for category, lens, rows in fetched:
            raw_count += len(rows)
            for index, row in enumerate(rows):
                pool, rejection = _pool_policy.normalize_record(
                    row, category, lens, index
                )
                if rejection:
                    normalization_rejections[rejection] += 1
                else:
                    normalized.append(pool)
        deduplicated = _pool_policy.deduplicate_records(normalized)
        ranked, eligibility_rejections = _pool_policy.rank_pools(
            deduplicated,
            risk_profile,
            started_at,
            float(strategy_config.total_amount_quote),
        )
        all_rejections = Counter(normalization_rejections)
        all_rejections.update(eligibility_rejections)
        request_diagnostics.update(
            {
                "raw_record_count": raw_count,
                "normalized_record_count": len(normalized),
                "deduplicated_pool_count": len(deduplicated),
            }
        )
        return _compact(
            {
                **base,
                "scan_status": "success",
                "eligible_count": len(ranked),
                "returned_count": min(config.limit, len(ranked)),
                "ranked_candidates": ranked[: config.limit],
                "rejection_reasons": {
                    "normalization": dict(sorted(normalization_rejections.items())),
                    "eligibility": eligibility_rejections,
                    "all": dict(sorted(all_rejections.items())),
                },
                "errors": [],
            }
        )
    except Exception as error:
        debug.update(
            {
                "exception_type": type(error).__name__,
                "exception": str(error),
                "traceback": traceback.format_exc(),
            }
        )
        return _compact(
            {
                "scan_status": "failed",
                "controller_id": config.controller_id,
                "observed_at": started_at,
                "eligible_count": 0,
                "ranked_candidates": [],
                "rejection_reasons": {
                    "normalization": {},
                    "eligibility": {},
                    "all": {},
                },
                "errors": [f"{type(error).__name__}: {error}"],
                "qualification": QUALIFICATION,
            }
        )


async def run(config: Config, context: Any):
    started_at = datetime.now(timezone.utc).isoformat()
    started_monotonic = time.monotonic()
    debug: dict[str, Any] = {}
    try:
        output = await _execute(config, context, debug)
    except asyncio.CancelledError:
        await _reporting.finish_cancelled(
            "scan",
            config,
            started_at=started_at,
            started_monotonic=started_monotonic,
            debug=debug,
        )
        raise
    return await _reporting.finish(
        "scan",
        config,
        output,
        started_at=started_at,
        started_monotonic=started_monotonic,
        debug=debug,
    )
