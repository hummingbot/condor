"""Scan & rank Meteora DAMM v2 (AMM) pools by fee yield for LP entry.

Agent-local routine for meteora_launch_lp. Sources pools directly from the **Meteora DAMM v2 data API**
(https://damm-v2.datapi.meteora.ag/pools) — NOT GeckoTerminal, which does not cover DAMM v2 AMM
pools well — filters to the configured quote asset, drops launch-fee-scheduler and unverified pools
by default, ranks by the API's native `fee_tvl_ratio` (fees(window)/TVL), and returns a shortlist
with the exact fields to act via `manage_amm` (connector=meteora, network=solana-mainnet-beta,
pool_address, base/quote mints).

Related DAMM v2 endpoints for deeper analysis (not used here, available to the agent):
- OHLCV:            https://docs.meteora.ag/api-reference/damm-v2/pools/ohlcv
- Historical volume: https://docs.meteora.ag/api-reference/damm-v2/pools/historical-volume

The scanner deliberately skips pools with an active fee scheduler (base fee often starts near 99%
and decays — a token-launch trap) unless include_launch_pools is set.
"""
import io
import logging

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

DAMM_V2_API = "https://damm-v2.datapi.meteora.ag/pools"
CONNECTOR = "meteora"
NETWORK = "solana-mainnet-beta"  # hummingbot-api / Gateway network id for manage_amm

_QUOTE_MINTS = {
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}
_WINDOWS = {"1h", "2h", "4h", "12h", "24h"}
_WINDOW_HOURS = {"1h": 1, "2h": 2, "4h": 4, "12h": 12, "24h": 24}


def _apr(fee_yield: float, window: str) -> float:
    """Annualize a window fee yield into a fee APR %.

    The Meteora API's fee_tvl_ratio is ALREADY a percentage per window
    (verified: fees.24h / tvl × 100 == fee_tvl_ratio.24h), so only scale by
    windows-per-year.
    """
    return fee_yield * (8760 / _WINDOW_HOURS.get(window, 24))


def _build_chart(ranked: list[dict], window: str) -> tuple[bytes | None, object]:
    """Horizontal fee-APR bar chart of the ranked pools (PNG bytes + plotly fig)."""
    try:
        import plotly.graph_objects as go
    except ImportError:
        return None, None

    pools = list(reversed(ranked))
    n = len(pools)
    labels, texts, vals, colors = [], [], [], []
    for i, c in enumerate(pools):
        rank = n - i
        detail = (f"TVL ${c['tvl']:,.0f} · vol {window} ${c['vol_win']:,.0f} · "
                  f"fee {c['base_fee_pct']:.2f}%")
        labels.append(f"#{rank}  {c['pair']}<br>"
                      f"<span style='color:#8b949e;font-size:9px'>{detail}</span>")
        apr = _apr(c["fee_yield"], window)
        vals.append(apr)
        texts.append(f"{apr:,.0f}%")
        colors.append("#d4a017" if rank <= 3 else "#8664c6")

    fig = go.Figure(go.Bar(
        y=labels, x=vals, orientation="h",
        marker=dict(color=colors, line=dict(width=0)),
        text=texts, textposition="outside",
        textfont=dict(size=11, color="#ffffff", family="monospace"),
        hovertemplate="<b>%{y}</b><br>Fee APR: %{text}<extra></extra>",
        showlegend=False,
    ))
    fig.update_layout(
        title=dict(text=f"Meteora DAMM v2 — Fee APR (annualized from {window} fees/TVL)",
                   font=dict(size=15, color="#ffffff"), x=0.5, xanchor="center"),
        height=max(450, n * 52 + 130), width=1100,
        paper_bgcolor="#1a1a2e", plot_bgcolor="#1a1a2e",
        font=dict(size=11, color="#e0e0e0"),
        margin=dict(l=300, r=90, t=70, b=40), bargap=0.3,
        xaxis=dict(showticklabels=False, showgrid=False, zeroline=False,
                   range=[0, (max(vals) if vals else 1) * 1.18]),
        yaxis=dict(showgrid=False, tickfont=dict(color="#e0e0e0", size=11)),
    )
    try:
        buf = io.BytesIO()
        fig.write_image(buf, format="png", scale=2)
        return buf.getvalue(), fig
    except Exception as e:
        logger.warning(f"damm_v2_scanner: PNG export failed: {e}")
        return None, fig


