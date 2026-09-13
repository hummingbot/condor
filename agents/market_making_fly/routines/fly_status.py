"""Latest state of a fly run: posture per pair, last observation, guard, memory."""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.fly.run_state import RunDir
from condor.memory.paths import agent_home
from condor.reports import ReportBuilder

logger = logging.getLogger(__name__)

CATEGORY = "Bot Analysis"
AGENT_SLUG = "market_making_fly"


class Config(BaseModel):
    """Read a fly run's state.json / latest.json / recent events without touching the brain."""

    run_name: str = Field(
        default="fly", description="Run directory under the agent home"
    )
    recent: int = Field(default=20, description="Recent ticks to list")


def _fmt(value, digits=3) -> str:
    if isinstance(value, (int, float)):
        return f"{value:+.{digits}f}" if isinstance(value, float) else str(value)
    return "—" if value is None else str(value)


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    root = agent_home(AGENT_SLUG) / "fly" / config.run_name
    if not root.exists():
        raise FileNotFoundError(
            f"No fly run at {root}; start fly_brain with run_name={config.run_name!r}"
        )
    run_dir = RunDir(root)
    state = run_dir.load_state()
    latest = (
        json.loads(run_dir.latest_path.read_text())
        if run_dir.latest_path.exists()
        else {}
    )
    events = run_dir.recent_events(config.recent)
    guard = state.get("guard", {})
    postures = state.get("postures", {})
    baselines = state.get("baselines", {})
    applied = state.get("applied", {})
    neural = latest.get("neural", {})
    memory = neural.get("memory", {})

    lines = [
        f"run: {config.run_name}",
        f"tick: {state.get('tick', 0)}",
        f"halted: {guard.get('halted') or 'no'}",
        f"anchor_equity: {_fmt(state.get('anchor'))}",
        f"session_high_net: {_fmt(guard.get('session_high_net'))}",
        f"ticks_since_high: {guard.get('ticks_since_high', 0)}",
        f"applies_today: {guard.get('applies_today', 0)}",
        f"last_pair: {latest.get('pair', '—')}",
        f"last_stimulus: {latest.get('stimulus', '—')}",
        f"last_pnl_delta: {_fmt(latest.get('pnl_delta'))}",
        f"last_execution: {(latest.get('execution') or {}).get('status', '—')} "
        f"({(latest.get('execution') or {}).get('reason', '')})",
        f"kc_spikes: {neural.get('kc_spikes', '—')}",
        f"gate_spikes: {neural.get('gate_spikes', '—')}",
        f"trend_hz: {_fmt(neural.get('trend_hz'))}",
        f"arousal_hz: {_fmt(neural.get('arousal_hz'))}",
        f"changed_edges: {memory.get('changed_edges', '—')}",
        f"mean_efficacy: {_fmt(memory.get('mean_efficacy'), 4)}",
    ]
    for pair, posture in postures.items():
        if posture:
            lines.append(
                f"{pair}: {posture['regime']} spread_x={posture['spread_mult']:.2f} "
                f"lean_bp={posture['shift_bps']:+.2f} trend_z={posture['trend_z']:+.2f} "
                f"arousal_z={posture['arousal_z']:+.2f} baseline_n={len((baselines.get(pair) or {}).get('trend', []))}"
            )
        else:
            lines.append(
                f"{pair}: no posture yet (baseline_n={len((baselines.get(pair) or {}).get('trend', []))})"
            )
    lines.append(
        "caveat: regime/spread/lean are an engineered readout of spike counts; dopamine "
        "pulses report P&L change, not credit for the last posture; no learning is validated"
    )

    builder = ReportBuilder(f"Fly status — {config.run_name}")
    builder.source("routine", "fly_status")
    builder.tags(["fly", "status"])
    builder.manual_order()
    builder.section("01 / RUN", str(root))
    builder.kpi("Tick", str(state.get("tick", 0)))
    builder.kpi("Halted", guard.get("halted") or "no")
    builder.kpi("Anchor equity", _fmt(state.get("anchor")))
    builder.kpi("Session high", _fmt(guard.get("session_high_net")))
    builder.kpi("Changed edges", str(memory.get("changed_edges", "—")))
    builder.section("02 / POSTURES", "Per pair")
    builder.table(
        [
            {
                "Pair": pair,
                "Regime": (p or {}).get("regime", "—"),
                "Spread ×": (p or {}).get("spread_mult", "—"),
                "Lean bp": (p or {}).get("shift_bps", "—"),
                "Trend z": (p or {}).get("trend_z", "—"),
                "Arousal z": (p or {}).get("arousal_z", "—"),
                "Applied buy": (applied.get(pair) or {}).get("buy_spreads", "—"),
                "Applied sell": (applied.get(pair) or {}).get("sell_spreads", "—"),
            }
            for pair, p in postures.items()
        ],
        [
            "Pair",
            "Regime",
            "Spread ×",
            "Lean bp",
            "Trend z",
            "Arousal z",
            "Applied buy",
            "Applied sell",
        ],
    )
    builder.section("03 / RECENT TICKS", "Newest last")
    builder.table(
        [
            {
                "Tick": e.get("tick"),
                "Pair": e.get("pair"),
                "Stim": e.get("stimulus", "—"),
                "Δ": _fmt(e.get("pnl_delta")),
                "Regime": (e.get("posture") or {}).get("regime", "—"),
                "Exec": (e.get("execution") or {}).get("status", "—"),
                "Reason": str((e.get("execution") or {}).get("reason", ""))[:60],
            }
            for e in events
        ],
        ["Tick", "Pair", "Stim", "Δ", "Regime", "Exec", "Reason"],
    )
    await builder.save()
    return "\n".join(lines)
