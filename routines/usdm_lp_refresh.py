"""USDM1/USDC Orca liquidity refresh — re-center both positions on NAV.

Once per call (one-shot, schedule externally for the weekly cadence). The job:

  1. Read the M1 NAV oracle and the Orca pool's current price.
  2. Discover the wallet's positions on the pool (no hardcoded position
     addresses) and record each one's current price width.
  3. Plan a single, capped price-shift swap that nudges the pool price to NAV
     (Orca-only — no M1 whitelist needed), then plan re-centering each position
     at its existing width around NAV.
  4. dry_run=True  → print the full plan and STOP (the human gate).
     dry_run=False → execute, strictly ordered, abort-on-failure:
        shift swap → for each position: close (removes liquidity + collects
        fees + closes NFT) → open a new position at the NAV-centered range.

Why shift before closing: the swap needs in-range liquidity to move price along
the curve; re-opening while pool price == NAV is what makes the re-centered
position open balanced two-sided instead of all-one-token. The shift trades
against our own liquidity, so its cost is the 0.01% fee + tiny impact — a
positioning cost, not profit.

Mirrors usdm_arb_execute.py's helpers and dry_run gate.
"""

CATEGORY = "Liquidity"

import asyncio
import json as _json
import logging
import math
from typing import Optional

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

NETWORK = "mainnet-beta"
CHAIN_NETWORK = "solana-mainnet-beta"


class Config(BaseModel):
    """Refresh the USDM1/USDC Orca positions: shift pool to NAV (capped), then
    re-center both positions at their current widths on NAV."""

    gateway_url: str = Field(default="http://localhost:15888", description="Gateway base URL")
    solana_wallet: str = Field(
        default="29hB4hN13nm79A2BrWxVzWo3KNrjxyNKrXgetBWe6xzh",
        description="Solana wallet that owns the positions",
    )
    orca_pool: str = Field(
        default="6U4cpqp5eBGJdL2EsuNosnjDuxGMaDzHfTYiEsuwT5Ey",
        description="Orca USDM1/USDC pool address",
    )
    max_shift_usdc: float = Field(
        default=2000.0, description="Hard cap on the price-shift swap notional (USDC)",
    )
    shift_skip_bps: float = Field(
        default=1.0, description="Skip the price shift if |pool - NAV| is within this (bps)",
    )
    slippage_pct: float = Field(default=1.0, description="Slippage % for swap + open-position")
    dry_run: bool = Field(default=True, description="Print the plan only — execute nothing")


# ── HTTP helpers (verbatim from usdm_arb_execute.py) ────────────────────────

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


# ── Reads ───────────────────────────────────────────────────────────────────

async def _fetch_nav(session, base: str) -> float:
    r = await _get(session, base, "/connectors/usdm/quote-swap", {
        "chainNetwork": CHAIN_NETWORK,
        "baseToken": "USDM0", "quoteToken": "USDM1",
        "amount": 100, "side": "SELL",
    })
    return float(r["usdm1Price"])


async def _fetch_pool_mid(session, base: str, pool: str) -> float:
    r = await _get(session, base, "/connectors/orca/clmm/pool-info", {
        "network": NETWORK, "poolAddress": pool,
    })
    return float(r["price"])


async def _fetch_positions(session, base: str, wallet: str, pool: str) -> list[dict]:
    rows = await _get(session, base, "/connectors/orca/clmm/positions-owned", {
        "network": NETWORK, "walletAddress": wallet,
    })
    return [p for p in rows if p.get("poolAddress") == pool]


async def _fetch_balances(session, base: str, wallet: str) -> dict:
    try:
        r = await _post(session, base, "/chains/solana/balances", {
            "network": NETWORK, "address": wallet, "tokens": ["SOL", "USDC", "USDM1"],
        })
        return r.get("balances", {}) if isinstance(r, dict) else {}
    except Exception as e:
        logger.warning(f"balance read failed: {e}")
        return {}


