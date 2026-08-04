CATEGORY = "Market Data"

import asyncio
import io
import logging
import re
from typing import Optional

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

GECKO_BASE = "https://api.geckoterminal.com/api/v2"
NETWORK = "solana"
PAGES_PER_DEX = 3

DEX_IDS = {
    "orca": ["orca-whirlpool"],
    "raydium": ["raydium-clmm", "raydium"],
    "meteora": ["meteora-dlmm", "meteora"],
}

DEX_COLORS = {
    "orca": "#FFD700",
    "raydium": "#9945FF",
    "meteora": "#00D1FF",
}


class Config(BaseModel):
    """Scan top Solana CLMM pools on Orca, Raydium & Meteora via GeckoTerminal."""

    min_volume_24h: float = Field(default=50_000, description="Min 24h volume (USD)")
    min_tvl: float = Field(default=25_000, description="Min TVL (USD)")
    sort_by: str = Field(
        default="fee_tvl_ratio",
        description="Ranking: fee_tvl_ratio, volume, tvl, vol_tvl_ratio",
    )
    top_n: int = Field(default=15, description="Number of pools to show")
    dex_filter: Optional[str] = Field(
        default=None, description="Filter single DEX: orca, raydium, meteora"
    )
    search_token: Optional[str] = Field(
        default=None, description="Filter pools containing this token symbol"
    )
    pages: int = Field(
        default=PAGES_PER_DEX, description="Pages to fetch per DEX (20 pools/page)"
    )


