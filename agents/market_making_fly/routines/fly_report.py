"""A dashboard for a running fly: the fly itself, its book, its neurons, its calls.

Laid out after stonkfly's dashboard — the fly, the bag, the neuron strip, the
latest neural order, the decision log, and what the fly is actually looking at.
Stonkfly draws its fly with Three.js; a Condor report carries Plotly, so the fly
here is Mesh3d geometry that orbits by dragging and beats its wings on play.

Everything except the fly is read back from the run directory and the live bot,
so the report says what happened rather than what was intended.
"""

from __future__ import annotations

import sys
from pathlib import Path

_AGENT_DIR = str(Path(__file__).resolve().parents[1])
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

import json
import logging
import time

import numpy as np
import plotly.graph_objects as go
from flybrain.brainviz import brain_figure, coverage, readout_figure
from flybrain.fly3d import ACCENT, BODY, GROUND, LIMB, fly_figure
from flybrain.market import LiveMarket
from flybrain.naming import pair_names
from flybrain.run_state import RunDir
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.memory.paths import agent_home
from condor.paths import safe_id
from condor.reports import ReportBuilder

logger = logging.getLogger(__name__)

CATEGORY = "Bot Analysis"

# Columns of the report's 12-wide grid, and the panel heights that make each
# row's two halves finish level on a wide screen. Everything collapses to full
# width under the runtime's 800px breakpoint, where heights stop mattering.
#
# A panel is its figure plus 34px of padding and border, measured rather than
# guessed. Narrow panels are also released from the stylesheet's 400px floor,
# which exists to stop a full-width chart being squashed and would otherwise
# stop the brain matching the card stack beside it.
FLY, FRAME = 6, 6
# Six and six, not five and seven: the cards wrap by their own 230px minimum,
# so a five-column stack needs a ~1150px container before it fits two per row
# and stops towering over the brain. Six needs ~970, which covers every screen
# that is meaningfully wider than the 800px breakpoint.
CARDS, BRAIN = 6, 6
PANEL_CHROME = 34  # the panel's own padding and border, measured
FULL_ROW = 400  # the stylesheet's floor for a full-width chart panel
# Row one is sized so the frame fills its half: 16:9 at six columns is ~335px.
ROW_ONE = 430
# Row two matches the card stack, which is what it is: six cards at two per
# row is three rows of roughly 98px plus two 16px gaps. A card's exact height
# drifts a few pixels with the container, so this lands within ~5px rather
# than exactly — nothing about a KPI card's height is ours to set.
ROW_TWO = 3 * 98 + 2 * 16
AGENT_SLUG = "market_making_fly"

# How an execution status reads in the decision log.
RESULT_WORDS = {
    "APPLIED": "APPLIED",
    "SHADOW": "SHADOW",
    "HOLD": "HOLD",
    "VETO": "VETO",
    "CLOSED": "BOOK CLOSED",
    "STOP_BOT": "BOT STOPPED",
    "ERROR": "UPDATE FAILED",
    "HALT": "HALT",
    "TICK_ERROR": "TICK FAILED",
}


class Config(BaseModel):
    """Dashboard for one fly run: the fly, its book, its neurons and its calls."""

    run_name: str = Field(
        default="fly", description="Run directory under the agent home"
    )
    recent: int = Field(default=12, ge=1, le=100, description="Decisions to list")
    connector_name: str = Field(
        default="hyperliquid_perpetual", description="Connector the bots run on"
    )


def _fmt(value, digits=2, plus=False) -> str:
    if value is None or isinstance(value, bool):
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:+,.{digits}f}" if plus else f"{number:,.{digits}f}"


def _clock(wall_time) -> str:
    if not wall_time:
        return "—"
    return time.strftime("%H:%M:%S", time.localtime(float(wall_time)))


def _read_frame(path: Path) -> np.ndarray | None:
    """The exact pixels the retina last received, as saved by the loop."""
    if not path.exists():
        return None
    from PIL import Image

    return np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)


def _frame_figure(frame: np.ndarray, height: int = 340) -> go.Figure:
    """That frame, full size, as the report's sensory panel."""
    fig = go.Figure(go.Image(z=frame))
    fig.update_layout(
        height=height,
        margin=dict(l=0, r=0, t=6, b=6),
        paper_bgcolor=GROUND,
        plot_bgcolor=GROUND,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
    )
    return fig


