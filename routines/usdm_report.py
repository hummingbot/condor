"""USDM1 cross-venue report.

For each enabled CLMM pool (Orca on Solana, Uniswap on Ethereum):
  - Fetch on-chain pool-info (price, fee, liquidity, TVL) — fall back to a
    tiny quote when pool-info fails.
  - Compare the pool's effective USDC/USDM1 price to the M1 NAV oracle on
    the SAME chain (each chain has its own /connectors/usdm/quote-swap call;
    the comparison stays chain-local even though M1's two NAVs are usually
    identical).
  - Quote BUY and SELL at each amount in a configurable ladder and render
    the per-size deviation from chain NAV in bps.

The report includes:
  - KPIs (per-chain NAV, per-pool spot price + bps deviation, best arb edge)
  - Plotly figure: per-chain price vs NAV oracle (NAV line, pool spot, BUY/SELL ladder)
  - Plotly figure: per-pool liquidity composition (USDM1 vs USDC, normalized)
  - Pool Info table (price, fee, liquidity, TVL, source)
  - Effective Prices table (BUY/SELL ladder)
  - Arb Matrix table — every (buy, sell) pair across all venues including
    the M1 broker venues on each chain (the M1 broker is always implicitly
    in the matrix as the NAV reference; it can't be disabled).

Read-only — does not submit transactions.
"""

CATEGORY = "Arbitrage"

import asyncio
import json
import logging
import math
from typing import Optional

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Fixed deployment-level constants — pool addresses and Gateway URL don't
# change between runs, so they're not Config fields.
GATEWAY_URL = "http://localhost:15888"
ORCA_POOL = "6U4cpqp5eBGJdL2EsuNosnjDuxGMaDzHfTYiEsuwT5Ey"
UNISWAP_POOL = "0x6f161ad0e297ecb9d1b33c048272ccc964cb4b6a"

# Solana inventory wallets (consolidated on botcamp since 2026-07-21 — the
# Swig was unwound; it holds only PDA-rent SOL and is no longer tracked).
SOLANA_WALLETS = {
    "DQcmxgGCEwThGCzV6NmFG2WsbUpch3HLoZAhctcgeRM9": "Botcamp (narrow + wide + float)",
}

# Ethereum inventory wallets (tracked since 2026-08-05): the arb wallet used
# for M1 mint ⇄ Uniswap sells. No LP positions on the ETH side — balances
# only.
ETHEREUM_WALLETS = {
    "0x4C0001C6A45e53593c1D5771BF3707546555F310": "Arb wallet (mint⇄Uniswap)",
}

# Per-chain metadata. The M1 broker venue per chain (`m1-sol`, `m1-eth`) is
# always implicitly enabled in the arb matrix — it's the NAV reference, not
# a tradable pool, so it doesn't get a toggle.
CHAINS = {
    "solana": {
        "network": "mainnet-beta",
        "chain_network": "solana-mainnet-beta",
        "m1_venue": "m1-sol",
    },
    "ethereum": {
        "network": "mainnet",
        "chain_network": "ethereum-mainnet",
        "m1_venue": "m1-eth",
    },
}

# Venue metadata: dex name + chain + network + kind. M1 venues are virtual
# (price always at NAV) and always present.
VENUES = {
    "orca": {"chain": "solana", "dex": "orca", "kind": "pool"},
    "m1-sol": {"chain": "solana", "dex": "usdm", "kind": "m1"},
    "uniswap": {"chain": "ethereum", "dex": "uniswap", "kind": "pool"},
    "m1-eth": {"chain": "ethereum", "dex": "usdm", "kind": "m1"},
}


class Config(BaseModel):
    """USDM1 cross-venue report — per-chain NAV vs pool prices with quote ladder + arb matrix."""

    amounts: str = Field(
        default="10,100,1000,10000,100000,1000000",
        description="USDM1 amounts to probe (comma-separated). Ladder runs up to 1M to track the goal of a 1,000,000 on-chain swap within 20 bps; large sizes will show big deviations (and may error on thin pools) until liquidity grows.",
    )
    enable_orca: bool = Field(default=True, description="Include Solana Orca pool")
    enable_uniswap: bool = Field(
        default=True, description="Include Ethereum Uniswap pool"
    )
    slippage_pct: float = Field(
        default=99.0,
        description="Slippage % for pool quotes (high so the quote always returns)",
    )


# ── Gateway HTTP helpers ───────────────────────────────────────────────────


async def _api(
    session: aiohttp.ClientSession, base: str, path: str, params: Optional[dict] = None
) -> dict:
    url = f"{base.rstrip('/')}{path}"
    async with session.get(
        url, params=params, timeout=aiohttp.ClientTimeout(total=30)
    ) as r:
        text = await r.text()
        if not r.ok:
            raise RuntimeError(f"GET {path} → HTTP {r.status}: {text[:200]}")
        return json.loads(text) if text else {}


async def _api_post(
    session: aiohttp.ClientSession, base: str, path: str, body: dict
) -> dict:
    url = f"{base.rstrip('/')}{path}"
    async with session.post(
        url, json=body, timeout=aiohttp.ClientTimeout(total=30)
    ) as r:
        text = await r.text()
        if not r.ok:
            raise RuntimeError(f"POST {path} → HTTP {r.status}: {text[:200]}")
        return json.loads(text) if text else {}


async def _fetch_wallet(session, base: str, address: str) -> dict:
    """Solana wallet inventory: SOL/USDC/USDM1 balances + Orca positions in the
    USDM1/USDC pool. Partial failures are recorded, not raised — the report
    should still render with whatever it got."""
    out: dict = {"address": address, "balances": None, "positions": None, "err": []}
    try:
        r = await _api_post(
            session,
            base,
            "/chains/solana/balances",
            {
                "network": CHAINS["solana"]["network"],
                "address": address,
                "tokens": ["SOL", "USDC", "USDM1"],
            },
        )
        out["balances"] = r.get("balances", {})
    except Exception as e:
        out["err"].append(f"balances: {str(e)[:120]}")
    try:
        out["positions"] = await _api(
            session,
            base,
            "/connectors/orca/clmm/positions-owned",
            {
                "network": CHAINS["solana"]["network"],
                "poolAddress": ORCA_POOL,
                "walletAddress": address,
            },
        )
    except Exception as e:
        out["err"].append(f"positions: {str(e)[:120]}")
    return out


async def _fetch_eth_wallet(session, base: str, address: str) -> dict:
    """Ethereum wallet inventory: ETH/USDC/USDM1 balances. No positions — the
    Ethereum wallets don't LP the Uniswap pool."""
    out = {"address": address, "balances": None, "err": []}
    try:
        r = await _api_post(
            session,
            base,
            "/chains/ethereum/balances",
            {
                "network": CHAINS["ethereum"]["network"],
                "address": address,
                "tokens": ["ETH", "USDC", "USDM1"],
            },
        )
        out["balances"] = r.get("balances", {})
    except Exception as e:
        out["err"].append(f"balances: {str(e)[:120]}")
    return out


async def _fetch_chain_nav(session, base: str, chain: str) -> dict:
    """Per-chain NAV. M1 returns the same number on both chains today, but
    we query each chain's endpoint independently so the comparison is
    architecturally chain-local. If a non-Solana chain's M1 endpoint is down
    (the whole /ethereum/* namespace 404'd on 2026-07-02), fall back to the
    Solana NAV — M1 serves the same NAV on both chains."""

    async def _nav_via(chain_network: str) -> dict:
        r = await _api(
            session,
            base,
            "/connectors/usdm/quote-swap",
            {
                "chainNetwork": chain_network,
                "baseToken": "USDM0",
                "quoteToken": "USDM1",
                "amount": 100,
                "side": "SELL",
            },
        )
        return {"nav": float(r["usdm1Price"]), "rate": r.get("usdm1Rate", "—")}

    cn = CHAINS[chain]["chain_network"]
    try:
        return await _nav_via(cn)
    except Exception as e:
        if chain == "solana":
            raise
        nav = await _nav_via(CHAINS["solana"]["chain_network"])
        nav["fallback"] = f"solana NAV reused ({cn} fetch failed: {str(e)[:120]})"
        return nav


