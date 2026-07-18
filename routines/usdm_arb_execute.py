"""USDM1 single-cycle arbitrage executor — port of usdm1_tri_venue_arb.py's _execute_cycle.

On invocation:
  1. Fetch NAV + per-venue (Orca, M1-sol, Uniswap, M1-eth) BUY/SELL quotes at order_amount.
  2. Compute the best (buy_venue, sell_venue) pair after gas.
  3. If the net edge >= min_profitability, either:
       - dry_run=True  → log the planned cycle, do nothing.
       - dry_run=False → fire leg-1 (buy), then leg-2 (sell). Records pending M1
         async settlement state. Aborts cleanly if leg-1 fails, FLAGS EXPOSED if
         leg-2 fails.

Designed to be invoked once per call (one-shot, not continuous). Schedule
externally if you want a loop.
"""

CATEGORY = "Arbitrage"

import asyncio
import logging
import time
from typing import Optional

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

VENUES = {
    "orca": {"chain": "solana", "network": "mainnet-beta", "dex": "orca", "kind": "pool"},
    "m1-sol": {"chain": "solana", "network": "mainnet-beta", "dex": "usdm", "kind": "m1"},
    "uniswap": {"chain": "ethereum", "network": "mainnet", "dex": "uniswap", "kind": "pool"},
    "m1-eth": {"chain": "ethereum", "network": "mainnet", "dex": "usdm", "kind": "m1"},
}


class Config(BaseModel):
    """Fire ONE USDM1/USDC arbitrage cycle across enabled venues (or simulate in dry_run)."""

    gateway_url: str = Field(default="http://localhost:15888", description="Gateway base URL")
    solana_wallet: str = Field(
        default="29hB4hN13nm79A2BrWxVzWo3KNrjxyNKrXgetBWe6xzh",
        description="Solana wallet (must already be in Gateway + M1-whitelisted)",
    )
    ethereum_wallet: str = Field(
        default="0xDA50C69342216b538Daf06FfECDa7363E0B96684",
        description="Ethereum wallet (must already be in Gateway + M1-whitelisted)",
    )
    orca_pool: str = Field(default="6U4cpqp5eBGJdL2EsuNosnjDuxGMaDzHfTYiEsuwT5Ey", description="Orca pool address")
    uniswap_pool: str = Field(default="0x6f161ad0e297ecb9d1b33c048272ccc964cb4b6a", description="Uniswap pool address")

    enable_orca: bool = Field(default=True, description="Enable Solana Orca pool leg")
    enable_m1_sol: bool = Field(default=True, description="Enable Solana M1 broker leg")
    enable_uniswap: bool = Field(default=False, description="Enable Ethereum Uniswap pool leg")
    enable_m1_eth: bool = Field(default=False, description="Enable Ethereum M1 broker leg")

    order_amount: float = Field(default=10.0, description="USDM1 size per cycle")
    min_profitability: float = Field(
        default=0.001, description="Minimum net edge as a fraction of notional (0.001 = 10 bps)",
    )
    slippage_pct: float = Field(default=1.0, description="Slippage % for pool execution")
    dry_run: bool = Field(default=True, description="Log decision only — don't actually trade")


# ── HTTP helpers ───────────────────────────────────────────────────────────

async def _get(session, base: str, path: str, params=None) -> dict:
    async with session.get(f"{base.rstrip('/')}{path}", params=params, timeout=aiohttp.ClientTimeout(total=60)) as r:
        text = await r.text()
        if not r.ok:
            raise RuntimeError(f"GET {path} → HTTP {r.status}: {text[:200]}")
        import json as _json
        return _json.loads(text) if text else {}


async def _post(session, base: str, path: str, body: dict) -> dict:
    async with session.post(
        f"{base.rstrip('/')}{path}", json=body, timeout=aiohttp.ClientTimeout(total=120),
    ) as r:
        text = await r.text()
        if not r.ok:
            raise RuntimeError(f"POST {path} → HTTP {r.status}: {text[:300]}")
        import json as _json
        return _json.loads(text) if text else {}


# ── Quote helpers ──────────────────────────────────────────────────────────

