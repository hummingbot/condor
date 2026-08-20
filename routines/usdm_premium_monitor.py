"""USDM1 Ethereum premium + pool-trade monitor.

Watches the Uniswap V3 USDM1/USDC pool against the M1 NAV oracle AND tracks
every swap in the pool, classifying the counterparty:

- "fleet"  — the 3-EOA bot fleet (~27k txs each, one shared router contract,
  ~daily $15–20 alternating trades; harmless volume/keeper activity).
- "whale"  — 0xca7315a9…, the low-activity wallet whose single $50,000 market
  buy on 2026-08-04 created the +290 bps premium arbed on 2026-08-08.
- "us"     — the arb wallet.
- "unknown"— anyone new (worth a look).

Alerts on: premium ≥ threshold_bps (with a sell-probe edge quote), and any
single swap ≥ whale_trade_usd from a non-us wallet — the whale returning is
the leading signal for the next premium spike, faster than the premium itself.

History (premium checks + trades) appends to data/usdm_premium_history.jsonl
for later charting. Read-only — never submits transactions.
"""

CATEGORY = "Monitoring"

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Continuous routine — has an internal loop
CONTINUOUS = True

GATEWAY_URL = "http://localhost:15888"
UNISWAP_POOL = "0x6f161ad0e297ecb9d1b33c048272ccc964cb4b6a"
SWAP_TOPIC = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
HISTORY = Path(__file__).resolve().parent.parent / "data" / "usdm_premium_history.jsonl"


def _eth_rpc() -> str:
    """RPC endpoint for log fetching. Public endpoints reject eth_getLogs, so a
    private URL is read from data/eth_rpc.txt (gitignored) or the env — never
    hardcoded here (this file may be committed)."""
    p = HISTORY.parent / "eth_rpc.txt"
    if p.exists():
        url = p.read_text().strip()
        if url:
            return url
    return os.environ.get("USDM_MONITOR_ETH_RPC", "https://ethereum-rpc.publicnode.com")


ETH_RPC = _eth_rpc()

FLEET = {"0x138c8b01fa4f1771730a211df6613e349379d52e",
         "0x60ffe2cd891bf9b590b2551cb36850450c330ed3",
         "0xbd93ffd91c48c013e8907c0cdc74b3231e80654f"}
WHALE = {"0xca7315a93c657024bdd207888690565c81b4d2c1"}
US = {"0x4c0001c6a45e53593c1d5771bf3707546555f310"}


class Config(BaseModel):
    """Premium + pool-trade monitor for the ETH Uniswap USDM1/USDC pool."""

    threshold_bps: float = Field(default=50.0, description="Alert when pool premium over NAV ≥ this many bps")
    whale_trade_usd: float = Field(default=5000.0, description="Alert immediately on any single non-us swap ≥ this USD size")
    probe_usdc: float = Field(default=1500.0, description="Sell-probe size in USDC used to project the per-cycle arb edge")
    interval_sec: int = Field(default=300, description="Check interval in seconds")
    cooldown_min: int = Field(default=60, description="Minutes between repeat premium alerts")


def _actor(addr: str) -> str:
    a = addr.lower()
    if a in US:
        return "us"
    if a in FLEET:
        return "fleet"
    if a in WHALE:
        return "whale"
    return "unknown"


def _sint(hexstr: str, bits: int = 256) -> int:
    v = int(hexstr, 16)
    return v - 2**bits if v >= 2 ** (bits - 1) else v


