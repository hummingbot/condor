"""Boot the Agora debate server in mock mode and exercise the full HTTP contract.

Verifies /health, a real /debate (full transcript), and /history — proving the
server, engine and Condor routine contract line up end to end, with no LLM key.
Run: /home/carlito/projects/condor/.venv/bin/python agents/agora/tests/smoke_server.py
"""

import asyncio
import sys
import time
from pathlib import Path

import httpx

SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(SERVER_DIR))

import uvicorn
from agora_server import app  # import after sys.path is set

PORT = 8511
BASE = f"http://127.0.0.1:{PORT}"


async def _main() -> int:
    cfg = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(cfg)
    import threading

    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    # wait for readiness
    async with httpx.AsyncClient() as client:
        for _ in range(40):
            try:
                r = await client.get(f"{BASE}/health", timeout=2)
                if r.status_code == 200:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.25)
        else:
            print("[FAIL] server did not become ready"); return 1

        h = r.json()
        print(f"[OK] /health -> status={h['status']} model={h['llm_provider']} pairs={h['pairs_tracked']}")

        # payload mirrors what agora_debate.py sends
        payload = {
            "trading_pair": "BTC-USDT",
            "pair": "BTC",
            "market_data": "Current price 63,977. Downtrend, RSI 51, SMA20 below SMA50. Period change -3.5%.",
            "fundamentals_data": "BTC market cap $1.28T, 24h vol $26.9B.",
            "news_data": "(none)",
            "social_data": "(none)",
        }
        d = await client.post(f"{BASE}/debate", json=payload, timeout=30)
        if d.status_code != 200:
            print(f"[FAIL] /debate -> {d.status_code} {d.text[:200]}"); return 1
        body = d.json()
        print(f"[OK] /debate -> {body['decision']} {body['direction']} conf={body['confidence']}%")
        print(f"     rationale[:90]: {body['rationale'][:90]}")
        print(f"     bull_case[:70]: {body['bull_case'][:70]}")
        print(f"     bear_case[:70]: {body['bear_case'][:70]}")
        print(f"     reports keys: {sorted(body['reports'].keys())}")

        must = ("decision", "direction", "confidence", "rationale", "risk_assessment",
                "bull_case", "bear_case", "reports", "request_id", "timestamp")
        missing = [k for k in must if k not in body]
        if missing:
            print(f"[FAIL] /debate missing fields: {missing}"); return 1

        hist = await client.get(f"{BASE}/history/BTC-USDT?limit=5", timeout=10)
        jh = hist.json()
        print(f"[OK] /history/BTC-USDT -> count={jh['count']}")
        if jh["count"] < 1:
            print("[FAIL] history empty"); return 1

    server.should_exit = True
    print("\n=== RESULT: SERVER CONTRACT OK ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
