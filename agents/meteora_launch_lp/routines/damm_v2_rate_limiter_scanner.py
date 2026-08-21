"""Scan Meteora DAMM v2 pools that have an active Alpha Vault (rate-limiter).

The Meteora DAMM v2 Alpha Vault is the canonical rate-limiting mechanism —
it gates early buys through a vault deposit/buy flow rather than letting bots
buy atomically at pool open. Pools with a real alpha_vault address (non-null,
i.e. not the Solana system program 11111...11111) have this feature active.

Fetches from the Meteora DAMM v2 data API, filters for pools with a valid
alpha_vault address, applies TVL/vol/quote filters, and ranks by the API's
native fee_tvl_ratio (fees/TVL per window).
"""

import io
import logging

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

DAMM_V2_API = "https://damm-v2.datapi.meteora.ag/pools"
NETWORK = "solana-mainnet-beta"

_QUOTE_MINTS = {
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}
_WINDOWS = {"1h", "2h", "4h", "12h", "24h"}
_WINDOW_HOURS = {"1h": 1, "2h": 2, "4h": 4, "12h": 12, "24h": 24}

# Solana system program — the null alpha_vault placeholder
_NULL_PUBKEY = "11111111111111111111111111111111"


def _has_alpha_vault(vault_addr: str | None) -> bool:
    """True only when pool has a real (non-null) Alpha Vault address."""
    if not vault_addr:
        return False
    s = vault_addr.strip()
    if not s or s == _NULL_PUBKEY:
        return False
    if all(c == "1" for c in s):
        return False
    return True


def _apr(fee_yield: float, window: str) -> float:
    return fee_yield * (8760 / _WINDOW_HOURS.get(window, 24))


def _build_chart(ranked: list[dict], window: str):
    import plotly.graph_objects as go

    pools = list(reversed(ranked))
    n = len(pools)
    labels, texts, vals, colors = [], [], [], []
    for i, c in enumerate(pools):
        rank = n - i
        detail = (
            f"TVL ${c['tvl']:,.0f} · vol {window} ${c['vol_win']:,.0f} · "
            f"fee {c['base_fee_pct']:.2f}%"
        )
        labels.append(
            f"#{rank}  {c['pair']}<br>"
            f"<span style='color:#8b949e;font-size:9px'>{detail}</span>"
        )
        apr = _apr(c["fee_yield"], window)
        vals.append(apr)
        texts.append(f"{apr:,.0f}%")
        colors.append("#d4a017" if rank <= 3 else "#8664c6")

    fig = go.Figure(
        go.Bar(
            y=labels,
            x=vals,
            orientation="h",
            marker=dict(color=colors, line=dict(width=0)),
            text=texts,
            textposition="outside",
            textfont=dict(size=11, color="#ffffff", family="monospace"),
            hovertemplate="<b>%{y}</b><br>Fee APR: %{text}<extra></extra>",
            showlegend=False,
        )
    )
    fig.update_layout(
        title=dict(
            text=f"Meteora DAMM v2 Alpha Vault Pools — Fee APR (annualized from {window})",
            font=dict(size=15, color="#ffffff"),
            x=0.5,
            xanchor="center",
        ),
        height=max(450, n * 52 + 130),
        width=1100,
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#1a1a2e",
        font=dict(size=11, color="#e0e0e0"),
        margin=dict(l=300, r=90, t=70, b=40),
        bargap=0.3,
        xaxis=dict(
            showticklabels=False,
            showgrid=False,
            zeroline=False,
            range=[0, (max(vals) if vals else 1) * 1.18],
        ),
        yaxis=dict(showgrid=False, tickfont=dict(color="#e0e0e0", size=11)),
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
    )
    buf = io.BytesIO()
    fig.write_image(buf, format="png", scale=2)
    return buf.getvalue(), fig


