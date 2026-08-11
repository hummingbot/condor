"""Smart-Money Flow read: cross-market capital-flow + on-chain (Solana) pulse.

Pulls a flow-and-positioning composite that a price chart alone cannot show:
  * CoinGecko /global        -> risk regime (mcap momentum, BTC dominance)
  * CoinGecko /coins/markets -> per-asset flow intensity (volume/mcap, 24h chg)
  * CoinGecko /search/trending -> sector/asset momentum (what is heating up)
  * Solana / GeckoTerminal   -> on-chain DeFi flow pulse (top-pool volume +
                                momentum + TVL) — the crypto-native signal.
                                Solana has materially deeper on-chain liquidity
                                than XRPL, so it is the DEFAULT on-chain source.
  * XRPL JSON-RPC (optional) -> legacy DEX AMM pulse, off by default.

All fetches are defensive and async; any source failing degrades gracefully
rather than raising into the tick. The orchestration lives in `run()`; the
pure data + scoring functions are importable for testing without Telegram.

Execution venue is independent of this signal: pair the read with Derive perps
(`derive_perpetual`) — or any perp connector — via the agent's trading context.
"""
import asyncio
import logging
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

# Keyless endpoints (CoinGecko + GeckoTerminal free tier, public Solana RPC).
CG = "https://api.coingecko.com/api/v3"
GECKO = "https://api.geckoterminal.com/api/v2"
XRPL_NODES = [
    "https://xrplcluster.com/",
    "https://s1.ripple.com:51234",
    "https://s2.ripple.com:51234",
]

# Default cross-market context assets (also the tradable perps on Derive).
CONTEXT_ASSETS = [
    {"id": "bitcoin", "symbol": "BTC"},
    {"id": "ethereum", "symbol": "ETH"},
    {"id": "solana", "symbol": "SOL"},
]
SOL_MINT = "So11111111111111111111111111111111111111112"  # anchors the Solana pulse


class Config(BaseModel):
    """Compute the Smart-Money Flow composite: cross-market + Solana on-chain pulse."""
    context_assets: str = Field(
        default="bitcoin,ethereum,solana",
        description="Comma-separated CoinGecko ids for regime + per-asset flow read",
    )
    solana_top_n: int = Field(
        default=10,
        description="Number of SOL top pools to pull for the on-chain DeFi flow pulse",
    )
    xrpl_amm_issuer: str = Field(
        default="",
        description="OPTIONAL legacy XRPL issuer for an RLUSD/XRP AMM pulse (empty = skip)",
    )
    xrpl_watch_addresses: str = Field(
        default="",
        description="OPTIONAL comma-separated XRPL addresses to watch (empty = skip)",
    )
    top_n_trending: int = Field(default=7, description="Trending coins to factor into momentum")


# ── helpers ────────────────────────────────────────────────────────────────

async def _get_json(url: str, timeout: float = 12) -> "dict | list | None":
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                logger.warning("flow: %s -> HTTP %s", url, resp.status_code)
                return None
            return resp.json()
    except Exception as exc:  # never raise into a tick
        logger.warning("flow: fetch failed %s: %s", url, type(exc).__name__)
        return None


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ── raw fetchers (pure async, importable) ──────────────────────────────────

async def fetch_global() -> "dict | list | None":
    return await _get_json(f"{CG}/global")


async def fetch_trending(top_n: int = 7) -> list:
    data = await _get_json(f"{CG}/search/trending")
    if not data:
        return []
    coins = data.get("coins", [])[:top_n]
    return [c.get("item", {}).get("symbol", "").upper() for c in coins]


async def fetch_markets(ids: list[str]) -> "dict | list | None":
    id_str = "%2C".join(ids)
    return await _get_json(
        f"{CG}/coins/markets?vs_currency=usd&ids={id_str}"
        f"&order=market_cap_desc&per_page=50&page=1&sparkline=false"
    )


