"""
Derive options market read → directional signal for SOL-PERP.

Pulls live options data from Derive's public API across all active expiries and
computes four signals that cannot be inferred from a price chart alone:

  1. 25-Delta Risk Reversal (50%) — IV(25Δ call) − IV(25Δ put), near-term weighted.
     Positive = smart money is bidding calls = bullish.
  2. Put/Call Open Interest Ratio (35%) — log-scaled, OI-weighted across expiries.
     Call-heavy OI = bullish; put-heavy = bearish.
  3. ATM IV Term Structure (15%) — near vs far ATM IV.
     Inverted curve = near-term fear = bearish.
  4. Net Gamma Exposure (GEX) — modulates conviction, not direction.
     Positive GEX (dealers long gamma) → price gravity, dampen signal (0.75×).
     Negative GEX (dealers short gamma) → momentum, amplify signal (1.25×).

Output: LONG / SHORT / HOLD + composite score (−1 to +1).
"""

import asyncio
import logging
import math
from datetime import datetime, timezone

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.reports import ReportBuilder
from routines.base import RoutineResult

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"
DERIVE_API = "https://api.lyra.finance"


class Config(BaseModel):
    """Derive options market read → directional signal for SOL-PERP."""

    currency: str = Field(default="SOL", description="Currency to analyze (SOL or ETH)")
    perp_instrument: str = Field(
        default="SOL-PERP", description="Perpetual instrument to signal"
    )
    rr_weight: float = Field(
        default=0.50, description="Weight for 25D Risk Reversal (0–1)"
    )
    oi_weight: float = Field(
        default=0.35, description="Weight for Put/Call OI Ratio (0–1)"
    )
    ts_weight: float = Field(
        default=0.15, description="Weight for Term Structure (0–1)"
    )
    min_threshold: float = Field(
        default=0.40, description="Min |composite score| to act; below = HOLD"
    )


# ── API helpers ──────────────────────────────────────────────────────────────