class Config(BaseModel):
    """Rank Meteora DAMM v2 pools with Alpha Vault (rate-limiter) by fee yield."""

    quote_asset: str = Field(
        default="SOL", description="Quote token: SOL, USDC, or USDT"
    )
    ranking_window: str = Field(
        default="24h", description="Fee-yield window: 1h, 2h, 4h, 12h, or 24h"
    )
    top_n: int = Field(default=10, description="Number of ranked pools to return")
    min_tvl_usd: float = Field(default=25000.0, description="Minimum pool TVL in USD")
    min_vol24h_usd: float = Field(
        default=5000.0, description="Minimum 24h volume in USD"
    )
    include_launch_pools: bool = Field(
        default=False, description="Include pools with active fee-scheduler"
    )
    verified_only: bool = Field(
        default=False, description="Require both tokens verified"
    )
    exclude_pools: list[str] = Field(
        default=[], description="Pool addresses to skip (already held)"
    )


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    quote = config.quote_asset.upper()
    if quote not in _QUOTE_MINTS:
        return (
            f"damm_v2_rate_limiter_scanner: unsupported quote_asset '{quote}' "
            f"— supported: {', '.join(_QUOTE_MINTS)}."
        )
    quote_mint = _QUOTE_MINTS[quote]
    window = config.ranking_window if config.ranking_window in _WINDOWS else "24h"

    params = {
        "page": 1,
        "page_size": 500,
        "sort_by": "tvl:desc",
        "filter_by": "is_blacklisted=false",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                DAMM_V2_API, params=params, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status != 200:
                    return f"damm_v2_rate_limiter_scanner: Meteora API returned HTTP {resp.status}"
                payload = await resp.json()
    except Exception as e:
        return f"damm_v2_rate_limiter_scanner: failed to reach Meteora DAMM v2 API: {e}"

    pools = payload.get("data", [])
    excl = set(config.exclude_pools)
    candidates = []

    for p in pools:
        if not _has_alpha_vault(p.get("alpha_vault")):
            continue

        tx = p.get("token_x") or {}
        ty = p.get("token_y") or {}
        mints = {tx.get("address"), ty.get("address")}
        if quote_mint not in mints:
            continue
        if p.get("address") in excl:
            continue

        tvl = float(p.get("tvl") or 0)
        if tvl < config.min_tvl_usd:
            continue

        vol24h = float((p.get("volume") or {}).get("24h") or 0)
        if vol24h < config.min_vol24h_usd:
            continue

        cfg = p.get("pool_config") or {}
        if not config.include_launch_pools and cfg.get("is_fee_scheduler_active"):
            continue
        if config.verified_only and not (
            tx.get("is_verified") and ty.get("is_verified")
        ):
            continue

        base, quote_tok = (ty, tx) if tx.get("address") == quote_mint else (tx, ty)
        fee_yield = float((p.get("fee_tvl_ratio") or {}).get(window) or 0)
        vol_win = float((p.get("volume") or {}).get(window) or 0)

        candidates.append(
            {
                "pool": p.get("address"),
                "pair": p.get("name")
                or f"{base.get('symbol', '?')}-{quote_tok.get('symbol', '?')}",
                "base_mint": base.get("address"),
                "quote_mint": quote_tok.get("address"),
                "base_fee_pct": float(cfg.get("base_fee_pct") or 0),
                "tvl": tvl,
                "vol_win": vol_win,
                "vol24h": vol24h,
                "fee_yield": fee_yield,
                "price": float(p.get("current_price") or 0),
                "alpha_vault": p.get("alpha_vault") or "",
            }
        )

    if not candidates:
        return (
            f"damm_v2_rate_limiter_scanner: no {quote}-quoted DAMM v2 Alpha Vault pools passed filters "
            f"(min TVL ${config.min_tvl_usd:,.0f}, min vol24h ${config.min_vol24h_usd:,.0f}, "
            f"verified_only={config.verified_only})."
        )

    candidates.sort(key=lambda c: c["fee_yield"], reverse=True)
    ranked = candidates[: config.top_n]

    columns = [
        "#",
        "Pair",
        "FeeYield",
        "BaseFee",
        "TVL",
        f"Vol{window}",
        "Price",
        "Pool",
        "BaseMint",
        "AlphaVault",
    ]
    rows = []
    for i, c in enumerate(ranked, 1):
        av = c["alpha_vault"]
        rows.append(
            {
                "#": i,
                "Pair": c["pair"],
                "FeeYield": f"{c['fee_yield']:.3f}%",
                "BaseFee": f"{c['base_fee_pct']:.3f}%",
                "TVL": f"${c['tvl']:,.0f}",
                f"Vol{window}": f"${c['vol_win']:,.0f}",
                "Price": f"{c['price']:.4g}",
                "Pool": c["pool"],
                "BaseMint": c["base_mint"],
                "AlphaVault": (av[:8] + "…") if len(av) > 8 else av,
                "quote_mint": c["quote_mint"],
            }
        )

    top = rows[0]
    summary = (
        f"Found {len(candidates)} {quote}-quoted DAMM v2 pools with Alpha Vault (rate-limiter). "
        f"Ranked top {len(ranked)} by {window} fee yield. "
        f"Top: **{top['Pair']}** — yield {top['FeeYield']}, base fee {top['BaseFee']}, "
        f"TVL {top['TVL']}, pool `{top['Pool']}`.\n"
        f"Act via manage_amm(connector='meteora', network='{NETWORK}', "
        f"pool_address=<Pool>, base_token=<BaseMint>, quote_token=<quote_mint>)."
    )

    chart_bytes, plotly_fig = _build_chart(ranked, window)

    chat_id = getattr(context, "_chat_id", None)
    if chart_bytes and chat_id and context.bot:
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=io.BytesIO(chart_bytes),
                caption=f"DAMM v2 Alpha Vault pools — top {len(ranked)} {quote}-quoted by fee APR",
            )
        except Exception as e:
            logger.warning(f"damm_v2_rate_limiter_scanner: chart send failed: {e}")

    from condor.reports import ReportBuilder

    builder = ReportBuilder("DAMM v2 Alpha Vault (Rate-Limiter) Pool Scanner")
    builder.source("routine", "damm_v2_rate_limiter_scanner")
    builder.tags(["meteora", "damm-v2", "alpha-vault", "rate-limiter", "lp", "yield"])
    builder.kpi("Alpha Vault Pools Found", str(len(candidates)))
    builder.kpi("Top Fee APR", f"{_apr(ranked[0]['fee_yield'], window):,.0f}%")
    builder.kpi("Top Pool TVL", f"${ranked[0]['tvl']:,.0f}")
    builder.markdown(
        f"Meteora DAMM v2 pools with an active Alpha Vault (rate-limiter), "
        f"ranked by {window} fees/TVL. Filters: quote={quote}, "
        f"min TVL ${config.min_tvl_usd:,.0f}, min vol24h ${config.min_vol24h_usd:,.0f}, "
        f"launch pools {'included' if config.include_launch_pools else 'excluded'}, "
        f"verified_only={config.verified_only}."
    )
    builder.plotly(plotly_fig)
    builder.table(rows, columns)
    builder.manual_order()
    await builder.save()

    from routines.base import RoutineResult

    return RoutineResult(
        text=summary,
        table_data=rows,
        table_columns=columns,
        chart_image=chart_bytes,
    )
