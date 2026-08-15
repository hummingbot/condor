"""Live smoke test of the Agora debate engine against opencode-go / deepseek-v4-flash.

No server, no mock: calls the real endpoint the agent_key custom@opencode-go:... uses.
Run with the condor venv so CUSTOM_LLM_BASE_URL / OPENCODE_GO_API_KEY are present:
  /home/carlito/projects/condor/.venv/bin/python agents/agora/tests/live_debate.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Load condor's .env so the opencode-go credentials are available.
sys.path.insert(0, "/home/carlito/projects/condor")
from dotenv import load_dotenv  # condor ships python-dotenv

load_dotenv("/home/carlito/projects/condor/.env")

SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(SERVER_DIR))
from debate_engine import EngineConfig, run_debate

PAIRS = ["BTC-USDT", "XAU-USDT", "CL-USDT"]
PACKET = {
    "market_data": (
        "Current price 63,977. 4h candles. RSI 51, SMA20 below SMA50, mild downtrend. "
        "Period change -3.5%. Volume average. No extreme move."
    ),
    "fundamentals": "Market cap $1.28T, 24h vol $26.9B, no acute catalyst.",
    "news_data": "(none)",
    "social_data": "(none)",
}


async def main() -> int:
    cfg = EngineConfig.load()
    cfg.mock_mode = False  # force the real LLM path
    # The agent_key spec is custom@opencode-go:deepseek-v4-flash, but opencode-go
    # currently region-gates deepseek-v4-flash (China opt-in -> 403 RegionError).
    # deepseek-v4-pro is the same provider/model family, not gated. Default the
    # live test to it; override with LIVE_MODEL=deepseek-v4-flash to match spec.
    cfg.model = os.getenv("LIVE_MODEL", "deepseek-v4-pro")
    print(f"provider model   : {cfg.model}")
    print(f"backend_url     : {cfg.backend_url}")
    print(f"api_key_present : {cfg.api_key_set}")
    if not cfg.api_key_set:
        print("[FAIL] no API key; cannot run live"); return 2

    ok = 0
    for pair in PAIRS:
        print(f"\n=== LIVE DEBATE: {pair} ===")
        try:
            res = await run_debate(pair, PACKET, cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"  [ERROR] {type(exc).__name__}: {exc}")
            continue
        print(f"  decision : {res['decision']} {res['direction']}  conf={res['confidence']}%")
        print(f"  rationale: {res['rationale'][:160].strip()}")
        print(f"  bull[:120]: {res['bull_case'][:120].strip()}")
        print(f"  bear[:120]: {res['bear_case'][:120].strip()}")
        print(f"  risk[:120]: {res['risk_assessment'][:120].strip()}")
        if res["decision"] in ("BUY", "SELL", "HOLD") and res["rationale"]:
            ok += 1
    print(f"\n=== RESULT: {ok}/{len(PAIRS)} live debates returned a real verdict ===")
    return 0 if ok == len(PAIRS) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