async def _post(session: aiohttp.ClientSession, path: str, payload: dict) -> dict:
    async with session.post(
        f"{DERIVE_API}/{path}",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        resp.raise_for_status()
        return await resp.json()


async def _fetch_perp_ticker(session: aiohttp.ClientSession, instrument: str) -> dict:
    data = await _post(session, "public/get_ticker", {"instrument_name": instrument})
    return data.get("result") or {}


async def _fetch_instruments(
    session: aiohttp.ClientSession, currency: str
) -> list[dict]:
    data = await _post(
        session,
        "public/get_instruments",
        {"currency": currency, "instrument_type": "option", "expired": False},
    )
    return data.get("result") or []


async def _fetch_tickers(
    session: aiohttp.ClientSession, currency: str, expiry_date: str
) -> dict:
    data = await _post(
        session,
        "public/get_tickers",
        {
            "currency": currency,
            "instrument_type": "option",
            "expired": False,
            "expiry_date": expiry_date,
        },
    )
    return (data.get("result") or {}).get("tickers") or {}


# ── Option parsing ────────────────────────────────────────────────────────────


def _parse_options(tickers: dict) -> list[dict]:
    out = []
    for name, t in tickers.items():
        parts = name.split("-")
        if len(parts) < 4:
            continue
        op = t.get("option_pricing") or {}
        stats = t.get("stats") or {}
        try:
            out.append(
                {
                    "name": name,
                    "strike": float(parts[2]),
                    "type": parts[3],  # "C" or "P"
                    "delta": float(op.get("d") or 0),
                    "gamma": float(op.get("g") or 0),
                    "iv": float(op.get("i") or 0),
                    "oi": float(stats.get("oi") or 0),
                    "vol": float(stats.get("v") or 0),
                    "mark": float(t.get("M") or 0),
                }
            )
        except (ValueError, TypeError):
            continue
    return out


# ── Signal calculators ────────────────────────────────────────────────────────


def _compute_25d_rr(opts: list[dict]) -> tuple[float | None, str, str]:
    """(rr, call_name, put_name). None if insufficient data."""
    calls = [
        o for o in opts if o["type"] == "C" and o["iv"] > 0 and abs(o["delta"]) > 0.01
    ]
    puts = [
        o for o in opts if o["type"] == "P" and o["iv"] > 0 and abs(o["delta"]) > 0.01
    ]
    if not calls or not puts:
        return None, "", ""
    c25 = min(calls, key=lambda x: abs(abs(x["delta"]) - 0.25))
    p25 = min(puts, key=lambda x: abs(abs(x["delta"]) - 0.25))
    return c25["iv"] - p25["iv"], c25["name"], p25["name"]


def _compute_pc_oi(opts: list[dict]) -> tuple[float | None, float, float]:
    """(score +1=bullish…-1=bearish, call_oi, put_oi)."""
    call_oi = sum(o["oi"] for o in opts if o["type"] == "C")
    put_oi = sum(o["oi"] for o in opts if o["type"] == "P")
    if call_oi + put_oi < 10:
        return None, call_oi, put_oi
    ratio = (put_oi + 0.1) / (call_oi + 0.1)
    # log ratio: negative = call-heavy (bullish), positive = put-heavy (bearish)
    score = -math.tanh(math.log(ratio) * 1.5)  # flip so +1 = bullish
    return score, call_oi, put_oi


def _atm_iv(opts: list[dict], spot: float) -> float | None:
    atm_calls = [o for o in opts if o["type"] == "C" and o["iv"] > 0]
    if not atm_calls:
        return None
    return min(atm_calls, key=lambda x: abs(x["strike"] - spot))["iv"]


def _gex(opts: list[dict], spot: float) -> float:
    """Net gamma exposure USD. Positive = dealers long gamma."""
    total = 0.0
    for o in opts:
        sign = 1.0 if o["type"] == "C" else -1.0
        total += sign * o["gamma"] * o["oi"] * (spot**2) * 0.01
    return total


def _confidence(
    composite: float, rr_score: float, oi_score: float, ts_score: float
) -> str:
    """How many of the 3 sub-signals agree with the EMITTED direction.

    The reference must be sign(composite), not the dominant vote: with weighted
    scores and the GEX amplifier, the composite can point LONG while two of
    three raw votes point SHORT — grading agreement against the dominant vote
    would then stamp MEDIUM on a signal the majority disagrees with.
    """
    votes = [
        1 if rr_score >= 0.2 else (-1 if rr_score <= -0.2 else 0),
        1 if oi_score >= 0.2 else (-1 if oi_score <= -0.2 else 0),
        1 if ts_score >= 0.1 else (-1 if ts_score <= -0.1 else 0),
    ]
    emitted = 1 if composite > 0 else (-1 if composite < 0 else 0)
    if not emitted:
        return "LOW"
    agree = sum(1 for v in votes if v == emitted)
    return "HIGH" if agree == 3 else ("MEDIUM" if agree == 2 else "LOW")


# ── Main ─────────────────────────────────────────────────────────────────────


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            perp_data, instruments = await asyncio.gather(
                _fetch_perp_ticker(session, config.perp_instrument),
                _fetch_instruments(session, config.currency),
            )
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        return f"Derive API unavailable ({type(e).__name__}: {e}) — no options signal this tick"

    spot = float(perp_data.get("index_price") or 0)
    if spot <= 0:
        return f"Could not fetch spot price for {config.perp_instrument}"

    funding_rate = float((perp_data.get("perp_details") or {}).get("funding_rate") or 0)

    # Find unique active expiry dates
    active = [i for i in instruments if i.get("is_active")]
    expiry_dates = sorted(
        set(
            datetime.fromtimestamp(
                i["option_details"]["expiry"], tz=timezone.utc
            ).strftime("%Y%m%d")
            for i in active
            if i.get("option_details")
        )
    )
    if not expiry_dates:
        return "No active options found"

    # Fetch all expiry tickers in parallel
    sem = asyncio.Semaphore(6)

    async def fetch_guarded(exp: str) -> tuple[str, dict]:
        async with sem:
            async with aiohttp.ClientSession() as s:
                tickers = await _fetch_tickers(s, config.currency, exp)
        return exp, tickers

    results = await asyncio.gather(
        *[fetch_guarded(e) for e in expiry_dates], return_exceptions=True
    )
    expiry_opts, failed_expiries = {}, []
    for exp, res in zip(expiry_dates, results):
        if isinstance(res, BaseException):
            logger.warning("options_flow: expiry %s fetch failed: %s", exp, res)
            failed_expiries.append(exp)
        else:
            expiry_opts[exp] = _parse_options(res[1])
    if not expiry_opts:
        return (
            f"Derive API unavailable (all {len(expiry_dates)} expiry fetches failed) "
            "— no options signal this tick"
        )

    # ── Per-expiry signals ──
    today = datetime.now(tz=timezone.utc)

    def dte(exp_str: str) -> int:
        return max(
            1,
            (
                datetime.strptime(exp_str, "%Y%m%d").replace(tzinfo=timezone.utc)
                - today
            ).days,
        )

    rr_map, rr_detail, oi_map, iv_map = {}, {}, {}, {}
    for exp, opts in expiry_opts.items():
        rr, c_name, p_name = _compute_25d_rr(opts)
        if rr is not None:
            rr_map[exp] = rr
            rr_detail[exp] = (c_name, p_name)
        oi_score, c_oi, p_oi = _compute_pc_oi(opts)
        if oi_score is not None:
            oi_map[exp] = (oi_score, c_oi, p_oi)
        iv = _atm_iv(opts, spot)
        if iv is not None:
            iv_map[exp] = iv

    # ── Composite score ──
    # 25D RR: near-term weighted (w = 1/DTE)
    rr_num, rr_den = 0.0, 0.0
    for exp, rr in rr_map.items():
        w = 1.0 / dte(exp)
        rr_num += math.tanh(rr / 0.05) * w
        rr_den += w
    rr_score = rr_num / rr_den if rr_den > 0 else 0.0

    # P/C OI: weighted by log(total OI)
    oi_num, oi_den = 0.0, 0.0
    for exp, (score, c_oi, p_oi) in oi_map.items():
        total_oi = c_oi + p_oi
        if total_oi < 50:
            continue
        w = math.log1p(total_oi)
        oi_num += score * w
        oi_den += w
    oi_score = oi_num / oi_den if oi_den > 0 else 0.0

    # Term structure
    ts_score = 0.0
    if len(iv_map) >= 2:
        sorted_ivs = [iv_map[e] for e in sorted(iv_map)]
        near_iv, far_iv = sorted_ivs[0], sorted_ivs[-1]
        if near_iv > far_iv * 1.05:
            ts_score = -0.5  # inverted = fear
        elif far_iv > near_iv * 1.05:
            ts_score = +0.2  # contango = mild bullish
    else:
        near_iv, far_iv = 0.0, 0.0

    # GEX from nearest liquid expiry
    gex_exp, gex_val = None, 0.0
    for exp in sorted(expiry_opts):
        if sum(o["oi"] for o in expiry_opts[exp]) > 100:
            gex_exp = exp
            gex_val = _gex(expiry_opts[exp], spot)
            break
    # No liquid expiry → no gamma read → neutral amplifier (never amplify on missing data)
    gex_amp = 0.75 if gex_val > 0 else (1.25 if gex_val < 0 else 1.0)

    composite = max(
        -1.0,
        min(
            1.0,
            (
                rr_score * config.rr_weight
                + oi_score * config.oi_weight
                + ts_score * config.ts_weight
            )
            * gex_amp,
        ),
    )

    if composite >= config.min_threshold:
        direction, emoji = "LONG", "🟢"
    elif composite <= -config.min_threshold:
        direction, emoji = "SHORT", "🔴"
    else:
        direction, emoji = "HOLD", "🟡"

    confidence = _confidence(composite, rr_score, oi_score, ts_score)

    # ── Report ────────────────────────────────────────────────────────────────
    builder = ReportBuilder(f"Options Oracle — {config.currency}/USDC")
    builder.source("routine", "options_flow")
    builder.tags(["options", "signal", config.currency, "perp"])
    builder.manual_order()

    builder.section("SIGNAL", f"Derive options market read → {config.perp_instrument}")
    builder.kpi("Direction", f"{emoji} {direction}")
    builder.kpi("Composite Score", f"{composite:+.3f}")
    builder.kpi("Confidence", confidence)
    builder.kpi(f"{config.currency} Spot", f"${spot:.3f}")
    builder.kpi("Funding Rate", f"{funding_rate * 100:.4f}%/hr")

    builder.section("SIGNAL BREAKDOWN", "Weighted sub-signals feeding the composite")
    builder.kpi("25D Risk Reversal", f"{rr_score:+.3f}  (wt {config.rr_weight:.0%})")
    builder.kpi("P/C OI Ratio", f"{oi_score:+.3f}  (wt {config.oi_weight:.0%})")
    builder.kpi("Term Structure", f"{ts_score:+.3f}  (wt {config.ts_weight:.0%})")
    if gex_exp is not None:
        builder.kpi("GEX Amplifier", f"{gex_val:+.1f}  →  {gex_amp:.2f}×")
    else:
        builder.kpi("GEX Amplifier", "n/a (no liquid expiry)  →  1.00×")
    if failed_expiries:
        builder.markdown(
            f"⚠️ {len(failed_expiries)}/{len(expiry_dates)} expiry fetches failed "
            f"({', '.join(failed_expiries)}) — composite computed from partial data."
        )

    # Per-expiry table
    exp_rows = []
    for exp in sorted(expiry_opts):
        d = dte(exp)
        rr_v = rr_map.get(exp)
        oi_d = oi_map.get(exp)
        iv_v = iv_map.get(exp)
        c_oi = oi_d[1] if oi_d else 0
        p_oi = oi_d[2] if oi_d else 0
        exp_rows.append(
            {
                "Expiry": exp,
                "DTE": d,
                "ATM IV": f"{iv_v*100:.1f}%" if iv_v else "—",
                "25D RR": f"{rr_v:+.4f}" if rr_v is not None else "—",
                "Bias": (
                    ("BULL" if rr_v and rr_v > 0 else "BEAR")
                    if rr_v is not None
                    else "—"
                ),
                "Call OI": f"{c_oi:.0f}",
                "Put OI": f"{p_oi:.0f}",
                "P/C Ratio": f"{p_oi/max(c_oi,1):.2f}",
            }
        )

    builder.section(
        "PER-EXPIRY BREAKDOWN", "Signals across all active option expiry dates"
    )
    builder.table(
        exp_rows,
        ["Expiry", "DTE", "ATM IV", "25D RR", "Bias", "Call OI", "Put OI", "P/C Ratio"],
    )

    # IV surface for nearest liquid expiry
    if gex_exp:
        surf_opts = [
            o
            for o in expiry_opts[gex_exp]
            if o["iv"] > 0 and abs(o["strike"] - spot) / spot < 0.30
        ]
        surf_rows = sorted(
            [
                {
                    "Strike": o["strike"],
                    "Type": o["type"],
                    "IV %": f"{o['iv']*100:.1f}%",
                    "Delta": f"{o['delta']:+.3f}",
                    "OI": f"{o['oi']:.0f}",
                    "Vol 24h": f"{o['vol']:.1f}",
                }
                for o in surf_opts
            ],
            key=lambda x: (x["Strike"], x["Type"]),
        )

        builder.section(
            f"IV SURFACE — {gex_exp}",
            "Options within ±30% of spot, nearest liquid expiry",
        )
        builder.table(surf_rows, ["Strike", "Type", "IV %", "Delta", "OI", "Vol 24h"])

    builder.section("METHODOLOGY", "")
    builder.markdown(
        "**25D Risk Reversal** (50%): IV(25Δ call) − IV(25Δ put), weighted by 1/DTE so "
        "near-term expiries dominate. Positive = calls bid = bullish.\n\n"
        "**Put/Call OI Ratio** (35%): log(put\\_OI / call\\_OI), OI-magnitude weighted. "
        "Call-heavy → bullish; put-heavy → bearish.\n\n"
        "**Term Structure** (15%): near vs far ATM IV. "
        "Inverted (near > far) = near-term fear = bearish (−0.5). "
        "Normal contango = mild bullish (+0.2).\n\n"
        "**GEX Amplifier**: dealers long gamma (positive GEX) → price gravity → dampen 0.75×. "
        "Dealers short gamma (negative GEX) → momentum → amplify 1.25×.\n\n"
        f"Threshold: |composite| ≥ {config.min_threshold} → act; below → HOLD."
    )

    await builder.save()

    gex_str = (
        f"GEX {gex_val:+.0f} ({gex_amp:.2f}×)"
        if gex_exp is not None
        else "GEX n/a (1.00×)"
    )
    summary = (
        f"{emoji} Options Oracle: **{direction}** (score {composite:+.3f} | {confidence} confidence)\n"
        f"{config.currency} @ ${spot:.3f} | "
        f"25D RR {rr_score:+.3f} | P/C OI {oi_score:+.3f} | TS {ts_score:+.3f} | {gex_str}"
    )
    if failed_expiries:
        summary += f"\n⚠️ partial data: {len(failed_expiries)}/{len(expiry_dates)} expiry fetches failed"

    return RoutineResult(
        text=summary,
        sections=[
            {"type": "kpi", "label": "Direction", "value": f"{emoji} {direction}"},
            {"type": "kpi", "label": "Score", "value": f"{composite:+.3f}"},
            {"type": "kpi", "label": "Confidence", "value": confidence},
            {
                "type": "kpi",
                "label": f"{config.currency} Spot",
                "value": f"${spot:.3f}",
            },
            {"type": "kpi", "label": "Funding/hr", "value": f"{funding_rate*100:.4f}%"},
        ],
    )
