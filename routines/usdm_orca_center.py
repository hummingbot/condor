"""Center the USDM1/USDC Orca pool price on the M1 NAV.

The interest-bearing USDM1 NAV ticks up continuously; the Orca pool price only
moves when traded, so the pool spot drifts below NAV between trades. This
routine nudges the pool spot back to NAV with a SINGLE direct Orca swap, using
wallet inventory — no M1 deposit/redeem, no whitelist dependency:

  - spot < NAV  → BUY USDM1 (USDC in)  → pushes spot up
  - spot > NAV  → SELL USDM1 (USDC out) → pushes spot down

Sizing: to land the *resulting spot* on NAV (not the average fill), target an
average fill price of (spot + NAV)/2 — under constant in-range liquidity the
average fill sits ~halfway between the old and new spot. The swap is capped by
both `max_order_usdc` and the wallet's free balance for the input token.

dry_run=True → print the plan and STOP (human gate).
dry_run=False → execute the one swap, then re-read pool spot.

Mirrors usdm_arb_execute.py's helpers and dry_run gate.
"""

CATEGORY = "Liquidity"

import json as _json
import logging
from typing import Optional

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

NETWORK = "mainnet-beta"
CHAIN_NETWORK = "solana-mainnet-beta"


class Config(BaseModel):
    """Center the USDM1/USDC Orca pool spot on the M1 NAV with one capped swap."""

    gateway_url: str = Field(default="http://localhost:15888", description="Gateway base URL")
    solana_wallet: str = Field(
        default="29hB4hN13nm79A2BrWxVzWo3KNrjxyNKrXgetBWe6xzh",
        description="Solana wallet funding the swap",
    )
    orca_pool: str = Field(
        default="6U4cpqp5eBGJdL2EsuNosnjDuxGMaDzHfTYiEsuwT5Ey",
        description="Orca USDM1/USDC pool address",
    )
    max_order_usdc: float = Field(default=2000.0, description="Hard cap on the swap notional (USDC)")
    skip_bps: float = Field(default=1.0, description="Skip if |spot - NAV| is already within this (bps)")
    slippage_pct: float = Field(default=1.0, description="Slippage % for the swap")
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


async def _fetch_nav(session, base: str) -> float:
    r = await _get(session, base, "/connectors/usdm/quote-swap", {
        "chainNetwork": CHAIN_NETWORK, "baseToken": "USDM0", "quoteToken": "USDM1",
        "amount": 100, "side": "SELL",
    })
    return float(r["usdm1Price"])


async def _fetch_pool(session, base: str, pool: str) -> dict:
    """Returns {spot, fee_frac} from on-chain pool-info."""
    r = await _get(session, base, "/connectors/orca/clmm/pool-info", {"network": NETWORK, "poolAddress": pool})
    fee_pct = float(r.get("feePct", 0.01))  # percent, e.g. 0.01 for a 0.01% tier
    return {"spot": float(r["price"]), "fee_frac": fee_pct / 100.0}


async def _fetch_spot(session, base: str, pool: str) -> float:
    return (await _fetch_pool(session, base, pool))["spot"]


async def _fetch_balances(session, base: str, wallet: str) -> dict:
    try:
        r = await _post(session, base, "/chains/solana/balances", {
            "network": NETWORK, "address": wallet, "tokens": ["SOL", "USDC", "USDM1"],
        })
        return r.get("balances", {}) if isinstance(r, dict) else {}
    except Exception as e:
        logger.warning(f"balance read failed: {e}")
        return {}


async def _quote(session, base: str, pool: str, side: str, amount: float, slip: float) -> Optional[dict]:
    try:
        return await _get(session, base, "/connectors/orca/clmm/quote-swap", {
            "network": NETWORK, "baseToken": "USDM1", "quoteToken": "USDC",
            "amount": amount, "side": side, "poolAddress": pool, "slippagePct": slip,
        })
    except Exception as e:
        logger.warning(f"quote {side} {amount} failed: {e}")
        return None