def _venue_enabled(v: str, cfg: Config) -> bool:
    return {"orca": cfg.enable_orca, "m1-sol": cfg.enable_m1_sol,
            "uniswap": cfg.enable_uniswap, "m1-eth": cfg.enable_m1_eth}[v]


def _venue_wallet(v: str, cfg: Config) -> str:
    return cfg.ethereum_wallet if VENUES[v]["chain"] == "ethereum" else cfg.solana_wallet


def _chain_network(chain: str) -> str:
    return "ethereum-mainnet" if chain == "ethereum" else "solana-mainnet-beta"


async def _fetch_nav(session, base: str) -> float:
    r = await _get(session, base, "/connectors/usdm/quote-swap", {
        "chainNetwork": "solana-mainnet-beta",
        "baseToken": "USDM0", "quoteToken": "USDM1",
        "amount": 100, "side": "SELL",
    })
    return float(r["usdm1Price"])


async def _quote_venue(session, base: str, venue: str, cfg: Config, nav: float) -> Optional[dict]:
    meta = VENUES[venue]
    amt = cfg.order_amount
    if meta["kind"] == "m1":
        notional = amt * nav
        return {"buy": notional, "sell": notional}
    pool = cfg.orca_pool if venue == "orca" else cfg.uniswap_pool
    try:
        buy = await _get(session, base, f"/connectors/{meta['dex']}/clmm/quote-swap", {
            "network": meta["network"], "baseToken": "USDM1", "quoteToken": "USDC",
            "amount": amt, "side": "BUY", "poolAddress": pool, "slippagePct": cfg.slippage_pct,
        })
        sell = await _get(session, base, f"/connectors/{meta['dex']}/clmm/quote-swap", {
            "network": meta["network"], "baseToken": "USDM1", "quoteToken": "USDC",
            "amount": amt, "side": "SELL", "poolAddress": pool, "slippagePct": cfg.slippage_pct,
        })
        return {"buy": float(buy["amountIn"]), "sell": float(sell["amountOut"])}
    except Exception as e:
        logger.warning(f"{venue} quote failed: {e}")
        return None


async def _estimate_gas(session, base: str, chain: str) -> float:
    """Live gas estimate in USDC. Returns 0 if anything goes wrong (script
    can still decide based on gross edge)."""
    try:
        gas_r = await _get(session, base, f"/chains/{chain}/estimate-gas", {
            "network": _chain_network(chain).split("-", 1)[1],
        })
        native_fee = float(gas_r.get("fee", 0))
        if chain == "solana":
            px = await _get(session, base, "/connectors/jupiter/router/quote-swap", {
                "network": "mainnet-beta", "baseToken": "SOL", "quoteToken": "USDC",
                "amount": 1, "side": "SELL",
            })
        else:
            px = await _get(session, base, "/connectors/uniswap/router/quote-swap", {
                "network": "mainnet", "baseToken": "ETH", "quoteToken": "USDC",
                "amount": 0.01, "side": "SELL",
            })
        return native_fee * float(px.get("price", 0))
    except Exception as e:
        logger.warning(f"gas estimate failed for {chain}: {e}")
        return 0.0


# ── Execution ──────────────────────────────────────────────────────────────

async def _execute_pool_leg(session, base: str, venue: str, side: str, cfg: Config) -> dict:
    meta = VENUES[venue]
    pool = cfg.orca_pool if venue == "orca" else cfg.uniswap_pool
    r = await _post(session, base, f"/connectors/{meta['dex']}/clmm/execute-swap", {
        "network": meta["network"], "baseToken": "USDM1", "quoteToken": "USDC",
        "side": side, "amount": cfg.order_amount, "poolAddress": pool,
        "slippagePct": cfg.slippage_pct, "walletAddress": _venue_wallet(venue, cfg),
    })
    return {"ok": r.get("status") == 1, "signature": r.get("signature", ""), "raw": r}


