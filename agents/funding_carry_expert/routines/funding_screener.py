"""Screen perps for holdable funding carry: spot hedge available, stable sign, real size."""
import asyncio
import logging
import statistics as st

import httpx
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

BITGET = "https://api.bitget.com/api/v2"

# Round-trip cost across all four legs (buy spot + short perp, then unwind).
COST_TAKER_BPS = 21.0
COST_MAKER_BPS = 8.0

PERIODS_PER_YEAR = 3 * 365  # 8h funding


class Config(BaseModel):
    """Rank perps by holdable funding carry — spot hedge, sign stability, then size."""

    min_volume_usd: float = Field(default=5_000_000, description="Min 24h perp volume")
    min_abs_funding_bps: float = Field(
        default=0.5, description="Min |current funding| per 8h, in bps"
    )
    require_spot: bool = Field(
        default=True, description="Require a spot market (needed to hedge). Keep true."
    )
    min_stability_pct: float = Field(
        default=65.0, description="Min %% of periods funding held one sign"
    )
    top_n: int = Field(default=10, description="Candidates to report")
    history_pages: int = Field(default=3, description="Funding history pages (100 each)")


async def _get(http: httpx.AsyncClient, url: str):
    r = await http.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


async def _funding_history(http: httpx.AsyncClient, symbol: str, pages: int) -> list:
    """Funding rates in bps per 8h, oldest first."""
    out: dict[int, float] = {}
    for page in range(1, pages + 1):
        try:
            d = await _get(
                http,
                f"{BITGET}/mix/market/history-fund-rate?symbol={symbol}"
                f"&productType=usdt-futures&pageSize=100&pageNo={page}",
            )
        except Exception:
            break
        rows = d.get("data") or []
        if not rows:
            break
        for r in rows:
            try:
                out[int(r["fundingTime"])] = float(r["fundingRate"]) * 10_000
            except (KeyError, TypeError, ValueError):
                continue
    return [out[t] for t in sorted(out)]


