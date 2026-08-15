"""
sats_scan.py — Condor Routine for the SATS (Self-Aware Trend System) agent.

Runs the adaptive SuperTrend + 4-factor Trend Quality Index engine over OHLCV
candles pulled through the Hummingbot client (venue-agnostic: the connector is
read from Config, defaulting to binance_perpetual). Returns a per-symbol
verdict string (LONG/SHORT/FLAT + confidence + levels) that the agent LLM reads,
and publishes a ReportBuilder dashboard for judges.

Zero-LLM signal path: every computation here is deterministic math.
"""
import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"  # Market Data | Analysis | Arbitrage | Monitoring

# Engine lives one level up from routines/ (agents/sats/sats_engine.py).
_ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

from sats_engine import SATSEngine, Candle, SATSSignal, TQIFactors  # noqa: E402

# ── Config ────────────────────────────────────────────────────────
class Config(BaseModel):
    """SATS scan — run the adaptive SuperTrend engine over the perp basket and return directional verdicts."""

    symbols: List[str] = Field(
        default=["BTC-USDT", "INJ-USDT", "NEAR-USDT", "FIL-USDT", "SUI-USDT", "DOGE-USDT", "SOL-USDT"],
        description="Perp pairs to scan (Hummingbot pair format, quoted in USDT)"
    )
    connector_name: str = Field(
        default="binance_perpetual",
        description="Execution/data venue connector. Venue-agnostic signal layer — swap freely."
    )
    timeframes: Dict[str, dict] = Field(
        default={
            "primary": {"tf": "15m", "limit": 120},
            "context_1h": {"tf": "1h", "limit": 100},
            "context_4h": {"tf": "4h", "limit": 80},
        },
        description="Timeframe config — primary drives signals, 1h/4h provide trend context"
    )
    # Engine parameters (competition-tuned for crypto 48h sprint)
    sats_atr_period: int = Field(default=10, description="ATR period for SuperTrend base")
    sats_atr_multiplier: float = Field(default=3.0, description="Base ATR multiplier")
    sats_q_strength: float = Field(default=0.8, description="How much TQI influences bands")
    sats_curve_power: float = Field(default=1.3, description="Power curve exponent")
    sats_asym_strength: float = Field(default=0.35, description="Asymmetry intensity")
    sats_flip_threshold: float = Field(default=0.35, description="TQI below this = weak regime")
    # TQI weights
    sats_w_efficiency: float = Field(default=1.5, description="Efficiency weight")
    sats_w_volatility: float = Field(default=0.8, description="Volatility weight")
    sats_w_structure: float = Field(default=1.0, description="Structure weight")
    sats_w_momentum: float = Field(default=1.2, description="Momentum weight")
    # Confidence thresholds
    sats_conf_obvious: float = Field(default=0.70, description="TQI for dumb_obvious")
    sats_conf_decent: float = Field(default=0.45, description="TQI for decent")
    sats_conf_min: float = Field(default=0.25, description="Minimum TQI to consider entry")
    # Cache
    cache_ttl_seconds: int = Field(default=180, description="Candle cache TTL (seconds)")
    min_candles: int = Field(default=60, description="Minimum candles required for a signal")


# ── Module-level candle cache (survives across routine runs in-process) ──
_CACHE: Dict[str, Dict[str, Any]] = {}


async def _fetch_candles(
    context: ContextTypes.DEFAULT_TYPE,
    config: Config,
    symbol: str,
    timeframe: str,
    limit: int,
) -> List[Dict[str, Any]]:
    """Fetch candles via the Hummingbot client, using an in-process TTL cache."""
    key = f"{config.connector_name}:{symbol}:{timeframe}"
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached.get("fetched_at", 0) < config.cache_ttl_seconds:
        return cached["candles"]

    try:
        client = await get_client(context._chat_id, context=context)
        if not client:
            logger.warning("No Hummingbot server available for %s", key)
            return []
        result = await client.market_data.get_candles(
            connector_name=config.connector_name,
            trading_pair=symbol,
            interval=timeframe,
            max_records=limit,
        )
        # Defensive parse: list, or dict with candles/data key.
        if isinstance(result, list):
            candles = result
        elif isinstance(result, dict):
            candles = result.get("candles") or result.get("data") or []
        else:
            candles = []
        _CACHE[key] = {"candles": candles, "fetched_at": now}
        return candles
    except Exception as e:
        logger.warning("Candle fetch failed for %s: %s", key, e)
        return []