async def _execute_m1_leg(session, base: str, venue: str, side: str, cfg: Config, nav: float) -> dict:
    chain_network = _chain_network(VENUES[venue]["chain"])
    if side == "BUY":  # deposit USDC -> USDM1
        body = {
            "chainNetwork": chain_network, "walletAddress": _venue_wallet(venue, cfg),
            "amount": cfg.order_amount * nav, "tokenCode": "USDM1",
        }
        r = await _post(session, base, "/connectors/usdm/deposit", body)
    else:  # redeem USDM1 -> USDC
        body = {
            "chainNetwork": chain_network, "walletAddress": _venue_wallet(venue, cfg),
            "tokenCode": "USDM1", "amount": cfg.order_amount,
        }
        r = await _post(session, base, "/connectors/usdm/redeem", body)
    return {"ok": r.get("status") in (1, 0), "signature": r.get("signature", ""), "raw": r}


async def _execute_leg(session, base: str, venue: str, side: str, cfg: Config, nav: float) -> dict:
    return await (_execute_m1_leg(session, base, venue, side, cfg, nav)
                  if VENUES[venue]["kind"] == "m1"
                  else _execute_pool_leg(session, base, venue, side, cfg))


# ── Runner ─────────────────────────────────────────────────────────────────

async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    base = config.gateway_url.rstrip("/")
    enabled = [v for v in ("orca", "m1-sol", "uniswap", "m1-eth") if _venue_enabled(v, config)]
    if len(enabled) < 2:
        return f"Need ≥2 enabled venues to arbitrage; enabled: {enabled}"

    out: list[str] = []
    async with aiohttp.ClientSession() as session:
        # ── Refresh NAV + quotes in parallel ──────────────────────────────
        try:
            nav = await _fetch_nav(session, base)
        except Exception as e:
            return f"Failed to fetch NAV: {e}"

        quotes_tasks = [_quote_venue(session, base, v, config, nav) for v in enabled]
        results = await asyncio.gather(*quotes_tasks, return_exceptions=True)
        quotes = {}
        for v, r in zip(enabled, results):
            if isinstance(r, Exception):
                logger.warning(f"{v} quote raised: {r}")
                continue
            if r:
                quotes[v] = r

        if len(quotes) < 2:
            return f"Have <2 live quotes ({list(quotes.keys())}); cannot search for arb."

        # ── Live gas (USDC/leg) per chain, also in parallel ──────────────
        chains_in_use = {VENUES[v]["chain"] for v in quotes}
        gas_results = await asyncio.gather(*[_estimate_gas(session, base, c) for c in chains_in_use])
        gas_per_chain = dict(zip(chains_in_use, gas_results))

        # ── Score every (buy, sell) pair, find best ──────────────────────
        notional = config.order_amount * nav
        scored = []
        for buy_v in quotes:
            for sell_v in quotes:
                if buy_v == sell_v:
                    continue
                gross = quotes[sell_v]["sell"] - quotes[buy_v]["buy"]
                gas = gas_per_chain.get(VENUES[buy_v]["chain"], 0) + gas_per_chain.get(VENUES[sell_v]["chain"], 0)
                net = gross - gas
                buy_dev_bps = ((quotes[buy_v]["buy"] / config.order_amount) - nav) / nav * 10000
                sell_dev_bps = ((quotes[sell_v]["sell"] / config.order_amount) - nav) / nav * 10000
                scored.append({
                    "buy": buy_v, "sell": sell_v,
                    "buy_dev_bps": buy_dev_bps, "sell_dev_bps": sell_dev_bps,
                    "edge_bps": sell_dev_bps - buy_dev_bps,
                    "gross": gross, "gas": gas, "net": net,
                    "profitability": net / notional if notional > 0 else 0,
                })
        scored.sort(key=lambda s: s["net"], reverse=True)
        best = scored[0]

        # ── Decision narrative ──────────────────────────────────────────
        out.append(f"USDM1 Arb Execute (dry_run={config.dry_run})")
        out.append(f"  order: {config.order_amount} USDM1  ·  NAV: {nav:.9f}  ·  min profit: {config.min_profitability*100:.4f}%")
        out.append(f"  enabled: {enabled}   live quotes: {sorted(quotes.keys())}")
        gas_str = ", ".join(f"{c}={g:.6f}" for c, g in gas_per_chain.items())
        out.append(f"  live gas (USDC/leg): {gas_str}")
        out.append("")
        out.append(f"  BEST: buy {best['buy']} ({best['buy_dev_bps']:+.2f} bps) → "
                   f"sell {best['sell']} ({best['sell_dev_bps']:+.2f} bps)")
        out.append(f"        edge {best['edge_bps']:+.2f} bps   "
                   f"gross {best['gross']:+.4f} − gas {best['gas']:.4f} = "
                   f"net {best['net']:+.4f} USDC ({best['profitability']*100:+.4f}%)")

        if best["profitability"] < config.min_profitability:
            out.append("")
            out.append(f"  → NO ARB: net profitability below gate ({config.min_profitability*100:.4f}%)")
            return "\n".join(out)

        if config.dry_run:
            out.append("")
            out.append(f"  → DRY RUN: would BUY {config.order_amount} USDM1 on {best['buy']}, "
                       f"then SELL on {best['sell']}.")
            out.append(f"  → Expected net: {best['net']:+.4f} USDC ({best['profitability']*100:+.4f}%)")
            return "\n".join(out)

        # ── Live execution ───────────────────────────────────────────────
        out.append("")
        out.append(f"  → FIRING leg 1: BUY {config.order_amount} USDM1 on {best['buy']}")
        try:
            leg1 = await _execute_leg(session, base, best["buy"], "BUY", config, nav)
        except Exception as e:
            out.append(f"    leg-1 raised: {e}")
            out.append("  → cycle aborted, no exposure.")
            return "\n".join(out)
        out.append(f"    leg-1 result: ok={leg1['ok']}  sig={leg1['signature']}")
        if not leg1["ok"]:
            out.append("  → leg-1 failed, cycle aborted, no exposure.")
            return "\n".join(out)

        out.append(f"  → FIRING leg 2: SELL {config.order_amount} USDM1 on {best['sell']}")
        try:
            leg2 = await _execute_leg(session, base, best["sell"], "SELL", config, nav)
        except Exception as e:
            out.append(f"    leg-2 raised: {e}")
            out.append(f"  *** EXPOSED: leg-2 failed after leg-1 on {best['buy']} settled. "
                       f"You're holding {config.order_amount} extra USDM1 on {VENUES[best['buy']]['chain']}.")
            return "\n".join(out)
        out.append(f"    leg-2 result: ok={leg2['ok']}  sig={leg2['signature']}")
        if not leg2["ok"]:
            out.append(f"  *** EXPOSED: leg-2 failed after leg-1 on {best['buy']} settled. "
                       f"You're holding {config.order_amount} extra USDM1 on {VENUES[best['buy']]['chain']}.")
            return "\n".join(out)

        out.append("")
        out.append(f"  → CYCLE COMPLETE: net {best['net']:+.4f} USDC (subject to async M1 settlement)")

    # ── Report ────────────────────────────────────────────────────────────
    try:
        from condor.reports import ReportBuilder
        builder = ReportBuilder(f"USDM1 Arb Cycle ({'dry-run' if config.dry_run else 'live'}, size {config.order_amount})")
        builder.source("routine", "usdm_arb_execute").tags(
            ["arbitrage", "usdm1", "execute", "dry-run" if config.dry_run else "live"]
        )
        builder.manual_order()

        builder.kpi("NAV", f"{nav:.6f}")
        builder.kpi("Net Edge", f"{best['net']:+.4f} USDC", trend="up" if best["net"] > 0 else "down")
        builder.kpi(
            "Profitability", f"{best['profitability']*100:+.4f}%",
            trend="up" if best["profitability"] >= config.min_profitability else "down",
        )

        # All pairs scored
        builder.markdown("## All Pair Edges (sorted by net)")
        pair_rows = []
        for s in scored:
            pair_rows.append({
                "BUY": s["buy"], "SELL": s["sell"],
                "Buy vs NAV": f"{s['buy_dev_bps']:+.2f} bps",
                "Sell vs NAV": f"{s['sell_dev_bps']:+.2f} bps",
                "Edge": f"{s['edge_bps']:+.2f} bps",
                "Gross": f"{s['gross']:+.4f}",
                "Gas": f"{s['gas']:.4f}",
                "Net": f"{s['net']:+.4f}",
                "Profit %": f"{s['profitability']*100:+.4f}%",
            })
        builder.table(pair_rows)
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return "\n".join(out)
