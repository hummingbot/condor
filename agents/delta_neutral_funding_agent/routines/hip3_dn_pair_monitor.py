"""HIP-3 Delta-Neutral Pair Monitor — live hedge beta, funding, and sizing for the DN funding MM strategy."""
import asyncio
import logging
import math
import time

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client

logger = logging.getLogger(__name__)
CATEGORY = "Monitoring"
HL_URL = "https://api.hyperliquid.xyz/info"


class Config(BaseModel):
    """Live hedge beta + funding monitor for HIP-3 Delta-Neutral Funding MM — emits ADJUSTMENT RECOMMENDATION."""
    leg_a: str = Field(default="CL", description="Coin A (xyz: prefix added automatically)")
    leg_b: str = Field(default="BRENTOIL", description="Coin B (xyz: prefix added automatically)")
    interval: str = Field(default="1h", description="Candle interval for beta/correlation window")
    lookback_hours: int = Field(default=168, description="Lookback window in hours (7d) for beta + correlation")
    configured_hedge_beta: float = Field(default=1.02, description="Configured hedge beta from strategy; compare vs live")
    total_amount_quote: float = Field(default=500.0, description="Total gross notional (quote) to deploy")
    net_delta_band_pct: float = Field(default=0.1, description="Net delta tolerance band as fraction of total")
    min_corr: float = Field(default=0.9, description="Minimum return correlation gate")
    beta_drift_tol: float = Field(default=0.2, description="RESIZE trigger: |live_beta - configured| / configured > this")
    fee_bps_per_side: float = Field(default=1.3, description="All-in maker fee per side in bps")


# ── Math helpers (stdlib only) ────────────────────────────────────────────────

def _std(vals: list) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    m = sum(vals) / n
    return math.sqrt(sum((v - m) ** 2 for v in vals) / n)


def _pearson(xs: list, ys: list) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sx = _std(xs)
    sy = _std(ys)
    if sx < 1e-12 or sy < 1e-12:
        return 0.0
    cov = sum((xs[k] - mx) * (ys[k] - my) for k in range(n)) / n
    return max(-1.0, min(1.0, cov / (sx * sy)))


def _ols_beta(x: list, y: list) -> float:
    """OLS slope y on x: beta = Cov(x,y) / Var(x). Used as hedge ratio."""
    n = len(x)
    if n < 2:
        raise ValueError(f"Need >=2 data points for OLS, got {n}")
    mx = sum(x) / n
    my = sum(y) / n
    ss_xx = sum((xi - mx) ** 2 for xi in x) / n
    ss_xy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / n
    if ss_xx < 1e-20:
        raise ValueError("Zero variance in leg_b returns — cannot compute hedge beta")
    return ss_xy / ss_xx


def _interval_to_hours(interval: str) -> float:
    return {"1m": 1/60, "5m": 5/60, "15m": 0.25, "30m": 0.5,
            "1h": 1.0, "4h": 4.0, "12h": 12.0, "1d": 24.0}.get(interval, 1.0)


# ── HL API helpers ─────────────────────────────────────────────────────────────