async def _fetch_pools_page(
    session: aiohttp.ClientSession, dex_id: str, page: int
) -> list[dict]:
    url = f"{GECKO_BASE}/networks/{NETWORK}/dexes/{dex_id}/pools"
    params = {"page": str(page)}
    try:
        async with session.get(
            url, params=params, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status != 200:
                logger.warning(f"GeckoTerminal {dex_id} p{page}: HTTP {resp.status}")
                return []
            body = await resp.json()
            return body.get("data", [])
    except Exception as e:
        logger.warning(f"GeckoTerminal {dex_id} p{page} failed: {e}")
        return []


async def _fetch_all_pools(
    session: aiohttp.ClientSession, dex_ids: list[str], pages: int
) -> list[dict]:
    tasks = [
        _fetch_pools_page(session, dex_id, page)
        for dex_id in dex_ids
        for page in range(1, pages + 1)
    ]
    results = await asyncio.gather(*tasks)
    pools = []
    for batch in results:
        pools.extend(batch)
    return pools


def _extract_fee_pct(name: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)%", name)
    if m:
        return float(m.group(1)) / 100
    return 0.0025


def _fmt_price(raw: str) -> str:
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return raw
    if val >= 1000:
        return f"{val:,.0f}"
    if val >= 1:
        return f"{val:.2f}"
    if val >= 0.001:
        return f"{val:.4f}"
    if val >= 0.000001:
        return f"{val:.6f}"
    return f"{val:.2e}"


def _fmt_usd(val: float) -> str:
    if val >= 1_000_000:
        return f"${val / 1_000_000:.1f}M"
    if val >= 1_000:
        return f"${val / 1_000:.1f}K"
    return f"${val:.0f}"


def _fmt_pct(val: float) -> str:
    if abs(val) >= 1000:
        return f"{val:,.0f}%"
    if abs(val) >= 10:
        return f"{val:.0f}%"
    return f"{val:.1f}%"


def _dex_family(dex_id: str) -> str:
    for family, ids in DEX_IDS.items():
        if dex_id in ids:
            return family
    return "other"


DEX_POOL_URLS = {
    "raydium-clmm": "https://raydium.io/clmm/create-position/?pool_id={addr}",
    "raydium": "https://raydium.io/liquidity/increase/?pool_id={addr}",
    "orca-whirlpool": "https://www.orca.so/pools/{addr}",
}


def _pool_url(dex_id: str, address: str) -> str:
    template = DEX_POOL_URLS.get(dex_id)
    if template:
        return template.format(addr=address)
    return f"https://www.geckoterminal.com/solana/pools/{address}"


def _parse_pool(raw: dict) -> dict | None:
    attr = raw.get("attributes", {})
    name = attr.get("name", "")
    volume_24h = float(attr.get("volume_usd", {}).get("h24", 0) or 0)
    tvl = float(attr.get("reserve_in_usd", 0) or 0)

    fee_pct = _extract_fee_pct(name)
    fee_24h = volume_24h * fee_pct
    fee_tvl_ratio = (fee_24h / tvl * 365) if tvl > 0 else 0
    vol_tvl_ratio = (volume_24h / tvl) if tvl > 0 else 0

    base_price = attr.get("base_token_price_usd", "?")

    price_change_24h = attr.get("price_change_percentage", {})
    pct_24h = float(price_change_24h.get("h24", 0) or 0)

    dex_rel = raw.get("relationships", {}).get("dex", {}).get("data", {})
    dex_id = dex_rel.get("id", "unknown") if dex_rel else "unknown"
    dex_label = dex_id.replace("-", " ").title()
    dex_family = _dex_family(dex_id)

    pool_address = attr.get("address", "")
    clean_name = re.sub(r"\s*\d+(\.\d+)?%\s*$", "", name).strip()

    return {
        "name": clean_name,
        "dex": dex_label,
        "dex_id": dex_id,
        "dex_family": dex_family,
        "volume_24h": volume_24h,
        "tvl": tvl,
        "fee_pct": fee_pct,
        "fee_24h": fee_24h,
        "fee_tvl_ratio": fee_tvl_ratio,
        "vol_tvl_ratio": vol_tvl_ratio,
        "base_price": base_price,
        "pct_24h": pct_24h,
        "address": pool_address,
    }


def _build_chart(top: list[dict], sort_by: str) -> tuple[bytes | None, any]:
    try:
        import plotly.graph_objects as go
    except ImportError:
        return None, None

    pools = list(reversed(top))
    n = len(pools)

    # Two-line labels: pool name + details, all baked into the PNG
    labels = []
    for i, p in enumerate(pools):
        rank = n - i
        dex_short = p["dex_family"].capitalize()
        detail = f"Vol: {_fmt_usd(p['volume_24h'])} · TVL: {_fmt_usd(p['tvl'])} · V/T: {p['vol_tvl_ratio']:.1f}x"
        labels.append(
            f"#{rank}  {p['name']}  ({dex_short})<br><span style='color:#8b949e;font-size:9px'>{detail}</span>"
        )

    colors = [DEX_COLORS.get(p["dex_family"], "#888888") for p in pools]

    # Pick primary bar metric based on sort
    if sort_by == "volume":
        bar_vals = [p["volume_24h"] for p in pools]
        bar_texts = [_fmt_usd(v) for v in bar_vals]
        metric_label = "Volume 24h"
    elif sort_by == "tvl":
        bar_vals = [p["tvl"] for p in pools]
        bar_texts = [_fmt_usd(v) for v in bar_vals]
        metric_label = "TVL"
    elif sort_by == "vol_tvl_ratio":
        bar_vals = [p["vol_tvl_ratio"] for p in pools]
        bar_texts = [f"{v:.1f}x" for v in bar_vals]
        metric_label = "Vol/TVL Ratio"
    else:
        bar_vals = [p["fee_tvl_ratio"] for p in pools]
        bar_texts = [_fmt_pct(v) for v in bar_vals]
        metric_label = "Fee APR (est.)"

    fig = go.Figure()

    fig.add_trace(
        go.Bar(
            y=labels,
            x=bar_vals,
            orientation="h",
            marker=dict(color=colors, line=dict(width=0)),
            text=bar_texts,
            textposition="outside",
            textfont=dict(size=11, color="#ffffff", family="monospace"),
            hovertemplate="<b>%{y}</b><br>" + metric_label + ": %{text}<extra></extra>",
            showlegend=False,
        )
    )

    # DEX legend
    for family, color in DEX_COLORS.items():
        fig.add_trace(
            go.Bar(
                y=[None],
                x=[None],
                orientation="h",
                marker_color=color,
                name=family.capitalize(),
                showlegend=True,
            )
        )

    max_val = max(bar_vals) if bar_vals else 1
    height = max(550, n * 56 + 140)

    fig.update_layout(
        title=dict(
            text=f"Solana Pool Scanner — Top {n} by {metric_label}",
            font=dict(size=16, color="#ffffff"),
            x=0.5,
            xanchor="center",
        ),
        height=height,
        width=1100,
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#1a1a2e",
        font=dict(size=11, color="#e0e0e0"),
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.04,
            xanchor="center",
            x=0.5,
            font=dict(size=12),
            bgcolor="rgba(0,0,0,0)",
        ),
        margin=dict(l=340, r=80, t=80, b=50),
        bargap=0.3,
        xaxis=dict(
            showticklabels=False,
            showgrid=False,
            zeroline=False,
            range=[0, max_val * 1.2],
        ),
        yaxis=dict(
            showgrid=False,
            tickfont=dict(color="#e0e0e0", size=11),
        ),
    )

    buf = io.BytesIO()
    fig.write_image(buf, format="png", scale=2)
    buf.seek(0)
    return buf.getvalue(), fig


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    from routines.base import RoutineResult

    if config.dex_filter:
        key = config.dex_filter.lower()
        if key not in DEX_IDS:
            return f"Unknown DEX: {key}. Options: orca, raydium, meteora"
        dex_ids = DEX_IDS[key]
    else:
        dex_ids = [did for ids in DEX_IDS.values() for did in ids]

    async with aiohttp.ClientSession() as session:
        raw_pools = await _fetch_all_pools(session, dex_ids, config.pages)

    all_pools = [p for raw in raw_pools if (p := _parse_pool(raw))]

    filtered = [
        p
        for p in all_pools
        if p["volume_24h"] >= config.min_volume_24h and p["tvl"] >= config.min_tvl
    ]

    if config.search_token:
        token = config.search_token.upper()
        filtered = [p for p in filtered if token in p["name"].upper()]

    if not filtered:
        return "No pools match the filters. Try lowering min_volume or min_tvl."

    seen = set()
    unique = []
    for p in filtered:
        if p["address"] not in seen:
            seen.add(p["address"])
            unique.append(p)
    filtered = unique

    sort_keys = {
        "volume": lambda p: p["volume_24h"],
        "tvl": lambda p: p["tvl"],
        "vol_tvl_ratio": lambda p: p["vol_tvl_ratio"],
        "fee_tvl_ratio": lambda p: p["fee_tvl_ratio"],
    }
    filtered.sort(
        key=sort_keys.get(config.sort_by, sort_keys["fee_tvl_ratio"]), reverse=True
    )
    top = filtered[: config.top_n]

    dex_counts = {}
    for p in filtered:
        dex_counts[p["dex_family"]] = dex_counts.get(p["dex_family"], 0) + 1

    lines = [f"Solana Pool Scanner — Top {len(top)} by {config.sort_by}\n"]
    for i, p in enumerate(top, 1):
        fee_str = f"{p['fee_pct'] * 100:.2f}".rstrip("0").rstrip(".")
        chg = p["pct_24h"]
        chg_str = f"+{chg:.1f}%" if chg >= 0 else f"{chg:.1f}%"
        lines.append(
            f"{i}. {p['name']} ({p['dex']}) [{fee_str}% fee]\n"
            f"   Vol: {_fmt_usd(p['volume_24h'])} | TVL: {_fmt_usd(p['tvl'])} | "
            f"V/T: {p['vol_tvl_ratio']:.1f}x\n"
            f"   Fee APR: {_fmt_pct(p['fee_tvl_ratio'])} | "
            f"Price: ${_fmt_price(p['base_price'])} ({chg_str})"
        )

    dist_str = " | ".join(f"{k}: {v}" for k, v in sorted(dex_counts.items()))
    lines.append(f"\nScanned {len(all_pools)} pools, {len(filtered)} passed filters.")
    lines.append(f"Distribution: {dist_str}")

    report_text = "\n".join(lines)

    chart_bytes, plotly_fig = _build_chart(top, config.sort_by)

    chat_id = getattr(context, "_chat_id", None)
    if chart_bytes and chat_id and context.bot:
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=io.BytesIO(chart_bytes),
                caption=f"Solana Pool Scanner — Top {len(top)} pools",
            )
        except Exception as e:
            logger.warning(f"Failed to send chart to Telegram: {e}")

    # Report: chart FIRST, then table
    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder("Solana Pool Scanner")
        builder.source("routine", "solana_pool_scanner").tags(
            ["solana", "dex", "pools", "yield"]
        )

        builder.markdown(
            f"Scanned {len(all_pools)} pools across Orca, Raydium, Meteora. "
            f"{len(filtered)} passed filters (vol >= {_fmt_usd(config.min_volume_24h)}, "
            f"tvl >= {_fmt_usd(config.min_tvl)}). Distribution: {dist_str}"
        )

        if plotly_fig is not None:
            builder.plotly(plotly_fig)

        builder.table(
            [
                {
                    "#": i,
                    "Pool": p["name"],
                    "DEX": p["dex"],
                    "Vol 24h": _fmt_usd(p["volume_24h"]),
                    "TVL": _fmt_usd(p["tvl"]),
                    "V/T": f"{p['vol_tvl_ratio']:.1f}x",
                    "Fee": f"{p['fee_pct'] * 100:.2f}".rstrip("0").rstrip(".") + "%",
                    "Fee APR": _fmt_pct(p["fee_tvl_ratio"]),
                    "Price": f"${_fmt_price(p['base_price'])}",
                    "24h": f"{p['pct_24h']:+.1f}%",
                    "Link": f'<a href="{_pool_url(p["dex_id"], p["address"])}" target="_blank" title="Open on {p["dex_family"].capitalize()}">&#x1F517;</a>',
                }
                for i, p in enumerate(top, 1)
            ]
        )

        builder.manual_order()
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return RoutineResult(text=report_text, chart_image=chart_bytes)
