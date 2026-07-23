"""Fail-closed monitor for the pinned ANSEM/SOL Meteora DLMM pool."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from typing import Any

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Market Data"

ANSEM_SOL_POOL = "6e7V9eegCHw997T72MxgwwJipZ6GJyZF8NvjkzT1rvpN"
ANSEM_MINT = "9cRCn9rGT8V2imeM2BaKs13yhMEais3ruM3rPvTGpump"
WSOL_MINT = "So11111111111111111111111111111111111111112"
METEORA_DATA_API = "https://dlmm.datapi.meteora.ag"


class MonitoringError(RuntimeError):
    """Raised when required monitoring data is unavailable or malformed."""


class Config(BaseModel):
    """Monitor one exact Meteora pool and produce a bounded LP proposal."""

    connector: str = Field(default="meteora", description="CLMM connector name")
    network: str = Field(default="solana-mainnet-beta", description="Network ID")
    pool_address: str = Field(
        default=ANSEM_SOL_POOL,
        description="Pinned Meteora DLMM pool address",
    )
    trading_pair: str = Field(default="ANSEM-SOL", description="Display pair")
    base_mint: str = Field(default=ANSEM_MINT, description="Expected base mint")
    quote_mint: str = Field(default=WSOL_MINT, description="Expected quote mint")
    target_usd: float = Field(default=100.0, gt=0, description="Total LP budget in USD")
    range_pct: float = Field(default=5.5, gt=0, lt=100)
    max_range_pct: float = Field(default=6.5, gt=0, lt=100)
    min_range_pct: float = Field(default=4.0, gt=0, lt=100)
    max_bins: int = Field(default=68, ge=3, le=68)
    low_volatility_pct: float = Field(default=10.0, ge=0)
    high_volatility_pct: float = Field(default=20.0, gt=0)
    min_tvl_usd: float = Field(default=500_000.0, ge=0)
    reference_tvl_usd: float | None = Field(default=None, gt=0)
    max_tvl_drop_pct: float = Field(default=50.0, gt=0, le=100)
    max_price_drop_24h_pct: float = Field(default=50.0, gt=0, le=100)
    recent_rebalance_count: int = Field(default=0, ge=0)
    max_rebalances_24h: int = Field(default=5, ge=1)
    request_timeout_sec: float = Field(default=10.0, gt=0, le=30)


def _finite_float(value: Any, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise MonitoringError(f"invalid {field_name}: {value!r}") from exc
    if not math.isfinite(parsed):
        raise MonitoringError(f"invalid {field_name}: {value!r}")
    return parsed


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _first_value(data: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return None


def _position_activity(position: dict[str, Any]) -> bool | None:
    """Return active/inactive, or None when the payload is ambiguous."""
    status = str(position.get("status", "")).upper()
    if status in {"CLOSED", "COMPLETE", "TERMINATED", "FAILED"}:
        return False
    status_claims_active = status in {
        "OPEN",
        "OPENING",
        "RUNNING",
        "IN_RANGE",
        "OUT_OF_RANGE",
    }

    liquidity = _first_value(position, ("liquidity", "current_liquidity", "liq"))
    if liquidity is not None:
        parsed = _optional_float(liquidity)
        if parsed is None:
            return None
        if parsed > 0:
            return True
        return None if status_claims_active else False

    base_amount = _first_value(
        position,
        ("base_token_amount", "base_amount", "amount_base", "token_a_amount"),
    )
    quote_amount = _first_value(
        position,
        ("quote_token_amount", "quote_amount", "amount_quote", "token_b_amount"),
    )
    if base_amount is not None or quote_amount is not None:
        base = _optional_float(base_amount or 0)
        quote = _optional_float(quote_amount or 0)
        if base is None or quote is None:
            return None
        if base > 0 or quote > 0:
            return True
        return None if status_claims_active else False

    return None


def _position_in_range(position: dict[str, Any], current_price: float) -> bool | None:
    value = position.get("in_range")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.upper()
        if normalized in {"IN_RANGE", "TRUE"}:
            return True
        if normalized in {"OUT_OF_RANGE", "FALSE"}:
            return False

    lower = _optional_float(position.get("lower_price"))
    upper = _optional_float(position.get("upper_price"))
    if lower is None or upper is None or lower <= 0 or lower >= upper:
        return None
    return lower <= current_price <= upper


def _select_range_pct(config: Config, metrics: dict[str, float]) -> tuple[float, str]:
    if config.min_range_pct > config.max_range_pct:
        raise MonitoringError("min_range_pct cannot exceed max_range_pct")
    if config.low_volatility_pct >= config.high_volatility_pct:
        raise MonitoringError("low_volatility_pct must be below high_volatility_pct")

    normal = min(max(config.range_pct, config.min_range_pct), config.max_range_pct)
    observed = max(
        abs(metrics["price_change_24h_pct"]), metrics["high_low_range_24h_pct"]
    )
    if observed >= config.high_volatility_pct:
        return config.max_range_pct, "high_24h_volatility"
    if observed <= config.low_volatility_pct:
        return config.min_range_pct, "low_24h_volatility"
    return normal, "normal_24h_volatility"


def _build_bin_range(
    current_price: float, bin_step: int, requested_pct: float, max_bins: int
) -> dict[str, Any]:
    """Build a valid range whose active+side bins never exceed max_bins."""
    if current_price <= 0 or bin_step <= 0:
        raise MonitoringError("current_price and bin_step must be positive")
    if not 0 < requested_pct < 100:
        raise MonitoringError("requested range must be between 0 and 100 percent")

    multiplier = 1 + bin_step / 10_000
    log_step = math.log(multiplier)
    lower_bins_requested = math.ceil(-math.log(1 - requested_pct / 100) / log_step)
    upper_bins_requested = math.ceil(math.log(1 + requested_pct / 100) / log_step)
    requested_total = lower_bins_requested + upper_bins_requested + 1

    if requested_total > max_bins:
        side_bins = max_bins - 1
        # One extra lower bin offsets multiplicative asymmetry and keeps the
        # percentage distance below/above the current price approximately even.
        lower_bins = (side_bins + 1) // 2
        upper_bins = side_bins - lower_bins
        capped = True
    else:
        lower_bins = lower_bins_requested
        upper_bins = upper_bins_requested
        capped = False

    lower = current_price / (multiplier**lower_bins)
    upper = current_price * (multiplier**upper_bins)
    return {
        "lower": round(lower, 12),
        "upper": round(upper, 12),
        "requested_range_pct": round(requested_pct, 4),
        "effective_lower_pct": round((1 - lower / current_price) * 100, 4),
        "effective_upper_pct": round((upper / current_price - 1) * 100, 4),
        "lower_bins": lower_bins,
        "upper_bins": upper_bins,
        "total_bins": lower_bins + upper_bins + 1,
        "max_bins": max_bins,
        "capped_by_bin_limit": capped,
        "requested_total_bins": requested_total,
    }


def _ohlcv_metrics(payload: Any) -> dict[str, float]:
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise MonitoringError("Meteora OHLCV response has no data list")

    valid: list[dict[str, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            item = {
                "timestamp": _finite_float(row.get("timestamp"), "OHLCV timestamp"),
                "open": _finite_float(row.get("open"), "OHLCV open"),
                "high": _finite_float(row.get("high"), "OHLCV high"),
                "low": _finite_float(row.get("low"), "OHLCV low"),
                "close": _finite_float(row.get("close"), "OHLCV close"),
            }
        except MonitoringError:
            continue
        if min(item["open"], item["high"], item["low"], item["close"]) > 0:
            valid.append(item)

    if len(valid) < 2:
        raise MonitoringError("fewer than two valid 24h OHLCV candles")
    valid.sort(key=lambda item: item["timestamp"])
    first_open = valid[0]["open"]
    last_close = valid[-1]["close"]
    high = max(item["high"] for item in valid)
    low = min(item["low"] for item in valid)
    return {
        "candle_count": len(valid),
        "open_24h": first_open,
        "close_24h": last_close,
        "high_24h": high,
        "low_24h": low,
        "price_change_24h_pct": (last_close / first_open - 1) * 100,
        "high_low_range_24h_pct": (high / low - 1) * 100,
    }


async def _fetch_json(
    url: str, timeout_sec: float, params: dict[str, Any] | None = None
) -> Any:
    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params=params) as response:
            if response.status != 200:
                body = (await response.text())[:200]
                raise MonitoringError(
                    f"Meteora data API returned HTTP {response.status}: {body}"
                )
            return await response.json()


async def _get_official_pool_info(config: Config) -> dict[str, Any]:
    payload = await _fetch_json(
        f"{METEORA_DATA_API}/pools/{config.pool_address}",
        config.request_timeout_sec,
    )
    if not isinstance(payload, dict):
        raise MonitoringError("Meteora pool response is not an object")
    return payload


async def _get_ohlcv(config: Config) -> Any:
    end_time = int(time.time())
    return await _fetch_json(
        f"{METEORA_DATA_API}/pools/{config.pool_address}/ohlcv",
        config.request_timeout_sec,
        {
            "timeframe": "1h",
            "start_time": end_time - 25 * 60 * 60,
            "end_time": end_time,
        },
    )


async def _get_gateway_pool_info(
    client: Any, connector: str, network: str, pool_address: str
) -> dict[str, Any] | None:
    """Gateway availability is diagnostic; official Meteora data is authoritative."""
    try:
        result = await client.gateway_clmm.get_pool_info(
            connector=connector,
            network=network,
            pool_address=pool_address,
        )
        return result if isinstance(result, dict) else None
    except Exception as exc:
        logger.warning("Gateway pool lookup failed: %s", exc)
        return None


async def _get_positions(
    client: Any, connector: str, network: str, pool_address: str
) -> list[dict[str, Any]]:
    """Get positions without turning an API failure into a false empty wallet."""
    try:
        result = await client.gateway_clmm.get_positions_owned(
            connector=connector,
            network=network,
            pool_address=pool_address,
        )
    except Exception as exc:
        raise MonitoringError(f"position lookup failed: {exc}") from exc

    if isinstance(result, list):
        positions = result
    elif isinstance(result, dict):
        positions = result.get("positions", result.get("data"))
    else:
        positions = None
    if not isinstance(positions, list) or not all(
        isinstance(item, dict) for item in positions
    ):
        raise MonitoringError("position lookup returned a malformed payload")
    return positions


def _pause(config: Config, *errors: str) -> str:
    return json.dumps(
        {
            "action": "pause",
            "ready_to_create": False,
            "pool": {
                "address": config.pool_address,
                "trading_pair": config.trading_pair,
            },
            "errors": list(errors),
            "message": "Required data could not be verified; do not create or rebalance.",
        },
        ensure_ascii=False,
        indent=2,
    )


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Return deterministic LP inputs; never infer 'no position' from an error."""
    chat_id = context._chat_id if hasattr(context, "_chat_id") else None
    client = await get_client(chat_id, context=context)
    if not client:
        return _pause(config, "No server available")
    if not config.pool_address:
        return _pause(config, "pool_address is required")

    tasks = (
        _get_official_pool_info(config),
        _get_ohlcv(config),
        _get_positions(client, config.connector, config.network, config.pool_address),
        _get_gateway_pool_info(
            client, config.connector, config.network, config.pool_address
        ),
    )
    pool_result, ohlcv_result, positions_result, gateway_pool = await asyncio.gather(
        *tasks, return_exceptions=True
    )
    for result in (pool_result, ohlcv_result, positions_result, gateway_pool):
        if isinstance(result, asyncio.CancelledError):
            raise result
    fetch_errors = []
    if isinstance(pool_result, BaseException):
        fetch_errors.append(f"pool data unavailable: {pool_result}")
    if isinstance(ohlcv_result, BaseException):
        fetch_errors.append(f"24h OHLCV unavailable: {ohlcv_result}")
    if isinstance(positions_result, BaseException):
        fetch_errors.append(str(positions_result))
    if fetch_errors:
        return _pause(config, *fetch_errors)

    pool = pool_result
    positions = positions_result
    assert isinstance(pool, dict)
    assert isinstance(positions, list)

    try:
        address = str(pool.get("address", ""))
        token_x = pool.get("token_x") or {}
        token_y = pool.get("token_y") or {}
        base_mint = str(token_x.get("address") or pool.get("mint_x") or "")
        quote_mint = str(token_y.get("address") or pool.get("mint_y") or "")
        current_price = _finite_float(pool.get("current_price"), "current_price")
        pool_config = pool.get("pool_config") or {}
        bin_step = int(
            _finite_float(pool_config.get("bin_step", pool.get("bin_step")), "bin_step")
        )
        tvl_usd = _finite_float(pool.get("tvl"), "tvl")
        quote_usd = _finite_float(token_y.get("price"), "quote token USD price")
        reported_base_usd = _finite_float(token_x.get("price"), "base token USD price")
        metrics = _ohlcv_metrics(ohlcv_result)
    except (MonitoringError, TypeError, ValueError) as exc:
        return _pause(config, str(exc))

    blockers: list[str] = []
    if address != config.pool_address:
        blockers.append(
            f"pool address mismatch: expected {config.pool_address}, got {address}"
        )
    if base_mint != config.base_mint or quote_mint != config.quote_mint:
        blockers.append(
            "pool mint mismatch: "
            f"expected {config.base_mint}/{config.quote_mint}, "
            f"got {base_mint}/{quote_mint}"
        )
    if bool(pool.get("is_blacklisted")):
        blockers.append("Meteora marks this pool as blacklisted")
    if current_price <= 0 or bin_step <= 0 or quote_usd <= 0 or reported_base_usd <= 0:
        blockers.append("pool price, token prices, and bin step must be positive")
    if tvl_usd < config.min_tvl_usd:
        blockers.append(
            f"TVL ${tvl_usd:,.0f} is below minimum ${config.min_tvl_usd:,.0f}"
        )

    implied_base_usd = current_price * quote_usd
    price_consistency_pct = (
        abs(implied_base_usd / reported_base_usd - 1) * 100
        if reported_base_usd > 0
        else math.inf
    )
    if price_consistency_pct > 10:
        blockers.append(
            f"pool and token USD prices disagree by {price_consistency_pct:.1f}%"
        )

    tvl_drop_pct = None
    if config.reference_tvl_usd:
        tvl_drop_pct = (1 - tvl_usd / config.reference_tvl_usd) * 100
        if tvl_drop_pct >= config.max_tvl_drop_pct:
            blockers.append(
                f"TVL dropped {tvl_drop_pct:.1f}% from the supplied reference"
            )
    if metrics["price_change_24h_pct"] <= -config.max_price_drop_24h_pct:
        blockers.append(
            f"price dropped {abs(metrics['price_change_24h_pct']):.1f}% in 24h"
        )
    if config.recent_rebalance_count >= config.max_rebalances_24h:
        blockers.append(
            f"24h rebalance limit reached ({config.recent_rebalance_count})"
        )

    try:
        requested_pct, range_reason = _select_range_pct(config, metrics)
        suggested_range = _build_bin_range(
            current_price, bin_step, requested_pct, config.max_bins
        )
    except MonitoringError as exc:
        blockers.append(str(exc))
        suggested_range = None
        range_reason = "invalid_range_configuration"

    active_positions: list[dict[str, Any]] = []
    ambiguous_positions: list[str] = []
    any_out_of_range = False
    total_value_quote = 0.0
    total_fees_base = 0.0
    total_fees_quote = 0.0

    for index, position in enumerate(positions):
        position_id = str(
            position.get("position_address")
            or position.get("address")
            or position.get("id")
            or f"index:{index}"
        )
        activity = _position_activity(position)
        if activity is None:
            ambiguous_positions.append(position_id)
            continue
        if not activity:
            continue

        in_range = _position_in_range(position, current_price)
        if in_range is None:
            ambiguous_positions.append(position_id)
            continue
        any_out_of_range = any_out_of_range or not in_range

        lower = _optional_float(position.get("lower_price")) or 0.0
        upper = _optional_float(position.get("upper_price")) or 0.0
        base_amount = (
            _optional_float(
                _first_value(
                    position,
                    (
                        "base_token_amount",
                        "base_amount",
                        "amount_base",
                        "token_a_amount",
                    ),
                )
            )
            or 0.0
        )
        quote_amount = (
            _optional_float(
                _first_value(
                    position,
                    (
                        "quote_token_amount",
                        "quote_amount",
                        "amount_quote",
                        "token_b_amount",
                    ),
                )
            )
            or 0.0
        )
        base_fee = _optional_float(position.get("base_fee_pending")) or 0.0
        quote_fee = _optional_float(position.get("quote_fee_pending")) or 0.0
        pnl = position.get("pnl_summary") or {}
        value_quote = _optional_float(pnl.get("current_lp_value_quote"))
        if value_quote is None:
            value_quote = base_amount * current_price + quote_amount

        total_value_quote += value_quote
        total_fees_base += base_fee
        total_fees_quote += quote_fee
        active_positions.append(
            {
                "position_address": position_id,
                "executor_id": position.get("executor_id"),
                "in_range": in_range,
                "lower_price": lower,
                "upper_price": upper,
                "base_amount": base_amount,
                "quote_amount": quote_amount,
                "base_fee_pending": base_fee,
                "quote_fee_pending": quote_fee,
                "value_quote": round(value_quote, 9),
                "value_usd": round(value_quote * quote_usd, 2),
            }
        )

    if ambiguous_positions:
        blockers.append(
            "position activity/range is ambiguous for: "
            + ", ".join(ambiguous_positions)
        )
    if len(active_positions) > 1:
        blockers.append(
            f"found {len(active_positions)} active positions; strategy allows one"
        )

    target_base_amount = config.target_usd / 2 / reported_base_usd
    target_quote_amount = config.target_usd / 2 / quote_usd
    target_quote_exposure = config.target_usd / quote_usd

    if blockers:
        action = "pause"
        message = "Risk/data checks failed; do not create, close, or rebalance."
    elif not active_positions:
        action = "no_position"
        message = (
            "No active position verified; creation is eligible after balance checks."
        )
    elif any_out_of_range:
        action = "rebalance_candidate"
        message = (
            "Position is out of range; require confirmation/cooldown/cost checks "
            "before closing it."
        )
    else:
        action = "hold"
        message = "The single active position is in range."

    volume = pool.get("volume") or {}
    fees = pool.get("fees") or {}
    fee_tvl = pool.get("fee_tvl_ratio") or {}
    output = {
        "action": action,
        "ready_to_create": action == "no_position",
        "message": message,
        "blockers": blockers,
        "pool": {
            "address": address,
            "trading_pair": config.trading_pair,
            "base_mint": base_mint,
            "quote_mint": quote_mint,
            "mints_verified": base_mint == config.base_mint
            and quote_mint == config.quote_mint,
            "current_price_quote_per_base": round(current_price, 12),
            "bin_step_bps": bin_step,
            "tvl_usd": round(tvl_usd, 2),
            "volume_24h_usd": round(_optional_float(volume.get("24h")) or 0, 2),
            "fees_24h_usd": round(_optional_float(fees.get("24h")) or 0, 2),
            "fee_tvl_ratio_24h_pct": round(_optional_float(fee_tvl.get("24h")) or 0, 6),
            "gateway_available": isinstance(gateway_pool, dict),
        },
        "market": {
            **{key: round(value, 8) for key, value in metrics.items()},
            "base_usd": round(reported_base_usd, 8),
            "quote_usd": round(quote_usd, 8),
            "implied_base_usd": round(implied_base_usd, 8),
            "price_consistency_pct": round(price_consistency_pct, 4),
            "tvl_drop_from_reference_pct": (
                round(tvl_drop_pct, 4) if tvl_drop_pct is not None else None
            ),
        },
        "positions": {
            "raw_count": len(positions),
            "active_count": len(active_positions),
            "open": active_positions,
            "any_out_of_range": any_out_of_range,
            "total_value_quote": round(total_value_quote, 9),
            "total_value_usd": round(total_value_quote * quote_usd, 2),
            "total_fees_base": round(total_fees_base, 9),
            "total_fees_quote": round(total_fees_quote, 9),
            "total_fees_usd": round(
                total_fees_base * reported_base_usd + total_fees_quote * quote_usd,
                2,
            ),
        },
        "suggested_range": (
            {**suggested_range, "reason": range_reason}
            if suggested_range is not None
            else None
        ),
        "target_allocation": {
            "target_usd": config.target_usd,
            "side": 3,
            "base_amount": round(target_base_amount, 6),
            "quote_amount": round(target_quote_amount, 9),
            "quote_exposure": round(target_quote_exposure, 9),
            "requires_both_assets": True,
        },
        "risk_inputs": {
            "min_tvl_usd": config.min_tvl_usd,
            "reference_tvl_usd": config.reference_tvl_usd,
            "recent_rebalance_count": config.recent_rebalance_count,
            "max_rebalances_24h": config.max_rebalances_24h,
        },
    }
    return json.dumps(output, ensure_ascii=False, indent=2)
