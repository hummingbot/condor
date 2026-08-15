"""Agora debate server — FastAPI wrapper around the self-contained debate engine.

Endpoints (contract consumed by the Condor `agora_debate` / `agora_init` routines):
  GET  /health      -> {status, graph_loaded, pairs_tracked, debates_run, uptime_seconds, llm_provider}
  POST /debate      -> {pair, decision, direction, confidence, rationale, risk_assessment,
                         bull_case, bear_case, reports, timestamp, request_id}
  GET  /history/{pair}?limit=20
  GET  /            -> metadata

Run:
  python agents/agora/server/agora_server.py --host 127.0.0.1 --port 8500
Set AGORA_API_KEY (or leave CUSTOM_LLM_API_KEY/OPENCODE_GO_API_KEY set in the env) for live LLM debates; leave mock_mode:true
in agora_config.yaml (or --mock) to exercise the full pipeline without a key.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from debate_engine import EngineConfig, run_debate

logger = logging.getLogger("agora")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [AGORA] %(levelname)s %(message)s",
)

CONFIG = EngineConfig.load()
SESSION_START = datetime.now(timezone.utc)
DECISION_HISTORY: dict[str, list[dict]] = {}

app = FastAPI(title="Agora — Multi-Agent Debate Trader", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)


# ── Models ────────────────────────────────────────────────────────────────
class DebateRequest(BaseModel):
    pair: str
    trading_pair: str | None = None
    market_data: str = ""
    news_data: str = ""
    social_data: str = ""
    fundamentals_data: str = ""


class DebateResponse(BaseModel):
    pair: str
    decision: str
    direction: str
    confidence: int
    rationale: str
    risk_assessment: str
    bull_case: str
    bear_case: str
    reports: dict[str, str]
    timestamp: str
    request_id: str


# ── Helpers ──────────────────────────────────────────────────────────────
def _track(pair: str, record: dict) -> None:
    DECISION_HISTORY.setdefault(pair, []).append(record)
    if len(DECISION_HISTORY[pair]) > 500:
        DECISION_HISTORY[pair] = DECISION_HISTORY[pair][-500:]


# ── Routes ───────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    total = sum(len(v) for v in DECISION_HISTORY.values())
    return {
        "status": "ready",
        "graph_loaded": True,
        "pairs_tracked": CONFIG.pairs,
        "debates_run": total,
        "uptime_seconds": (datetime.now(timezone.utc) - SESSION_START).total_seconds(),
        "llm_provider": CONFIG.model if not CONFIG.mock_mode else f"{CONFIG.model} (mock)",
    }


@app.post("/debate", response_model=DebateResponse)
async def debate(req: DebateRequest):
    pair = req.trading_pair or req.pair
    if not pair:
        raise HTTPException(status_code=400, detail="pair is required")

    # Validate the asset is on our debate list (by base symbol or full pair).
    base = pair.split("-")[0].upper()
    known = {p.split("-")[0].upper() for p in CONFIG.pairs}
    if base not in known and pair.upper() not in {p.upper() for p in CONFIG.pairs}:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported pair: {pair}. Supported: {CONFIG.pairs}",
        )

    if not CONFIG.mock_mode and not CONFIG.api_key_set:
        raise HTTPException(
            status_code=503,
            detail="No LLM API key configured (set AGORA_API_KEY, CUSTOM_LLM_API_KEY or OPENCODE_GO_API_KEY). Enable mock_mode to test without one.",
        )

    packet = {
        "market_data": req.market_data,
        "fundamentals": req.fundamentals_data,
        "news_data": req.news_data,
        "social_data": req.social_data,
    }

    try:
        result = await run_debate(pair, packet, CONFIG)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Debate failed for %s", pair)
        raise HTTPException(status_code=500, detail=f"Debate failed: {exc}")

    result["timestamp"] = datetime.now(timezone.utc).isoformat()
    result["request_id"] = hashlib.sha256(
        f"{pair}:{result['timestamp']}".encode()
    ).hexdigest()[:12]

    _track(pair, result)
    return DebateResponse(**{k: result[k] for k in DebateResponse.model_fields})


@app.get("/history/{pair}")
async def history(pair: str, limit: int = 20):
    return {"pair": pair, "decisions": DECISION_HISTORY.get(pair, [])[-limit:], "count": len(DECISION_HISTORY.get(pair, []))}


@app.get("/")
async def root():
    return {
        "name": "Agora Server",
        "version": "2.0.0",
        "framework": "self-contained debate engine (OpenAI-compatible)",
        "mock_mode": CONFIG.mock_mode,
        "endpoints": ["/health", "/debate", "/history/{pair}"],
    }


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Agora Debate Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8500)
    parser.add_argument("--mock", action="store_true", help="Force mock mode (no LLM key needed)")
    args = parser.parse_args()

    if args.mock:
        CONFIG.mock_mode = True

    if CONFIG.mock_mode:
        logger.warning("MOCK MODE: debates return deterministic verdicts (no real LLM).")
    elif not CONFIG.api_key_set:
        logger.warning("No AGORA_API_KEY / CUSTOM_LLM_API_KEY set — live debates will fail. Use --mock to test.")

    logger.info("Starting Agora Server on %s:%d (model=%s)", args.host, args.port, CONFIG.model)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