def _pnl_figure(events: list[dict]) -> go.Figure | None:
    """Equity across the ticks whose P&L was actually reported."""
    points = [
        (e["tick"], float(e["equity"]))
        for e in events
        if e.get("pnl_known") and e.get("equity") is not None
    ]
    if len(points) < 2:
        return None
    ticks, equity = zip(*points)
    fig = go.Figure(
        go.Scatter(
            x=list(ticks),
            y=list(equity),
            mode="lines",
            line=dict(color=ACCENT, width=2),
            fill="tozeroy",
            fillcolor="rgba(201,242,77,0.10)",
            name="net P&L",
        )
    )
    fig.update_layout(
        height=FULL_ROW - PANEL_CHROME,
        margin=dict(l=48, r=16, t=10, b=36),
        paper_bgcolor=GROUND,
        plot_bgcolor=GROUND,
        font=dict(color=BODY, family="monospace", size=11),
        xaxis=dict(title="tick", gridcolor="#18202e", zerolinecolor="#18202e"),
        yaxis=dict(
            title="net P&L (quote)", gridcolor="#18202e", zerolinecolor="#243044"
        ),
        showlegend=False,
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
    )
    fig.add_hline(y=0, line=dict(color=LIMB, width=1, dash="dot"))
    return fig


async def _holdings(
    client, connector_name: str, pairs: list[str]
) -> tuple[list[dict], int | None]:
    """What the fly's bots hold now, and how many positions they have closed.

    The trade count is the sum of each controller's close-type counts — round
    trips actually completed, not orders placed. ``None`` when no bot reported,
    so the report can say "unknown" rather than "zero".
    """
    market = LiveMarket(client, connector_name, "5m", 72)
    bots = await market.bots()
    rows: list[dict] = []
    trades: int | None = None
    for pair in pairs:
        names = pair_names(pair)
        running, bot = LiveMarket.find_bot(bots, names.bot_name)
        if bot is None:
            rows.append({"Market": pair, "Bot": "not running", "Side": "—"})
            continue
        perf = (bot.get("performance") or {}).get(names.config_name) or {}
        inner = perf.get("performance", perf) if isinstance(perf, dict) else {}
        closes = inner.get("close_type_counts") or {}
        if isinstance(closes, dict):
            trades = (trades or 0) + sum(int(v or 0) for v in closes.values())
        positions = inner.get("positions_summary") or []
        amount = sum(
            float(p.get("amount", 0) or 0) for p in positions if isinstance(p, dict)
        )
        rows.append(
            {
                "Market": pair,
                "Bot": running,
                "Side": ("LONG" if amount > 0 else "SHORT" if amount < 0 else "FLAT"),
                "Position": _fmt(abs(amount), 4),
                "Realized": _fmt(inner.get("realized_pnl_quote"), 4, plus=True),
                "Unrealized": _fmt(inner.get("unrealized_pnl_quote"), 4, plus=True),
                "Volume": _fmt(inner.get("volume_traded")),
            }
        )
    return rows, trades


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    from config_manager import get_client

    root = agent_home(AGENT_SLUG) / "fly" / safe_id(config.run_name)
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
    events = run_dir.recent_events(max(config.recent, 60))
    provenance = (
        json.loads(run_dir.provenance_path.read_text())
        if run_dir.provenance_path.exists()
        else {}
    )

    settings = provenance.get("settings") or {}
    guard = state.get("guard", {})
    postures = state.get("postures", {})
    pairs = list(postures)
    # A halt, a closed book or a failed tick records no observation, so the
    # neuron panel reads back the last tick that actually ran the brain rather
    # than showing dashes over a run with thirty ticks of history behind it.
    observed = next(
        (e for e in reversed(events) if (e.get("neural") or {}).get("total_spikes")),
        latest,
    )
    neural = observed.get("neural", {})
    memory = neural.get("memory", {})
    neurons = (provenance.get("circuit") or {}).get("neurons")
    last_posture = observed.get("posture") or latest.get("posture") or {}
    execution = latest.get("execution") or {}
    stale = observed is not latest

    client = await get_client(context._chat_id, context=context)
    holdings, trades = (
        await _holdings(client, config.connector_name, pairs) if client else ([], None)
    )
    book_net = sum(
        float(info.get("net", 0) or 0)
        for info in (latest.get("bots") or {}).values()
        if isinstance(info, dict)
    )
    book_volume = latest.get("volume")

    halted = guard.get("halted")
    alive = "HALTED" if halted else "RUNNING"

    builder = ReportBuilder(f"Fly — {config.run_name}")
    builder.source("routine", "fly_report")
    builder.tags(["fly", "dashboard", config.run_name])
    builder.manual_order()

    # ── FLY.EXE ──────────────────────────────────────────────────────────────
    sensory = _read_frame(run_dir.frame_path)
    # The prose belongs under the heading, not in a box below the figures it
    # describes. `section` escapes its description, so this is plain text —
    # no markdown syntax, which would show as literal asterisks.
    builder.section(
        "WHAT THE FLY SEES",
        (
            f"The 320×180 frame fed to the retina on tick {observed.get('tick')} — "
            f"{settings.get('n_candles', '?')} × "
            f"{settings.get('candle_interval', '?')} candles, volume and the live "
            "bid/ask — shown on its monitor and again at full size beside it. This "
            "is the picture the posture below was decoded from. No quotes, "
            "inventory or P&L are drawn, because those reach the fly only as "
            "dopamine. Drag the scene to orbit it."
            if sensory is not None
            else "No input frame has been recorded for this run yet."
        ),
    )
    # Two halves of the grid, not one figure split internally: below the
    # layout's 800px breakpoint these stack on their own.
    builder.plotly(
        fly_figure(
            title=f"FLY.EXE — {alive}",
            subtitle=f"{', '.join(pairs) or 'no market'}",
            chart=sensory,
            height=ROW_ONE - PANEL_CHROME,
        ),
        width=FLY,
    )
    if sensory is not None:
        builder.plotly(
            _frame_figure(sensory, height=ROW_ONE - PANEL_CHROME), width=FRAME
        )

    # ── NEURONS & NEURAL ORDER ───────────────────────────────────────────────
    # Six cards, not fifteen. The rest of the numbers are narrative, and the
    # picture says more about where the activity sat than a card ever could.
    drawn, mapped, neurons_total = coverage()
    active = neural.get("active_neurons")
    posture_line = (
        f"Decoded from trend z {_fmt(last_posture.get('trend_z'), 2, plus=True)} and "
        f"arousal z {_fmt(last_posture.get('arousal_z'), 2, plus=True)}, gated on "
        f"{neural.get('gate_spikes', '—')} DNpe017 spike(s)."
        + ("" if last_posture.get("warm", True) else " Baseline still forming.")
    )
    observation_line = (
        f" The observation ran "
        f"{_fmt(float(neural['brain_ms']) / 1000, 1) if neural.get('brain_ms') else '—'} s "
        f"of neural time and produced {neural.get('total_spikes', 0):,} spikes, "
        f"{neural.get('kc_spikes', 0):,} of them in Kenyon cells. Stimulus "
        f"{observed.get('stimulus', 'none')} — {neural.get('reward_spikes', 0)} PAM11 "
        f"and {neural.get('aversive_spikes', 0)} PPL101 spikes — left "
        f"{(memory.get('changed_edges') or 0):,} of "
        f"{(memory.get('plastic_edges') or 0):,} plastic edges away from baseline, "
        f"mean efficacy {_fmt(memory.get('mean_efficacy'), 5)}."
    )
    builder.section("THE FLY BRAIN", posture_line + observation_line)
    # Cards on the left, the brain on the right, the same way the fly and its
    # frame sit above — five columns and seven of the runtime's twelve, both
    # collapsing to full width below its 800px breakpoint.
    builder.kpi(
        "Active neurons",
        (
            f"{active:,}  ·  {100 * active / neurons_total:.1f}%"
            if active is not None
            else "—"
        ),
        width=CARDS,
    )
    builder.kpi(
        "Mean firing rate",
        f"{neural['mean_rate_hz']:.1f} Hz" if neural.get("mean_rate_hz") else "—",
        width=CARDS,
    )
    builder.kpi("Regime", str(last_posture.get("regime", "—")).upper(), width=CARDS)
    builder.kpi("Spread ×", _fmt(last_posture.get("spread_mult")), width=CARDS)
    builder.kpi(
        "Lean",
        f"{_fmt(last_posture.get('shift_bps'), 2, plus=True)} bp",
        width=CARDS,
    )
    builder.kpi(
        "Result",
        RESULT_WORDS.get(execution.get("status"), execution.get("status", "—")),
        width=CARDS,
    )
    builder.plotly(
        brain_figure(
            run_dir.load_activity(),
            title="NEURAL ACTIVITY",
            height=ROW_TWO - PANEL_CHROME,
        ),
        width=BRAIN,
    )
    builder.plotly(readout_figure(neural, last_posture, height=260))
    # ── DECISIONS & POSITIONS ────────────────────────────────────────────────
    # One panel: what the fly called, and what those calls left it holding.
    builder.section(
        "DECISIONS & POSITIONS",
        f"Last {min(config.recent, len(events))} observations, newest last — and "
        "what the fly's own bots are holding right now",
    )
    builder.table(
        [
            {
                "Time": _clock(e.get("wall_time")),
                "Tick": e.get("tick"),
                "Market": e.get("pair"),
                "Neural proposal": (
                    f"{str((e.get('posture') or {}).get('regime', '—')).upper()}"
                    f" ×{_fmt((e.get('posture') or {}).get('spread_mult'))}"
                    f" {_fmt((e.get('posture') or {}).get('shift_bps'), 2, plus=True)}bp"
                ),
                "R−L": f"{_fmt((e.get('neural') or {}).get('trend_hz'), 1, plus=True)} Hz",
                "Stimulus": e.get("stimulus", "—"),
                "Result": RESULT_WORDS.get(
                    (e.get("execution") or {}).get("status"),
                    (e.get("execution") or {}).get("status", "—"),
                ),
                "Reason": str((e.get("execution") or {}).get("reason", ""))[:54],
            }
            for e in events[-config.recent :]
        ],
        [
            "Time",
            "Tick",
            "Market",
            "Neural proposal",
            "R−L",
            "Stimulus",
            "Result",
            "Reason",
        ],
    )
    if holdings:
        builder.table(
            holdings,
            ["Market", "Bot", "Side", "Position", "Realized", "Unrealized", "Volume"],
        )
    else:
        builder.markdown(
            "_No live bot data — either no server is bound to this chat, or the fly is "
            "running in shadow with nothing deployed._"
        )

    # ── PERFORMANCE ──────────────────────────────────────────────────────────
    builder.section(
        "PERFORMANCE",
        (
            (f"Halted: {halted}. " if halted else "")
            + f"Session high {_fmt(guard.get('session_high_net'), 4, plus=True)}, "
            f"{guard.get('ticks_since_high', 0)} observation(s) since, "
            f"{guard.get('applies_today', 0)} config change(s) applied today."
            if guard.get("session_high_net") is not None
            else "No P&L has been reported yet, so the breakers have nothing to judge."
        ),
    )
    builder.kpi("Ticks", f"{state.get('tick', 0):,}")
    builder.kpi("Net P&L", _fmt(book_net, 4, plus=True))
    builder.kpi("Trades", f"{trades:,}" if trades is not None else "—")
    builder.kpi("Volume", _fmt(book_volume))
    pnl = _pnl_figure(events)
    if pnl is not None:
        builder.plotly(pnl)
    await builder.save()

    lines = [
        f"run: {config.run_name}",
        f"state: {alive}" + (f" ({halted})" if halted else ""),
        f"tick: {state.get('tick', 0)}",
        f"markets: {', '.join(pairs) or '—'}",
        f"net_pnl: {_fmt(book_net, 4, plus=True)}",
        f"volume: {_fmt(book_volume)}",
        f"last_regime: {last_posture.get('regime', '—')}"
        + (f" (observed tick {observed.get('tick')})" if stale else ""),
        f"last_result: {execution.get('status', '—')}",
        f"latest_spikes: {neural.get('total_spikes', '—')}",
        f"changed_edges: {memory.get('changed_edges', '—')}",
    ]
    for row in holdings:
        lines.append(
            f"{row['Market']}: {row.get('Side', '—')} {row.get('Position', '')} "
            f"realized {row.get('Realized', '—')} volume {row.get('Volume', '—')}"
        )
    lines.append(
        "caveat: the readout is engineered, the pulses are value feedback rather than "
        "credit, and no profitable learning is demonstrated"
    )
    return "\n".join(lines)