def _assess(hist: list) -> dict:
    """Causal assessment: pick the side from a trailing window, collect the next period."""
    trail_n = 3
    if len(hist) < trail_n + 10:
        return {}

    gross, entries, side, hits, scored = 0.0, 0, 0, 0, 0
    for i in range(trail_n, len(hist)):
        trail = st.fmean(hist[i - trail_n:i])
        want = 1 if trail > 0 else -1
        if want != side:
            entries += 1
            side = want
        gross += side * hist[i]
        hits += 1 if side * hist[i] > 0 else 0
        scored += 1

    periods = len(hist)
    days = periods / 3
    dominant = max(
        sum(1 for x in hist if x > 0), sum(1 for x in hist if x < 0)
    ) / periods * 100
    flips = sum(1 for i in range(1, periods) if (hist[i] > 0) != (hist[i - 1] > 0))

    return {
        "periods": periods,
        "days": days,
        "mean_bps": st.fmean(hist),
        "dominant_pct": dominant,
        "hit_pct": 100 * hits / scored if scored else 0.0,
        "flips": flips,
        "side_changes": entries,
        "gross_bps": gross,
        "net_taker_bps": gross - entries * COST_TAKER_BPS,
        "ann_hold_pct": (sum(hist) - COST_TAKER_BPS) / days * 365 / 100 if days else 0.0,
    }


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    out: list[str] = []

    async with httpx.AsyncClient() as http:
        try:
            tick_task = _get(http, f"{BITGET}/mix/market/tickers?productType=usdt-futures")
            spot_task = _get(http, f"{BITGET}/spot/market/tickers")
            tickers, spot = await asyncio.gather(tick_task, spot_task)
        except Exception as exc:
            return f"screener: ERROR fetching Bitget markets — {exc}"

        spot_symbols = {t.get("symbol") for t in (spot.get("data") or [])}

        cands = []
        for t in tickers.get("data") or []:
            try:
                sym = t["symbol"]
                rate = float(t.get("fundingRate") or 0) * 10_000
                vol = float(t.get("usdtVolume") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            if vol < config.min_volume_usd or abs(rate) < config.min_abs_funding_bps:
                continue
            if config.require_spot and sym not in spot_symbols:
                continue
            cands.append((sym, rate, vol, sym in spot_symbols))

        cands.sort(key=lambda r: -abs(r[1]))
        cands = cands[: max(config.top_n * 3, config.top_n)]

        hists = await asyncio.gather(
            *[_funding_history(http, s, config.history_pages) for s, _, _, _ in cands],
            return_exceptions=True,
        )

    rows = []
    for (sym, rate, vol, has_spot), hist in zip(cands, hists):
        if isinstance(hist, Exception) or not hist:
            continue
        a = _assess(hist)
        if not a:
            continue
        a.update(symbol=sym, now_bps=rate, volume=vol, has_spot=has_spot)
        rows.append(a)

    if not rows:
        return (
            "screener: no candidates passed the filters.\n"
            f"filters: volume>${config.min_volume_usd:,.0f}, "
            f"|funding|>{config.min_abs_funding_bps}bps, require_spot={config.require_spot}"
        )

    passed = [r for r in rows if r["dominant_pct"] >= config.min_stability_pct]
    rejected = [r for r in rows if r["dominant_pct"] < config.min_stability_pct]

    # Rank by what actually matters: realised annualised carry from history.
    passed.sort(key=lambda r: -r["ann_hold_pct"])

    out.append("=== SCREEN ===")
    out.append(f"require_spot: {config.require_spot}   (false = naked perp, NOT a carry)")
    out.append(f"min_volume_usd: {config.min_volume_usd:,.0f}")
    out.append(f"min_abs_funding_bps: {config.min_abs_funding_bps}")
    out.append(f"min_stability_pct: {config.min_stability_pct}")
    out.append(f"scanned: {len(rows)}   passed: {len(passed)}   rejected_unstable: {len(rejected)}")

    out.append("")
    out.append("=== CANDIDATES (ranked by realised annualised carry) ===")
    if not passed:
        out.append("none — every candidate failed the sign-stability filter")
    for r in passed[: config.top_n]:
        side = "short_perp" if r["mean_bps"] > 0 else "long_perp"
        out.append("")
        out.append(f"symbol: {r['symbol']}")
        out.append(f"  side: {side}   (long spot + short perp = collect positive funding)")
        out.append(f"  funding_now_bps: {r['now_bps']:.3f}")
        out.append(f"  funding_mean_bps: {r['mean_bps']:.3f}   <- size on this, NOT on now")
        out.append(f"  mean_annualised_pct: {r['mean_bps'] * PERIODS_PER_YEAR / 100:.1f}%")
        out.append(f"  history_days: {r['days']:.0f}   periods: {r['periods']}")
        out.append(f"  sign_stability_pct: {r['dominant_pct']:.0f}")
        out.append(f"  sign_flips: {r['flips']}")
        out.append(f"  net_hold_annualised_pct: {r['ann_hold_pct']:.1f}   <- buy-and-hold, after 1 round trip")
        out.append(f"  volume_24h_usd: {r['volume']:,.0f}")
        days_to_cover = (
            COST_TAKER_BPS / abs(r["mean_bps"]) / 3 if r["mean_bps"] else float("inf")
        )
        out.append(f"  days_to_cover_taker_cost: {days_to_cover:.0f}")

    if rejected:
        out.append("")
        out.append("=== REJECTED — unstable sign (would churn fees) ===")
        for r in sorted(rejected, key=lambda x: -abs(x["now_bps"]))[:8]:
            out.append(
                f"  {r['symbol']}: now {r['now_bps']:+.2f}bps, "
                f"stability {r['dominant_pct']:.0f}%, flips {r['flips']} "
                f"-> hold-net {r['ann_hold_pct']:+.1f}%/yr"
            )
        out.append("  high funding on an unstable sign loses money — this is the main trap")

    out.append("")
    out.append("=== REMINDERS ===")
    out.append("hold_policy: CONTINUOUS — never exit on a single negative funding print")
    out.append(f"cost_round_trip_bps: taker {COST_TAKER_BPS:.0f} / maker {COST_MAKER_BPS:.0f}")
    out.append("spot_leg_required: a missing spot market means no hedge and no carry")

    return "\n".join(out)