async def _rpc(session: aiohttp.ClientSession, payload) -> dict:
    async with session.post(ETH_RPC, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as r:
        return await r.json()


async def _api(session: aiohttp.ClientSession, path: str, params: Optional[dict] = None) -> dict:
    async with session.get(f"{GATEWAY_URL}{path}", params=params,
                           timeout=aiohttp.ClientTimeout(total=30)) as r:
        text = await r.text()
        if not r.ok:
            raise RuntimeError(f"GET {path} → HTTP {r.status}: {text[:200]}")
        return json.loads(text) if text else {}


async def _check_premium(session: aiohttp.ClientSession, probe_usdc: float) -> dict:
    nav_q = await _api(session, "/connectors/usdm/quote-swap", {
        "chainNetwork": "ethereum-mainnet", "baseToken": "USDM1",
        "quoteToken": "USDM0", "amount": 100, "side": "BUY"})
    nav = float(nav_q["usdm1Price"])
    pool = await _api(session, "/connectors/uniswap/clmm/pool-info", {
        "network": "mainnet", "poolAddress": UNISWAP_POOL})
    spot = float(pool["price"])
    out = {"nav": nav, "spot": spot, "premium_bps": (spot / nav - 1) * 1e4,
           "edge_usd": None}
    if out["premium_bps"] > 0:
        try:
            q = await _api(session, "/connectors/uniswap/clmm/quote-swap", {
                "network": "mainnet", "baseToken": "USDM1", "quoteToken": "USDC",
                "amount": round(probe_usdc / nav, 4), "side": "SELL",
                "poolAddress": UNISWAP_POOL, "slippagePct": 1})
            out["edge_usd"] = float(q["amountOut"]) - probe_usdc
        except Exception as e:
            logger.warning(f"usdm_premium_monitor: sell probe failed: {str(e)[:120]}")
    return out


async def _fetch_new_swaps(session: aiohttp.ClientSession, from_block: int, to_block: int) -> list:
    """Swap events in (from_block, to_block], with tx sender resolved."""
    r = await _rpc(session, {"jsonrpc": "2.0", "method": "eth_getLogs", "params": [{
        "address": UNISWAP_POOL, "topics": [SWAP_TOPIC],
        "fromBlock": hex(from_block + 1), "toBlock": hex(to_block)}], "id": 1})
    if "error" in r:
        raise RuntimeError(f"eth_getLogs: {r['error'].get('message', r['error'])[:120]}")
    logs = r.get("result") or []
    swaps = []
    for lg in logs:
        d = lg["data"][2:]
        words = [d[i:i + 64] for i in range(0, len(d), 64)]
        usdm1 = _sint(words[0]) / 1e18  # token0; + = into pool (a SELL of USDM1)
        usdc = _sint(words[1]) / 1e6
        price = (int(words[2], 16) / 2**96) ** 2 * 1e12
        tx = await _rpc(session, {"jsonrpc": "2.0", "method": "eth_getTransactionByHash",
                                  "params": [lg["transactionHash"]], "id": 1})
        sender = (tx.get("result") or {}).get("from", "0x?")
        swaps.append({
            "block": int(lg["blockNumber"], 16), "tx": lg["transactionHash"],
            "from": sender, "actor": _actor(sender),
            "side": "SELL" if usdm1 > 0 else "BUY",
            "usd": abs(usdc), "usdm1": abs(usdm1), "price": price,
        })
    return swaps


def _append_history(record: dict) -> None:
    try:
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        logger.error(f"usdm_premium_monitor: history write failed: {e}")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    chat_id = context._chat_id if context is not None and hasattr(context, "_chat_id") else None

    async def notify(text: str) -> None:
        if context is None or chat_id is None:
            print(text)
            return
        try:
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"usdm_premium_monitor: failed to send alert: {e}")

    above = False
    last_alert = 0.0
    checks = alerts = errors = 0
    last_block: Optional[int] = None
    start_time = time.time()

    from condor.reports import LiveReport

    report = LiveReport(
        "USDM1 ETH Premium Monitor",
        source_name="usdm_premium_monitor",
        tags=["monitoring", "usdm", "live"],
    )

    # Preload history so the chart survives restarts.
    prem_hist: list = []   # (ts, premium_bps)
    trade_hist: list = []  # swap dicts + ts
    try:
        if HISTORY.exists():
            for line in HISTORY.read_text().splitlines()[-2000:]:
                rec = json.loads(line)
                prem_hist.append((rec["ts"], rec["premium_bps"]))
                for s in rec.get("swaps", []):
                    trade_hist.append({**s, "ts": rec["ts"]})
    except Exception as e:
        logger.warning(f"usdm_premium_monitor: history preload failed: {e}")

    async def render_report(c: dict) -> None:
        import datetime as _dt

        import plotly.graph_objects as go

        report.clear()
        report.builder.manual_order()
        report.builder.kpi("Premium vs NAV", f"{c['premium_bps']:+.2f} bps")
        report.builder.kpi("Pool price", f"{c['spot']:.6f}")
        report.builder.kpi("NAV", f"{c['nav']:.6f}")
        if c["edge_usd"] is not None:
            report.builder.kpi(f"Edge / ${config.probe_usdc:,.0f} cycle", f"{c['edge_usd']:+,.2f} USDC")
        report.builder.kpi("Alerts", str(alerts))
        mins = int((time.time() - start_time) // 60)
        report.builder.kpi("Runtime", f"{mins // 60}h {mins % 60}m")

        if len(prem_hist) >= 2:
            xs = [_dt.datetime.fromtimestamp(t) for t, _ in prem_hist]
            ys = [p for _, p in prem_hist]
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=xs, y=ys, mode="lines", line=dict(color="#58a6ff", width=2),
                name="premium", hovertemplate="%{x|%b %d %H:%M}<br>%{y:+.1f} bps<extra></extra>"))
            fig.add_hline(y=config.threshold_bps, line=dict(color="#ef4444", width=1, dash="dash"),
                          annotation_text=f"alert {config.threshold_bps:.0f} bps")
            fig.add_hline(y=0, line=dict(color="rgba(255,255,255,0.35)", width=1, dash="dot"))
            for s in trade_hist:
                if s["actor"] != "us" and s["usd"] >= config.whale_trade_usd:
                    fig.add_vline(x=_dt.datetime.fromtimestamp(s["ts"]),
                                  line=dict(color="#eb6834", width=1, dash="dot"))
            fig.update_layout(
                height=340, template="plotly_dark", paper_bgcolor="#1a1a2e",
                plot_bgcolor="#16213e", margin=dict(t=40, b=30, l=50, r=20),
                title_text="Pool premium over NAV (bps)", title_font_size=14,
                showlegend=False)
            report.builder.plotly(fig)

        if trade_hist:
            rows = []
            for s in trade_hist[-25:][::-1]:
                rows.append({
                    "Time (UTC)": _dt.datetime.utcfromtimestamp(s["ts"]).strftime("%m-%d %H:%M"),
                    "Actor": s["actor"], "Side": s["side"],
                    "USD": f"{s['usd']:,.2f}", "Price": f"{s['price']:.5f}",
                    "From": s["from"][:10] + "…",
                })
            report.builder.markdown("### Pool trades (newest first)")
            report.builder.table(rows)
        else:
            report.builder.markdown("_No pool trades observed yet._")
        await report.update()

    async with aiohttp.ClientSession() as session:
        try:
            first = await _check_premium(session, config.probe_usdc)
            head = await _rpc(session, {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1})
            last_block = int(head["result"], 16)
        except Exception as e:
            return (f"usdm_premium_monitor: startup check failed — Gateway at {GATEWAY_URL} "
                    f"and RPC {ETH_RPC.split('//')[1][:25]} both up? ({str(e)[:150]})")

        await notify(
            f"🟢 *USDM1 premium+trade monitor started*\n"
            f"Premium {first['premium_bps']:+.1f} bps · alerts: premium ≥ {config.threshold_bps:.0f} bps "
            f"or any swap ≥ ${config.whale_trade_usd:,.0f} · every {config.interval_sec}s"
        )

        while True:
            try:
                c = await _check_premium(session, config.probe_usdc)
                head = await _rpc(session, {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1})
                block = int(head["result"], 16)
                swaps = await _fetch_new_swaps(session, last_block, block) if block > last_block else []
                last_block = block
                checks += 1
                now = time.time()

                rec = {"ts": int(now), "premium_bps": round(c["premium_bps"], 2),
                       "spot": c["spot"], "nav": c["nav"]}
                if swaps:
                    rec["swaps"] = swaps
                _append_history(rec)
                prem_hist.append((int(now), round(c["premium_bps"], 2)))
                del prem_hist[:-2000]
                for s in swaps:
                    trade_hist.append({**s, "ts": int(now)})
                del trade_hist[:-500]
                try:
                    await render_report(c)
                except Exception as e:
                    logger.error(f"usdm_premium_monitor: report render failed: {str(e)[:150]}")

                # Big-trade alert (the whale-returns signal) — immediate, no cooldown.
                for s in swaps:
                    if s["actor"] != "us" and s["usd"] >= config.whale_trade_usd:
                        who = {"whale": "the Aug-4 whale 0xca7315a9…", "fleet": "the bot fleet"}.get(
                            s["actor"], f"NEW wallet {s['from'][:10]}…")
                        await notify(
                            f"🐋 *Large USDM1 pool trade* — {who}\n"
                            f"{s['side']} ${s['usd']:,.0f} → pool now `{s['price']:.5f}` "
                            f"({(s['price'] / c['nav'] - 1) * 1e4:+.0f} bps vs NAV)\n"
                            f"tx `{s['tx'][:18]}…`"
                        )
                        alerts += 1

                if c["premium_bps"] >= config.threshold_bps:
                    if now - last_alert >= config.cooldown_min * 60:
                        edge = (f"\nSell probe (${config.probe_usdc:,.0f}): projected edge "
                                f"*{c['edge_usd']:+,.2f} USDC/cycle*") if c["edge_usd"] is not None else ""
                        await notify(
                            f"🚨 *USDM1 ETH premium alert*\n"
                            f"Pool `{c['spot']:.6f}` vs NAV `{c['nav']:.6f}` = *{c['premium_bps']:+.1f} bps*{edge}\n"
                            f"Runbook: mint at NAV → sell on Uniswap (arb wallet `0x4C0001…F310`)."
                        )
                        last_alert = now
                        alerts += 1
                    above = True
                elif above:
                    await notify(f"✅ USDM1 ETH premium back under {config.threshold_bps:.0f} bps "
                                 f"({c['premium_bps']:+.1f} bps)")
                    above = False
                    last_alert = 0.0

                if swaps:
                    logger.info(f"usdm_premium_monitor: {len(swaps)} new swap(s): " + "; ".join(
                        f"{s['actor']} {s['side']} ${s['usd']:,.0f}" for s in swaps))
                logger.info(f"usdm_premium_monitor: {c['premium_bps']:+.1f} bps")
                errors = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                errors += 1
                logger.error(f"usdm_premium_monitor: check failed ({errors}): {str(e)[:150]}")
                if errors >= 5:
                    await notify(f"🔴 USDM1 monitor stopping after {errors} consecutive failures "
                                 f"(last: {str(e)[:120]}). Gateway/RPC up?")
                    return f"Stopped after {errors} consecutive failures ({checks} checks, {alerts} alerts)"

            await asyncio.sleep(config.interval_sec)
