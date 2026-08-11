"""Detect fresh EasyA launchpad graduations into Meteora DAMM v2, for early-LP entry.

Agent-local routine for meteora_launch_lp. EasyA-graduated tokens carry a vanity mint suffix
**`EASY`** and land in a **SOL-quoted, 2% static-fee** DAMM v2 pool (no fee scheduler), so graduations
are discoverable directly off the Meteora DAMM v2 data API (https://damm-v2.datapi.meteora.ag/pools)
— NO EasyA API needed. This routine filters the pool feed to `*EASY` base mints, recent `created_at`,
and TVL/volume floors, then ranks by 24h fee yield (traction) so the agent LPs the ones actually
earning rather than every graduation.

It does NOT decide entry — the agent still gates each candidate on sellability (honeypot), real
post-graduation demand, and quality, and must wait for the initial dump to clear before providing
liquidity. See AGENT.md.

Note: early LP here is directional-long the token (you must buy the base token to pair it with SOL),
so size small and capped.
"""
import io
import logging
import time
from datetime import datetime, timezone

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

DAMM_V2_API = "https://damm-v2.datapi.meteora.ag/pools"
CONNECTOR = "meteora"
NETWORK = "solana-mainnet-beta"  # hummingbot-api / Gateway network id for manage_amm
SOL_MINT = "So11111111111111111111111111111111111111112"
EASYA_MINT_SUFFIX = "EASY"  # EasyA vanity-suffix marker on graduated token mints


GECKO_OHLCV = "https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/hour"


async def _fetch_ohlcv(session: aiohttp.ClientSession, pool: str, hours: float) -> tuple[list[dict], str]:
    """Hourly OHLCV bars covering the pool's whole post-graduation life.

    Primary: GeckoTerminal (up to 1000 hourly bars, price in USD). Fallback: the Meteora
    DAMM v2 data API's native OHLCV (price in SOL), which caps at ~10 bars per response,
    so a coarse timeframe is chosen to still span the pool's age.
    Returns (bars, price_unit) where bars are {timestamp, open, high, low, close, volume}.
    """
    limit = max(12, min(int(hours) + 4, 1000))
    try:
        async with session.get(GECKO_OHLCV.format(pool=pool), params={"limit": limit},
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                ol = (await resp.json())["data"]["attributes"]["ohlcv_list"]
                if ol:
                    bars = [{"timestamp": b[0], "open": b[1], "high": b[2], "low": b[3],
                             "close": b[4], "volume": b[5]} for b in reversed(ol)]
                    return bars, "USD"
    except Exception as e:
        logger.warning(f"easya_graduation_monitor: GeckoTerminal OHLCV failed for {pool}: {e}")

    # Fallback: Meteora native (≈10 bars max) — pick a timeframe wide enough to span the age.
    timeframe = "1h" if hours <= 10 else "2h" if hours <= 20 else "4h" if hours <= 40 else "12h"
    try:
        async with session.get(f"{DAMM_V2_API}/{pool}/ohlcv", params={"timeframe": timeframe},
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return [], ""
            return (await resp.json()).get("data", []), "SOL"
    except Exception as e:
        logger.warning(f"easya_graduation_monitor: Meteora OHLCV failed for {pool}: {e}")
        return [], ""


def _build_chart(top: dict, bars: list[dict], price_unit: str = "SOL") -> tuple[bytes | None, object]:
    """Price + volume panel for the top graduation — shows the post-graduation
    dump/stabilization path the agent must wait out before LPing."""
    if not bars:
        return None, None
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        return None, None

    ts = [datetime.fromtimestamp(b["timestamp"], tz=timezone.utc) for b in bars]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.72, 0.28], vertical_spacing=0.04)
    fig.add_trace(go.Candlestick(
        x=ts, open=[b["open"] for b in bars], high=[b["high"] for b in bars],
        low=[b["low"] for b in bars], close=[b["close"] for b in bars],
        increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        name=f"Price ({price_unit})", showlegend=False,
    ), row=1, col=1)
    fig.add_trace(go.Bar(
        x=ts, y=[b["volume"] for b in bars],
        marker_color="#8664c6", name="Volume", showlegend=False,
    ), row=2, col=1)
    fig.update_layout(
        title=dict(
            text=(f"{top['pair']} — post-graduation price path "
                  f"(age {top['age_h']:.0f}h · TVL ${top['tvl']:,.0f} · "
                  f"fee yield 24h {top['fee_yield24h']:.2f}%)"),
            font=dict(size=14, color="#ffffff"), x=0.5, xanchor="center"),
        height=560, width=1100,
        paper_bgcolor="#1a1a2e", plot_bgcolor="#1a1a2e",
        font=dict(size=11, color="#e0e0e0"),
        margin=dict(l=70, r=30, t=70, b=40),
        xaxis_rangeslider_visible=False,
        yaxis=dict(title=f"Price ({price_unit})", showgrid=True, gridcolor="#2a2a3e",
                   tickformat=".2e"),
        yaxis2=dict(title=f"Vol ({price_unit})", showgrid=False),
        xaxis2=dict(showgrid=False),
    )
    try:
        buf = io.BytesIO()
        fig.write_image(buf, format="png", scale=2)
        return buf.getvalue(), fig
    except Exception as e:
        logger.warning(f"easya_graduation_monitor: PNG export failed: {e}")
        return None, fig


