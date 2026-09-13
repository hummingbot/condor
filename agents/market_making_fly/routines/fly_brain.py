"""The fly loop: chart → connectome → posture → pmm_mister config, with P&L dopamine.

One shared brain is shown up to three HIP-3 markets in round-robin. Each tick:

1. fetch candles + live book for this tick's pair;
2. read the combined net P&L of the fly's bots, turn its change since the last
   observation into ``reward`` / ``aversive`` / ``none``;
3. render the chart, run the neural window in the worker process (the dopamine
   pulse is delivered during it), decode spike counts into a posture;
4. checkpoint the brain and commit the accounting anchor BEFORE anything is
   applied;
5. map the posture to a config, pass it through the guard, apply it in ``live``
   mode (``shadow`` only records what it would have applied);
6. persist events, latest.json, the input frame, and the live report.

The guard can veto or halt; nothing in this file chooses a posture.
"""

from __future__ import annotations

import sys
from pathlib import Path

_AGENT_DIR = str(Path(__file__).resolve().parents[1])
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)
# Condor re-executes this file when it changes but keeps imported modules
# cached: after editing anything under flybrain/, restart Condor. Purging the
# cache here is not an option — each routine would re-import its own copy and
# the spawned brain worker could no longer pickle flybrain.worker._init.

import asyncio
import logging
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
from flybrain import worker
from flybrain.chart import market_frame
from flybrain.decoder import (
    Baseline,
    Channels,
    DecoderSettings,
    Hysteresis,
    Posture,
    decode,
    should_apply,
)
from flybrain.guard import (
    GuardSettings,
    GuardState,
    Halt,
    Veto,
    check_apply_window,
    check_collateral,
    check_config,
    check_market_open,
    check_not_halted,
    check_pnl,
    check_price_move,
    default_max_loss,
    record_apply,
    resume,
)
from flybrain.market import FixtureMarket, LiveMarket, required_collateral
from flybrain.naming import pair_names, parse_pairs
from flybrain.posture import MarketSpec, build_config, config_diff
from flybrain.reinforcement import reinforcement
from flybrain.run_state import RunDir, source_hashes
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.memory.paths import agent_home
from condor.paths import safe_id
from condor.reports import LiveReport

logger = logging.getLogger(__name__)

CONTINUOUS = True
CATEGORY = "Monitoring"
AGENT_SLUG = "market_making_fly"
ROUTINE_NAME = "fly_brain"
BPS = 1e-4


class Config(BaseModel):
    """Fly-connectome market maker: one brain, up to three HIP-3 markets, P&L dopamine."""

    pairs: str = Field(
        default="XYZ:DRAM-USD,XYZ:SPCX-USD,XYZ:SMSN-USD",
        description="1 to 3 uppercase HIP-3 pairs, comma-separated — this list IS the market count (bot {token}-fly, config {token}_fly_mm)",
    )
    picked_spreads_bps: str = Field(
        default="8,8,8",
        description="Scanner spread per pair in bp, same order as pairs",
    )
    connector_name: str = Field(
        default="hyperliquid_perpetual", description="Connector"
    )
    total_amount_quote: float = Field(
        default=500.0, description="Capital per pair (quote)"
    )
    leverage: int = Field(default=3, description="Leverage per pair (cap 5)")
    portfolio_allocation: float = Field(
        default=0.2,
        description="Fraction of total_amount_quote quoted per cycle; each order is total × allocation / 4 and must clear the exchange minimum (10 USD on HIP-3) — one market at 200 quote needs 0.2+",
    )
    mode: str = Field(
        default="shadow", description="shadow (record only) or live (apply configs)"
    )
    learning: bool = Field(
        default=True, description="False freezes the memory rule (control run)"
    )
    run_name: str = Field(
        default="fly",
        description="Run directory under the agent home (letters, digits, dot, dash, underscore); new name = new brain lineage",
    )
    resume_reviewed: bool = Field(
        default=False, description="Clear a transient halt after review"
    )
    interval_sec: int = Field(
        default=60, description="Wall seconds between observations"
    )
    neural_ms: float = Field(
        default=500.0, description="Neural time per observation (ms)"
    )
    candle_interval: str = Field(
        default="5m", description="Candle interval the fly sees"
    )
    n_candles: int = Field(default=72, description="Candles on the chart")
    reward_deadband_bps: float = Field(
        default=1.0, description="Pulse deadband, bp of combined capital"
    )
    baseline_window: int = Field(
        default=60, description="Observations per pair in the rolling baseline"
    )
    baseline_warmup: int = Field(
        default=10, description="Observations before the first non-neutral posture"
    )
    center_bias: bool = Field(
        default=True, description="Subtract the rolling mean of the trend channel"
    )
    min_apply_interval_sec: int = Field(
        default=300, description="Per-pair cooldown between config applies"
    )
    max_loss_quote: float = Field(
        default=0.0, description="Loss stop in quote; 0 = 4% of combined capital"
    )
    fixture: bool = Field(
        default=False, description="Offline synthetic market (shadow only)"
    )
    fast: bool = Field(
        default=False, description="Skip wall waits (fixture/shadow only)"
    )
    steps: int = Field(
        default=0, description="Stop after N observations; 0 runs until stopped"
    )


