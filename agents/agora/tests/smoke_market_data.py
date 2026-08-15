"""Smoke-test agora_data pure functions against live public APIs (no Condor runtime)."""
import asyncio, sys, types
from pathlib import Path

# Stub telegram.ext so the module imports without the Condor runtime installed.
if "telegram" not in sys.modules:
    tg = types.ModuleType("telegram"); ext = types.ModuleType("telegram.ext")
    class _CT:  # minimal stand-in
        DEFAULT_TYPE = object
    ext.ContextTypes = _CT
    tg.ext = ext
    sys.modules["telegram"] = tg; sys.modules["telegram.ext"] = ext

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "routines"))
import aiohttp
import agora_data as ad


async def main():
    for source in ("bitget", "gate_io"):
        cfg = ad.Config(market_source=source, kline_limit=60)
        print(f"\n================ SOURCE: {source} ================")
        async with aiohttp.ClientSession() as s:
            results = await asyncio.gather(
                *(ad._gather_pair(s, p, cfg) for p in ad._parse_pairs(cfg.pairs))
            )
        for r in results:
            m = r["metrics"]
            status = "OK " if r["candle_count"] else "FAIL"
            print(f"[{status}] {r['pair']:<10} candles={r['candle_count']:<4} "
                  f"price={m.get('price', 0):>12,.4f}  chg={m.get('change_pct', 0):+6.2f}%  "
                  f"rsi={m.get('rsi', 0):>5.1f}  {m.get('trend','-')}")
            print(f"        fundamentals[{r['fundamentals_source']}]: {(r['fundamentals'] or '(none)')[:95]}")

    print("\n---------------- SAMPLE BRIEFING PACKET (Bitget XAU-USDT) ----------------")
    cfg = ad.Config(market_source="bitget", kline_limit=60)
    async with aiohttp.ClientSession() as s:
        r = await ad._gather_pair(s, "XAU-USDT", cfg)
    print(r["market_data"])
    print("\nFUNDAMENTALS:", r["fundamentals"])

asyncio.run(main())
