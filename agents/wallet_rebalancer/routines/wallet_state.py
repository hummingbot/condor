"""Read the configured Solana wallet's SOL/USDC state vs target allocation.

Agent-local to wallet_rebalancer. Identity and endpoints come from the
platform, never hardcoded: the wallet defaults to the venue's DEFAULT ACCOUNT
in the accounts store, and the RPC endpoint is ``CONDOR_SOLANA_RPC`` from the
environment (the same variable ``condor accounts add solana`` consumes),
falling back to the venue's documented public default only when unset. The
RPC URL may embed a credential — it is never echoed in output or reports.

Returns JSON: balances, USD values, current vs target allocation, per-asset
drift, whether the goal tolerance is met, and ONE suggested swap (direction +
base amount) to move toward target — the agent judges and executes, this
routine never trades.
"""

import json
import logging
import os

import aiohttp
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

# The venue's documented public default (CredField rpc_url: "public default
# when omitted") — used only when CONDOR_SOLANA_RPC is not set.
PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
PRICE_URL = "https://lite-api.jup.ag/price/v3"
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def _rpc_url() -> tuple[str, str]:
    """(url, source_label). Never log/echo the url — it may embed a key."""
    env_url = (os.environ.get("CONDOR_SOLANA_RPC") or "").strip()
    if env_url:
        return env_url, "env:CONDOR_SOLANA_RPC"
    return PUBLIC_RPC, "public-default"


def _default_wallet() -> str:
    """The venue's default account from the accounts store."""
    from condor.executors.wallets import account_store

    return account_store().resolve("solana", None).custody_address


class Config(BaseModel):
    """Read wallet SOL/USDC allocation vs targets and suggest the rebalancing swap."""

    wallet: str = Field(
        default="",
        description="Solana wallet address; empty = the venue's default "
        "account from the accounts store",
    )
    targets: dict = Field(
        default={"SOL": 0.90, "USDC": 0.10},
        description="Target allocation fractions by symbol (must sum to ~1)",
    )
    tolerance_pct: float = Field(
        default=1.0, description="Goal tolerance: max abs drift in percentage points"
    )
    fee_reserve_sol: float = Field(
        default=0.02, description="SOL never spent (network fees)"
    )


async def _rpc(
    session: aiohttp.ClientSession, url: str, method: str, params: list
) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    async with session.post(url, json=payload) as resp:
        if resp.status != 200:
            raise RuntimeError(f"solana rpc {method} -> HTTP {resp.status}")
        data = await resp.json()
    if "error" in data:
        raise RuntimeError(f"solana rpc {method} -> {data['error']}")
    return data.get("result") or {}