async def fetch_solana_pulse(top_n: int = 10) -> dict | None:
    """On-chain DeFi flow pulse anchored on SOL's own top pools (GeckoTerminal, keyless).

    We query the SOL token's pools rather than the global top-volume list, because
    the global list is dominated by fresh memecoin/pump pools with meaningless
    momentum. SOL's top pools (SOL/USDC, staking LP pairs) are the representative
    read. Solana carries materially deeper on-chain liquidity than XRPL, so it is
    the DEFAULT on-chain signal source.
    """
    data = await _get_json(f"{GECKO}/networks/solana/tokens/{SOL_MINT}/pools?page=1&include=base_token,dex")
    if not data:
        return None
    pools = []
    for p in data.get("data", [])[:top_n]:
        a = p.get("attributes", {})
        vol = _num(a.get("volume_usd", {}).get("h24"))
        tvl = _num(a.get("reserve_in_usd"))
        if tvl < 50_000:  # skip dust/illiquid pools (meaningless momentum)
            continue
        pools.append({
            "name": a.get("name", "?"),
            "dex": a.get("dex_id", ""),
            "vol24h": vol,
            "chg24h": _num(a.get("price_change_percentage", {}).get("h24")),
            "tvl": tvl,
        })
    if not pools:
        return None
    total_vol = sum(p["vol24h"] for p in pools)
    chgs = sorted(p["chg24h"] for p in pools if p["chg24h"] is not None)
    med_chg = chgs[len(chgs) // 2] if chgs else 0.0
    import math

    vol_score = _clamp(math.log10(total_vol + 1) / 9.0)  # ~1e9 -> ~1.0
    mom_score = _clamp(med_chg / 15.0)
    return {
        "pools": pools,
        "total_vol24h": round(total_vol, 2),
        "median_chg24h": round(med_chg, 2),
        "flow_score": round(_clamp(vol_score * 0.5 + mom_score * 0.5), 3),
    }


async def fetch_xrpl_amm_pulse(issuer: str) -> dict | None:
    """OPTIONAL legacy XRPL RLUSD/XRP AMM liquidity pulse (graceful if unreachable)."""
    if not issuer:
        return None
    payload = {
        "method": "amm_info",
        "params": [{"asset": {"currency": "XRP"},
                    "asset2": {"currency": "RLUSD", "issuer": issuer}}],
    }
    import httpx

    for node in XRPL_NODES:
        try:
            async with httpx.AsyncClient(timeout=12) as http:
                resp = await http.post(node, json=payload)
                data = resp.json().get("result", {})
            amm = data.get("amm")
            if not amm:
                continue
            amount = amm.get("amount", {})
            try:
                xrp = float(amount.get("value", 0)) if isinstance(amount, dict) else 0.0
            except Exception:
                xrp = 0.0
            return {"xrp_liquidity": xrp, "trading_fee_bps": amm.get("trading_fee")}
        except Exception as exc:
            logger.warning("flow: xrpl amm pulse failed on %s: %s", node, type(exc).__name__)
            continue
    return None


# ── synthesis (pure, importable) ───────────────────────────────────────────

def synthesize(global_d, markets_d, trending_syms, solana_pulse, xrpl_amm) -> dict:
    """Turn raw fetches into a multi-asset flow composite. Returns a structured dict."""
    # --- risk regime from /global ---
    regime = {"label": "NEUTRAL", "mcap_change_24h": 0.0, "btc_dominance": 0.0,
              "eth_dominance": 0.0, "score": 0.0}
    if global_d:
        g = global_d.get("data", {})
        mc = _num(g.get("market_cap_change_percentage_24h_usd"))
        dom = g.get("market_cap_percentage", {})
        btc_d = _num(dom.get("btc"))
        eth_d = _num(dom.get("eth"))
        regime["mcap_change_24h"] = round(mc, 2)
        regime["btc_dominance"] = round(btc_d, 2)
        regime["eth_dominance"] = round(eth_d, 2)
        risk_score = _clamp(mc / 5.0) - _clamp((btc_d - 50.0) / 10.0) * 0.3
        regime["score"] = round(_clamp(risk_score), 3)
        regime["label"] = "RISK-ON" if regime["score"] > 0.15 else ("RISK-OFF" if regime["score"] < -0.15 else "NEUTRAL")

    # --- per-asset flow intensity from /coins/markets ---
    trending_set = {s.upper() for s in trending_syms}
    assets_out = []
    for m in (markets_d or []):
        sym = (m.get("symbol") or "").upper()
        mcap = _num(m.get("market_cap"))
        vol = _num(m.get("total_volume"))
        chg = _num(m.get("price_change_percentage_24h"))
        vol_mcap = (vol / mcap) if mcap > 0 else 0.0
        # Flow score: 24h momentum is the dominant driver (a strong day should
        # trigger), volume intensity is a secondary confirmation. Clamp the
        # volume term so thin books don't dominate; amplify the change term so a
        # real +/-6-10% day clears the +/-0.4 entry threshold.
        flow = _clamp(vol_mcap * 5.0, -0.5, 0.5) * 0.4 + _clamp(chg / 6.0) * 0.6
        if sym in trending_set:
            flow = _clamp(flow + 0.1)
        assets_out.append({
            "symbol": sym,
            "pair": f"{sym}-USDC",
            "flow_score": round(flow, 3),
            "volume_to_mcap": round(vol_mcap, 3),
            "price_change_24h": round(chg, 2),
            "trending": sym in trending_set,
        })
    assets_out.sort(key=lambda a: abs(a["flow_score"]), reverse=True)

    # --- on-chain pulse (Solana default; XRPL optional) ---
    onchain = {"source": "solana", "solana": solana_pulse, "xrpl": xrpl_amm}
    solana_flow = (solana_pulse or {}).get("flow_score", 0.0)

    # --- directional verdict (perps: LONG / SHORT / HOLD) ---
    best = assets_out[0] if assets_out else None
    direction = "HOLD"
    rationale = "no decisive flow"
    if best and abs(best["flow_score"]) >= 0.4 and regime["label"] != "NEUTRAL":
        if best["flow_score"] > 0 and regime["label"] == "RISK-ON":
            direction = "LONG"
            rationale = "RISK-ON + positive flow"
        elif best["flow_score"] < 0 and regime["label"] == "RISK-OFF":
            direction = "SHORT"
            rationale = "RISK-OFF + negative flow"
        else:
            rationale = "regime/flow conflict"
    elif best and abs(best["flow_score"]) < 0.4:
        rationale = "flow below entry threshold"

    return {
        "regime": regime,
        "assets": assets_out,
        "onchain": onchain,
        "solana_flow": round(solana_flow, 3),
        "best_asset": best,
        "direction": direction,
        "rationale": rationale,
    }


def _format_verdict(sig: dict) -> str:
    r = sig["regime"]
    best = sig.get("best_asset")
    lines = [
        "## Smart-Money Flow Read",
        f"- Regime: **{r['label']}** (mcap 24h {r['mcap_change_24h']:+.2f}%, BTC dom {r['btc_dominance']:.1f}%)",
        f"- Verdict: **{sig['direction']}** ({sig['rationale']})",
    ]
    if best:
        lines.append(f"- Best flow: **{best['symbol']}** score {best['flow_score']:+.2f} "
                     f"(vol/mcap {best['volume_to_mcap']}, 24h {best['price_change_24h']:+.2f}%, trend={best['trending']})")
    sp = sig["onchain"].get("solana")
    if sp:
        lines.append(f"- Solana on-chain pulse: flow {sp['flow_score']:+.2f}, "
                     f"top-pool vol24h ${sp['total_vol24h']:,.0f}, median chg {sp['median_chg24h']:+.1f}%")
        for p in sp["pools"][:4]:
            lines.append(f"    - {p['name'][:24]:24} vol ${p['vol24h']:,.0f} chg {p['chg24h']:+.1f}%")
    if sig["onchain"].get("xrpl"):
        lines.append(f"- XRPL AMM pulse: {sig['onchain']['xrpl']}")
    lines.append("- Per-asset:")
    for a in sig["assets"][:5]:
        lines.append(f"    - {a['symbol']}: {a['flow_score']:+.2f}  "
                     f"(vol/mcap {a['volume_to_mcap']}, 24h {a['price_change_24h']:+.2f}%, trend={a['trending']})")
    return "\n".join(lines)


# ── orchestration ──────────────────────────────────────────────────────────

async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Entry point invoked by manage_routines. Fetches, scores, reports, returns."""
    ids = [s.strip() for s in config.context_assets.split(",") if s.strip()]

    global_d, trending, markets, solana = await asyncio.gather(
        fetch_global(),
        fetch_trending(config.top_n_trending),
        fetch_markets(ids),
        fetch_solana_pulse(config.solana_top_n),
    )
    xrpl_amm = await fetch_xrpl_amm_pulse(config.xrpl_amm_issuer) if config.xrpl_amm_issuer else None

    sig = synthesize(global_d, markets, trending, solana, xrpl_amm)

    # Rich dashboard via ReportBuilder (mandatory in every routine).
    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder("Smart-Money Flow")
        builder.source("routine", "onchain_flow").tags(["flow", "on-chain", "solana", "smart-money"])
        builder.section("01 / RISK REGIME", "Cross-market risk-on/off from total mcap momentum and BTC dominance")
        builder.kpi("Regime", sig["regime"]["label"])
        builder.kpi("Mcap 24h", f"{sig['regime']['mcap_change_24h']:+.2f}%")
        builder.kpi("BTC Dominance", f"{sig['regime']['btc_dominance']:.1f}%")
        builder.kpi("Direction", sig["direction"])
        builder.kpi("Solana Flow", f"{sig['solana_flow']:+.2f}")
        sp = sig["onchain"].get("solana")
        if sp:
            builder.section("02 / SOLANA ON-CHAIN PULSE", "SOL top pools by 24h volume — crypto-native flow")
            builder.kpi("Top-pool Vol 24h", f"${sp['total_vol24h']:,.0f}")
            builder.kpi("Median Chg 24h", f"{sp['median_chg24h']:+.1f}%")
            builder.kpi("On-chain Flow", f"{sp['flow_score']:+.2f}")
            builder.table(sp["pools"][:8], ["name", "dex", "vol24h", "chg24h", "tvl"])
        if sig["assets"]:
            builder.section("03 / CROSS-MARKET CONTEXT", "Market-wide flow intensity (BTC/ETH/SOL)")
            builder.table(sig["assets"][:6], ["symbol", "pair", "flow_score", "volume_to_mcap", "price_change_24h", "trending"])
        builder.markdown(_format_verdict(sig))
        builder.manual_order()
        await builder.save()
    except Exception as exc:
        logger.warning("flow: report generation failed: %s", exc)

    return _format_verdict(sig)