def _to_candle(raw: Dict[str, Any]) -> Optional[Candle]:
    """Map a Hummingbot API candle dict to the engine's Candle dataclass."""
    try:
        o, h, l, c = raw.get("open"), raw.get("high"), raw.get("low"), raw.get("close")
        if any(v is None for v in (o, h, l, c)):
            return None
        return Candle(
            timestamp=int(raw.get("timestamp", 0)),
            open=float(o),
            high=float(h),
            low=float(l),
            close=float(c),
            volume=float(raw.get("volume", 0.0)),
        )
    except (TypeError, ValueError):
        return None


def _build_engine(config: Config) -> SATSEngine:
    return SATSEngine(
        atr_period=config.sats_atr_period,
        atr_multiplier=config.sats_atr_multiplier,
        q_strength=config.sats_q_strength,
        curve_power=config.sats_curve_power,
        asym_strength=config.sats_asym_strength,
        flip_threshold=config.sats_flip_threshold,
        w_efficiency=config.sats_w_efficiency,
        w_volatility=config.sats_w_volatility,
        w_structure=config.sats_w_structure,
        w_momentum=config.sats_w_momentum,
        confidence_obvious=config.sats_conf_obvious,
        confidence_decent=config.sats_conf_decent,
        confidence_min=config.sats_conf_min,
    )


