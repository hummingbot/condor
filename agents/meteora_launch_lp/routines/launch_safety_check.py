"""Objective safety gates for a freshly-graduated Meteora DAMM v2 token, as executable checks.

Agent-local routine for meteora_launch_lp. Given a pool address, programmatically evaluates the
gates the agent cannot do through its tools: **mint-authority renounced** and **top-holder
concentration** (Solana RPC), plus **freeze-authority disabled**, **verified**, **LP lock**, and
**TVL** (from the Meteora DAMM v2 data API). Returns a PASS/FAIL verdict with per-gate reasons.

It does NOT check sellability (honeypot) — that needs a live two-way quote through the pool, which
the agent does itself with manage_amm(quote_swap, side=SELL) + (side=BUY). See the launch_safety_check
SKILL. This routine covers the deterministic on-chain/static gates only.

RPC: defaults to the public mainnet endpoint (rate-limited but fine for occasional checks); override
with rpc_url for a private endpoint. No credentials are hardcoded.
"""

import logging

import aiohttp
from pydantic import BaseModel, Field, field_validator
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

DAMM_V2_API = "https://damm-v2.datapi.meteora.ag/pools"
DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
SOL_MINT = "So11111111111111111111111111111111111111112"


class Config(BaseModel):
    """Objective safety gates for a graduated Meteora DAMM v2 token."""

    pool_address: str = Field(description="DAMM v2 pool address to vet (required)")
    rpc_url: str = Field(default=DEFAULT_RPC, description="Solana RPC endpoint")

    @field_validator("rpc_url", mode="before")
    @classmethod
    def _rpc_url_default(cls, v):
        # Callers (e.g. strategy configs) may pass an empty string meaning "no private RPC" —
        # fall back to the public endpoint instead of failing every RPC gate on an invalid URL.
        return v or DEFAULT_RPC

    max_top10_holder_pct: float = Field(
        default=60.0, description="Fail if top-10 holders exceed this % of supply"
    )
    require_mint_renounced: bool = Field(
        default=True, description="Fail if the token mint authority is not renounced"
    )
    require_freeze_disabled: bool = Field(
        default=True, description="Fail if the freeze authority is not disabled"
    )
    require_verified: bool = Field(
        default=False, description="Fail if the token is not verified"
    )
    min_lock_pct: float = Field(
        default=0.0,
        description="Require locked+vested liquidity ≥ this % of TVL (0 = off)",
    )
    min_tvl_usd: float = Field(default=10000.0, description="Fail below this TVL")


async def _rpc(session, url, method, params):
    async with session.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=aiohttp.ClientTimeout(total=20),
    ) as r:
        if r.status != 200:
            raise RuntimeError(f"RPC {method} -> HTTP {r.status}")
        data = await r.json()
        if "error" in data:
            raise RuntimeError(f"RPC {method} error: {data['error']}")
        return data.get("result")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    gates: list[tuple[str, bool, str]] = []  # (name, passed, detail)

    # 1. Pool + token metadata from the Meteora DAMM v2 API.
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                DAMM_V2_API,
                params={"query": config.pool_address, "page_size": 1},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    return f"launch_safety_check: Meteora API HTTP {resp.status}"
                pool = (await resp.json()).get("data", [{}])[0]
    except Exception as e:
        return f"launch_safety_check: failed to fetch pool {config.pool_address}: {e}"

    if not pool or pool.get("address") != config.pool_address:
        return f"launch_safety_check: pool {config.pool_address} not found in Meteora DAMM v2 feed."

    tx, ty = pool.get("token_x", {}), pool.get("token_y", {})
    base = ty if tx.get("address") == SOL_MINT else tx  # the non-SOL side
    base_mint = base.get("address")
    tvl = float(pool.get("tvl") or 0)
    cfg = pool.get("pool_config", {}) or {}

    # Static gates from the API.
    gates.append(
        (
            "tvl",
            tvl >= config.min_tvl_usd,
            f"${tvl:,.0f} (min ${config.min_tvl_usd:,.0f})",
        )
    )
    if config.require_verified:
        gates.append(
            (
                "verified",
                bool(base.get("is_verified")),
                f"{base.get('symbol')} verified={base.get('is_verified')}",
            )
        )
    if config.require_freeze_disabled:
        gates.append(
            (
                "freeze_disabled",
                bool(base.get("freeze_authority_disabled")),
                f"freeze_authority_disabled={base.get('freeze_authority_disabled')}",
            )
        )
    if config.min_lock_pct > 0:
        locked = float(pool.get("permanent_lock_liquidity") or 0)
        vested = sum(
            float(v or 0) for v in (pool.get("vested_liquidity") or {}).values()
        )
        lock_pct = (locked + vested) / tvl * 100 if tvl else 0.0
        gates.append(
            (
                "lp_lock",
                lock_pct >= config.min_lock_pct,
                f"{lock_pct:.1f}% locked/vested (min {config.min_lock_pct}%)",
            )
        )

    # 2. On-chain gates via RPC: mint authority + top-holder concentration.
    try:
        async with aiohttp.ClientSession() as session:
            if config.require_mint_renounced:
                info = await _rpc(
                    session,
                    config.rpc_url,
                    "getAccountInfo",
                    [base_mint, {"encoding": "jsonParsed"}],
                )
                parsed = (
                    ((info or {}).get("value") or {})
                    .get("data", {})
                    .get("parsed", {})
                    .get("info", {})
                )
                mint_auth = parsed.get("mintAuthority")
                gates.append(
                    (
                        "mint_renounced",
                        mint_auth is None,
                        f"mintAuthority={mint_auth or 'null (renounced)'}",
                    )
                )

            supply_res = await _rpc(
                session, config.rpc_url, "getTokenSupply", [base_mint]
            )
            total = float((supply_res or {}).get("value", {}).get("uiAmount") or 0)
            largest = await _rpc(
                session, config.rpc_url, "getTokenLargestAccounts", [base_mint]
            )
            accounts = (largest or {}).get("value", [])
            top10 = sum(float(a.get("uiAmount") or 0) for a in accounts[:10])
            top10_pct = (top10 / total * 100) if total else 100.0
            gates.append(
                (
                    "holder_concentration",
                    top10_pct <= config.max_top10_holder_pct,
                    f"top-10 hold {top10_pct:.1f}% (max {config.max_top10_holder_pct}%)",
                )
            )
    except Exception as e:
        gates.append(("rpc_checks", False, f"RPC checks failed: {e}"))

    passed_all = all(ok for _, ok, _ in gates)
    lines = [
        f"Safety check for {base.get('symbol','?')} ({base_mint}) — pool {config.pool_address}",
        f"VERDICT: {'PASS' if passed_all else 'FAIL'}",
        "",
    ]
    for name, ok, detail in gates:
        lines.append(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    lines.append("")
    lines.append(
        "NOTE: sellability (honeypot) is NOT checked here — round-trip a SELL and BUY quote via "
        "manage_amm(quote_swap) before entering. See the launch_safety_check skill."
    )
    return "\n".join(lines)
