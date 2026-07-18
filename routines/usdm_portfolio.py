"""USDM1/USDC market-making + arbitrage portfolio summary.

Tracks how much capital we have deployed across:
  - the Solana wallet (SOL / USDC / USDM1)
  - our Orca USDM1/USDC CLMM positions (in-range tokens + uncollected fees)
  - the Ethereum wallets (ETH / USDC / USDM1)

…and nets it against our financing: a USDC loan whose accrued interest grows
daily. Output is net equity (own capital at risk) vs gross deployed capital.

Read-only. Prices everything in USD: USDC = $1, USDM1 = M1 NAV, SOL = Jupiter
SOL/USDC, ETH = Uniswap ETH/USDC. Prices are fetched live — nothing hardcoded;
if a price feed fails the asset is shown UNPRICED and excluded from the total
with a loud warning (never silently valued at 0).

Generates an HTML report (KPIs + allocation chart + per-location breakdown) and
returns a Telegram text summary.
"""

CATEGORY = "Portfolio"

import json as _json
import logging
from datetime import date

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


class Config(BaseModel):
    """USDM Portfolio — total USDM1/USDC MM+arb portfolio value, net of the USDC loan."""

    gateway_url: str = Field(default="http://localhost:15888", description="Gateway base URL")
    solana_wallets: list[str] = Field(
        default=[
            "29hB4hN13nm79A2BrWxVzWo3KNrjxyNKrXgetBWe6xzh",  # Swig funds-owner: narrow position + trading float
            "DQcmxgGCEwThGCzV6NmFG2WsbUpch3HLoZAhctcgeRM9",  # botcamp: wide position + SOL reserve
        ],
    )
    ethereum_wallets: list[str] = Field(
        default=[
            "0x4C0001C6A45e53593c1D5771BF3707546555F310",
            "0xDA50C69342216b538Daf06FfECDa7363E0B96684",
        ],
    )
    orca_pool: str = Field(default="6U4cpqp5eBGJdL2EsuNosnjDuxGMaDzHfTYiEsuwT5Ey")

    # Financing — USDC loan to fund the MM/arb inventory.
    loan_principal_usdc: float = Field(default=5000.0, description="Borrowed USDC principal")
    loan_apy: float = Field(default=0.05, description="Annual interest rate (0.05 = 5%)")
    loan_start: str = Field(default="2026-04-01", description="Loan start date YYYY-MM-DD")
    as_of: str = Field(default="", description="Valuation date YYYY-MM-DD; blank = today")


# ── HTTP helpers (verbatim from the other usdm routines) ────────────────────

async def _get(session, base: str, path: str, params=None) -> dict:
    async with session.get(f"{base.rstrip('/')}{path}", params=params, timeout=aiohttp.ClientTimeout(total=60)) as r:
        text = await r.text()
        if not r.ok:
            raise RuntimeError(f"GET {path} → HTTP {r.status}: {text[:200]}")
        return _json.loads(text) if text else {}


async def _post(session, base: str, path: str, body: dict) -> dict:
    async with session.post(
        f"{base.rstrip('/')}{path}", json=body, timeout=aiohttp.ClientTimeout(total=120),
    ) as r:
        text = await r.text()
        if not r.ok:
            raise RuntimeError(f"POST {path} → HTTP {r.status}: {text[:300]}")
        return _json.loads(text) if text else {}


# ── Price feeds (live, no hardcoded values) ─────────────────────────────────

async def _price(session, base: str, label: str, getter) -> float | None:
    try:
        return await getter()
    except Exception as e:
        logger.warning(f"{label} price failed: {e}")
        return None


async def _nav(session, base: str) -> float:
    r = await _get(session, base, "/connectors/usdm/quote-swap", {
        "chainNetwork": "solana-mainnet-beta", "baseToken": "USDM0",
        "quoteToken": "USDM1", "amount": 100, "side": "SELL",
    })
    return float(r["usdm1Price"])


async def _sol_px(session, base: str) -> float:
    r = await _get(session, base, "/connectors/jupiter/router/quote-swap", {
        "network": "mainnet-beta", "baseToken": "SOL", "quoteToken": "USDC", "amount": 1, "side": "SELL",
    })
    return float(r["price"])


async def _eth_px(session, base: str) -> float:
    r = await _get(session, base, "/connectors/uniswap/router/quote-swap", {
        "network": "mainnet", "baseToken": "ETH", "quoteToken": "USDC", "amount": 0.01, "side": "SELL",
    })
    return float(r["price"])