def _data_hash(candles: List[Dict[str, Any]]) -> str:
    """MD5 of the last 20 closes — cheap change-gating."""
    raw = json.dumps([[c.get("timestamp"), c.get("close")] for c in candles[-20:]], sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()


def _htf_context(candles_1h: List[Candle], candles_4h: List[Candle], engine: SATSEngine) -> Dict[str, Any]:
    """Trend alignment from higher timeframes."""
    ctx: Dict[str, Any] = {"h1_direction": "FLAT", "h4_direction": "FLAT", "aligned": False}
    if len(candles_1h) >= 60:
        sig = engine.evaluate(candles_1h)
        ctx["h1_direction"] = sig.direction
        ctx["h1_tqi"] = sig.tqi.combined if sig.tqi else None
    if len(candles_4h) >= 60:
        sig = engine.evaluate(candles_4h)
        ctx["h4_direction"] = sig.direction
        ctx["h4_tqi"] = sig.tqi.combined if sig.tqi else None
    if ctx["h1_direction"] != "FLAT" and ctx["h4_direction"] != "FLAT":
        ctx["aligned"] = ctx["h1_direction"] == ctx["h4_direction"]
    return ctx


def _format_verdict(decisions: Dict[str, Dict[str, Any]], active: int, obvious: int) -> str:
    """Compact verdict string the agent LLM reads."""
    lines = [f"SATS scan: {active}/{len(decisions)} symbols active ({obvious} obvious)."]
    for sym, d in decisions.items():
        if d.get("direction") == "FLAT":
            lines.append(f"  {sym}: FLAT (TQI {d.get('tqi_combined', 0):.2f})")
        else:
            lines.append(
                f"  {sym}: {d['direction']} {d.get('confidence', '?')} "
                f"(TQI {d.get('tqi_combined', 0):.2f}, entry {d.get('entry_price')}, "
                f"stop {d.get('stop_loss')}, aligned={d.get('htf_aligned', False)})"
            )
    return "\n".join(lines)


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Fetch candles for the basket, run the SATS engine, return a verdict string."""
    engine = _build_engine(config)
    decisions: Dict[str, Dict[str, Any]] = {}

    for symbol in config.symbols:
        try:
            tf = config.timeframes["primary"]
            raw = await _fetch_candles(context, config, symbol, tf["tf"], tf.get("limit", 120))
            if len(raw) < config.min_candles:
                decisions[symbol] = {"direction": "FLAT", "status": f"insufficient data ({len(raw)} candles)"}
                continue

            candles = [c for c in (_to_candle(r) for r in raw) if c is not None]
            if len(candles) < config.min_candles:
                decisions[symbol] = {"direction": "FLAT", "status": "bad candle mapping"}
                continue

            signal = engine.evaluate(candles)

            # HTF context for alignment gating
            h1_raw = await _fetch_candles(context, config, symbol, config.timeframes["context_1h"]["tf"], config.timeframes["context_1h"].get("limit", 100))
            h4_raw = await _fetch_candles(context, config, symbol, config.timeframes["context_4h"]["tf"], config.timeframes["context_4h"].get("limit", 80))
            h1_c = [c for c in (_to_candle(r) for r in h1_raw) if c is not None]
            h4_c = [c for c in (_to_candle(r) for r in h4_raw) if c is not None]
            ctx = _htf_context(h1_c, h4_c, engine)

            tqi = signal.tqi.combined if signal.tqi else 0.0
            decision: Dict[str, Any] = {
                "symbol": symbol,
                "direction": signal.direction,
                "confidence": signal.confidence,
                "confidence_score": signal.confidence_score,
                "tqi_combined": tqi,
                "entry_price": signal.entry_price,
                "stop_loss": signal.stop_loss,
                "take_profit_1r": signal.take_profit_1r,
                "take_profit_2r": signal.take_profit_2r,
                "take_profit_3r": signal.take_profit_3r,
                "htf_aligned": ctx["aligned"],
                "h1_direction": ctx["h1_direction"],
                "h4_direction": ctx["h4_direction"],
                "data_hash": _data_hash(raw),
            }
            # Alignment gate: down-grade confidence when 1h disagrees with primary.
            if signal.direction != "FLAT" and ctx["h1_direction"] not in ("FLAT", signal.direction):
                if decision["confidence"] == "dumb_obvious":
                    decision["confidence"] = "decent"
                elif decision["confidence"] == "decent":
                    decision["confidence"] = "iffy"
            decisions[symbol] = decision
        except Exception as e:
            logger.warning("SATS scan error for %s: %s", symbol, e)
            decisions[symbol] = {"direction": "FLAT", "status": f"error: {e}"}

    active = sum(1 for d in decisions.values() if d.get("direction") != "FLAT")
    obvious = sum(1 for d in decisions.values() if d.get("confidence") == "dumb_obvious")

    # ── ReportBuilder dashboard (mandatory in every routine) ──
    try:
        from condor.reports import ReportBuilder
        builder = ReportBuilder("SATS — Self-Aware Trend System")
        builder.source("routine", "sats_scan").tags(["sats", "trend", "tqi", "zero-llm"])
        builder.kpi("Active Signals", str(active))
        builder.kpi("Obvious (TQI≥0.70)", str(obvious))
        builder.kpi("Basket", str(len(decisions)))
        builder.section("01 / SIGNALS", "Per-symbol SATS verdicts with TQI breakdown and HTF alignment.")
        rows = []
        for sym, d in decisions.items():
            rows.append({
                "symbol": sym,
                "direction": d.get("direction"),
                "confidence": d.get("confidence", "none"),
                "tqi": f"{d.get('tqi_combined', 0):.2f}",
                "entry": d.get("entry_price"),
                "stop": d.get("stop_loss"),
                "aligned": d.get("htf_aligned", False),
            })
        builder.table(rows, ["symbol", "direction", "confidence", "tqi", "entry", "stop", "aligned"])
        builder.section("02 / ENGINE", "Adaptive parameters in force this tick.")
        builder.kpi("ATR period", str(config.sats_atr_period))
        builder.kpi("ATR mult (base)", str(config.sats_atr_multiplier))
        builder.kpi("TQI weights", f"eff={config.sats_w_efficiency:.1f} vol={config.sats_w_volatility:.1f} struct={config.sats_w_structure:.1f} mom={config.sats_w_momentum:.1f}")
        builder.markdown(_format_verdict(decisions, active, obvious))
        builder.manual_order()
        await builder.save()
    except Exception as e:
        logger.warning("ReportBuilder failed: %s", e)

    return _format_verdict(decisions, active, obvious)