async def _fetch_candles(session: aiohttp.ClientSession, coin: str, interval: str,
                          start_ms: int, end_ms: int) -> list:
    payload = {"type": "candleSnapshot",
               "req": {"coin": coin, "interval": interval,
                       "startTime": start_ms, "endTime": end_ms}}
    async with session.post(HL_URL, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        if resp.status != 200:
            raise RuntimeError(f"candleSnapshot HTTP {resp.status} for {coin}")
        data = await resp.json()
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected candleSnapshot response for {coin}: {type(data)}")
        return data


async def _fetch_meta_and_ctxs(session: aiohttp.ClientSession) -> tuple:
    payload = {"type": "metaAndAssetCtxs", "dex": "xyz"}
    async with session.post(HL_URL, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        if resp.status != 200:
            raise RuntimeError(f"metaAndAssetCtxs HTTP {resp.status}")
        data = await resp.json()
        if not isinstance(data, list) or len(data) < 2:
            raise RuntimeError(f"Unexpected metaAndAssetCtxs shape: {type(data)}")
        return data[0], data[1]


def _candles_to_series(candles: list, coin: str) -> dict:
    result = {}
    for c in candles:
        t = c.get("t") or c.get("T")
        close = c.get("c") or c.get("close")
        if t is not None and close is not None:
            try:
                result[int(t)] = float(close)
            except (ValueError, TypeError):
                pass
    if not result:
        raise ValueError(f"Empty candle series after parsing for {coin}")
    return result


def _align_two(series_a: dict, series_b: dict) -> tuple:
    common_ts = sorted(set(series_a.keys()) & set(series_b.keys()))
    if not common_ts:
        raise ValueError("No common timestamps between leg_a and leg_b candles")
    return [series_a[t] for t in common_ts], [series_b[t] for t in common_ts]


def _find_ctx(universe: list, ctxs: list, coin_name: str) -> dict:
    for asset, ctx in zip(universe, ctxs):
        if asset.get("name", "") == coin_name:
            return ctx
    raise ValueError(
        f"Leg '{coin_name}' not found in xyz universe — "
        "verify coin name (e.g. 'CL', 'BRENTOIL') and that it trades on Hyperliquid HIP-3"
    )


# ── Main ──────────────────────────────────────────────────────────────────────

async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    coin_a = f"xyz:{config.leg_a}"
    coin_b = f"xyz:{config.leg_b}"

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - config.lookback_hours * 3600 * 1000

    # ── 1. Fetch candles (parallel) + metaAndAssetCtxs (3 calls total) ───────
    async with aiohttp.ClientSession() as session:
        candles_a_raw, candles_b_raw, meta_ctxs = await asyncio.gather(
            _fetch_candles(session, coin_a, config.interval, start_ms, now_ms),
            _fetch_candles(session, coin_b, config.interval, start_ms, now_ms),
            _fetch_meta_and_ctxs(session),
        )
    meta, ctxs = meta_ctxs

    # ── 2. Parse + align candles ───────────────────────────────────────────────
    series_a = _candles_to_series(candles_a_raw, coin_a)
    series_b = _candles_to_series(candles_b_raw, coin_b)
    prices_a, prices_b = _align_two(series_a, series_b)

    if len(prices_a) < 10:
        raise ValueError(
            f"Only {len(prices_a)} aligned bars for {coin_a}/{coin_b}; need >=10. "
            "Check coin names and that both legs trade on Hyperliquid xyz."
        )

    # ── 3. Log returns + stats ─────────────────────────────────────────────────
    log_a = [math.log(prices_a[i] / prices_a[i-1]) for i in range(1, len(prices_a))
              if prices_a[i-1] > 0 and prices_a[i] > 0]
    log_b = [math.log(prices_b[i] / prices_b[i-1]) for i in range(1, len(prices_b))
              if prices_b[i-1] > 0 and prices_b[i] > 0]
    n_rets = min(len(log_a), len(log_b))
    log_a = log_a[:n_rets]
    log_b = log_b[:n_rets]

    if n_rets < 4:
        raise ValueError(f"Too few return bars ({n_rets}) to compute statistics")

    hours_per_bar = _interval_to_hours(config.interval)
    bars_per_year = 365 * 24 / hours_per_bar

    corr = _pearson(log_a, log_b)
    # hedge beta = OLS slope of leg_a returns on leg_b returns: Cov(ra,rb)/Var(rb)
    live_beta = _ols_beta(log_b, log_a)
    vol_a = _std(log_a) * math.sqrt(bars_per_year)
    vol_b = _std(log_b) * math.sqrt(bars_per_year)

    # ── 4. Funding + mids from metaAndAssetCtxs ───────────────────────────────
    universe = meta.get("universe", [])
    ctx_a = _find_ctx(universe, ctxs, coin_a)
    ctx_b = _find_ctx(universe, ctxs, coin_b)

    mark_a = float(ctx_a.get("markPx") or ctx_a.get("midPx") or 0)
    mark_b = float(ctx_b.get("markPx") or ctx_b.get("midPx") or 0)
    if mark_a <= 0:
        raise ValueError(f"Zero/missing markPx for {coin_a}")
    if mark_b <= 0:
        raise ValueError(f"Zero/missing markPx for {coin_b}")

    funding_a_hr = float(ctx_a.get("funding") or 0)   # per-hour rate
    funding_b_hr = float(ctx_b.get("funding") or 0)
    funding_a_yr = funding_a_hr * 24 * 365 * 100       # annualized %
    funding_b_yr = funding_b_hr * 24 * 365 * 100

    # ── 5. Two delta-neutral configs ──────────────────────────────────────────
    B = live_beta  # hedge ratio: leg_b_notional = B * leg_a_notional

    # Config A: LONG leg_a / SHORT leg_b
    #   LONG leg_a: carry = -funding_a_hr (if +ve rate, long pays; if -ve, long receives)
    #   SHORT leg_b: carry = +B*funding_b_hr (if +ve rate, short receives; scaled by notional ratio B)
    net_carry_hr_A = -funding_a_hr + B * funding_b_hr
    # Config B: SHORT leg_a / LONG leg_b
    net_carry_hr_B = funding_a_hr - B * funding_b_hr

    if net_carry_hr_A >= net_carry_hr_B:
        recommended_config = "A"
        net_carry_hr_best = net_carry_hr_A
        config_desc = f"LONG {config.leg_a} / SHORT {config.leg_b}"
        short_leg, short_leg_fund = config.leg_b, funding_b_hr
        long_leg,  long_leg_fund  = config.leg_a, funding_a_hr
    else:
        recommended_config = "B"
        net_carry_hr_best = net_carry_hr_B
        config_desc = f"SHORT {config.leg_a} / LONG {config.leg_b}"
        short_leg, short_leg_fund = config.leg_a, funding_a_hr
        long_leg,  long_leg_fund  = config.leg_b, funding_b_hr

    net_carry_yr = net_carry_hr_best * 24 * 365 * 100

    # ── 5b. Fetch live positions ──────────────────────────────────────────────
    from condor.fetchers.positions import fetch_positions
    client = await get_client(context._chat_id, context=context)
    raw_positions = await fetch_positions(client, connector_name="hyperliquid_perpetual") if client else []

    sym_a = f"XYZ:{config.leg_a}-USD"
    sym_b = f"XYZ:{config.leg_b}-USD"
    amount_a = 0.0
    amount_b = 0.0
    for pos in raw_positions:
        pair = pos.get("trading_pair", "")
        if pair == sym_a:
            amount_a = float(pos.get("amount", 0.0))
        elif pair == sym_b:
            amount_b = float(pos.get("amount", 0.0))

    has_live_position = bool(raw_positions)
    signed_notional_a = amount_a * mark_a
    signed_notional_b = amount_b * mark_b
    actual_net_factor_delta = B * signed_notional_a + signed_notional_b

    # ── 6. Delta-neutral sizing using live beta (TARGET — theoretical) ────────
    gross = config.total_amount_quote
    leg_a_notional = gross / (1 + B)
    leg_b_notional = B * leg_a_notional

    if recommended_config == "A":
        signed_a = +leg_a_notional
        signed_b = -leg_b_notional
    else:
        signed_a = -leg_a_notional
        signed_b = +leg_b_notional

    # theoretical net delta (≈0 by construction — use actual_net_factor_delta for breach checks)
    net_factor_delta = signed_a * B + signed_b
    delta_band = config.net_delta_band_pct * gross

    # ── 7. Adjustment decision (priority order) ───────────────────────────────
    beta_drift = abs(live_beta - config.configured_hedge_beta) / max(abs(config.configured_hedge_beta), 1e-10)
    corr_pass = corr >= config.min_corr

    flip_flags = []
    if short_leg_fund <= 0:
        flip_flags.append(
            f"WARNING: SHORT leg ({short_leg}) funding <= 0 "
            f"({short_leg_fund*24*365*100:.1f}%/yr) — carry eroded, short is paying"
        )
    if long_leg_fund > 0.0001 / (24 * 365):   # > ~0.09%/yr => long paying materially
        flip_flags.append(
            f"WARNING: LONG leg ({long_leg}) funding > 0 "
            f"({long_leg_fund*24*365*100:.1f}%/yr) — long is also paying, carry compressed"
        )

    actual_delta_breach = has_live_position and abs(actual_net_factor_delta) > delta_band

    if not corr_pass:
        recommendation = (
            f"HOLD/FLATTEN: correlation broke "
            f"(corr={corr:.3f} < min={config.min_corr}) — "
            "hedge unreliable; reduce or flatten positions."
        )
    elif net_carry_yr <= 0:
        recommendation = (
            f"REDUCE/ROTATE: funding no longer favorable "
            f"(net carry={net_carry_yr:.1f}%/yr <= 0) — "
            "consider rotating to a different pair or flattening."
        )
    elif beta_drift > config.beta_drift_tol:
        recommendation = (
            f"RESIZE: live beta ({live_beta:.4f}) drifted from configured "
            f"({config.configured_hedge_beta:.4f}); "
            f"drift={beta_drift:.1%} > tol={config.beta_drift_tol:.0%} — "
            f"re-split notionals at B={live_beta:.4f}. Still runnable."
        )
    elif actual_delta_breach:
        recommendation = (
            f"HEDGE: actual net factor delta ${actual_net_factor_delta:+.2f} "
            f"breaches band +/-${delta_band:.2f} — "
            f"adjust {config.leg_a} or {config.leg_b} position to rebalance delta."
        )
    else:
        recommendation = (
            f"RUN config {recommended_config}: deploy/maintain {config_desc} "
            f"at ${leg_a_notional:.1f}/{config.leg_a} and ${leg_b_notional:.1f}/{config.leg_b} "
            f"(net carry {net_carry_yr:.1f}%/yr, corr {corr:.3f} OK, beta in-band)."
        )

    # ── 8. Format summary ─────────────────────────────────────────────────────
    lines = [
        f"**HIP-3 DN Pair Monitor — {config.leg_a} / {config.leg_b}**",
        f"Window: {config.lookback_hours}h {config.interval} candles  |  {n_rets} return bars",
        "",
        f"RECOMMENDATION: {recommendation}",
    ]
    for flag in flip_flags:
        lines.append(flag)
    lines += [
        "",
        "── MARKET DATA ──",
        f"  Mark {config.leg_a:<10}: ${mark_a:.4f}",
        f"  Mark {config.leg_b:<10}: ${mark_b:.4f}",
        "",
        "── BETA & CORRELATION ──",
        f"  Live hedge beta   : {live_beta:.4f}  (configured: {config.configured_hedge_beta:.4f}, drift: {beta_drift:.1%})",
        f"  Return correlation: {corr:.4f}  (gate >={config.min_corr}) -> {'PASS' if corr_pass else 'FAIL'}",
        f"  Ann vol {config.leg_a:<10}: {vol_a:.1%}",
        f"  Ann vol {config.leg_b:<10}: {vol_b:.1%}",
        "",
        "── FUNDING (live, per-hour -> annualized) ──",
        f"  {config.leg_a} funding: {funding_a_hr*1e6:.3f} micro/hr  ({funding_a_yr:+.1f}%/yr)",
        f"  {config.leg_b} funding: {funding_b_hr*1e6:.3f} micro/hr  ({funding_b_yr:+.1f}%/yr)",
        "",
        "── CONFIG COMPARISON ──",
        f"  Config A (LONG {config.leg_a} / SHORT {config.leg_b}): {net_carry_hr_A*24*365*100:+.1f}%/yr",
        f"  Config B (SHORT {config.leg_a} / LONG {config.leg_b}): {net_carry_hr_B*24*365*100:+.1f}%/yr",
        f"  Recommended       : Config {recommended_config} ({config_desc})",
        f"  Net carry (best)  : {net_carry_yr:+.1f}%/yr",
        "",
        "── TARGET SIZING (theoretical, live B) ──",
        f"  Gross notional: ${gross:.2f}",
        f"  Leg A ({config.leg_a}): ${abs(signed_a):.2f}  ({'LONG' if signed_a > 0 else 'SHORT'})",
        f"  Leg B ({config.leg_b}): ${abs(signed_b):.2f}  ({'LONG' if signed_b > 0 else 'SHORT'})",
        f"  Net factor delta TARGET (theoretical): {net_factor_delta:.4f}  (band +/-{delta_band:.2f})",
        "",
        "── ACTUAL POSITIONS ──",
    ]
    if not has_live_position:
        lines.append("  no live position — flat  (actual delta = 0.00)")
    else:
        actual_band_label = "BREACH" if actual_delta_breach else "IN-BAND"
        lines += [
            f"  Leg A ({config.leg_a}): ${signed_notional_a:+.2f}  ({'LONG' if signed_notional_a >= 0 else 'SHORT'}, amount={amount_a:+.6f})",
            f"  Leg B ({config.leg_b}): ${signed_notional_b:+.2f}  ({'LONG' if signed_notional_b >= 0 else 'SHORT'}, amount={amount_b:+.6f})",
            f"  Actual net factor delta: ${actual_net_factor_delta:+.2f}  vs +/-${delta_band:.2f} -> {actual_band_label}",
        ]
    summary = "\n".join(lines)

    # ── 9. ReportBuilder ──────────────────────────────────────────────────────
    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(f"HIP-3 DN Monitor: {config.leg_a}/{config.leg_b}")
        builder.source("routine", "hip3_dn_pair_monitor").tags(
            ["market-making", "hip3", "delta-neutral", config.leg_a.lower(), config.leg_b.lower()]
        )

        action_word = recommendation.split(":")[0]
        builder.kpi("Pair",        f"{config.leg_a}/{config.leg_b}")
        builder.kpi("Action",      action_word)
        builder.kpi("Config",      f"Config {recommended_config}")
        builder.kpi("Net Carry",   f"{net_carry_yr:.1f}%/yr")
        builder.kpi("Correlation", f"{corr:.3f}")
        builder.kpi("Live Beta",   f"{live_beta:.4f}")
        builder.kpi("Beta Drift",  f"{beta_drift:.1%}")
        builder.kpi("Corr Gate",   "PASS" if corr_pass else "FAIL")
        if has_live_position:
            _abl = "BREACH" if actual_delta_breach else "IN-BAND"
            builder.kpi("Actual Net Delta", f"${actual_net_factor_delta:+.2f} ({_abl})")
        else:
            builder.kpi("Actual Net Delta", "flat (no position)")

        rows = [
            {"Metric": f"Mark {config.leg_a}",                              "Value": f"${mark_a:.4f}"},
            {"Metric": f"Mark {config.leg_b}",                              "Value": f"${mark_b:.4f}"},
            {"Metric": "Return Correlation",                                 "Value": f"{corr:.4f}"},
            {"Metric": "Live Hedge Beta",                                    "Value": f"{live_beta:.4f}"},
            {"Metric": "Configured Beta",                                    "Value": f"{config.configured_hedge_beta:.4f}"},
            {"Metric": "Beta Drift",                                         "Value": f"{beta_drift:.1%}"},
            {"Metric": f"Ann Vol {config.leg_a}",                           "Value": f"{vol_a:.1%}"},
            {"Metric": f"Ann Vol {config.leg_b}",                           "Value": f"{vol_b:.1%}"},
            {"Metric": f"{config.leg_a} Funding/yr",                        "Value": f"{funding_a_yr:+.1f}%"},
            {"Metric": f"{config.leg_b} Funding/yr",                        "Value": f"{funding_b_yr:+.1f}%"},
            {"Metric": f"Carry Config A ({config.leg_a}L/{config.leg_b}S)", "Value": f"{net_carry_hr_A*24*365*100:+.1f}%/yr"},
            {"Metric": f"Carry Config B ({config.leg_a}S/{config.leg_b}L)", "Value": f"{net_carry_hr_B*24*365*100:+.1f}%/yr"},
            {"Metric": "Recommended Config",                                 "Value": f"Config {recommended_config}: {config_desc}"},
            {"Metric": "Net Carry (best)",                                   "Value": f"{net_carry_yr:+.1f}%/yr"},
            {"Metric": f"Leg A ({config.leg_a}) TARGET Notional",           "Value": f"${abs(signed_a):.2f} ({'L' if signed_a > 0 else 'S'}) [theoretical]"},
            {"Metric": f"Leg B ({config.leg_b}) TARGET Notional",           "Value": f"${abs(signed_b):.2f} ({'L' if signed_b > 0 else 'S'}) [theoretical]"},
            {"Metric": "Net Factor Delta TARGET (theoretical)",              "Value": f"{net_factor_delta:.4f}"},
            {"Metric": "Delta Band",                                         "Value": f"+/-{delta_band:.2f}"},
        ]
        if not has_live_position:
            rows.append({"Metric": "Actual Positions", "Value": "no live position — flat"})
        else:
            _abl = "BREACH" if actual_delta_breach else "IN-BAND"
            rows += [
                {"Metric": f"Leg A ({config.leg_a}) ACTUAL Notional",      "Value": f"${signed_notional_a:+.2f} ({'L' if signed_notional_a >= 0 else 'S'}, amt={amount_a:+.6f})"},
                {"Metric": f"Leg B ({config.leg_b}) ACTUAL Notional",      "Value": f"${signed_notional_b:+.2f} ({'L' if signed_notional_b >= 0 else 'S'}, amt={amount_b:+.6f})"},
                {"Metric": "Actual Net Factor Delta",                       "Value": f"${actual_net_factor_delta:+.2f} ({_abl})"},
            ]
        builder.table(rows, ["Metric", "Value"])
        builder.markdown(summary)
        builder.manual_order()
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return summary