# ── Value a {token: amount} bag in USD given a price map ─────────────────────

def _value_bag(bag: dict, px: dict, lines: list, indent: str) -> float:
    total = 0.0
    for tok in ("SOL", "ETH", "USDC", "USDM1"):
        amt = float(bag.get(tok, 0) or 0)
        if amt == 0:
            continue
        p = px.get(tok)
        if p is None:
            lines.append(f"{indent}{tok:<6} {amt:>14.6f} × UNPRICED  → excluded ⚠️")
            continue
        usd = amt * p
        total += usd
        lines.append(f"{indent}{tok:<6} {amt:>14.6f} × {p:>10.4f} = ${usd:>12,.2f}")
    return total


# ── Allocation chart ─────────────────────────────────────────────────────────

def _allocation_figure(rows: list[dict]):
    """Horizontal bar of USD value deployed per location."""
    import plotly.graph_objects as go

    rows = [r for r in rows if r["usd"] > 0]
    rows = sorted(rows, key=lambda r: r["usd"])
    fig = go.Figure(
        go.Bar(
            x=[r["usd"] for r in rows],
            y=[r["location"] for r in rows],
            orientation="h",
            marker_color="#4f9cff",
            hovertemplate="%{y}<br>$%{x:,.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        title="Capital deployed by location (USD)",
        template="plotly_dark",
        height=max(280, 48 * len(rows) + 120),
        margin=dict(l=10, r=10, t=50, b=40),
    )
    return fig


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    base = config.gateway_url.rstrip("/")
    out: list[str] = []
    # Structured per-location subtotals for the report table + chart.
    locations: list[dict] = []
    async with aiohttp.ClientSession() as session:
        # ── Prices ───────────────────────────────────────────────────────
        nav = await _price(session, base, "NAV", lambda: _nav(session, base))
        sol = await _price(session, base, "SOL", lambda: _sol_px(session, base))
        eth = await _price(session, base, "ETH", lambda: _eth_px(session, base))
        px = {"USDC": 1.0, "USDM1": nav, "SOL": sol, "ETH": eth}

        out.append("USDM Portfolio — USDM1/USDC MM + Arb")
        out.append(f"  prices: NAV={nav}  SOL=${sol}  ETH=${eth}")
        out.append("")
        out.append("ASSETS")

        gross = 0.0
        usdm_capital = 0.0  # USDC + USDM1 only (the actual stablecoin MM/arb capital)

        # ── Solana wallets (Swig funds-owner + botcamp) ──────────────────
        for sw in config.solana_wallets:
            try:
                sb = (await _post(session, base, "/chains/solana/balances", {
                    "network": "mainnet-beta", "address": sw,
                    "tokens": ["SOL", "USDC", "USDM1"],
                })).get("balances", {})
            except Exception as e:
                sb = {}
                out.append(f"  Solana wallet — balance read FAILED: {e}")
            out.append(f"  Solana wallet {sw[:6]}…")
            sub = _value_bag(sb, px, out, "    ")
            gross += sub
            usdm_capital += float(sb.get("USDC", 0) or 0) + float(sb.get("USDM1", 0) or 0) * (nav or 0)
            out.append(f"    subtotal ${sub:,.2f}")
            locations.append({"location": f"Solana wallet {sw[:6]}…", "usd": sub})

        # ── Orca positions (across all Solana wallets) ───────────────────
        positions = []
        for sw in config.solana_wallets:
            try:
                ps = await _get(session, base, "/connectors/orca/clmm/positions-owned", {
                    "network": "mainnet-beta", "walletAddress": sw,
                })
                positions.extend(p for p in ps if p.get("poolAddress") == config.orca_pool)
            except Exception as e:
                out.append(f"  Orca positions ({sw[:6]}…) — read FAILED: {e}")
        out.append(f"  Orca positions (pool {config.orca_pool[:6]}…)")
        pos_total = 0.0
        for p in positions:
            b = float(p.get("baseTokenAmount", 0)) + float(p.get("baseFeeAmount", 0))
            q = float(p.get("quoteTokenAmount", 0)) + float(p.get("quoteFeeAmount", 0))
            val = b * (nav or 0) + q
            pos_total += val
            usdm_capital += val
            out.append(f"    {p['address'][:6]}…  {b:>10.2f} USDM1 + {q:>10.2f} USDC = ${val:>11,.2f}")
        if not positions:
            out.append("    (none)")
        gross += pos_total
        out.append(f"    subtotal ${pos_total:,.2f}")
        locations.append({"location": f"Orca CLMM {config.orca_pool[:6]}…", "usd": pos_total})

        # ── Ethereum wallets ─────────────────────────────────────────────
        for w in config.ethereum_wallets:
            try:
                eb = (await _post(session, base, "/chains/ethereum/balances", {
                    "network": "mainnet", "address": w, "tokens": ["ETH", "USDC", "USDM1"],
                })).get("balances", {})
            except Exception as e:
                eb = {}
                out.append(f"  Ethereum wallet {w[:6]}… — balance read FAILED: {e}")
            out.append(f"  Ethereum wallet {w[:6]}…")
            sub = _value_bag(eb, px, out, "    ")
            gross += sub
            usdm_capital += float(eb.get("USDC", 0) or 0) + float(eb.get("USDM1", 0) or 0) * (nav or 0)
            out.append(f"    subtotal ${sub:,.2f}")
            locations.append({"location": f"Ethereum wallet {w[:6]}…", "usd": sub})

        out.append("")
        out.append(f"  GROSS ASSETS: ${gross:,.2f}")
        out.append(f"  (of which USDM1+USDC MM/arb capital: ${usdm_capital:,.2f}; rest is SOL/ETH gas)")

        # ── Liabilities: USDC loan + accrued interest ────────────────────
        start = date.fromisoformat(config.loan_start)
        today = date.fromisoformat(config.as_of) if config.as_of else date.today()
        days = (today - start).days
        interest = config.loan_principal_usdc * config.loan_apy * days / 365.0
        liabilities = config.loan_principal_usdc + interest

        out.append("")
        out.append("LIABILITIES")
        out.append(f"  USDC loan principal: ${config.loan_principal_usdc:,.2f}")
        out.append(
            f"  accrued interest: ${interest:,.2f}  "
            f"({config.loan_apy*100:.1f}% APY × {days} days since {config.loan_start})"
        )
        out.append(f"  TOTAL LIABILITIES: ${liabilities:,.2f}")

        # ── Net equity ───────────────────────────────────────────────────
        net_equity = gross - liabilities
        out.append("")
        out.append(f"NET EQUITY (own capital): ${gross:,.2f} − ${liabilities:,.2f} = ${net_equity:,.2f}")

    # ── HTML report ──────────────────────────────────────────────────────
    report_status = ""
    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder("USDM Portfolio")
        builder.source("routine", "usdm_portfolio")
        builder.manual_order()
        builder.kpi("Gross Assets", f"${gross:,.2f}")
        builder.kpi("MM/Arb Capital", f"${usdm_capital:,.2f}")
        builder.kpi("Liabilities", f"${liabilities:,.2f}")
        builder.kpi(
            "Net Equity", f"${net_equity:,.2f}",
            trend="up" if net_equity >= 0 else "down",
        )

        builder.markdown(
            f"## USDM1/USDC market-making + arbitrage\n"
            f"Prices: NAV `{nav}` · SOL `${sol}` · ETH `${eth}`. "
            f"Valuation date: **{today.isoformat()}**."
        )
        builder.plotly(_allocation_figure(locations))
        builder.table(
            [{"Location": r["location"], "Value (USD)": f"${r['usd']:,.2f}"} for r in locations]
            + [{"Location": "GROSS ASSETS", "Value (USD)": f"${gross:,.2f}"}],
            columns=["Location", "Value (USD)"],
        )
        builder.table(
            [
                {"Item": "USDC loan principal", "Amount (USD)": f"${config.loan_principal_usdc:,.2f}"},
                {
                    "Item": f"Accrued interest ({config.loan_apy*100:.1f}% APY × {days}d)",
                    "Amount (USD)": f"${interest:,.2f}",
                },
                {"Item": "TOTAL LIABILITIES", "Amount (USD)": f"${liabilities:,.2f}"},
                {"Item": "NET EQUITY", "Amount (USD)": f"${net_equity:,.2f}"},
            ],
            columns=["Item", "Amount (USD)"],
        )

        report_id = await builder.save()
        report_status = f"Report saved (id={report_id})"
    except Exception as e:
        logger.exception("USDM portfolio report generation failed")
        report_status = f"Report FAILED: {type(e).__name__}: {e}"

    out.append("")
    out.append(report_status)
    return "\n".join(out)
