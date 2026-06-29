"""Score Orca and Meteora LP pools for analysis-only pool selection."""

import logging
from math import log10
from typing import Any
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class PoolInput(BaseModel):
    protocol: str = Field(..., description="Pool protocol, usually orca or meteora")
    name: str | None = None
    pair: str | None = None
    address: str | None = None
    fee_24h_usd: float | None = None
    volume_24h_usd: float | None = None
    fee_rate: float | None = None
    liquidity_usd: float | None = None
    tvl_usd: float | None = None
    trend_pct_24h: float | None = None
    volatility_pct_24h: float | None = None
    trend_label: str | None = None
    warnings: list[str] = Field(default_factory=list)
    api_warning: str | None = None
    data_age_minutes: float | None = None

    @field_validator("warnings", mode="before")
    @classmethod
    def default_warnings(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [value]
        return value


class Config(BaseModel):
    pools: list[PoolInput] = Field(default_factory=list, description="Candidate pools to score")
    min_liquidity_usd: float = Field(default=100_000)
    preferred_liquidity_usd: float = Field(default=1_000_000)
    target_fee_apr: float = Field(default=0.30)
    max_downtrend_pct_24h: float = Field(default=-3.0)
    max_healthy_volatility_pct_24h: float = Field(default=8.0)
    stale_after_minutes: float = Field(default=60.0)
    max_best: int = Field(default=5)
    max_watchlist: int = Field(default=5)
    fee_weight: float = Field(default=0.35)
    liquidity_weight: float = Field(default=0.25)
    trend_weight: float = Field(default=0.25)
    warning_weight: float = Field(default=0.15)
    report_title: str = Field(default="LP Pool Watcher")

    @field_validator("pools", mode="before")
    @classmethod
    def default_pools(cls, value: Any) -> list[Any]:
        if value is None or value == "":
            return []
        return value

    @field_validator("report_title", mode="before")
    @classmethod
    def default_report_title(cls, value: Any) -> str:
        return "LP Pool Watcher" if value is None or value == "" else value


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def liquidity_of(pool: PoolInput) -> float | None:
    return pool.liquidity_usd if pool.liquidity_usd is not None else pool.tvl_usd


def fee_24h_of(pool: PoolInput) -> tuple[float | None, str]:
    if pool.fee_24h_usd is not None:
        return pool.fee_24h_usd, "reported_24h_fee"
    if pool.volume_24h_usd is not None and pool.fee_rate is not None:
        return pool.volume_24h_usd * pool.fee_rate, "volume_x_fee_rate_proxy"
    return None, "missing_fee"


def score_fee(pool: PoolInput, config: Config) -> tuple[float, str, float | None]:
    fee_24h, source = fee_24h_of(pool)
    liq = liquidity_of(pool)
    if fee_24h is None or liq is None or liq <= 0:
        return 0.0, source, None
    fee_apr = (fee_24h / liq) * 365.0
    return clamp((fee_apr / max(config.target_fee_apr, 0.0001)) * 100.0), source, fee_apr


def score_liquidity(pool: PoolInput, config: Config) -> float:
    liq = liquidity_of(pool)
    if liq is None or liq <= 0:
        return 0.0
    if liq >= config.preferred_liquidity_usd:
        return 100.0
    if liq < config.min_liquidity_usd:
        return clamp((liq / config.min_liquidity_usd) * 50.0)
    span = max(log10(config.preferred_liquidity_usd) - log10(config.min_liquidity_usd), 0.0001)
    return clamp(50.0 + ((log10(liq) - log10(config.min_liquidity_usd)) / span) * 50.0)


def score_trend(pool: PoolInput, config: Config) -> tuple[float, str]:
    label = (pool.trend_label or "").lower().replace(" ", "_")
    trend = pool.trend_pct_24h
    volatility = pool.volatility_pct_24h
    if label in {"sideways", "slight_up", "slight_uptrend"}:
        base = 95.0
    elif label in {"uptrend", "trending_up"}:
        base = 80.0
    elif label in {"downtrend", "trending_down"}:
        base = 25.0
    elif label == "volatile":
        base = 35.0
    elif trend is None:
        base = 45.0
    elif -1.0 <= trend <= 3.0:
        base = 100.0
    elif 3.0 < trend <= 8.0:
        base = 80.0
    elif config.max_downtrend_pct_24h <= trend < -1.0:
        base = 55.0
    elif trend < config.max_downtrend_pct_24h:
        base = 10.0
    else:
        base = 45.0
    if volatility is not None and volatility > config.max_healthy_volatility_pct_24h:
        base -= min(40.0, (volatility - config.max_healthy_volatility_pct_24h) * 4.0)
    return clamp(base), label or ("missing_trend" if trend is None else f"trend_{trend:.2f}%")


def warning_list(pool: PoolInput) -> list[str]:
    warnings = list(pool.warnings or [])
    if pool.api_warning:
        warnings.append(pool.api_warning)
    return [str(w) for w in warnings if str(w).strip()]


def exclusion_reasons(pool: PoolInput, config: Config) -> list[str]:
    reasons: list[str] = []
    liq = liquidity_of(pool)
    if liq is None:
        reasons.append("missing_liquidity")
    elif liq < config.min_liquidity_usd:
        reasons.append("low_liquidity")
    if warning_list(pool):
        reasons.append("api_warning")
    if pool.trend_pct_24h is not None and pool.trend_pct_24h < config.max_downtrend_pct_24h:
        reasons.append("strong_downtrend")
    if pool.data_age_minutes is not None and pool.data_age_minutes > config.stale_after_minutes:
        reasons.append("stale_data")
    if pool.fee_24h_usd is None and not (pool.volume_24h_usd is not None and pool.fee_rate is not None):
        reasons.append("missing_fee")
    return reasons


def pool_id(pool: PoolInput) -> str:
    return pool.name or pool.pair or pool.address or "unknown_pool"


def money(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:.2f}"


def pct(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:+.{digits}f}%"


def apr(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def score_pools(config: Config) -> dict[str, Any]:
    weights_total = config.fee_weight + config.liquidity_weight + config.trend_weight + config.warning_weight
    if weights_total <= 0:
        raise ValueError("At least one scoring weight must be positive")
    scored: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    data_gaps: list[str] = []
    for pool in config.pools:
        protocol = pool.protocol.lower().strip()
        if protocol not in {"orca", "meteora"}:
            excluded.append({"pool": pool_id(pool), "protocol": pool.protocol, "reasons": ["unsupported_protocol"]})
            continue
        fee_score, fee_source, fee_apr = score_fee(pool, config)
        liquidity_score = score_liquidity(pool, config)
        trend_score, trend_reason = score_trend(pool, config)
        warnings = warning_list(pool)
        warning_score = 0.0 if warnings else 100.0
        reasons = exclusion_reasons(pool, config)
        total = (fee_score * config.fee_weight + liquidity_score * config.liquidity_weight + trend_score * config.trend_weight + warning_score * config.warning_weight) / weights_total
        if pool.data_age_minutes is None:
            data_gaps.append(f"{pool_id(pool)}: missing data_age_minutes")
        if pool.trend_pct_24h is None and not pool.trend_label:
            data_gaps.append(f"{pool_id(pool)}: missing trend signal")
        item = {
            "pool": pool_id(pool),
            "pair": pool.pair,
            "protocol": protocol,
            "address": pool.address,
            "score": round(total, 2),
            "scores": {"fee": round(fee_score, 2), "liquidity": round(liquidity_score, 2), "trend": round(trend_score, 2), "warnings": round(warning_score, 2)},
            "metrics": {"fee_24h_usd": fee_24h_of(pool)[0], "fee_source": fee_source, "fee_apr_proxy": None if fee_apr is None else round(fee_apr, 4), "liquidity_usd": liquidity_of(pool), "trend_pct_24h": pool.trend_pct_24h, "volatility_pct_24h": pool.volatility_pct_24h, "trend_reason": trend_reason, "data_age_minutes": pool.data_age_minutes},
            "warnings": warnings,
            "reasons": reasons,
        }
        (excluded if reasons else scored).append(item)
    ranked = sorted(scored, key=lambda x: x["score"], reverse=True)
    flagged = sorted(excluded, key=lambda x: x.get("score", 0), reverse=True)
    return {
        "best_pools": ranked[: config.max_best],
        "watchlist": flagged[: config.max_watchlist],
        "excluded": flagged[config.max_watchlist : config.max_watchlist + 5],
        "data_gaps": data_gaps[:20],
        "scoring": {"weights": {"fee": config.fee_weight, "liquidity": config.liquidity_weight, "trend": config.trend_weight, "warnings": config.warning_weight}, "thresholds": {"min_liquidity_usd": config.min_liquidity_usd, "preferred_liquidity_usd": config.preferred_liquidity_usd, "target_fee_apr": config.target_fee_apr, "max_downtrend_pct_24h": config.max_downtrend_pct_24h, "stale_after_minutes": config.stale_after_minutes}},
    }


def table_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        metrics = item.get("metrics", {})
        rows.append({"Pool": item.get("pool"), "Protocol": item.get("protocol"), "Score": item.get("score"), "24h Fees": money(metrics.get("fee_24h_usd")), "Fee APR": apr(metrics.get("fee_apr_proxy")), "Liquidity": money(metrics.get("liquidity_usd")), "24h Trend": pct(metrics.get("trend_pct_24h")), "Warnings": "none" if not item.get("warnings") else ", ".join(item.get("warnings", [])), "Flags": "none" if not item.get("reasons") else ", ".join(item.get("reasons", []))})
    return rows


def input_parameter_rows(config: Config) -> list[dict[str, Any]]:
    protocol_counts: dict[str, int] = {}
    for pool in config.pools:
        protocol = pool.protocol.lower().strip() or "unknown"
        protocol_counts[protocol] = protocol_counts.get(protocol, 0) + 1
    return [
        {"Parameter": "pools_count", "Value": len(config.pools)},
        {"Parameter": "pools_by_protocol", "Value": ", ".join(f"{k}: {v}" for k, v in sorted(protocol_counts.items())) or "none"},
        {"Parameter": "min_liquidity_usd", "Value": config.min_liquidity_usd},
        {"Parameter": "preferred_liquidity_usd", "Value": config.preferred_liquidity_usd},
        {"Parameter": "target_fee_apr", "Value": config.target_fee_apr},
        {"Parameter": "max_downtrend_pct_24h", "Value": config.max_downtrend_pct_24h},
        {"Parameter": "max_healthy_volatility_pct_24h", "Value": config.max_healthy_volatility_pct_24h},
        {"Parameter": "stale_after_minutes", "Value": config.stale_after_minutes},
        {"Parameter": "max_best", "Value": config.max_best},
        {"Parameter": "max_watchlist", "Value": config.max_watchlist},
        {"Parameter": "fee_weight", "Value": config.fee_weight},
        {"Parameter": "liquidity_weight", "Value": config.liquidity_weight},
        {"Parameter": "trend_weight", "Value": config.trend_weight},
        {"Parameter": "warning_weight", "Value": config.warning_weight},
        {"Parameter": "report_title", "Value": config.report_title},
    ]


def pool_input_rows(pools: list[PoolInput]) -> list[dict[str, Any]]:
    return [{"Pool": pool_id(pool), "Protocol": pool.protocol, "Pair": pool.pair or "n/a", "24h Fees": money(fee_24h_of(pool)[0]), "Liquidity": money(liquidity_of(pool)), "24h Trend": pct(pool.trend_pct_24h), "Warnings": len(warning_list(pool))} for pool in pools[:50]]


def format_text(result: dict[str, Any], total_pools: int) -> str:
    lines = ["LP Pool Watcher", f"Scored pools: {total_pools}", "", "BEST POOLS"]
    if result["best_pools"]:
        for i, item in enumerate(result["best_pools"], 1):
            metrics = item["metrics"]
            lines.append(f"{i}. {item['pool']} ({item['protocol']}) score {item['score']} | fees {money(metrics['fee_24h_usd'])} | liq {money(metrics['liquidity_usd'])} | trend {pct(metrics['trend_pct_24h'])}")
    else:
        lines.append("No pools passed all filters.")
    if result["watchlist"]:
        lines.extend(["", "WATCHLIST / FLAGGED"])
        for i, item in enumerate(result["watchlist"], 1):
            lines.append(f"{i}. {item['pool']} ({item['protocol']}) score {item.get('score')} | flags: {', '.join(item.get('reasons', []))}")
    if result["data_gaps"]:
        lines.extend(["", "DATA GAPS", *[f"- {gap}" for gap in result["data_gaps"][:10]]])
    lines.extend(["", "Scoring weights: fee 35%, liquidity 25%, trend 25%, warnings 15%", "No trades, swaps, LP changes, or executors were created."])
    return "\n".join(lines)


async def save_report(result: dict[str, Any], config: Config) -> None:
    try:
        from condor.reports import ReportBuilder
        builder = ReportBuilder(config.report_title)
        builder.source("routine", "optimal_pool_scorer").tags(["lp", "pools", "orca", "meteora"])
        builder.markdown(f"Scored {len(config.pools)} pools. Scores combine 24h fees, liquidity, trend quality, and API warning status.")
        builder.markdown("### Input Parameters")
        builder.table(input_parameter_rows(config))
        if config.pools:
            builder.markdown("### Input Pools")
            builder.table(pool_input_rows(config.pools))
        if result["best_pools"]:
            builder.markdown("### Best Pools")
            builder.table(table_rows(result["best_pools"]))
        if result["watchlist"]:
            builder.markdown("### Watchlist / Flagged")
            builder.table(table_rows(result["watchlist"]))
        if result["excluded"]:
            builder.markdown("### Excluded")
            builder.table(table_rows(result["excluded"]))
        if result["data_gaps"]:
            builder.markdown("### Data Gaps\n" + "\n".join(f"- {gap}" for gap in result["data_gaps"][:20]))
        thresholds = result["scoring"]["thresholds"]
        builder.markdown("### Scoring Thresholds\n" f"- Minimum liquidity: {money(thresholds['min_liquidity_usd'])}\n" f"- Preferred liquidity: {money(thresholds['preferred_liquidity_usd'])}\n" f"- Target fee APR proxy: {thresholds['target_fee_apr'] * 100:.1f}%\n" f"- Strong downtrend: below {thresholds['max_downtrend_pct_24h']:.1f}% over 24h\n" f"- Stale data: older than {thresholds['stale_after_minutes']:.0f} minutes")
        await builder.save()
    except Exception as exc:
        logger.warning("Report generation failed: %s", exc)


async def run(config: Config, context: Any) -> str:
    result = score_pools(config)
    await save_report(result, config)
    return format_text(result, len(config.pools))
