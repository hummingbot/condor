"""Run the multi-agent debate for each pair and publish the transcript.

Reads the packets cached by `agora_data`, POSTs them to the Agora debate server
(TradingAgents / LangGraph), stores each verdict, and renders the debate floor
to the dashboard — bull case, bear case, risk ruling, final verdict.

The transcript report is the point of this agent: judges can read *why* every
position was taken, not just that it was taken.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

_DECISIVE_HEDGES = ("might", "could", "possibly", "perhaps", "unclear", "uncertain")


class Config(BaseModel):
    """Run the Agora debate pipeline for each configured pair."""

    pairs: str = Field(
        default="BTC-USDT,XAU-USDT,CL-USDT",
        description="Comma-separated pairs to debate (Hummingbot format)",
    )
    server_url: str = Field(
        default="http://127.0.0.1:8500", description="Agora debate server base URL"
    )
    debate_timeout_seconds: int = Field(
        default=120, description="Per-pair debate timeout"
    )
    max_retries: int = Field(default=1, description="Retries per pair on failure")
    min_confidence: int = Field(
        default=65, description="Confidence floor surfaced as ACTIONABLE on the report"
    )


def _parse_pairs(raw: str) -> list[str]:
    return [p.strip().upper() for p in raw.split(",") if p.strip()]


def _mem_key(pair: str, kind: str) -> str:
    return f"agora_{kind}_{pair.replace('-', '_')}"


def _score_confidence(payload: dict) -> int:
    """Debate-consensus confidence when the server does not supply one.

    Mirrors the documented Agora rubric: a thorough, decisive debate earns
    conviction; a hedged or thin one does not.
    """
    reported = payload.get("confidence")
    if isinstance(reported, (int, float)) and 0 < reported <= 100:
        return int(reported)
    return _fallback_confidence(payload)


def _fallback_confidence(payload: dict) -> int:
    """Compute consensus confidence when the server did not supply one.

    Mirrors the server rubric: a thorough, decisive debate earns conviction; a
    hedged or thin one does not.
    """
    invest = str(payload.get("bull_case", "")) + str(payload.get("bear_case", ""))
    risk = str(payload.get("rationale", "")) + str(payload.get("risk_assessment", ""))
    judge = str(payload.get("rationale", ""))
    risk_judge = str(payload.get("risk_assessment", ""))

    score = 60
    if len(invest) > 2000:
        score += 10
    if len(risk) > 1000:
        score += 5
    if judge and not any(h in judge.lower() for h in _DECISIVE_HEDGES):
        score += 10
    if risk_judge and not any(h in risk_judge.lower() for h in _DECISIVE_HEDGES):
        score += 5
    return min(score, 95)


def _normalise(payload: dict, pair: str) -> dict:
    """Flatten a debate response into the record the tick consumes.

    Accepts the Agora v2 server contract (decision/direction/confidence plus
    bull_case/bear_case/rationale/risk_assessment/reports at the top level).
    """
    decision = str(payload.get("decision") or payload.get("final_decision") or "HOLD").upper()
    if "BUY" in decision or "LONG" in decision:
        decision, direction = "BUY", "LONG"
    elif "SELL" in decision or "SHORT" in decision:
        decision, direction = "SELL", "SHORT"
    else:
        decision, direction = "HOLD", "NONE"

    reports = payload.get("reports") or {}
    if isinstance(reports, dict):
        reports = {k: str(v)[:600] for k, v in reports.items()}

    return {
        "pair": pair,
        "decision": decision,
        "direction": direction,
        "confidence": _score_confidence(payload),
        "rationale": str(payload.get("rationale", ""))[:1200],
        "risk_assessment": str(payload.get("risk_assessment", ""))[:1200],
        "bull_case": str(payload.get("bull_case", ""))[:1200],
        "bear_case": str(payload.get("bear_case", ""))[:1200],
        "reports": reports,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def _server_healthy(session: aiohttp.ClientSession, url: str) -> bool:
    try:
        async with session.get(f"{url}/health", timeout=10) as resp:
            if resp.status != 200:
                return False
            body = await resp.json()
        return str(body.get("status", "")).lower() in ("ready", "warming", "ok")
    except Exception as exc:  # noqa: BLE001
        logger.warning("agora_debate: health check failed: %s", exc)
        return False


async def _load_packet(pair: str) -> dict:
    from mcp_servers.condor.tools import memory

    try:
        res = await memory.manage_memory(action="read", name=_mem_key(pair, "market"))
        if "error" in res:
            return {}
        return json.loads(res.get("content") or "{}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("agora_debate: could not load packet for %s: %s", pair, exc)
        return {}


async def _debate_pair(session, pair: str, cfg: Config) -> dict:
    packet = await _load_packet(pair)
    if not packet.get("market_data"):
        return {
            "pair": pair,
            "status": "no_data",
            "decision": "HOLD",
            "direction": "NONE",
            "confidence": 0,
            "error": "no cached market packet — run agora_data first",
        }

    body = {
        "pair": packet.get("base") or pair.split("-")[0],
        "trading_pair": pair,
        "market_data": packet.get("market_data", ""),
        "fundamentals_data": packet.get("fundamentals", ""),
        "news_data": packet.get("news", ""),
        "social_data": packet.get("sentiment", ""),
    }

    last_error = None
    for attempt in range(cfg.max_retries + 1):
        try:
            async with session.post(
                f"{cfg.server_url}/debate", json=body, timeout=cfg.debate_timeout_seconds
            ) as resp:
                if resp.status == 429:
                    last_error = "rate_limited"
                    break
                resp.raise_for_status()
                payload = await resp.json()
            record = _normalise(payload, pair)
            record["status"] = "ok"
            return record
        except asyncio.TimeoutError:
            last_error = f"timeout after {cfg.debate_timeout_seconds}s"
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < cfg.max_retries:
            await asyncio.sleep(2)

    # Fall back to the previous verdict so a blip does not blank the agent.
    from mcp_servers.condor.tools import memory

    try:
        prev = await memory.manage_memory(action="read", name=_mem_key(pair, "decision"))
        if "error" not in prev:
            cached = json.loads(prev.get("content") or "{}")
            cached["status"] = "stale"
            cached["error"] = last_error
            return cached
    except Exception:  # noqa: BLE001
        pass

    return {
        "pair": pair,
        "status": "error",
        "decision": "HOLD",
        "direction": "NONE",
        "confidence": 0,
        "error": last_error or "unknown",
    }


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE):
    """Debate every pair, persist verdicts, publish the debate-floor report."""
    pairs = _parse_pairs(config.pairs)
    if not pairs:
        return "agora_debate: no pairs configured."

    async with aiohttp.ClientSession() as session:
        if not await _server_healthy(session, config.server_url):
            msg = (
                f"Agora debate server unreachable at {config.server_url}. "
                "Run the agora_init routine, then retry. No trades this tick."
            )
            logger.warning("agora_debate: %s", msg)
            return msg
        records = await asyncio.gather(
            *(_debate_pair(session, p, config) for p in pairs)
        )

    from mcp_servers.condor.tools import memory

    for rec in records:
        if rec.get("status") in ("ok", "stale"):
            try:
                await memory.manage_memory(
                    action="write",
                    name=_mem_key(rec["pair"], "decision"),
                    content=json.dumps(rec),
                    description=f"Agora verdict for {rec['pair']}",
                    type="reference",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("agora_debate: verdict write failed: %s", exc)

    rows = []
    for rec in records:
        conf = int(rec.get("confidence", 0))
        actionable = (
            "YES" if rec.get("decision") in ("BUY", "SELL") and conf >= config.min_confidence
            else "no"
        )
        rows.append(
            {
                "Pair": rec["pair"],
                "Verdict": rec.get("decision", "HOLD"),
                "Direction": rec.get("direction", "NONE"),
                "Confidence": f"{conf}%",
                "Actionable": actionable,
                "Status": rec.get("status", "?"),
            }
        )
    columns = ["Pair", "Verdict", "Direction", "Confidence", "Actionable", "Status"]

    actionable_count = sum(1 for r in rows if r["Actionable"] == "YES")

    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder("Agora — The Debate Floor")
        builder.source("routine", "agora_debate")
        builder.tags(["agora", "debate", "multi-agent"])
        builder.kpi("Pairs Debated", str(len(records)))
        builder.kpi("Actionable Signals", str(actionable_count))
        builder.kpi("Confidence Floor", f"{config.min_confidence}%")
        builder.kpi(
            "Top Conviction",
            max((f"{r['Pair']} {r['Confidence']}" for r in rows), default="—"),
        )
        builder.section(
            "01 / VERDICTS", "What the parliament decided for each asset this tick."
        )
        builder.table(rows, columns)
        builder.section(
            "02 / TRANSCRIPTS",
            "Bull case, bear case and the risk judge's ruling — the full argument.",
        )
        for rec in records:
            if rec.get("status") == "error":
                builder.markdown(
                    f"### {rec['pair']} — unavailable\n`{rec.get('error', 'unknown')}`"
                )
                continue
            builder.markdown(
                f"### {rec['pair']} — {rec.get('decision')} "
                f"({rec.get('direction')}, {rec.get('confidence')}%)\n"
                f"**Bull case**\n\n{rec.get('bull_case') or '_none recorded_'}\n\n"
                f"**Bear case**\n\n{rec.get('bear_case') or '_none recorded_'}\n\n"
                f"**Research judge**\n\n{rec.get('rationale') or '_none recorded_'}\n\n"
                f"**Risk judge**\n\n{rec.get('risk_assessment') or '_none recorded_'}"
            )
        builder.manual_order()
        await builder.save()
    except Exception as exc:  # noqa: BLE001
        logger.warning("agora_debate: report generation failed: %s", exc)

    from routines.base import RoutineResult

    summary = (
        f"Agora debated {len(records)} pairs — {actionable_count} actionable "
        f"at ≥{config.min_confidence}% confidence."
    )
    return RoutineResult(text=summary, table_data=rows, table_columns=columns)