async def _quote_swap(session, base: str, pool: str, side: str, amount: float, slip: float) -> Optional[dict]:
    try:
        return await _get(session, base, "/connectors/orca/clmm/quote-swap", {
            "network": NETWORK, "baseToken": "USDM1", "quoteToken": "USDC",
            "amount": amount, "side": side, "poolAddress": pool, "slippagePct": slip,
        })
    except Exception as e:
        logger.warning(f"quote {side} {amount} failed: {e}")
        return None


# ── Geometry ──────────────────────────────────────────────────────────────

def _half_width(lower: float, upper: float) -> float:
    """Geometric half-width: hw such that [nav/(1+hw), nav*(1+hw)] preserves the
    log-price span of [lower, upper]."""
    return math.sqrt(upper / lower) - 1.0


def _recenter(nav: float, hw: float) -> tuple[float, float]:
    return nav / (1.0 + hw), nav * (1.0 + hw)


async def _plan_shift(session, base: str, cfg: Config, nav: float, mid: float, balances: dict) -> Optional[dict]:
    """Size a single capped swap to nudge pool price toward NAV. BUY USDM1 to
    raise price (mid<NAV), SELL to lower it (mid>NAV). Picks the laddered amount
    whose effective fill price lands closest to NAV without exceeding the
    notional cap OR the wallet's free balance for the input token. Returns None
    when no shift is needed."""
    dev_bps = (mid - nav) / nav * 10000
    if abs(dev_bps) <= cfg.shift_skip_bps:
        return None
    side = "BUY" if mid < nav else "SELL"

    # The shift's input token must be on hand: BUY spends USDC, SELL spends USDM1.
    free_usdc = float(balances.get("USDC", 0))
    free_usdm1 = float(balances.get("USDM1", 0))
    affordable_usdc = free_usdc if side == "BUY" else free_usdm1 * nav
    notional_cap = min(cfg.max_shift_usdc, affordable_usdc)
    limited_by = "balance" if affordable_usdc < cfg.max_shift_usdc else "max_shift_usdc"

    # Ladder of USDM1 amounts up to the notional cap (cap is a USDC notional ≈ amount*nav).
    cap_amt = notional_cap / nav
    ladder = [a for a in (cap_amt * f for f in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)) if a > 0]
    if not ladder:
        return {"side": side, "amount": 0.0, "eff_price": mid, "notional": 0.0, "dist": abs(mid - nav),
                "dev_bps_before": dev_bps, "capped": True, "limited_by": limited_by,
                "notional_cap": notional_cap}

    best = None
    for amt in ladder:
        q = await _quote_swap(session, base, cfg.orca_pool, side, amt, cfg.slippage_pct)
        if not q:
            continue
        amount_in = float(q["amountIn"])
        amount_out = float(q["amountOut"])
        eff = (amount_in / amount_out) if side == "BUY" else (amount_out / amount_in)
        notional = amount_in if side == "BUY" else amount_out
        if notional > cfg.max_shift_usdc:
            continue
        cand = {"side": side, "amount": amt, "eff_price": eff, "notional": notional,
                "dist": abs(eff - nav)}
        if best is None or cand["dist"] < best["dist"]:
            best = cand
    if best:
        best["dev_bps_before"] = dev_bps
        best["capped"] = best["amount"] >= ladder[-1] * 0.999
        best["limited_by"] = limited_by
        best["notional_cap"] = notional_cap
    return best


# ── Execution ───────────────────────────────────────────────────────────────

async def _execute_shift(session, base: str, cfg: Config, shift: dict) -> dict:
    r = await _post(session, base, "/connectors/orca/clmm/execute-swap", {
        "network": NETWORK, "baseToken": "USDM1", "quoteToken": "USDC",
        "side": shift["side"], "amount": shift["amount"], "poolAddress": cfg.orca_pool,
        "slippagePct": cfg.slippage_pct, "walletAddress": cfg.solana_wallet,
    })
    return {"ok": r.get("status") == 1, "signature": r.get("signature", ""), "raw": r}