async def run(config: Config, context=None) -> str:
    tol = float(config.tolerance_pct)
    rpc_url, rpc_source = _rpc_url()
    try:
        wallet = config.wallet.strip() or _default_wallet()
    except Exception as e:
        return json.dumps({"error": f"could not resolve default solana account: {e}"})

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20)
        ) as session:
            sol_res = await _rpc(session, rpc_url, "getBalance", [wallet])
            sol_balance = float(sol_res.get("value", 0)) / 1e9

            tok_res = await _rpc(
                session,
                rpc_url,
                "getTokenAccountsByOwner",
                [wallet, {"mint": USDC_MINT}, {"encoding": "jsonParsed"}],
            )
            usdc_balance = 0.0
            for acct in tok_res.get("value") or []:
                info = (((acct.get("account") or {}).get("data") or {})
                        .get("parsed") or {}).get("info") or {}
                amt = (info.get("tokenAmount") or {}).get("uiAmount")
                usdc_balance += float(amt or 0.0)

            async with session.get(PRICE_URL, params={"ids": SOL_MINT}) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"jupiter price -> HTTP {resp.status}")
                price_data = await resp.json()
            sol_price = float((price_data.get(SOL_MINT) or {}).get("usdPrice") or 0.0)
            if sol_price <= 0:
                raise RuntimeError(f"jupiter price unparseable: {price_data}")
    except Exception as e:
        return json.dumps({"error": f"wallet_state failed ({rpc_source}): {e}"})

    sol_usd = sol_balance * sol_price
    usdc_usd = usdc_balance  # 1 USDC ~= 1 USD
    total_usd = sol_usd + usdc_usd
    if total_usd <= 0:
        return json.dumps({"error": "wallet has no SOL or USDC value"})

    current = {"SOL": sol_usd / total_usd, "USDC": usdc_usd / total_usd}
    targets = {k: float(v) for k, v in (config.targets or {}).items()}
    drift_pts = {
        k: round((current.get(k, 0.0) - targets.get(k, 0.0)) * 100, 3)
        for k in sorted(set(current) | set(targets))
    }
    goal_met = all(abs(v) <= tol for v in drift_pts.values())

    # ONE suggested swap toward target (SOL<->USDC only).
    suggestion = None
    if not goal_met:
        sol_gap_usd = (targets.get("SOL", 0.0) - current.get("SOL", 0.0)) * total_usd
        if sol_gap_usd < 0:  # too much SOL -> sell SOL for USDC
            sell_sol = min(
                -sol_gap_usd / sol_price, max(sol_balance - config.fee_reserve_sol, 0)
            )
            suggestion = {
                "action": "SELL SOL for USDC",
                "side": "SELL",
                "base": "SOL",
                "quote": "USDC",
                "amount_base_sol": round(sell_sol, 4),
                "est_usd": round(sell_sol * sol_price, 2),
            }
        else:  # too little SOL -> buy SOL with USDC
            spend_usdc = min(sol_gap_usd, usdc_balance)
            suggestion = {
                "action": "BUY SOL with USDC",
                "side": "BUY",
                "base": "SOL",
                "quote": "USDC",
                "amount_base_sol": round(spend_usdc / sol_price, 4),
                "est_usd": round(spend_usdc, 2),
            }

    result = {
        "wallet": wallet,
        "rpc_source": rpc_source,  # label only — the URL itself may embed a key
        "sol_price_usd": round(sol_price, 4),
        "balances": {"SOL": round(sol_balance, 6), "USDC": round(usdc_balance, 4)},
        "usd_values": {"SOL": round(sol_usd, 2), "USDC": round(usdc_usd, 2),
                       "total": round(total_usd, 2)},
        "allocation_pct": {k: round(v * 100, 2) for k, v in current.items()},
        "target_pct": {k: round(v * 100, 2) for k, v in targets.items()},
        "drift_pts": drift_pts,
        "tolerance_pts": tol,
        "goal_met": goal_met,
        "fee_reserve_sol": config.fee_reserve_sol,
        "suggested_swap": suggestion,
    }

    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder("Wallet Rebalance State")
        builder.source("routine", "wallet_state").tags(["wallet", "rebalance"])
        builder.kpi("Total Value", f"${total_usd:,.2f}")
        builder.kpi("SOL", f"{current['SOL'] * 100:.1f}% (target {targets.get('SOL', 0) * 100:.0f}%)")
        builder.kpi("USDC", f"{current['USDC'] * 100:.1f}% (target {targets.get('USDC', 0) * 100:.0f}%)")
        builder.kpi("Goal met", "yes" if goal_met else "no")
        builder.table(
            [
                {"Asset": k, "Balance": result["balances"].get(k, 0),
                 "USD": result["usd_values"].get(k, 0),
                 "Current %": result["allocation_pct"].get(k, 0),
                 "Target %": result["target_pct"].get(k, 0),
                 "Drift (pts)": drift_pts.get(k, 0)}
                for k in sorted(targets)
            ],
            ["Asset", "Balance", "USD", "Current %", "Target %", "Drift (pts)"],
        )
        if suggestion:
            builder.markdown(f"**Suggested swap:** {suggestion['action']} — "
                             f"{suggestion['amount_base_sol']} SOL (~${suggestion['est_usd']})")
        builder.manual_order()
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return json.dumps(result, indent=2)