async def _quote_pool(
    session,
    base: str,
    dex: str,
    network: str,
    pool: str,
    side: str,
    amount: float,
    slippage: float,
) -> Optional[dict]:
    try:
        r = await _api(
            session,
            base,
            f"/connectors/{dex}/clmm/quote-swap",
            {
                "network": network,
                "baseToken": "USDM1",
                "quoteToken": "USDC",
                "amount": amount,
                "side": side,
                "poolAddress": pool,
                "slippagePct": slippage,
            },
        )
        return {"amountIn": float(r["amountIn"]), "amountOut": float(r["amountOut"])}
    except Exception as e:
        logger.warning(f"{dex} {side} quote failed at amount={amount}: {e}")
        return None


async def _fetch_pool_info(
    session, base: str, dex: str, network: str, pool: str, bin_count: int = 0
) -> Optional[dict]:
    try:
        params: dict = {"network": network, "poolAddress": pool}
        if bin_count > 0:
            params["binCount"] = bin_count
        r = await _api(session, base, f"/connectors/{dex}/clmm/pool-info", params)
        info = {
            "address": r.get("address", pool),
            "price": float(r["price"]) if r.get("price") is not None else None,
            "fee_pct": float(r.get("feePct", 0)),
            "base_amount": float(r.get("baseTokenAmount", 0)),
            "quote_amount": float(r.get("quoteTokenAmount", 0)),
            "tvl_usdc": (
                float(r.get("tvlUsdc", 0)) if r.get("tvlUsdc") is not None else None
            ),
            "source": "pool-info",
            "quote_normalized": False,
            # Per-bin distribution (only populated when bin_count > 0)
            "bins": r.get("bins") or [],
        }
        # Gateway's Uniswap pool-info sometimes returns quoteTokenAmount in
        # raw (unscaled) units when USDM1 isn't in the token registry — the
        # number comes back as the USDC integer offset by ~10^12 (the decimal
        # mismatch between USDM1's 18 and USDC's 6). Detect this by comparing
        # against base * spot and rescale if it's wildly off.
        if info["base_amount"] and info["quote_amount"] and info["price"]:
            expected = info["base_amount"] * info["price"]
            if info["quote_amount"] > expected * 100:
                # Try common decimal-mismatch scales
                for scale in (1e12, 1e6, 1e18):
                    candidate = info["quote_amount"] / scale
                    if candidate > 0 and abs(candidate - expected) / expected < 10:
                        logger.warning(
                            f"{dex} quoteTokenAmount looks raw ({info['quote_amount']:.0f}); "
                            f"normalized to {candidate:.4f} (expected ~{expected:.4f} = base*spot)"
                        )
                        info["quote_amount"] = candidate
                        info["quote_normalized"] = True
                        break
        # If TVL wasn't reported, compute it from base+quote (USDC-equivalent)
        if (
            info["tvl_usdc"] is None
            and info["base_amount"]
            and info["price"]
            and info["quote_amount"]
        ):
            info["tvl_usdc"] = (
                info["base_amount"] * info["price"] + info["quote_amount"]
            )
        return info
    except Exception as e:
        logger.warning(f"{dex} pool-info failed: {e}")
        return None


async def _spot_from_quote(
    session, base: str, dex: str, network: str, pool: str, slippage: float
) -> Optional[float]:
    q = await _quote_pool(session, base, dex, network, pool, "BUY", 0.01, slippage)
    return q["amountIn"] / 0.01 if q else None


# ── Probe + format helpers ─────────────────────────────────────────────────


def _bps(deviation: float) -> str:
    v = deviation * 10000
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f} bps"


# Symlog (signed-log) compression of a deviation, expressed in bps. The price
# chart plots slippage-from-NAV on this scale so a sub-bps move at size 10 and
# a ~1000 bps move at size 1M are both legible on one axis. 0 (== NAV) maps to
# 0; sign is preserved; magnitude is log10(1 + |bps|).
def _symlog_bps(deviation: float) -> float:
    bps = deviation * 10000.0
    return math.copysign(math.log10(1.0 + abs(bps)), bps)


# Tick anchors (in bps) for the symlog price axis; transformed via _symlog_bps.
_SYMLOG_TICK_BPS = [
    -1000,
    -300,
    -100,
    -30,
    -10,
    -3,
    -1,
    0,
    1,
    3,
    10,
    30,
    100,
    300,
    1000,
]


def _venue_chain(venue: str) -> str:
    return VENUES[venue]["chain"]


async def _probe(
    session,
    base: str,
    venue: str,
    cfg: Config,
    side: str,
    amount: float,
    chain_navs: dict,
) -> dict:
    """Effective price for a (venue, side, amount). Deviation is against the
    venue's OWN chain NAV (Ethereum venues vs Ethereum NAV, etc)."""
    meta = VENUES[venue]
    chain = meta["chain"]
    nav = chain_navs[chain]
    if meta["kind"] == "m1":
        return {"eff": nav, "notional": amount * nav, "dev": 0.0, "err": None}
    pool = ORCA_POOL if venue == "orca" else UNISWAP_POOL
    network = CHAINS[chain]["network"]
    q = await _quote_pool(
        session, base, meta["dex"], network, pool, side, amount, cfg.slippage_pct
    )
    if not q:
        return {"eff": 0.0, "notional": 0.0, "dev": 0.0, "err": "quote failed"}
    eff = q["amountIn"] / amount if side == "BUY" else q["amountOut"] / amount
    notional = q["amountIn"] if side == "BUY" else q["amountOut"]
    return {"eff": eff, "notional": notional, "dev": (eff - nav) / nav, "err": None}


# ── Visualizations ────────────────────────────────────────────────────────