async def _close_position(session, base: str, cfg: Config, position_address: str) -> dict:
    # close-position removes remaining liquidity + collects fees + closes the NFT.
    r = await _post(session, base, "/connectors/orca/clmm/close-position", {
        "network": NETWORK, "walletAddress": cfg.solana_wallet, "positionAddress": position_address,
    })
    data = r.get("data") or {}
    return {
        "ok": r.get("status") == 1, "signature": r.get("signature", ""),
        "base": float(data.get("baseTokenAmountRemoved", 0)) + float(data.get("baseFeeAmountCollected", 0)),
        "quote": float(data.get("quoteTokenAmountRemoved", 0)) + float(data.get("quoteFeeAmountCollected", 0)),
        "raw": r,
    }


async def _open_position(session, base: str, cfg: Config, lower: float, upper: float,
                         base_amt: float, quote_amt: float) -> dict:
    body = {
        "network": NETWORK, "walletAddress": cfg.solana_wallet, "poolAddress": cfg.orca_pool,
        "lowerPrice": lower, "upperPrice": upper, "slippagePct": cfg.slippage_pct,
    }
    if base_amt > 0:
        body["baseTokenAmount"] = base_amt
    if quote_amt > 0:
        body["quoteTokenAmount"] = quote_amt
    r = await _post(session, base, "/connectors/orca/clmm/open-position", body)
    return {"ok": r.get("status") == 1, "signature": r.get("signature", ""),
            "position": r.get("positionAddress", ""), "raw": r}


# ── Runner ────────────────────────────────────────────────────────────────