async def _plan(session, base: str, cfg: Config, nav: float, spot: float, fee_frac: float, balances: dict) -> Optional[dict]:
    """Size one swap so the resulting spot ≈ NAV. Returns None if already centered.

    Under constant in-range liquidity the curve-average fill sits halfway between
    old and new spot, so to land new_spot on NAV the curve-average must be
    (spot+NAV)/2. The quoted/realized average fill also carries the pool fee, so
    we inflate (BUY) / deflate (SELL) the curve target by the fee fraction."""
    dev_bps = (spot - nav) / nav * 10000
    if abs(dev_bps) <= cfg.skip_bps:
        return None
    side = "BUY" if spot < nav else "SELL"
    curve_avg = (spot + nav) / 2.0  # resulting spot ≈ NAV under constant liquidity
    # Fee sits on top of the curve cost: BUY pays more per unit, SELL receives less.
    target_avg = curve_avg * (1.0 + fee_frac) if side == "BUY" else curve_avg * (1.0 - fee_frac)

    free_usdc = float(balances.get("USDC", 0))
    free_usdm1 = float(balances.get("USDM1", 0))
    affordable = free_usdc if side == "BUY" else free_usdm1 * spot
    notional_cap = min(cfg.max_order_usdc, affordable)
    limited_by = "balance" if affordable < cfg.max_order_usdc else "max_order_usdc"
    cap_amt = notional_cap / nav
    if cap_amt <= 0:
        return {"side": side, "amount": 0.0, "dev_bps": dev_bps, "limited_by": limited_by,
                "notional": 0.0, "eff": spot, "pred_spot": spot, "residual_bps": dev_bps, "capped": True}

    ladder = [cap_amt * (i / 24.0) for i in range(1, 25)]
    best = None
    for amt in ladder:
        q = await _quote(session, base, cfg.orca_pool, side, amt, cfg.slippage_pct)
        if not q:
            continue
        ai, ao = float(q["amountIn"]), float(q["amountOut"])
        eff = (ai / ao) if side == "BUY" else (ao / ai)
        notional = ai if side == "BUY" else ao
        if notional > notional_cap:
            continue
        cand = {"side": side, "amount": amt, "eff": eff, "notional": notional, "dist": abs(eff - target_avg)}
        if best is None or cand["dist"] < best["dist"]:
            best = cand
    if not best:
        return None
    # Back out the fee-free curve average, then reflect to get the resulting spot.
    curve_avg_fill = best["eff"] / (1.0 + fee_frac) if side == "BUY" else best["eff"] / (1.0 - fee_frac)
    best["pred_spot"] = 2.0 * curve_avg_fill - spot
    best["residual_bps"] = (best["pred_spot"] - nav) / nav * 10000
    best["dev_bps"] = dev_bps
    best["limited_by"] = limited_by
    best["notional_cap"] = notional_cap
    best["capped"] = best["amount"] >= ladder[-1] * 0.999 and best["dist"] > 1e-9
    return best


async def _execute(session, base: str, cfg: Config, plan: dict) -> dict:
    r = await _post(session, base, "/connectors/orca/clmm/execute-swap", {
        "network": NETWORK, "baseToken": "USDM1", "quoteToken": "USDC",
        "side": plan["side"], "amount": plan["amount"], "poolAddress": cfg.orca_pool,
        "slippagePct": cfg.slippage_pct, "walletAddress": cfg.solana_wallet,
    })
    return {"ok": r.get("status") == 1, "signature": r.get("signature", ""), "raw": r}


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    base = config.gateway_url.rstrip("/")
    out: list[str] = []
    async with aiohttp.ClientSession() as session:
        try:
            nav = await _fetch_nav(session, base)
            pool = await _fetch_pool(session, base, config.orca_pool)
        except Exception as e:
            return f"Failed to read NAV/spot: {e}"
        spot, fee_frac = pool["spot"], pool["fee_frac"]
        balances = await _fetch_balances(session, base, config.solana_wallet)
        dev_bps = (spot - nav) / nav * 10000

        out.append(f"Orca USDM1/USDC center-on-NAV (dry_run={config.dry_run})")
        out.append(f"  NAV: {nav:.9f}   spot: {spot:.9f}   dev: {dev_bps:+.2f} bps")
        if balances:
            out.append("  wallet: " + "  ".join(f"{k}={balances[k]}" for k in ("SOL", "USDC", "USDM1") if k in balances))

        plan = await _plan(session, base, config, nav, spot, fee_frac, balances)
        if plan is None:
            out.append(f"  → already centered within skip_bps ({config.skip_bps} bps). No action.")
            return "\n".join(out)
        if plan["amount"] <= 0:
            out.append(f"  → cannot act: no free {'USDC' if plan['side'] == 'BUY' else 'USDM1'} to fund the {plan['side']}.")
            return "\n".join(out)

        cap_note = f"  [CAPPED by {plan['limited_by']}]" if plan.get("capped") else ""
        out.append("")
        out.append(f"  Action: {plan['side']} {plan['amount']:.4f} USDM1 (~{plan['notional']:.2f} USDC){cap_note}")
        out.append(f"    avg fill ≈ {plan['eff']:.9f}   → predicted spot ≈ {plan['pred_spot']:.9f} "
                   f"({plan['residual_bps']:+.2f} bps vs NAV)")
        out.append(f"    notional cap: {plan['notional_cap']:.2f} USDC (max_order={config.max_order_usdc:.0f}, limited_by={plan['limited_by']})")

        if config.dry_run:
            out.append("")
            out.append("  → DRY RUN: nothing executed. Re-invoke with dry_run=false to apply.")
            return "\n".join(out)

        out.append("")
        out.append(f"  → EXECUTING {plan['side']} {plan['amount']:.4f} USDM1")
        try:
            res = await _execute(session, base, config, plan)
        except Exception as e:
            out.append(f"    swap raised: {e}  → aborted.")
            return "\n".join(out)
        out.append(f"    ok={res['ok']} sig={res['signature']}")
        if not res["ok"]:
            out.append(f"    swap failed. raw={res['raw']}")
            return "\n".join(out)
        try:
            new_spot = await _fetch_spot(session, base, config.orca_pool)
            new_dev = (new_spot - nav) / nav * 10000
            out.append(f"  → DONE. spot {spot:.9f} → {new_spot:.9f}  (dev {dev_bps:+.2f} → {new_dev:+.2f} bps)")
        except Exception as e:
            out.append(f"  (post-swap spot readback failed: {e})")
        return "\n".join(out)