class Config(BaseModel):
    """Rank Meteora DAMM v2 pools by fee yield (fees/TVL) for AMM LP entry."""

    quote_asset: str = Field(default="SOL", description="Quote token to require on one side: SOL, USDC, or USDT")
    query: str | None = Field(default=None, description="Optional search (token symbol/name/address), e.g. 'JUP'")
    ranking_window: str = Field(default="24h", description="Fee-yield window: 1h, 2h, 4h, 12h, or 24h")
    top_n: int = Field(default=10, description="Number of ranked pools to return")
    min_tvl_usd: float = Field(default=25000.0, description="Minimum pool TVL in USD")
    include_launch_pools: bool = Field(default=False, description="Include active-fee-scheduler (launch) pools")
    verified_only: bool = Field(default=True, description="Require both tokens verified")
    exclude_pools: list[str] = Field(default=[], description="Pool addresses to exclude (already held)")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    quote = config.quote_asset.upper()
    if quote not in _QUOTE_MINTS:
        return f"damm_v2_scanner: unsupported quote_asset '{quote}' — supported: {', '.join(_QUOTE_MINTS)}."
    quote_mint = _QUOTE_MINTS[quote]

    window = config.ranking_window if config.ranking_window in _WINDOWS else "24h"

    # Fetch by TVL (surfaces REAL liquid pools) then rank by fee yield locally. Sorting the API by
    # fee_tvl_ratio instead floods the page with near-zero-TVL junk pools whose ratio is astronomical.
    params = {
        "page": 1,
        "page_size": 300,
        "sort_by": "tvl:desc",
        "filter_by": "is_blacklisted=false",
    }
    if config.query:
        params["query"] = config.query

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(DAMM_V2_API, params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    return f"damm_v2_scanner: Meteora DAMM v2 API returned HTTP {resp.status}"
                payload = await resp.json()
    except Exception as e:
        return f"damm_v2_scanner: failed to reach Meteora DAMM v2 API: {e}"

    pools = payload.get("data", [])
    excl = set(config.exclude_pools)
    candidates = []

    for p in pools:
        tx, ty = p.get("token_x", {}), p.get("token_y", {})
        mints = {tx.get("address"), ty.get("address")}
        if quote_mint not in mints:
            continue  # quote asset must be one side
        if p.get("address") in excl:
            continue
        if float(p.get("tvl") or 0) < config.min_tvl_usd:
            continue
        cfg = p.get("pool_config", {}) or {}
        if not config.include_launch_pools and cfg.get("is_fee_scheduler_active"):
            continue  # skip launch pools whose base fee starts ~99%
        if config.verified_only and not (tx.get("is_verified") and ty.get("is_verified")):
            continue

        # Base = the side that is NOT the quote asset.
        base, quote_tok = (ty, tx) if tx.get("address") == quote_mint else (tx, ty)
        fee_yield = float((p.get("fee_tvl_ratio") or {}).get(window) or 0)
        candidates.append({
            "pool": p.get("address"),
            "pair": p.get("name") or f"{base.get('symbol','?')}-{quote_tok.get('symbol','?')}",
            "base_symbol": base.get("symbol") or base.get("address", "")[:6],
            "quote_symbol": quote_tok.get("symbol") or quote,
            "base_mint": base.get("address"),
            "quote_mint": quote_tok.get("address"),
            "base_fee_pct": float(cfg.get("base_fee_pct") or 0),
            "tvl": float(p.get("tvl") or 0),
            "vol_win": float((p.get("volume") or {}).get(window) or 0),
            "fee_yield": fee_yield,
            "price": float(p.get("current_price") or 0),
        })

    if not candidates:
        return (f"damm_v2_scanner: no {quote}-quoted DAMM v2 pools passed the filters "
                f"(min TVL ${config.min_tvl_usd:,.0f}, verified_only={config.verified_only}, "
                f"launch_pools={config.include_launch_pools}).")

    candidates.sort(key=lambda c: c["fee_yield"], reverse=True)
    ranked = candidates[: config.top_n]

    columns = ["#", "Pair", "FeeYield", "BaseFee", "TVL", f"Vol{window}", "Price", "Pool", "BaseMint"]
    rows = []
    for i, c in enumerate(ranked, 1):
        rows.append({
            "#": i,
            "Pair": c["pair"],
            "FeeYield": f"{c['fee_yield']:.3f}%",
            "BaseFee": f"{c['base_fee_pct']:.3f}%",
            "TVL": f"${c['tvl']:,.0f}",
            f"Vol{window}": f"${c['vol_win']:,.0f}",
            "Price": f"{c['price']:.4g}",
            "Pool": c["pool"],
            "BaseMint": c["base_mint"],
            # manage_amm hints (constant across rows): connector=meteora, network=solana-mainnet-beta.
            "quote_mint": c["quote_mint"],
        })

    top = rows[0]
    summary = (
        f"Ranked {len(ranked)} {quote}-quoted Meteora DAMM v2 pools by fee yield ({window}) "
        f"from {len(candidates)} candidates. Top: **{top['Pair']}** — yield {top['FeeYield']}, "
        f"base fee {top['BaseFee']}, TVL {top['TVL']}, pool `{top['Pool']}`.\n"
        f"Act via manage_amm(connector='meteora', network='{NETWORK}', pool_address=<Pool>, "
        f"base_token=<BaseMint>, quote_token=<quote_mint>). Launch (fee-scheduler) pools "
        f"{'included' if config.include_launch_pools else 'excluded'}."
    )

    chart_bytes, plotly_fig = _build_chart(ranked, window)

    chat_id = getattr(context, "_chat_id", None)
    if chart_bytes and chat_id and context.bot:
        try:
            await context.bot.send_photo(
                chat_id=chat_id, photo=io.BytesIO(chart_bytes),
                caption=f"DAMM v2 fee-APR ranking — top {len(ranked)} {quote}-quoted pools",
            )
        except Exception as e:
            logger.warning(f"damm_v2_scanner: failed to send chart to Telegram: {e}")

    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder("DAMM v2 Fee-Yield Scanner")
        builder.source("routine", "damm_v2_scanner").tags(["meteora", "damm-v2", "lp", "yield"])
        builder.kpi("Pools ranked", str(len(ranked)))
        builder.kpi("Top fee APR", f"{_apr(ranked[0]['fee_yield'], window):,.0f}%")
        builder.kpi("Top pool TVL", f"${ranked[0]['tvl']:,.0f}")
        builder.markdown(
            f"{quote}-quoted Meteora DAMM v2 pools ranked by {window} fees/TVL "
            f"({len(candidates)} candidates passed filters; min TVL ${config.min_tvl_usd:,.0f}, "
            f"verified_only={config.verified_only}, launch pools "
            f"{'included' if config.include_launch_pools else 'excluded'})."
        )
        if plotly_fig is not None:
            builder.plotly(plotly_fig)
        builder.table(rows)
        builder.manual_order()
        await builder.save()
    except Exception as e:
        logger.warning(f"damm_v2_scanner: report save failed: {e}")

    try:
        from routines.base import RoutineResult
        return RoutineResult(text=summary, table_data=rows, table_columns=columns,
                             chart_image=chart_bytes)
    except Exception:
        lines = [summary, ""]
        for r in rows:
            lines.append(f"{r['#']}. {r['Pair']} | yield {r['FeeYield']} | fee {r['BaseFee']} | "
                         f"TVL {r['TVL']} | pool {r['Pool']} | base {r['BaseMint']}")
        return "\n".join(lines)