async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    base = config.gateway_url.rstrip("/")
    out: list[str] = []

    async with aiohttp.ClientSession() as session:
        # ── Reads ────────────────────────────────────────────────────────
        try:
            nav = await _fetch_nav(session, base)
        except Exception as e:
            return f"Failed to fetch NAV: {e}"
        try:
            mid = await _fetch_pool_mid(session, base, config.orca_pool)
        except Exception as e:
            return f"Failed to fetch Orca pool price: {e}"
        try:
            positions = await _fetch_positions(session, base, config.solana_wallet, config.orca_pool)
        except Exception as e:
            return f"Failed to fetch positions: {e}"

        if len(positions) != 2:
            return (f"Expected 2 positions on pool {config.orca_pool}, found {len(positions)}. "
                    f"Aborting — refusing to rebalance an unexpected position set.")

        balances = await _fetch_balances(session, base, config.solana_wallet)
        dev_bps = (mid - nav) / nav * 10000

        # ── Geometry per position ────────────────────────────────────────
        plans = []
        for p in positions:
            lower, upper = float(p["lowerPrice"]), float(p["upperPrice"])
            hw = _half_width(lower, upper)
            new_lower, new_upper = _recenter(nav, hw)
            plans.append({
                "address": p["address"],
                "old_lower": lower, "old_upper": upper,
                "new_lower": new_lower, "new_upper": new_upper,
                "half_width_pct": hw * 100,
                "base": float(p.get("baseTokenAmount", 0)),
                "quote": float(p.get("quoteTokenAmount", 0)),
            })

        # ── Plan the shift ───────────────────────────────────────────────
        shift = await _plan_shift(session, base, config, nav, mid, balances)

        # ── Narrative ────────────────────────────────────────────────────
        out.append(f"USDM1/USDC Orca LP Refresh (dry_run={config.dry_run})")
        out.append(f"  NAV: {nav:.9f}   pool mid: {mid:.9f}   dev: {dev_bps:+.2f} bps")
        if balances:
            bstr = "  ".join(f"{k}={balances[k]}" for k in ("SOL", "USDC", "USDM1") if k in balances)
            out.append(f"  wallet: {bstr}")
        out.append("")

        out.append("  Price shift:")
        if shift is None:
            out.append(f"    none — within shift_skip_bps ({config.shift_skip_bps} bps)")
        elif shift["amount"] <= 0:
            out.append(f"    SKIPPED — no free {'USDC' if shift['side'] == 'BUY' else 'USDM1'} "
                       f"to fund the {shift['side']} shift (pool will stay {dev_bps:+.2f} bps off NAV)")
        else:
            est_dev = (shift["eff_price"] - nav) / nav * 10000
            cap_note = f"  [CAPPED by {shift.get('limited_by')} — ~{est_dev:+.2f} bps residual]" if shift.get("capped") else ""
            out.append(f"    {shift['side']} {shift['amount']:.4f} USDM1  "
                       f"(~{shift['notional']:.2f} USDC, eff {shift['eff_price']:.9f}){cap_note}")
            out.append(f"    notional cap: {shift.get('notional_cap', config.max_shift_usdc):.2f} USDC "
                       f"(max_shift={config.max_shift_usdc:.0f}, limited_by={shift.get('limited_by')})")
        out.append("")

        out.append("  Re-center positions (same widths, centered on NAV):")
        for i, pl in enumerate(plans, 1):
            out.append(f"    [{i}] {pl['address'][:6]}…  ±{pl['half_width_pct']:.3f}%")
            out.append(f"        old [{pl['old_lower']:.6f}, {pl['old_upper']:.6f}]")
            out.append(f"        new [{pl['new_lower']:.6f}, {pl['new_upper']:.6f}]")
            out.append(f"        recover ≈ {pl['base']:.4f} USDM1 + {pl['quote']:.4f} USDC")

        # ── GATE ─────────────────────────────────────────────────────────
        if config.dry_run:
            out.append("")
            out.append("  → DRY RUN: nothing executed. Re-invoke with dry_run=false to apply.")
            return "\n".join(out)

        # ── Live execution (strictly ordered, abort on failure) ──────────
        out.append("")
        out.append("  → EXECUTING")

        if shift is not None:
            out.append(f"    shift: {shift['side']} {shift['amount']:.4f} USDM1")
            try:
                sres = await _execute_shift(session, base, config, shift)
            except Exception as e:
                out.append(f"      shift raised: {e}  → aborted, no positions touched.")
                return "\n".join(out)
            out.append(f"      ok={sres['ok']} sig={sres['signature']}")
            if not sres["ok"]:
                out.append("      shift failed → aborted, no positions touched.")
                return "\n".join(out)

        for i, pl in enumerate(plans, 1):
            out.append(f"    [{i}] close {pl['address'][:6]}…")
            try:
                cres = await _close_position(session, base, config, pl["address"])
            except Exception as e:
                out.append(f"      close raised: {e}  → STOP (remaining positions untouched).")
                return "\n".join(out)
            if not cres["ok"]:
                out.append(f"      close failed (status≠1) → STOP. raw={cres['raw']}")
                return "\n".join(out)
            base_amt, quote_amt = cres["base"], cres["quote"]
            out.append(f"      closed ok sig={cres['signature']}  recovered {base_amt:.4f} USDM1 + {quote_amt:.4f} USDC")

            out.append(f"    [{i}] open [{pl['new_lower']:.6f}, {pl['new_upper']:.6f}]")
            try:
                ores = await _open_position(session, base, config, pl["new_lower"], pl["new_upper"], base_amt, quote_amt)
            except Exception as e:
                out.append(f"      open raised: {e}  → EXPOSED: position {pl['address'][:6]}… closed but not reopened.")
                return "\n".join(out)
            if not ores["ok"]:
                out.append(f"      open failed (status≠1) → EXPOSED: funds in wallet, not redeployed. raw={ores['raw']}")
                return "\n".join(out)
            out.append(f"      opened ok sig={ores['signature']}  new position {ores['position'][:6]}…")

        # ── Settle summary ───────────────────────────────────────────────
        try:
            final_positions = await _fetch_positions(session, base, config.solana_wallet, config.orca_pool)
            final_mid = await _fetch_pool_mid(session, base, config.orca_pool)
            final_dev = (final_mid - nav) / nav * 10000
            out.append("")
            out.append(f"  → DONE. pool mid {final_mid:.9f} (dev {final_dev:+.2f} bps), "
                       f"{len(final_positions)} positions:")
            for p in final_positions:
                out.append(f"      {p['address'][:6]}…  [{float(p['lowerPrice']):.6f}, {float(p['upperPrice']):.6f}]")
        except Exception as e:
            out.append(f"  (post-run readback failed: {e})")

        return "\n".join(out)