def _specs(config: Config, pairs: list[str]) -> list[MarketSpec]:
    spreads = [float(x) for x in config.picked_spreads_bps.split(",") if x.strip()]
    if len(spreads) != len(pairs):
        raise ValueError(
            f"picked_spreads_bps has {len(spreads)} entries for {len(pairs)} pairs"
        )
    return [
        MarketSpec(
            connector_name=config.connector_name,
            trading_pair=pair,
            total_amount_quote=config.total_amount_quote,
            picked_spread_bps=spread,
            leverage=config.leverage,
            portfolio_allocation=config.portfolio_allocation,
        )
        for pair, spread in zip(pairs, spreads)
    ]


def _frame_figure(frame: np.ndarray) -> go.Figure:
    fig = go.Figure(go.Image(z=frame))
    fig.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        height=360,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
    )
    return fig


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    chat_id = context._chat_id
    if config.mode not in ("shadow", "live"):
        raise ValueError("mode must be shadow or live")
    if config.fixture and config.mode != "shadow":
        raise ValueError("fixture runs are shadow only")
    if config.fast and config.mode == "live":
        raise ValueError("fast is for fixture/shadow runs only")
    if config.interval_sec < 10:
        raise ValueError("interval_sec must be >= 10")
    pairs = parse_pairs(config.pairs)
    specs = _specs(config, pairs)
    for spec in specs:
        spec.check_order_size()  # fail at start, not on the first apply
    by_pair = {s.trading_pair: s for s in specs}
    decoder_settings = DecoderSettings(
        window=config.baseline_window,
        warmup=config.baseline_warmup,
        center_bias=config.center_bias,
    )
    hysteresis = Hysteresis(min_apply_interval_sec=config.min_apply_interval_sec)
    guard_settings = GuardSettings(max_loss_quote=config.max_loss_quote)
    max_loss = default_max_loss(specs, guard_settings)
    deadband = (
        config.reward_deadband_bps * BPS * sum(s.total_amount_quote for s in specs)
    )

    from flybrain.neural.common import DATA, GRAPH

    if not GRAPH.exists():
        raise RuntimeError(
            f"Connectome not prepared at {DATA}; run the fly_setup routine with "
            'action="prepare" first (downloads ~1.1 GB, needs a C++ compiler)'
        )
    run_dir = RunDir(agent_home(AGENT_SLUG) / "fly" / safe_id(config.run_name))
    run_dir.lock()
    pool = ProcessPoolExecutor(
        max_workers=1,
        mp_context=multiprocessing.get_context("spawn"),
        initializer=worker._init,
        initargs=(config.learning, 10.0, 200.0, 20.0),
    )
    loop = asyncio.get_running_loop()
    report = LiveReport(
        "Market Making Fly",
        source_name=ROUTINE_NAME,
        tags=["fly", "market-making", "hip3", config.mode],
        auto_refresh_seconds=config.interval_sec,
    )
    count = 0
    stop_reason = "stopped"
    try:
        # ---- state, provenance, checkpoint --------------------------------
        state = run_dir.load_state()
        guard_state = GuardState.from_dict(state.get("guard"))
        if guard_state.halted:
            resume(
                guard_state, config.resume_reviewed
            )  # raises Halt when not clearable
        baselines = {
            p: Baseline.from_dict(state.get("baselines", {}).get(p)) for p in pairs
        }
        postures: dict[str, Posture | None] = {
            p: Posture.from_dict(v) if (v := state.get("postures", {}).get(p)) else None
            for p in pairs
        }
        applied: dict[str, dict | None] = {
            p: state.get("applied", {}).get(p) for p in pairs
        }
        anchor = state.get("anchor")
        tick = int(state.get("tick", 0))
        pnl_carry: dict = state.get("pnl_carry", {})
        # Shadow keeps its own per-pair apply clock so the trial shows the same
        # cooldown live would, without touching the live guard's accounting.
        shadow_last: dict = state.get("shadow_last", {})

        from flybrain.data import verify

        dataset = await loop.run_in_executor(None, verify)
        brain_prov = await loop.run_in_executor(pool, worker._provenance)
        provenance = {
            "settings": {
                k: v
                for k, v in config.model_dump().items()
                if k
                not in (
                    "pairs",
                    "picked_spreads_bps",
                    "mode",
                    "fast",
                    "steps",
                    "resume_reviewed",
                    "run_name",
                    "fixture",
                )
            },
            "decoder": asdict(decoder_settings),
            "guard": asdict(guard_settings),
            "dataset": dataset,
            **brain_prov,
            "learning_validated": False,
            "source_sha256": source_hashes(),
        }
        run_dir.check_provenance(provenance)
        if state.get("checkpoint"):
            path = run_dir.verify_checkpoint(state["checkpoint"])
            await loop.run_in_executor(pool, worker._restore, str(path))

        if config.fixture:
            market = FixtureMarket(pairs, config.n_candles)
        else:
            from config_manager import get_client

            client = await get_client(chat_id, context=context)
            if not client:
                raise RuntimeError("No Hummingbot server available for this chat")
            market = LiveMarket(
                client, config.connector_name, config.candle_interval, config.n_candles
            )

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🪰 Fly started [{config.mode}] run={config.run_name} tick={tick} "
                f"pairs={','.join(pairs)} learning={config.learning}"
            ),
        )

        def persist(extra: dict | None = None) -> None:
            run_dir.save_state(
                {
                    "tick": tick,
                    "anchor": anchor,
                    "guard": guard_state.to_dict(),
                    "baselines": {p: b.to_dict() for p, b in baselines.items()},
                    "postures": {
                        p: (q.to_dict() if q else None) for p, q in postures.items()
                    },
                    "applied": applied,
                    "pnl_carry": pnl_carry,
                    "shadow_last": shadow_last,
                    **(extra or {}),
                }
            )

        async def pace() -> None:
            """Wait out the rest of the interval; every tick path ends here."""
            if config.fast:
                return
            until = started + config.interval_sec
            while time.monotonic() < until:
                if run_dir.stop_requested():
                    break
                await asyncio.sleep(min(1.0, until - time.monotonic()))

        # ---- the loop -------------------------------------------------------
        while not config.steps or count < config.steps:
            started = time.monotonic()
            if run_dir.stop_requested():
                stop_reason = "STOP file"
                break
            if guard_state.halted:
                stop_reason = f"halted: {guard_state.halted}"
                break
            pair = pairs[tick % len(pairs)]
            names = pair_names(pair)
            spec = by_pair[pair]
            row: dict = {
                "tick": tick,
                "wall_time": time.time(),
                "pair": pair,
                "mode": config.mode,
            }
            try:
                obs = await market.observe(pair)
                equity, volume, per_pair, pnl_carry = await market.equity(
                    pairs, pnl_carry
                )
                if anchor is None:
                    kind, delta = "none", 0.0
                else:
                    kind, delta = reinforcement(equity, anchor, deadband)
                    delta = float(delta)
                row.update(
                    {
                        "quote": {"bid": obs.bid, "ask": obs.ask, "open": obs.open},
                        "equity": equity,
                        "volume": volume,
                        "pnl_delta": delta,
                        "stimulus": kind,
                        "bots": per_pair,
                    }
                )
                check_pnl(equity, volume, guard_state, guard_settings, max_loss)

                if not obs.open:
                    # Nothing new for the fly to see; keep the anchor so the next
                    # open observation carries the whole interval's P&L change.
                    try:
                        stop = check_market_open(
                            pair, False, guard_state, guard_settings
                        )
                    except Veto as veto:
                        row["execution"] = {"status": "CLOSED", "reason": str(veto)}
                        stop = False
                    if stop:
                        stopped = config.mode == "live" and await market.stop_bot(pair)
                        row["execution"] = {
                            "status": "STOP_BOT" if stopped else "CLOSED",
                            "reason": f"book closed {guard_state.closed_ticks[pair]} ticks",
                        }
                    tick += 1
                    persist()
                    run_dir.append_event(row)
                    run_dir.write_latest(row)
                    count += 1
                    if config.steps and count >= config.steps:
                        stop_reason = f"{count} steps done"
                        break
                    await pace()
                    continue
                check_market_open(pair, True, guard_state, guard_settings)

                frame = market_frame(
                    pair, obs.candles, obs.bid, obs.ask, config.n_candles
                )
                neural = await loop.run_in_executor(
                    pool, worker._observe, frame, kind, config.neural_ms
                )
                posture = decode(
                    Channels(
                        neural["trend_hz"], neural["arousal_hz"], neural["gate_spikes"]
                    ),
                    baselines[pair],
                    decoder_settings,
                )
                proposed = build_config(spec, posture)

                # Checkpoint and anchor are committed before any apply.
                ck = run_dir.checkpoint_path(tick)
                sha = await loop.run_in_executor(pool, worker._checkpoint, str(ck))
                anchor = equity
                tick += 1
                persist({"checkpoint": {"file": ck.name, "sha256": sha}})

                neural_row = {k: v for k, v in neural.items() if k != "cell_ids"}
                row.update({"neural": neural_row, "posture": posture.to_dict()})

                now = time.time()
                last_apply_ts = (
                    guard_state.last_apply.get(pair)
                    if config.mode == "live"
                    else shadow_last.get(pair)
                )
                ok, why = should_apply(
                    postures[pair], posture, last_apply_ts, now, hysteresis
                )
                diff = config_diff(applied[pair], proposed)
                if not ok:
                    row["execution"] = {"status": "HOLD", "reason": why}
                elif config.mode == "shadow":
                    postures[pair] = posture
                    shadow_last[pair] = now
                    row["execution"] = {
                        "status": "SHADOW",
                        "reason": why,
                        "would_apply": diff,
                    }
                else:
                    try:
                        check_not_halted(guard_state)
                        unreported = [
                            p
                            for p, info in per_pair.items()
                            if info.get("running") and not info.get("reported")
                        ]
                        if unreported:
                            raise Veto(
                                "no performance report for "
                                + ", ".join(unreported)
                                + "; the guard cannot see P&L"
                            )
                        check_apply_window(guard_state, now, guard_settings)
                        check_config(proposed, spec)
                        check_collateral(
                            await market.available_usd(), required_collateral(specs)
                        )
                        check_price_move(
                            obs.mid, await market.fresh_mid(pair), guard_settings
                        )
                        try:
                            await market.apply(pair, proposed)
                        except Exception as failure:
                            logger.exception("fly apply failed for %s", pair)
                            row["execution"] = {
                                "status": "ERROR",
                                "reason": repr(failure)[:300],
                            }
                            record_apply(guard_state, pair, now, False, guard_settings)
                        else:
                            record_apply(guard_state, pair, now, True, guard_settings)
                            postures[pair] = posture
                            applied[pair] = proposed
                            row["execution"] = {
                                "status": "APPLIED",
                                "reason": why,
                                "diff": diff,
                            }
                    except Veto as veto:
                        row["execution"] = {"status": "VETO", "reason": str(veto)}
                persist()
                run_dir.append_event(row)
                run_dir.write_latest(row)
                run_dir.save_frame(frame)

                # ---- live report --------------------------------------------
                report.clear()
                b = report.builder
                b.manual_order()
                b.section(
                    "01 / WHAT THE FLY SEES",
                    f"{pair} — last input frame, tick {tick - 1}",
                )
                b.plotly(_frame_figure(frame))
                b.section(
                    "02 / THIS OBSERVATION", "Spikes, channels and the decoded posture"
                )
                b.kpi("Regime", posture.regime)
                b.kpi("Spread ×", f"{posture.spread_mult:.2f}")
                b.kpi("Lean", f"{posture.shift_bps:+.2f} bp")
                b.kpi("Trend z", f"{posture.trend_z:+.2f}")
                b.kpi("Arousal z", f"{posture.arousal_z:+.2f}")
                b.kpi("Gate", str(neural["gate_spikes"]))
                b.kpi("Stimulus", kind)
                b.kpi("P&L Δ", f"{delta:+.4f}")
                b.kpi("Equity", f"{equity:+.4f}")
                b.kpi("Execution", row["execution"]["status"])
                b.kpi("KC spikes", str(neural["kc_spikes"]))
                b.kpi("Changed edges", str(neural["memory"]["changed_edges"]))
                b.kpi("Mean efficacy", f"{neural['memory']['mean_efficacy']:.4f}")
                b.kpi("Compute s", f"{neural['compute_seconds']:.1f}")
                b.section(
                    "03 / POSTURES", "Last decided posture per pair and what is applied"
                )
                b.table(
                    [
                        {
                            "Pair": p,
                            "Regime": (postures[p].regime if postures[p] else "—"),
                            "Spread ×": (
                                f"{postures[p].spread_mult:.2f}" if postures[p] else "—"
                            ),
                            "Lean bp": (
                                f"{postures[p].shift_bps:+.2f}" if postures[p] else "—"
                            ),
                            "Baseline n": baselines[p].count,
                            "Applied buy": (applied[p] or {}).get("buy_spreads", "—"),
                            "Applied sell": (applied[p] or {}).get("sell_spreads", "—"),
                            "Bot": (
                                "running" if per_pair.get(p, {}).get("running") else "—"
                            ),
                        }
                        for p in pairs
                    ],
                    [
                        "Pair",
                        "Regime",
                        "Spread ×",
                        "Lean bp",
                        "Baseline n",
                        "Applied buy",
                        "Applied sell",
                        "Bot",
                    ],
                )
                b.section("04 / RECENT TICKS", "Newest last")
                events = run_dir.recent_events(40)
                b.table(
                    [
                        {
                            "Tick": e["tick"],
                            "Pair": e["pair"],
                            "Stim": e.get("stimulus", "—"),
                            "Δ": f"{e.get('pnl_delta', 0):+.3f}",
                            "Regime": (e.get("posture") or {}).get("regime", "—"),
                            "Spread ×": (e.get("posture") or {}).get(
                                "spread_mult", "—"
                            ),
                            "Lean": (e.get("posture") or {}).get("shift_bps", "—"),
                            "Exec": (e.get("execution") or {}).get("status", "—"),
                            "Reason": str((e.get("execution") or {}).get("reason", ""))[
                                :60
                            ],
                        }
                        for e in events
                    ],
                    [
                        "Tick",
                        "Pair",
                        "Stim",
                        "Δ",
                        "Regime",
                        "Spread ×",
                        "Lean",
                        "Exec",
                        "Reason",
                    ],
                )
                b.section("05 / GUARD", "Halts, breakers, apply budget")
                b.kpi("Halted", guard_state.halted or "no")
                b.kpi("Applies today", str(guard_state.applies_today))
                b.kpi("Session high", f"{guard_state.session_high_net:+.4f}")
                b.kpi("Ticks since high", str(guard_state.ticks_since_high))
                b.kpi("Loss stop", f"-{max_loss:.2f}")
                b.markdown(
                    "_The regime, spread multiplier and lean are decoded from spike counts "
                    "of identified cells; the mapping is engineered and unvalidated. Dopamine "
                    "pulses report P&L change, not credit for the last posture. See "
                    "docs/market_making_fly_design.md §15._"
                )
                await report.update()

                if row["execution"]["status"] in ("APPLIED", "STOP_BOT", "ERROR"):
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"🪰 {pair} tick {tick - 1}: {row['execution']['status']} — "
                            f"{posture.regime} ×{posture.spread_mult:.2f} {posture.shift_bps:+.1f}bp "
                            f"({row['execution']['reason']})"
                        )[:900],
                    )
            except asyncio.CancelledError:
                raise
            except Halt as halt:
                row["execution"] = {
                    "status": "HALT",
                    "reason": halt.reason,
                    "financial": halt.financial,
                }
                persist()
                run_dir.append_event(row)
                run_dir.write_latest(row)
                stopped = []
                if config.mode == "live":
                    for p in pairs:
                        try:
                            if await market.stop_bot(p):
                                stopped.append(pair_names(p).bot_name)
                        except Exception:
                            logger.exception("fly: stopping %s after halt failed", p)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"🪰 HALT ({'financial' if halt.financial else 'transient'}): {halt.reason}. "
                        f"Stopped bots: {', '.join(stopped) or 'none'}. "
                        + (
                            "A financial halt needs a new run_name."
                            if halt.financial
                            else "Restart with resume_reviewed=true after review."
                        )
                    ),
                )
                stop_reason = f"halted: {halt.reason}"
                break
            except Exception as failure:
                logger.exception("fly tick %s failed", tick)
                row["execution"] = {
                    "status": "TICK_ERROR",
                    "reason": repr(failure)[:300],
                }
                run_dir.append_event(row)
                run_dir.write_latest(row)
            count += 1
            if config.steps and count >= config.steps:
                stop_reason = f"{count} steps done"
                break
            await pace()
    except asyncio.CancelledError:
        stop_reason = "cancelled"
        raise
    finally:
        if report.report_id is not None:
            report.clear()
            report.builder.auto_refresh(None)
            report.builder.section("FLY STOPPED", stop_reason)
            report.builder.markdown(f"Run directory: `{run_dir.root}`")
            await report.update()
        pool.shutdown(wait=True, cancel_futures=True)
        run_dir.unlock()
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🪰 Fly stopped after {count} observations: {stop_reason}",
            )
        except Exception:
            logger.warning("fly: final notification failed")
    return f"Fly stopped after {count} observations: {stop_reason}"