class Config(BaseModel):
    """Detect fresh EasyA graduations into Meteora DAMM v2, ranked by fee yield."""

    max_age_hours: float = Field(default=72.0, description="Only pools created within this many hours")
    min_tvl_usd: float = Field(default=10000.0, description="Minimum pool TVL in USD (graduation liquidity floor)")
    min_vol24h_usd: float = Field(default=3000.0, description="Minimum 24h volume in USD (real post-grad demand)")
    verified_only: bool = Field(default=False, description="Require the graduated token to be verified")
    require_static_fee: bool = Field(default=True, description="Exclude fee-scheduler pools (EasyA grads are static)")
    top_n: int = Field(default=15, description="Number of ranked graduations to return")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    # No created_at sort on the API, so fetch a wide TVL-sorted page and filter locally.
    # EasyA pools graduate with ~$10k+ TVL, so they are within the top page by TVL.
    params = {"page": 1, "page_size": 1000, "sort_by": "tvl:desc", "filter_by": "is_blacklisted=false"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(DAMM_V2_API, params=params, timeout=aiohttp.ClientTimeout(total=25)) as resp:
                if resp.status != 200:
                    return f"easya_graduation_monitor: Meteora DAMM v2 API returned HTTP {resp.status}"
                payload = await resp.json()
    except Exception as e:
        return f"easya_graduation_monitor: failed to reach Meteora DAMM v2 API: {e}"

    now_ms = time.time() * 1000.0
    max_age_ms = config.max_age_hours * 3.6e6
    candidates = []

    for p in payload.get("data", []):
        tx, ty = p.get("token_x", {}), p.get("token_y", {})
        # Identify the EasyA token side (mint suffix EASY, not SOL); quote must be SOL.
        easy_side = None
        for side in (tx, ty):
            addr = side.get("address", "")
            if addr != SOL_MINT and addr.upper().endswith(EASYA_MINT_SUFFIX):
                easy_side = side
        if easy_side is None:
            continue
        mints = {tx.get("address"), ty.get("address")}
        if SOL_MINT not in mints:
            continue  # EasyA graduations are SOL-quoted

        created = p.get("created_at")
        age_h = (now_ms - created) / 3.6e6 if created else None
        if age_h is None or (now_ms - created) > max_age_ms:
            continue
        if float(p.get("tvl") or 0) < config.min_tvl_usd:
            continue
        if float((p.get("volume") or {}).get("24h") or 0) < config.min_vol24h_usd:
            continue
        cfg = p.get("pool_config", {}) or {}
        if config.require_static_fee and (cfg.get("is_fee_scheduler_active") or cfg.get("has_fee_scheduler")):
            continue
        if config.verified_only and not easy_side.get("is_verified"):
            continue

        candidates.append({
            "pool": p.get("address"),
            "pair": p.get("name") or f"{easy_side.get('symbol','?')}-SOL",
            "token_symbol": easy_side.get("symbol") or easy_side.get("address", "")[:6],
            "base_mint": easy_side.get("address"),
            "verified": bool(easy_side.get("is_verified")),
            "base_fee_pct": float(cfg.get("base_fee_pct") or 0),
            "tvl": float(p.get("tvl") or 0),
            "vol24h": float((p.get("volume") or {}).get("24h") or 0),
            "fee_yield24h": float((p.get("fee_tvl_ratio") or {}).get("24h") or 0),
            "age_h": age_h,
            "price": float(p.get("current_price") or 0),
        })

    if not candidates:
        return (f"easya_graduation_monitor: no EasyA graduations in the last {config.max_age_hours:.0f}h "
                f"passed the filters (min TVL ${config.min_tvl_usd:,.0f}, min vol24h ${config.min_vol24h_usd:,.0f}, "
                f"verified_only={config.verified_only}).")

    # Rank by traction: 24h fee yield (fees/TVL) — the ones actually earning.
    candidates.sort(key=lambda c: c["fee_yield24h"], reverse=True)
    ranked = candidates[: config.top_n]

    columns = ["#", "Pair", "Age(h)", "FeeYield", "TVL", "Vol24h", "Verified", "Price", "Pool", "BaseMint"]
    rows = []
    for i, c in enumerate(ranked, 1):
        rows.append({
            "#": i,
            "Pair": c["pair"],
            "Age(h)": f"{c['age_h']:.1f}",
            "FeeYield": f"{c['fee_yield24h']:.2f}%",
            "TVL": f"${c['tvl']:,.0f}",
            "Vol24h": f"${c['vol24h']:,.0f}",
            "Verified": "yes" if c["verified"] else "no",
            "Price": f"{c['price']:.4g}",
            "Pool": c["pool"],
            "BaseMint": c["base_mint"],
            "quote_mint": SOL_MINT,
        })

    top = rows[0]
    summary = (
        f"Found {len(ranked)} EasyA→DAMM v2 graduations (< {config.max_age_hours:.0f}h) passing filters, "
        f"from the pool feed. Top by fee yield: **{top['Pair']}** — yield {top['FeeYield']}, "
        f"age {top['Age(h)']}h, TVL {top['TVL']}, verified {top['Verified']}, pool `{top['Pool']}`.\n"
        f"GATE each before LPing: sellability (SELL+BUY quote both return), real post-dump demand, quality. "
        f"Then early add_liquidity(connector='meteora', network='{NETWORK}', pool_address=<Pool>) — small, "
        f"capped size (directional-long the token). Journal the position_address."
    )

    # Price/volume panel for the top graduation — the visual gate for "has the dump cleared?"
    top_c = ranked[0]
    async with aiohttp.ClientSession() as session:
        bars, price_unit = await _fetch_ohlcv(session, top_c["pool"], top_c["age_h"])
    chart_bytes, plotly_fig = _build_chart(top_c, bars, price_unit)

    chat_id = getattr(context, "_chat_id", None)
    if chart_bytes and chat_id and context.bot:
        try:
            await context.bot.send_photo(
                chat_id=chat_id, photo=io.BytesIO(chart_bytes),
                caption=f"{top_c['pair']} — post-graduation price path ({top_c['age_h']:.0f}h)",
            )
        except Exception as e:
            logger.warning(f"easya_graduation_monitor: failed to send chart to Telegram: {e}")

    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder("EasyA Graduation Monitor")
        builder.source("routine", "easya_graduation_monitor").tags(
            ["meteora", "damm-v2", "easya", "launch"])
        builder.kpi("Graduations found", str(len(ranked)))
        builder.kpi("Top fee yield 24h", top["FeeYield"])
        builder.kpi("Top age", f"{top['Age(h)']}h")
        builder.markdown(
            f"EasyA graduations into Meteora DAMM v2 in the last {config.max_age_hours:.0f}h "
            f"(min TVL ${config.min_tvl_usd:,.0f}, min vol24h ${config.min_vol24h_usd:,.0f}). "
            f"Gate each on sellability, safety, and post-dump demand before LPing."
        )
        if plotly_fig is not None:
            builder.plotly(plotly_fig)
        builder.table(rows)
        builder.manual_order()
        await builder.save()
    except Exception as e:
        logger.warning(f"easya_graduation_monitor: report save failed: {e}")

    try:
        from routines.base import RoutineResult
        return RoutineResult(text=summary, table_data=rows, table_columns=columns,
                             chart_image=chart_bytes)
    except Exception:
        lines = [summary, ""]
        for r in rows:
            lines.append(f"{r['#']}. {r['Pair']} | age {r['Age(h)']}h | yield {r['FeeYield']} | "
                         f"TVL {r['TVL']} | vol {r['Vol24h']} | verified {r['Verified']} | pool {r['Pool']}")
        return "\n".join(lines)