def _price_vs_oracle_figure(
    chain_navs: dict, pool_data: dict, probes: dict, amounts: list[float]
):
    """Per-chain subplot: execution deviation of each BUY/SELL size measured
    from the chain NAV, on a symlog bps axis with NAV at x=0. The pool spot is
    drawn as a reference diamond at its own offset from NAV. Anchoring every
    row on NAV (and sharing one x-range) keeps the NAV markers vertically
    aligned across chains — the cross-venue comparison the report is for."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    pool_venues = [v for v in ("orca", "uniswap") if v in pool_data]
    if not pool_venues:
        return None

    def _title(v):
        sp = pool_data[v].get("price")
        sp_str = f"spot {sp:.6f}" if sp else "spot n/a"
        return f"{v.upper()} on {_venue_chain(v).upper()}  ·  {sp_str}  ·  NAV {chain_navs[_venue_chain(v)]:.6f}"

    titles = [_title(v) for v in pool_venues]
    # Shared x-axes: every row plots deviation-from-NAV in bps on a symlog
    # scale, so NAV sits at x=0 on every row and one common range keeps the
    # NAV markers (and everything else) vertically comparable across chains.
    fig = make_subplots(
        rows=len(pool_venues),
        cols=1,
        subplot_titles=titles,
        vertical_spacing=0.24,
        shared_xaxes=True,
    )

    c_buy = "rgba(255, 99, 132, 0.95)"
    c_sell = "rgba(75, 192, 132, 0.95)"
    c_spot = "rgba(88, 166, 255, 0.95)"
    c_nav = "rgba(255, 255, 255, 0.45)"  # dashed vertical reference
    c_nav_dot = "rgba(239, 68, 68, 1.0)"  # red diamond marker on the spot row

    c_na = "rgba(150, 150, 160, 0.55)"  # muted marker for unsupported sizes

    all_xt: list[float] = []
    na_legend_shown = False
    for row, venue in enumerate(pool_venues, start=1):
        chain = _venue_chain(venue)
        nav = chain_navs[chain]
        pinfo = pool_data[venue]
        spot = pinfo.get("price")
        # Deviation reference is the chain NAV: x=0 == NAV on every row, so
        # the NAV markers align vertically across chains.
        ref = nav

        def _slip(eff):
            return (eff - ref) / ref

        # NAV reference line at x=0 (the symlog axis is centred on NAV).
        fig.add_vline(
            x=0.0,
            row=row,
            col=1,
            line=dict(color=c_nav, width=2, dash="dash"),
        )

        # NAV diamond at x=0.
        fig.add_trace(
            go.Scatter(
                x=[0.0],
                y=["spot"],
                mode="markers+text",
                marker=dict(
                    color=c_nav_dot,
                    size=18,
                    symbol="diamond",
                    line=dict(width=1.5, color="#1a1a2e"),
                ),
                text=[f"NAV {nav:.6f} (0.00)"],
                textposition="bottom center",
                textfont=dict(size=10, color=c_nav_dot),
                name="NAV",
                showlegend=(row == 1),
                hovertemplate=f"NAV: {nav:.6f} (0.00 bps)<extra>{chain.upper()}</extra>",
            ),
            row=row,
            col=1,
        )

        # Pool spot (blue diamond, labelled "Pool Price") at its offset from NAV.
        row_xt: list[float] = [0.0]  # NAV anchor
        if spot:
            spot_dev_bps = _slip(spot) * 10000
            xspot = _symlog_bps(_slip(spot))
            row_xt.append(xspot)
            fig.add_trace(
                go.Scatter(
                    x=[xspot],
                    y=["spot"],
                    mode="markers+text",
                    marker=dict(
                        color=c_spot,
                        size=18,
                        symbol="diamond",
                        line=dict(width=1.5, color="#1a1a2e"),
                    ),
                    text=[f"{spot:.6f} ({spot_dev_bps:+.2f})"],
                    textposition="top center",
                    textfont=dict(size=10, color=c_spot),
                    name="Pool Price",
                    showlegend=(row == 1),
                    hovertemplate=f"Pool Price: {spot:.6f} ({spot_dev_bps:+.2f} bps from NAV)<extra>{venue.upper()}</extra>",
                ),
                row=row,
                col=1,
            )
        for amt in amounts:
            b = probes.get((venue, amt, "BUY"))
            s = probes.get((venue, amt, "SELL"))
            label = f"{amt:g}"
            b_ok = bool(b) and not b["err"]
            s_ok = bool(s) and not s["err"]
            if b_ok:
                b_slip_bps = _slip(b["eff"]) * 10000
                xb = _symlog_bps(_slip(b["eff"]))
                row_xt.append(xb)
                fig.add_trace(
                    go.Scatter(
                        x=[xb],
                        y=[label],
                        mode="markers+text",
                        marker=dict(
                            color=c_buy,
                            size=12,
                            symbol="triangle-up",
                            line=dict(width=1, color="#1a1a2e"),
                        ),
                        text=[f"{b_slip_bps:+.2f}"],
                        textposition="bottom center",
                        textfont=dict(size=9, color=c_buy),
                        name="BUY",
                        showlegend=(row == 1 and amt == amounts[0]),
                        legendgroup="buy",
                        hovertemplate=f"BUY {amt:g}: {b['eff']:.6f} ({b_slip_bps:+.2f} bps from NAV)<extra>{venue.upper()}</extra>",
                    ),
                    row=row,
                    col=1,
                )
            if s_ok:
                s_slip_bps = _slip(s["eff"]) * 10000
                xs = _symlog_bps(_slip(s["eff"]))
                row_xt.append(xs)
                fig.add_trace(
                    go.Scatter(
                        x=[xs],
                        y=[label],
                        mode="markers+text",
                        marker=dict(
                            color=c_sell,
                            size=12,
                            symbol="triangle-down",
                            line=dict(width=1, color="#1a1a2e"),
                        ),
                        text=[f"{s_slip_bps:+.2f}"],
                        textposition="top center",
                        textfont=dict(size=9, color=c_sell),
                        name="SELL",
                        showlegend=(row == 1 and amt == amounts[0]),
                        legendgroup="sell",
                        hovertemplate=f"SELL {amt:g}: {s['eff']:.6f} ({s_slip_bps:+.2f} bps from NAV)<extra>{venue.upper()}</extra>",
                    ),
                    row=row,
                    col=1,
                )
            if b_ok and s_ok:
                fig.add_trace(
                    go.Scatter(
                        x=[xb, xs],
                        y=[label, label],
                        mode="lines",
                        line=dict(color="rgba(255,255,255,0.25)", width=2),
                        showlegend=False,
                        hoverinfo="skip",
                    ),
                    row=row,
                    col=1,
                )
            elif not b_ok and not s_ok:
                # Pool can't fill this size yet (quote 500s on the thin pool) —
                # still show the size row, marked n/a, so the path to the 1M
                # target is visible as liquidity grows.
                fig.add_trace(
                    go.Scatter(
                        x=[0.0],
                        y=[label],
                        mode="markers+text",
                        marker=dict(
                            color=c_na,
                            size=11,
                            symbol="x-thin",
                            line=dict(width=2, color=c_na),
                        ),
                        text=["n/a"],
                        textposition="middle right",
                        textfont=dict(size=9, color=c_na),
                        name="unsupported",
                        showlegend=not na_legend_shown,
                        legendgroup="na",
                        hovertemplate=f"{amt:g}: no quote — pool too thin to fill<extra>{venue.upper()}</extra>",
                    ),
                    row=row,
                    col=1,
                )
                na_legend_shown = True

        all_xt.extend(row_xt)
        fig.update_yaxes(title_text="size (USDM1)", row=row, col=1, type="category")

    # ONE range for every row (the axes are shared): NAV at x=0 lands on the
    # same vertical line in each subplot, and bps offsets read comparably
    # across chains. Tick anchors are the ones inside the common range.
    lo, hi = min(all_xt), max(all_xt)
    span = max(hi - lo, 0.4)
    x_lo, x_hi = lo - span * 0.15, hi + span * 0.15
    tickvals, ticktext = [], []
    for tb in _SYMLOG_TICK_BPS:
        tv = math.copysign(math.log10(1.0 + abs(tb)), tb)
        if x_lo <= tv <= x_hi:
            tickvals.append(tv)
            ticktext.append("0" if tb == 0 else f"{tb:+d}")
    fig.update_xaxes(
        range=[x_lo, x_hi],
        showticklabels=True,
        tickmode="array",
        tickvals=tickvals,
        ticktext=ticktext,
    )
    fig.update_xaxes(
        title_text="deviation from chain NAV (bps · symlog)",
        row=len(pool_venues),
        col=1,
    )

    fig.update_layout(
        height=300 * len(pool_venues) + 80,
        template="plotly_dark",
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#16213e",
        font=dict(color="#e0e0e0"),
        title_text="Execution deviation from chain NAV (per chain · pool spot for reference)",
        title_font_size=15,
        legend=dict(orientation="h", yanchor="top", y=-0.14, xanchor="center", x=0.5),
        margin=dict(t=70, b=100, l=70, r=30),
    )
    return fig


def _pool_distribution_figure(pool_data: dict, chain_navs: dict):
    """Two stacked-subplot view (Orca on top, Uniswap on bottom): per-bin
    USDM1 + USDC amounts around the active tick.

    Each bin is one tickSpacing wide. We plot two bars per bin (USDM1 in blue,
    USDC in green) and overlay vertical reference lines for the pool spot
    (blue dashed) and the chain NAV (red dashed). The active bin is highlighted
    by a vertical band so you can see which tick the current price sits in.
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    pool_venues = [
        v for v in ("orca", "uniswap") if v in pool_data and pool_data[v].get("bins")
    ]
    if not pool_venues:
        return None

    titles = [
        f"{v.upper()} on {_venue_chain(v).upper()}  ·  spot = {pool_data[v]['price']:.6f}  ·  "
        f"NAV = {chain_navs[_venue_chain(v)]:.6f}"
        for v in pool_venues
    ]
    # Shared x-axes so the NAV reference + bin positions line up vertically
    # across Orca and Uniswap subplots.
    fig = make_subplots(
        rows=len(pool_venues),
        cols=1,
        subplot_titles=titles,
        vertical_spacing=0.26,
        shared_xaxes=True,
    )

    # Compute one global x-range covering every bin's price, every spot, and
    # every chain NAV. Applied to every subplot below.
    all_x_global: list[float] = list(chain_navs.values())
    for v in pool_venues:
        all_x_global.append(pool_data[v]["price"])
        for b in pool_data[v]["bins"]:
            all_x_global.append(b["price"])
    global_lo, global_hi = min(all_x_global), max(all_x_global)
    global_margin = max(global_hi - global_lo, 1e-6) * 0.05
    x_range = [global_lo - global_margin, global_hi + global_margin]

    c_base = "rgba(88, 166, 255, 0.85)"  # USDM1 (blue)
    c_quote = "rgba(75, 192, 132, 0.85)"  # USDC (green)
    c_spot = "rgba(88, 166, 255, 0.9)"  # spot vertical (blue dashed)
    c_nav = "rgba(239, 68, 68, 1.0)"  # NAV vertical (red dashed)
    c_active = "rgba(255, 255, 255, 0.06)"

    for row, venue in enumerate(pool_venues, start=1):
        info = pool_data[venue]
        bins = info["bins"]
        chain = _venue_chain(venue)
        nav = chain_navs[chain]
        spot = info["price"]
        prices = [b["price"] for b in bins]
        base_amts = [b["baseTokenAmount"] for b in bins]
        quote_amts = [b["quoteTokenAmount"] for b in bins]

        # Per-venue bar width: each bin is one tickSpacing wide, but Orca and
        # Uniswap have different tick spacing, so size bars to that venue's own
        # median bin gap. Bars fill ~92% of the bin so adjacent bins read as a
        # near-continuous histogram (finer granularity than thin grouped bars),
        # with a hairline gap to keep bin boundaries legible.
        if len(prices) > 1:
            gaps = sorted(prices[i + 1] - prices[i] for i in range(len(prices) - 1))
            bin_w = gaps[len(gaps) // 2] * 0.92
        else:
            bin_w = max(spot * 1e-4, 1e-6)
        bar_w = [bin_w] * len(prices)

        # Highlight band over the bin that contains the current price. The
        # bin boundaries are spaced one tickSpacing apart; we band-shade the
        # bin whose lower price is the largest one ≤ spot.
        active_idx = None
        for i, p in enumerate(prices):
            if p <= spot:
                active_idx = i
        if active_idx is not None and active_idx + 1 < len(prices):
            fig.add_vrect(
                x0=prices[active_idx],
                x1=prices[active_idx + 1],
                fillcolor=c_active,
                line_width=0,
                layer="below",
                row=row,
                col=1,
            )

        # Full-width stacked bars: in a CLMM each bin holds liquidity almost
        # entirely on one side (USDC below spot, USDM1 above), so stacking lets
        # every bin render at full bin width while the lone mixed bin at the
        # active tick still shows its base/quote split.
        fig.add_trace(
            go.Bar(
                x=prices,
                y=base_amts,
                width=bar_w,
                name="USDM1 (base)",
                marker=dict(color=c_base, line=dict(color="#1a1a2e", width=0.5)),
                hovertemplate="price %{x:.6f}<br>USDM1: %{y:,.4f}<extra></extra>",
                showlegend=(row == 1),
            ),
            row=row,
            col=1,
        )
        fig.add_trace(
            go.Bar(
                x=prices,
                y=quote_amts,
                width=bar_w,
                name="USDC (quote)",
                marker=dict(color=c_quote, line=dict(color="#1a1a2e", width=0.5)),
                hovertemplate="price %{x:.6f}<br>USDC: %{y:,.4f}<extra></extra>",
                showlegend=(row == 1),
            ),
            row=row,
            col=1,
        )

        # NAV + spot reference lines
        fig.add_vline(
            x=nav,
            row=row,
            col=1,
            line=dict(color=c_nav, width=2, dash="dash"),
            annotation_text=f"NAV {nav:.6f}",
            annotation_position="top right",
            annotation_font_color=c_nav,
        )
        fig.add_vline(
            x=spot,
            row=row,
            col=1,
            line=dict(color=c_spot, width=2, dash="dot"),
            annotation_text=f"spot {spot:.6f}",
            annotation_position="top left",
            annotation_font_color=c_spot,
        )

        # Shared global x-range + tick labels on every row so Orca and Uniswap
        # bins line up vertically and both subplots show their price axis
        # (shared_xaxes hides upper rows' labels by default).
        fig.update_xaxes(range=x_range, showticklabels=True, row=row, col=1)
        fig.update_yaxes(
            title_text="amount" if row == 1 else "", row=row, col=1, rangemode="tozero"
        )
        fig.update_xaxes(title_text="price (USDC/USDM1)", row=row, col=1)

    fig.update_layout(
        barmode="stack",
        bargap=0,
        height=360 * len(pool_venues) + 60,
        template="plotly_dark",
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#16213e",
        font=dict(color="#e0e0e0"),
        title_text="Pool liquidity distribution (per-bin token amounts around active tick)",
        title_font_size=15,
        legend=dict(orientation="h", yanchor="top", y=-0.08, xanchor="center", x=0.5),
        margin=dict(t=80, b=70, l=70, r=30),
    )
    return fig


def _pool_info_figure(pool_data: dict, chain_navs: dict):
    """Two vertical bar charts side-by-side:
        1. Token amounts in each pool (raw base + quote balances, per pool)
        2. Implied (on-chain) pool price vs that chain's M1 NAV oracle

    Each pool is one x-tick on each subplot; per-side y-axis lets small pools
    coexist with large pools without being visually crushed (each subplot
    rangemode='tozero' but uses its own scale)."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    pool_venues = [
        v for v in ("orca", "uniswap") if v in pool_data and pool_data[v].get("price")
    ]
    if not pool_venues:
        return None

    labels = [f"{v.upper()}<br>({_venue_chain(v)})" for v in pool_venues]
    base_amts = [pool_data[v].get("base_amount") or 0 for v in pool_venues]
    quote_amts = [pool_data[v].get("quote_amount") or 0 for v in pool_venues]
    spot_prices = [pool_data[v]["price"] for v in pool_venues]
    navs = [chain_navs[_venue_chain(v)] for v in pool_venues]

    c_base = "rgba(88, 166, 255, 0.85)"  # USDM1 (blue)
    c_quote = "rgba(75, 192, 132, 0.85)"  # USDC (green)
    c_spot = "rgba(88, 166, 255, 0.95)"  # pool spot (blue)
    c_nav = "rgba(239, 68, 68, 1.0)"  # NAV (red)

    fig = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=("Token amounts (raw, per pool)", "Implied price vs chain NAV"),
        horizontal_spacing=0.16,
    )

    # ── Left subplot: token amounts (grouped bars per pool) ───────────────
    fig.add_trace(
        go.Bar(
            x=labels,
            y=base_amts,
            name="USDM1 (base)",
            marker=dict(color=c_base, line=dict(color="#1a1a2e", width=1)),
            text=[f"{a:,.2f}" for a in base_amts],
            textposition="outside",
            textfont=dict(size=11),
            hovertemplate="%{x}<br>USDM1: %{y:,.4f}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Bar(
            x=labels,
            y=quote_amts,
            name="USDC (quote)",
            marker=dict(color=c_quote, line=dict(color="#1a1a2e", width=1)),
            text=[f"{a:,.2f}" for a in quote_amts],
            textposition="outside",
            textfont=dict(size=11),
            hovertemplate="%{x}<br>USDC: %{y:,.4f}<extra></extra>",
        ),
        row=1,
        col=1,
    )

    # ── Right subplot: implied pool price vs NAV (per pool) ───────────────
    fig.add_trace(
        go.Bar(
            x=labels,
            y=spot_prices,
            name="Pool spot",
            marker=dict(color=c_spot, line=dict(color="#1a1a2e", width=1)),
            text=[f"{p:.6f}" for p in spot_prices],
            textposition="outside",
            textfont=dict(size=11),
            hovertemplate="%{x}<br>Spot: %{y:.6f}<extra></extra>",
            showlegend=False,
        ),
        row=1,
        col=2,
    )
    fig.add_trace(
        go.Bar(
            x=labels,
            y=navs,
            name="Chain NAV",
            marker=dict(color=c_nav, line=dict(color="#1a1a2e", width=1)),
            text=[f"{n:.6f}" for n in navs],
            textposition="outside",
            textfont=dict(size=11),
            hovertemplate="%{x}<br>NAV: %{y:.6f}<extra></extra>",
            showlegend=False,
        ),
        row=1,
        col=2,
    )

    fig.update_yaxes(title_text="amount", row=1, col=1, rangemode="tozero")
    fig.update_yaxes(
        title_text="USDC/USDM1",
        row=1,
        col=2,
        range=[min(spot_prices + navs) * 0.998, max(spot_prices + navs) * 1.002],
    )
    fig.update_layout(
        barmode="group",
        height=420,
        template="plotly_dark",
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#16213e",
        font=dict(color="#e0e0e0"),
        title_text="Pool info — token amounts and implied price",
        title_font_size=15,
        legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="center", x=0.5),
        margin=dict(t=80, b=70, l=60, r=30),
    )
    return fig


def _nav_projection_figure(chain_nav_meta: dict, pool_data: dict, weeks: int = 4):
    """Line chart of NAV projection per chain over the next `weeks` weeks,
    compounded at the chain's annualised rate (M1 returns rate in bps as a
    string; rate=165 → 1.65% APY). Current pool spot prices are overlaid as
    horizontal dashed lines so you can read off the implied 'buy-and-hold'
    crossover point."""
    import plotly.graph_objects as go

    fig = go.Figure()
    chain_colors = {
        "solana": "rgba(153, 102, 255, 0.9)",
        "ethereum": "rgba(54, 162, 235, 0.9)",
    }
    pool_color_by_chain = {
        "solana": "rgba(75, 192, 132, 0.7)",
        "ethereum": "rgba(255, 159, 64, 0.7)",
    }

    week_xs = list(range(weeks + 1))

    for chain, meta in chain_nav_meta.items():
        nav = meta["nav"]
        rate_bps = float(meta.get("rate", 0) or 0)
        apy = rate_bps / 10000.0
        projected = [nav * (1 + apy) ** (w * 7 / 365) for w in week_xs]
        color = chain_colors.get(chain, "rgba(200,200,200,0.9)")
        fig.add_trace(
            go.Scatter(
                x=week_xs,
                y=projected,
                mode="lines+markers+text",
                line=dict(color=color, width=2.5),
                marker=dict(color=color, size=8, line=dict(width=1, color="#1a1a2e")),
                text=[
                    f"{p:.6f}" if (w == 0 or w == weeks) else ""
                    for w, p in zip(week_xs, projected)
                ],
                textposition="top center",
                textfont=dict(size=10, color=color),
                name=f"{chain.title()} NAV  (APY {rate_bps/100:.2f}%)",
                hovertemplate=f"%{{x}}w · NAV %{{y:.9f}}<extra>{chain.upper()}</extra>",
            )
        )

    # Overlay current pool spot prices so you can see the time it takes for
    # NAV to "grow into" each pool's current discount/premium.
    for venue, info in pool_data.items():
        spot = info.get("price")
        if not spot:
            continue
        chain = _venue_chain(venue)
        color = pool_color_by_chain.get(chain, "rgba(200,200,200,0.7)")
        fig.add_hline(
            y=spot,
            line=dict(color=color, width=1.5, dash="dot"),
            annotation_text=f"{venue} spot {spot:.6f}",
            annotation_position="bottom right",
            annotation_font_color=color,
        )

    fig.update_layout(
        height=420,
        template="plotly_dark",
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#16213e",
        font=dict(color="#e0e0e0"),
        title_text=f"NAV projection — next {weeks} weeks (compounded at M1 APY)",
        title_font_size=15,
        xaxis_title="weeks from now",
        yaxis_title="USDC/USDM1",
        legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="center", x=0.5),
        margin=dict(t=70, b=80, l=60, r=30),
    )
    fig.update_xaxes(tickmode="linear", dtick=1)
    return fig


# ── Runner ─────────────────────────────────────────────────────────────────


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    base = GATEWAY_URL.rstrip("/")
    try:
        amounts = [float(s.strip()) for s in config.amounts.split(",") if s.strip()]
    except ValueError:
        return f"Invalid amounts string: {config.amounts!r} — expected comma-separated numbers"
    if not amounts:
        return "amounts is empty"

    # Always include the M1 broker venue on each chain whose pool is enabled
    enabled: list[str] = []
    if config.enable_orca:
        enabled += ["orca", "m1-sol"]
    if config.enable_uniswap:
        enabled += ["uniswap", "m1-eth"]
    if not enabled:
        return "No pools enabled — enable_orca and/or enable_uniswap."

    chains_in_use = sorted({_venue_chain(v) for v in enabled})

    async with aiohttp.ClientSession() as session:
        # ── per-chain NAV (concurrent) ────────────────────────────────────
        try:
            nav_results = await asyncio.gather(
                *[_fetch_chain_nav(session, base, c) for c in chains_in_use]
            )
        except Exception as e:
            return f"Failed to fetch chain NAV from {base}: {e}"
        chain_nav_meta = dict(zip(chains_in_use, nav_results))
        chain_navs = {c: m["nav"] for c, m in chain_nav_meta.items()}

        # ── pool-info per CLMM pool (concurrent) ──────────────────────────
        pool_venues = [v for v in ("orca", "uniswap") if v in enabled]
        # binCount drives the pool-distribution chart (per-bin token amounts
        # around the active tick). 64 bins gives a wide, fine-grained ±32-bin
        # view of how liquidity is laid out around spot/NAV on each venue.
        POOL_INFO_BIN_COUNT = 64
        pool_info_tasks = []
        for v in pool_venues:
            meta = VENUES[v]
            pool = ORCA_POOL if v == "orca" else UNISWAP_POOL
            pool_info_tasks.append(
                _fetch_pool_info(
                    session,
                    base,
                    meta["dex"],
                    CHAINS[meta["chain"]]["network"],
                    pool,
                    bin_count=POOL_INFO_BIN_COUNT,
                )
            )
        pool_info_results = await asyncio.gather(*pool_info_tasks)
        pool_data: dict[str, dict] = {}
        for v, info in zip(pool_venues, pool_info_results):
            if info is None:
                meta = VENUES[v]
                pool = ORCA_POOL if v == "orca" else UNISWAP_POOL
                spot = await _spot_from_quote(
                    session,
                    base,
                    meta["dex"],
                    CHAINS[meta["chain"]]["network"],
                    pool,
                    config.slippage_pct,
                )
                pool_data[v] = {
                    "address": pool,
                    "price": spot,
                    "fee_pct": None,
                    "base_amount": None,
                    "quote_amount": None,
                    "tvl_usdc": None,
                    "source": "quote-fallback" if spot else "fallback-failed",
                }
            else:
                pool_data[v] = info

        # ── Solana wallet inventory (Swig funds-owner + botcamp legacy) ───
        wallet_data: dict[str, dict] = {}
        if config.enable_orca:
            wallet_results = await asyncio.gather(
                *[_fetch_wallet(session, base, addr) for addr in SOLANA_WALLETS],
                return_exceptions=True,
            )
            for addr, w in zip(SOLANA_WALLETS, wallet_results):
                wallet_data[addr] = (
                    w
                    if not isinstance(w, Exception)
                    else {
                        "address": addr,
                        "balances": None,
                        "positions": None,
                        "err": [str(w)[:120]],
                    }
                )

        # ── Ethereum wallet inventory (arb wallet float) ──────────────────
        eth_wallet_data: dict[str, dict] = {}
        if config.enable_uniswap:
            eth_results = await asyncio.gather(
                *[_fetch_eth_wallet(session, base, addr) for addr in ETHEREUM_WALLETS],
                return_exceptions=True,
            )
            for addr, w in zip(ETHEREUM_WALLETS, eth_results):
                eth_wallet_data[addr] = (
                    w
                    if not isinstance(w, Exception)
                    else {
                        "address": addr,
                        "balances": None,
                        "err": [str(w)[:120]],
                    }
                )

        # ── probe BUY+SELL at each amount per venue (bounded concurrency —
        # an unbounded burst 429s the Solana RPC and blanks the ladder) ────
        sem = asyncio.Semaphore(1)

        async def _bounded_probe(*args):
            async with sem:
                return await _probe(*args)

        tasks, keys = [], []
        for venue in enabled:
            for amt in amounts:
                for side in ("BUY", "SELL"):
                    tasks.append(
                        _bounded_probe(
                            session, base, venue, config, side, amt, chain_navs
                        )
                    )
                    keys.append((venue, amt, side))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        probes = {}
        for k, r in zip(keys, results):
            probes[k] = (
                r
                if not isinstance(r, Exception)
                else {"eff": 0.0, "notional": 0.0, "dev": 0.0, "err": str(r)}
            )

    # ── Text output ────────────────────────────────────────────────────────
    lines = []
    nav_strs = [f"{c}={chain_navs[c]:.9f}" for c in chains_in_use]
    lines.append(f"USDM1 Report — chain NAVs: {', '.join(nav_strs)}")
    lines.append(
        f"Venues: {', '.join(enabled)}   amounts: {amounts}   slippage {config.slippage_pct}%"
    )
    lines.append("")

    if pool_data:
        lines.append("--- POOL INFO ---")
        for v, info in pool_data.items():
            chain = _venue_chain(v)
            nav = chain_navs[chain]
            if info.get("price") is None:
                lines.append(
                    f"  {v} ({chain}): price unavailable (source={info.get('source')})"
                )
                continue
            dev_bps = (info["price"] - nav) / nav * 10000
            base_str = (
                f"base={info['base_amount']:.4f}"
                if info.get("base_amount") is not None
                else "base=—"
            )
            quote_str = (
                f"quote={info['quote_amount']:.4f}"
                if info.get("quote_amount") is not None
                else "quote=—"
            )
            tvl_str = (
                f"TVL=${info['tvl_usdc']:.2f}"
                if info.get("tvl_usdc") is not None
                else "TVL=—"
            )
            fee_str = (
                f"fee={info['fee_pct']:.4f}%"
                if info.get("fee_pct") is not None
                else "fee=—"
            )
            lines.append(
                f"  {v} ({chain}): price={info['price']:.6f} vs NAV {dev_bps:+.2f} bps  "
                f"{base_str}  {quote_str}  {tvl_str}  {fee_str}  src={info['source']}"
            )
        lines.append("")

    if wallet_data:
        sol_nav = chain_navs.get("solana")
        lines.append("--- SOLANA WALLETS ---")
        for addr, w in wallet_data.items():
            label = SOLANA_WALLETS.get(addr, addr)
            bal = w.get("balances") or {}
            pos = w.get("positions") or []
            pos_value = sum(
                (p.get("quoteTokenAmount") or 0)
                + (p.get("baseTokenAmount") or 0) * (sol_nav or 0)
                for p in pos
            )
            float_value = (bal.get("USDC") or 0) + (bal.get("USDM1") or 0) * (
                sol_nav or 0
            )
            lines.append(
                f"  {label} ({addr[:8]}…): SOL={bal.get('SOL', 0):.4f}  USDC={bal.get('USDC', 0):.2f}  "
                f"USDM1={bal.get('USDM1', 0):.2f}  positions={len(pos)} (${pos_value:,.2f} at NAV)  "
                f"total≈${float_value + pos_value:,.2f}"
                + (f"  ERRORS: {'; '.join(w['err'])}" if w.get("err") else "")
            )
        lines.append("")

    if eth_wallet_data:
        eth_nav = chain_navs.get("ethereum")
        lines.append("--- ETHEREUM WALLETS ---")
        for addr, w in eth_wallet_data.items():
            label = ETHEREUM_WALLETS.get(addr, addr)
            bal = w.get("balances") or {}
            float_value = (bal.get("USDC") or 0) + (bal.get("USDM1") or 0) * (
                eth_nav or 0
            )
            lines.append(
                f"  {label} ({addr[:8]}…): ETH={bal.get('ETH', 0):.5f}  USDC={bal.get('USDC', 0):.2f}  "
                f"USDM1={bal.get('USDM1', 0):.2f}  total≈${float_value:,.2f}"
                + (f"  ERRORS: {'; '.join(w['err'])}" if w.get("err") else "")
            )
        lines.append("")

    for venue in enabled:
        chain = _venue_chain(venue)
        lines.append(f"--- {venue}  (compared to {chain} NAV) ---")
        lines.append(
            f"  {'amount':>8}  {'buy USDC/USDM1':>16}  {'vs NAV':>11}  {'sell USDC/USDM1':>17}  {'vs NAV':>11}  {'buy notional':>14}  {'sell notional':>14}"
        )
        for amt in amounts:
            b = probes[(venue, amt, "BUY")]
            s = probes[(venue, amt, "SELL")]
            if b["err"]:
                lines.append(f"  {amt:>8.0f}  BUY error: {b['err'][:60]}")
                continue
            if s["err"]:
                lines.append(f"  {amt:>8.0f}  SELL error: {s['err'][:60]}")
                continue
            lines.append(
                f"  {amt:>8.0f}  "
                f"{b['eff']:>16.6f}  {_bps(b['dev']):>11}  "
                f"{s['eff']:>17.6f}  {_bps(s['dev']):>11}  "
                f"{b['notional']:>14.4f}  {s['notional']:>14.4f}"
            )
        lines.append("")

    # Edges at every probed amount (not just the largest), keyed by amount.
    # The arb matrix in the report renders all of these; the text/KPI summary
    # below still focuses on the largest size as the canonical reference.
    largest = max(amounts)
    edges_by_amount: dict[float, list[dict]] = {}
    for amt in amounts:
        amt_edges: list[dict] = []
        for buy_v in enabled:
            for sell_v in enabled:
                if buy_v == sell_v:
                    continue
                b = probes[(buy_v, amt, "BUY")]
                s = probes[(sell_v, amt, "SELL")]
                if b["err"] or s["err"]:
                    continue
                amt_edges.append(
                    {
                        "buy": buy_v,
                        "sell": sell_v,
                        "amount": amt,
                        "edge_bps": (s["dev"] - b["dev"]) * 10000,
                        "gross": s["notional"] - b["notional"],
                        "buy_dev_bps": b["dev"] * 10000,
                        "sell_dev_bps": s["dev"] * 10000,
                    }
                )
        edges_by_amount[amt] = amt_edges
    edges = edges_by_amount.get(largest, [])
    if edges:
        edges_sorted = sorted(edges, key=lambda e: e["edge_bps"], reverse=True)
        best = edges_sorted[0]
        lines.append(f"--- Best arb pair (size {largest}) ---")
        lines.append(
            f"  buy {best['buy']} ({_bps(best['buy_dev_bps']/10000)}) → "
            f"sell {best['sell']} ({_bps(best['sell_dev_bps']/10000)})   "
            f"edge {best['edge_bps']:+.2f} bps   gross {best['gross']:+.4f} USDC"
        )

    # ── Report ─────────────────────────────────────────────────────────────
    report_status: str = ""
    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder("USDM1 Report")
        # source() is required for the report to appear in Condor's grouped UI
        # listing (list_reports_grouped filters out entries with empty source).
        # tags() is intentionally NOT called — those produce the noisy #badges
        # in the header that the user asked to remove.
        builder.source("routine", "usdm_report")
        builder.manual_order()

        # 4 KPI cards max: per-chain NAV (2) + per-pool spot-vs-NAV (up to 2).
        # The cross-chain "best edge" KPIs were removed — those numbers already
        # appear in the arb-matrix table at the bottom.
        for c, nav in chain_navs.items():
            builder.kpi(f"{c.title()} NAV", f"{nav:.6f}")
        for v, info in pool_data.items():
            if info.get("price") is None:
                continue
            nav = chain_navs[_venue_chain(v)]
            dev_bps = (info["price"] - nav) / nav * 10000
            builder.kpi(
                f"{v} spot vs {_venue_chain(v)} NAV",
                f"{info['price']:.6f}  ({dev_bps:+.2f} bps)",
                trend="up" if dev_bps > 0 else "down",
            )

        # Price vs NAV chart
        fig = _price_vs_oracle_figure(chain_navs, pool_data, probes, amounts)
        if fig is not None:
            builder.markdown(
                "## Execution deviation from chain NAV (pool spot shown for reference)"
            )
            builder.plotly(fig)

            # Actual numbers behind the chart: the BUY/SELL effective price and
            # slippage from the pool spot (price impact) at each size, per pool.
            # "n/a" = pool too thin to fill that size yet (the quote 500s).
            ladder_rows = []
            for venue in pool_venues:
                chain = _venue_chain(venue)
                spot = pool_data[venue].get("price")
                for amt in amounts:
                    b = probes.get((venue, amt, "BUY"))
                    s = probes.get((venue, amt, "SELL"))

                    def _cells(p):
                        if not p or p["err"]:
                            return "n/a", "n/a", "n/a"
                        slip = (p["eff"] - spot) / spot if spot else 0.0
                        return f"{p['eff']:.6f}", _bps(slip), f"{p['notional']:.2f}"

                    b_px, b_slip, b_not = _cells(b)
                    s_px, s_slip, s_not = _cells(s)
                    ladder_rows.append(
                        {
                            "Venue": venue,
                            "Chain": chain.title(),
                            "Size (USDM1)": f"{int(amt)}",
                            "BUY USDC/USDM1": b_px,
                            "BUY slip (from spot)": b_slip,
                            "BUY notional (USDC)": b_not,
                            "SELL USDC/USDM1": s_px,
                            "SELL slip (from spot)": s_slip,
                            "SELL notional (USDC)": s_not,
                        }
                    )
            if ladder_rows:
                builder.markdown("### Effective price ladder — slippage from pool spot")
                builder.table(ladder_rows)

        # ── Pool info + per-pool bin distribution around the active tick ──
        # Combined section: the pool-info summary (token amounts + implied
        # price vs NAV per pool) sits directly above the per-bin liquidity
        # distribution so the headline balances and the bin-by-bin breakdown
        # read together.
        pi_fig = _pool_info_figure(pool_data, chain_navs) if pool_data else None
        dist_fig = _pool_distribution_figure(pool_data, chain_navs)
        if pi_fig is not None or dist_fig is not None:
            dist_lines = ["## Pool info & distribution"]
            for v in ("orca", "uniswap"):
                info = pool_data.get(v)
                if not info or not info.get("bins"):
                    continue
                bins = info["bins"]
                prices = [b["price"] for b in bins]
                nav = chain_navs[_venue_chain(v)]
                # USDC (quote) sits below spot, USDM1 (base) above — quantify how
                # much of each side's depth currently sits on the favourable side
                # of the chain NAV (where arb/MM would want inventory).
                base_total = sum(b["baseTokenAmount"] for b in bins)
                quote_total = sum(b["quoteTokenAmount"] for b in bins)
                base_above_nav = sum(
                    b["baseTokenAmount"] for b in bins if b["price"] >= nav
                )
                quote_below_nav = sum(
                    b["quoteTokenAmount"] for b in bins if b["price"] <= nav
                )
                b_pct = (base_above_nav / base_total * 100) if base_total else 0.0
                q_pct = (quote_below_nav / quote_total * 100) if quote_total else 0.0
                dist_lines.append(
                    f"- **{v.upper()}** ({_venue_chain(v)}): {len(bins)} bins spanning "
                    f"{prices[0]:.6f}–{prices[-1]:.6f} USDC/USDM1 · "
                    f"spot {info['price']:.6f} vs NAV {nav:.6f} · "
                    f"{b_pct:.0f}% of USDM1 depth above NAV, {q_pct:.0f}% of USDC depth below NAV"
                )
            if dist_fig is not None:
                dist_lines.append(
                    "\nEach bar is one tick-spacing-wide bin: USDC (green) below spot, "
                    "USDM1 (blue) above; the red line is the chain NAV, the blue line is pool spot."
                )
            builder.markdown("\n".join(dist_lines))
            if pi_fig is not None:
                builder.plotly(pi_fig)
            if dist_fig is not None:
                builder.plotly(dist_fig)

        # ── Wallet inventory (Solana botcamp + Ethereum arb wallet) ───────
        if wallet_data or eth_wallet_data:
            sol_nav = chain_navs.get("solana") or 0.0
            wallet_rows, position_rows = [], []
            total_usd = 0.0
            for addr, w in wallet_data.items():
                label = SOLANA_WALLETS.get(addr, addr)
                bal = w.get("balances") or {}
                pos = w.get("positions") or []
                pos_value = sum(
                    (p.get("quoteTokenAmount") or 0)
                    + (p.get("baseTokenAmount") or 0) * sol_nav
                    for p in pos
                )
                float_value = (bal.get("USDC") or 0) + (bal.get("USDM1") or 0) * sol_nav
                total_usd += float_value + pos_value
                wallet_rows.append(
                    {
                        "Wallet": label,
                        "Address": f"{addr[:8]}…{addr[-4:]}",
                        "SOL": f"{bal.get('SOL', 0):.4f}",
                        "USDC": f"{bal.get('USDC', 0):,.2f}",
                        "USDM1": f"{bal.get('USDM1', 0):,.2f}",
                        "Positions": str(len(pos)),
                        "Positions value (USDC @ NAV)": f"{pos_value:,.2f}",
                        "Total ≈ USD": f"{float_value + pos_value:,.2f}",
                        "Status": "; ".join(w["err"]) if w.get("err") else "ok",
                    }
                )
                for p in pos:
                    lower, upper = p.get("lowerPrice"), p.get("upperPrice")
                    spot_p = p.get("price")
                    in_range = (
                        "✅"
                        if (
                            lower is not None
                            and upper is not None
                            and spot_p is not None
                            and lower <= spot_p <= upper
                        )
                        else "❌"
                    )
                    p_value = (p.get("quoteTokenAmount") or 0) + (
                        p.get("baseTokenAmount") or 0
                    ) * sol_nav
                    position_rows.append(
                        {
                            "Wallet": label,
                            "Position": f"{p['address'][:8]}…{p['address'][-4:]}",
                            "Range (USDC/USDM1)": (
                                f"{lower:.6f} – {upper:.6f}" if lower and upper else "—"
                            ),
                            "USDM1": f"{p.get('baseTokenAmount', 0):,.2f}",
                            "USDC": f"{p.get('quoteTokenAmount', 0):,.2f}",
                            "Value (USDC @ NAV)": f"{p_value:,.2f}",
                            "In range": in_range,
                        }
                    )
            eth_nav = chain_navs.get("ethereum") or 0.0
            eth_wallet_rows = []
            for addr, w in eth_wallet_data.items():
                label = ETHEREUM_WALLETS.get(addr, addr)
                bal = w.get("balances") or {}
                float_value = (bal.get("USDC") or 0) + (bal.get("USDM1") or 0) * eth_nav
                total_usd += float_value
                eth_wallet_rows.append(
                    {
                        "Wallet": label,
                        "Address": f"{addr[:8]}…{addr[-4:]}",
                        "ETH": f"{bal.get('ETH', 0):.5f}",
                        "USDC": f"{bal.get('USDC', 0):,.2f}",
                        "USDM1": f"{bal.get('USDM1', 0):,.2f}",
                        "Total ≈ USD": f"{float_value:,.2f}",
                        "Status": "; ".join(w["err"]) if w.get("err") else "ok",
                    }
                )
            builder.markdown(
                "## Solana & Ethereum wallets — Orca positions & float\n"
                f"Total tracked inventory ≈ **${total_usd:,.2f}** "
                "(USDC + USDM1 at each chain's NAV, wallet float + position holdings; SOL/ETH gas excluded)."
            )
            if wallet_rows:
                builder.table(wallet_rows)
            if position_rows:
                builder.table(position_rows)
            if eth_wallet_rows:
                builder.table(eth_wallet_rows)

        # ── NAV projection over the next 4 weeks ────────────────────────
        proj_weeks = 4
        builder.markdown(f"## NAV projection — next {proj_weeks} weeks")
        proj_fig = _nav_projection_figure(chain_nav_meta, pool_data, weeks=proj_weeks)
        builder.plotly(proj_fig)

        # Per-chain projection table + "buy-and-hold from pool spot" returns
        proj_rows = []
        for chain, meta in chain_nav_meta.items():
            nav = meta["nav"]
            rate_bps = float(meta.get("rate", 0) or 0)
            apy = rate_bps / 10000.0
            row = {"Chain": chain.title(), "APY": f"{rate_bps/100:.4f}%"}
            for w in range(proj_weeks + 1):
                price = nav * (1 + apy) ** (w * 7 / 365)
                delta_bps = (price - nav) / nav * 10000
                row[f"Week {w}"] = f"{price:.9f}"
                row[f"Week {w} Δ"] = f"{delta_bps:+.2f} bps" if w else "—"
            proj_rows.append(row)
        builder.table(proj_rows)

        # Buy-now-hold-N-weeks implied return per pool (sell at projected NAV)
        hold_rows = []
        for venue, info in pool_data.items():
            spot = info.get("price")
            if not spot:
                continue
            chain = _venue_chain(venue)
            nav = chain_navs[chain]
            apy = float(chain_nav_meta[chain].get("rate", 0) or 0) / 10000.0
            projected_nav = nav * (1 + apy) ** (proj_weeks * 7 / 365)
            usdc_per_unit = projected_nav - spot
            return_pct = (projected_nav / spot - 1) * 100
            # Annualise the implied return assuming you exit at projected NAV at week N
            annualised_pct = ((projected_nav / spot) ** (52 / proj_weeks) - 1) * 100
            hold_rows.append(
                {
                    "Venue": venue,
                    "Chain": chain,
                    "Buy at (pool spot)": f"{spot:.9f}",
                    f"Sell at week {proj_weeks} (proj NAV)": f"{projected_nav:.9f}",
                    "USDC per USDM1": f"{usdc_per_unit:+.6f}",
                    f"Return over {proj_weeks}w": f"{return_pct:+.4f}%",
                    "Annualised": f"{annualised_pct:+.4f}%",
                }
            )
        if hold_rows:
            builder.markdown(
                f"## Buy-and-hold {proj_weeks}w returns (pool now → projected NAV)"
            )
            builder.table(hold_rows)

        # ── Arb matrices (one per chain, all sizes in each) ──────────────
        venues_by_chain: dict[str, list[str]] = {}
        for v in enabled:
            venues_by_chain.setdefault(_venue_chain(v), []).append(v)

        sizes_label = ", ".join(f"{int(a)}" for a in amounts)
        builder.markdown(f"## Arb matrices  (sizes: {sizes_label} USDM1)")

        for chain in sorted(venues_by_chain.keys()):
            chain_venues = venues_by_chain[chain]
            chain_label = chain.title()
            nav_col_buy = f"Buy vs {chain_label} NAV"
            nav_col_sell = f"Sell vs {chain_label} NAV"

            pool_vs = [v for v in chain_venues if VENUES[v]["kind"] == "pool"]
            m1_vs = [v for v in chain_venues if VENUES[v]["kind"] == "m1"]

            chain_rows = []
            for amt in amounts:
                amt_edges = [
                    e
                    for e in edges_by_amount.get(amt, [])
                    if e["buy"] in chain_venues and e["sell"] in chain_venues
                ]
                if amt_edges:
                    # Sort by edge so the best opportunity at each size is on top
                    for e in sorted(
                        amt_edges, key=lambda x: x["edge_bps"], reverse=True
                    ):
                        chain_rows.append(
                            {
                                "Chain": chain_label,
                                "Amount (USDM1)": f"{int(amt)}",
                                "Route": f"BUY {e['buy']} → SELL {e['sell']}",
                                nav_col_buy: _bps(e["buy_dev_bps"] / 10000),
                                nav_col_sell: _bps(e["sell_dev_bps"] / 10000),
                                "Edge (bps)": f"{e['edge_bps']:+.2f}",
                                "Gross (USDC)": f"{e['gross']:+.4f}",
                                "Signal": "✅" if e["edge_bps"] > 0 else "❌",
                            }
                        )
                else:
                    # No computable edge at this size — the pool leg's quote
                    # failed (pool too thin to fill this size yet). Still surface
                    # both route directions so the 1M-within-20-bps target is
                    # tracked as liquidity grows.
                    for pv in pool_vs:
                        for mv in m1_vs:
                            for buy_v, sell_v in ((mv, pv), (pv, mv)):
                                chain_rows.append(
                                    {
                                        "Chain": chain_label,
                                        "Amount (USDM1)": f"{int(amt)}",
                                        "Route": f"BUY {buy_v} → SELL {sell_v}",
                                        nav_col_buy: "n/a",
                                        nav_col_sell: "n/a",
                                        "Edge (bps)": "n/a",
                                        "Gross (USDC)": "n/a",
                                        "Signal": "⚠️",
                                    }
                                )
            if chain_rows:
                builder.table(chain_rows)

        report_id = await builder.save()
        report_status = f"Report saved (id={report_id})"
    except Exception as e:
        logger.exception("Report generation failed")
        report_status = f"Report FAILED: {type(e).__name__}: {e}"

    lines.append("")
    lines.append(report_status)
    return "\n".join(lines)
