"""Run the fly over recorded candles, several ways, and compare them.

The agent's central claim — that P&L feedback shapes what the fly does — has
never had a control, because every live run is one sample of a market that
never repeats. This runs the same candles through the same brain as many times
as there are variants, changing one setting each time:

* ``live``     — the fly as deployed.
* ``no-memory``— the memory rule frozen. If this scores the same, the plastic
                 synapses are decoration.
* ``no-valence``— the memory rule still runs, but its output is disconnected
                 from the posture (``valence_gain`` ~ 0). Separates "the rule
                 does nothing" from "the rule does something the decoder does
                 not read".
* ``shuffled`` — reinforcement of the same frequency and magnitude, with the
                 sign randomised. The control the caveats have always demanded.
* ``widen``    — the old arousal direction, for the A/B that motivated the flip.
* ``two-sided``— a side threshold no trend reaches, so both sides stay on the
                 book. Says what taking a side away is worth.

Each variant gets its own brain process: a network that has already learned
from one variant is not a control for the next.
"""

from __future__ import annotations

import sys
from pathlib import Path

_AGENT_DIR = str(Path(__file__).resolve().parents[1])
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

import asyncio
import json
import logging
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor

import plotly.graph_objects as go
from flybrain import venue, worker
from flybrain.decoder import DecoderSettings
from flybrain.fly3d import ACCENT, BODY, GROUND, LIMB
from flybrain.naming import pair_names
from flybrain.posture import MarketSpec
from flybrain.replay import paired_stats, pooled_stats, replay, windows
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.memory.paths import agent_home
from condor.reports import ReportBuilder

logger = logging.getLogger(__name__)

CATEGORY = "Bot Analysis"
AGENT_SLUG = "market_making_fly"

# A gain cannot be zero — the decoder refuses a channel it would read and
# discard — so "disconnected" is the smallest gain that rounds out of every
# posture the size multiplier can express.
OFF = 1e-9

VARIANTS: dict[str, dict] = {
    "live": {},
    "no-memory": {"learning": False},
    "no-valence": {"valence_gain": OFF},
    "shuffled": {"shuffle": True},
    "widen": {"spread_gain": 0.5},
    "two-sided": {"z_side": 99.0},
}


class Config(BaseModel):
    """Replay the fly over historical candles and compare its variants."""

    trading_pair: str = Field(default="XYZ:DRAM-USD", description="Market to replay")
    connector_name: str = Field(default="hyperliquid_perpetual")
    interval: str = Field(default="5m", description="Candle interval, as the fly sees")
    max_records: int = Field(
        default=1000, ge=200, le=5000, description="Candles to fetch"
    )
    variants: str = Field(
        default="live,no-memory,no-valence,shuffled,widen,two-sided",
        description=f"Comma-separated, from: {', '.join(VARIANTS)}",
    )
    total_amount_quote: float = Field(default=200.0)
    portfolio_allocation: float = Field(default=0.3)
    range_bps: float = Field(
        default=0.0, description="0 measures the median candle range from the data"
    )
    maker_fee_bps: float = Field(default=0.0, description="0 fetches the venue's")
    leverage: int = Field(default=1)
    n_candles: int = Field(default=72, description="Window the retina sees")
    neural_ms: float = Field(default=500.0)
    baseline_window: int = Field(default=60)
    baseline_warmup: int = Field(default=10)
    seed: int = Field(default=7301, description="Seed for the shuffled control")
    windows: int = Field(
        default=1,
        ge=1,
        le=8,
        description="Replay every variant on this many contiguous slices of the "
        "series, and judge a control by how many of them it lost. One window is "
        "one sample: the sign of a result flipped between two windows on "
        "2026-09-13, so a single one settles nothing either way",
    )
    refresh_candles: bool = Field(
        default=False,
        description="Fetch a new window and pin it. Off by default: the venue "
        "only serves the latest N candles, so two runs an hour apart replay "
        "different markets and are not comparable — the sign of a control's "
        "difference flipped between two such windows on 2026-09-13",
    )
    concurrency: int = Field(
        default=4,
        ge=1,
        le=8,
        description="Variants to replay at once. Each holds its own brain (~250 MB) "
        "and saturates one core; they are independent, so this is wall time "
        "divided rather than work shared",
    )


def _settings(config: Config, overrides: dict) -> DecoderSettings:
    fields = {
        "window": config.baseline_window,
        "warmup": config.baseline_warmup,
    }
    fields.update(
        {k: v for k, v in overrides.items() if k not in ("learning", "shuffle")}
    )
    return DecoderSettings(**fields)


def _curve_figure(results: list) -> go.Figure:
    fig = go.Figure()
    palette = [ACCENT, "#7fb2ff", "#f2a35c", "#c58cff", "#6fd3c0"]
    for n, result in enumerate(results):
        fig.add_trace(
            go.Scatter(
                x=list(range(len(result.equity_curve))),
                y=result.equity_curve,
                mode="lines",
                name=result.variant,
                line=dict(color=palette[n % len(palette)], width=2),
            )
        )
    fig.update_layout(
        height=366,
        margin=dict(l=56, r=16, t=10, b=40),
        paper_bgcolor=GROUND,
        plot_bgcolor=GROUND,
        font=dict(color=BODY, family="monospace", size=11),
        xaxis=dict(title="tick", gridcolor="#18202e", zerolinecolor="#18202e"),
        yaxis=dict(
            title="net P&L (quote)", gridcolor="#18202e", zerolinecolor="#243044"
        ),
        legend=dict(orientation="h", yanchor="top", y=-0.18, xanchor="center", x=0.5),
    )
    fig.add_hline(y=0, line=dict(color=LIMB, width=1, dash="dot"))
    return fig


async def _candles(client, config: Config, pinned: Path) -> list[dict]:
    """The series every variant replays — and every *later* run replays too.

    The venue serves only the latest N candles, so fetching each time means two
    runs an hour apart are scored on different markets. That is not a detail:
    between two such windows the sign of the fly's difference from its frozen
    control reversed. So the first fetch is pinned to disk and reused until
    someone asks for a new one, which is what makes a lever's before and after
    a comparison rather than two anecdotes.
    """
    if pinned.exists() and not config.refresh_candles:
        saved = json.loads(pinned.read_text())
        if saved.get("interval") == config.interval and saved.get("candles"):
            return saved["candles"]
    for attempt in (1, 2):  # a cold feed answers on the second ask
        try:
            raw = await client.market_data.get_candles(
                config.connector_name,
                config.trading_pair,
                interval=config.interval,
                max_records=config.max_records,
            )
            rows = raw if isinstance(raw, list) else raw.get("data", raw.get("candles"))
            if rows:
                pinned.parent.mkdir(parents=True, exist_ok=True)
                pinned.write_text(
                    json.dumps(
                        {
                            "pair": config.trading_pair,
                            "interval": config.interval,
                            "fetched_at": time.time(),
                            "candles": list(rows),
                        }
                    )
                )
                return list(rows)
        except Exception:
            if attempt == 2:
                raise
    raise RuntimeError(f"No candles for {config.trading_pair}")


def _range_from_candles(candles: list[dict]) -> float:
    """The median bar's range in bp — the quantity the quote levels are built
    from, and the same median the scanner ranks reach by."""
    import statistics

    ranges = [
        (float(c["high"]) - float(c["low"])) / float(c["close"]) * 1e4
        for c in candles
        if float(c.get("close") or 0) > 0
    ]
    if not ranges:
        raise ValueError("No usable candles to measure a range from")
    return round(statistics.median(ranges), 2)


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    from config_manager import get_client

    names = [v.strip() for v in config.variants.split(",") if v.strip()]
    unknown = [v for v in names if v not in VARIANTS]
    if unknown:
        raise ValueError(f"Unknown variant(s) {unknown}; choose from {list(VARIANTS)}")

    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"
    slug = pair_names(config.trading_pair).slug
    home = agent_home(AGENT_SLUG) / "replay"
    home.mkdir(parents=True, exist_ok=True)
    pinned = home / f"{slug}-{config.interval}-candles.json"
    candles = await _candles(client, config, pinned)
    market_type = venue.market_type_for(config.connector_name)
    fee = config.maker_fee_bps or await venue.maker_fee_bps(
        config.connector_name, market_type, config.trading_pair
    )
    market_range = config.range_bps or _range_from_candles(candles)
    spec = MarketSpec(
        connector_name=config.connector_name,
        trading_pair=config.trading_pair,
        total_amount_quote=config.total_amount_quote,
        range_bps=market_range,
        leverage=config.leverage,
        portfolio_allocation=config.portfolio_allocation,
        maker_fee_bps=fee,
    )
    spec.check_order_size()
    record = home / f"{slug}.json"

    loop = asyncio.get_running_loop()
    gate = asyncio.Semaphore(config.concurrency)

    async def one(name: str, series: list[dict], label: str):
        overrides = VARIANTS[name]
        async with gate:
            # A fresh process per variant: the brain is stateful, and one that
            # has already learned is not a control for the next.
            pool = ProcessPoolExecutor(
                max_workers=1,
                mp_context=multiprocessing.get_context("spawn"),
                initializer=worker._init,
                initargs=(overrides.get("learning", True), 10.0, 200.0, 20.0),
            )
            try:

                def observe(frame, stimulus, neural_ms, _pool=pool):
                    return _pool.submit(
                        worker._observe, frame, stimulus, neural_ms
                    ).result()

                result = await loop.run_in_executor(
                    None,
                    lambda o=overrides, ob=observe: replay(
                        variant=name,
                        pair=config.trading_pair,
                        candles=series,
                        spec=spec,
                        settings=_settings(config, o),
                        observe=ob,
                        window=config.n_candles,
                        shuffle_seed=config.seed if o.get("shuffle") else None,
                        neural_ms=config.neural_ms,
                    ),
                )
            finally:
                pool.shutdown(wait=True)
        await context.bot.send_message(
            chat_id=context._chat_id,
            text=f"🪰 replay {name}{label}: net {result.equity_curve[-1]:+.4f} "
            f"over {result.ticks} ticks, {result.ledger.fills} fills",
        )
        return result

    slices = windows(candles, config.windows, config.n_candles)
    # Order is the caller's, not the order they finished in: the first variant
    # is the baseline every control is compared against.
    per_window: list[list] = []
    for index, series in enumerate(slices, 1):
        label = f" w{index}" if len(slices) > 1 else ""
        per_window.append(
            list(await asyncio.gather(*(one(name, series, label) for name in names)))
        )
    results = per_window[0]
    summaries = [r.summary() for r in results]
    # The brains are the expensive part and the arithmetic over their output is
    # not; keeping the curves means a better statistic never costs another run.
    record.write_text(
        json.dumps(
            {
                "pair": config.trading_pair,
                "interval": config.interval,
                "candles": len(candles),
                "range_bps": market_range,
                "fee_bps": fee,
                "summaries": summaries,
                "curves": {r.variant: r.equity_curve for r in results},
            },
            indent=1,
        )
    )
    builder = ReportBuilder(f"Fly replay — {config.trading_pair}")
    builder.source("routine", "fly_replay")
    builder.tags(["fly", "replay", "control", config.trading_pair])
    builder.manual_order()
    builder.section(
        "WHAT WAS REPLAYED",
        f"{len(candles):,} {config.interval} candles of {config.trading_pair}"
        + (" (freshly fetched)" if config.refresh_candles else " (the pinned window)")
        + ", "
        f"{results[0].ticks if results else 0} ticks after the "
        f"{config.n_candles}-candle window. Every variant saw the same series and "
        f"the same geometry — {market_range:.2f} bp median bar range, {fee:.2f} bp "
        "maker fee — and each ran on its own freshly seeded brain. Fills assume "
        "a quote the price touched was ours, so every P&L here is an upper "
        "bound; the bias is identical across variants, which is what makes the "
        "comparison worth reading and the absolute number not.",
    )
    builder.kpi("Market", config.trading_pair)
    builder.kpi("Candles", f"{len(candles):,}")
    builder.kpi("Ticks", f"{results[0].ticks if results else 0:,}")
    builder.kpi("Maker fee", f"{fee:.2f} bp")

    builder.section("VARIANTS", "One row per run, over identical candles")
    builder.table(
        [
            {
                "Variant": s["variant"],
                "Net P&L": f"{s['net']:+.4f}",
                "Realized": f"{s['realized']:+.4f}",
                "Fees": f"{s['fees']:.4f}",
                "Fills": f"{s['fills']:,}",
                "Round trips": f"{s['round_trips']:,}",
                "Applies": f"{s['applies']:,}",
                "Unconfident": f"{s['unconfident']:,}",
                "Spread ×": f"{s['mean_spread_mult']:.2f}",
                "Size ×": f"{s['mean_size_mult']:.2f}",
                "One-sided": f"{s['one_sided']:,}",
            }
            for s in summaries
        ],
        [
            "Variant",
            "Net P&L",
            "Realized",
            "Fees",
            "Fills",
            "Round trips",
            "Applies",
            "Unconfident",
            "Spread ×",
            "Size ×",
            "One-sided",
        ],
    )
    builder.plotly(_curve_figure(results))

    if len(results) > 1 and len(per_window) > 1:
        # The verdict that matters when there is more than one window: the
        # increments pooled across all of them, and how many windows the fly
        # actually lost. A control it beats in two of four is noise whatever
        # the total says.
        builder.section(
            "ACROSS WINDOWS",
            f"{len(per_window)} contiguous slices of the same series, each "
            "replayed by every variant on its own freshly seeded brain. "
            "'Windows lost' counts the slices where the control finished ahead "
            "of the deployed fly — a real difference should show in the pooled "
            "increments *and* in most windows, and one that shows in only the "
            "total was carried by one slice.",
        )
        pooled_rows = []
        for position, name in enumerate(names[1:], start=1):
            curves = [
                (window[0].equity_curve, window[position].equity_curve)
                for window in per_window
            ]
            stats = pooled_stats(curves)
            pooled_rows.append(
                {
                    "Against": f"{names[0]} − {name}",
                    "Pooled per-tick": f"{stats['mean_diff']:+.5f}",
                    "Pooled t": f"{stats['t']:+.2f}",
                    "Windows won": f"{stats['led']}/{stats['windows']}",
                    "Reads as": (
                        "distinguishable"
                        if abs(stats["t"]) >= 2
                        and stats["led"] in (0, stats["windows"])
                        else "not distinguishable"
                    ),
                }
            )
        builder.table(
            pooled_rows,
            ["Against", "Pooled per-tick", "Pooled t", "Windows won", "Reads as"],
        )

    if len(results) > 1:
        base = results[0]
        rows = []
        for other in results[1:]:
            stats = paired_stats(base.equity_curve, other.equity_curve)
            rows.append(
                {
                    "Against": f"{base.variant} − {other.variant}",
                    "Mean per-tick earnings difference": f"{stats['mean_diff']:+.5f}",
                    "SD": f"{stats['sd']:.5f}",
                    "Final gap": f"{stats['final_gap']:+.4f}",
                    "t": f"{stats['t']:+.2f}",
                    "Reads as": (
                        "distinguishable"
                        if abs(stats["t"]) >= 2
                        else "not distinguishable"
                    ),
                }
            )
        builder.section(
            "AGAINST THE CONTROLS" + (" (first window)" if len(per_window) > 1 else ""),
            "How differently the deployed fly earns per tick, against each "
            "control. On increments, not on the equity curves themselves: a "
            "curve is cumulative, so once two runs separate every later tick "
            "inherits the gap and a t on levels measures when they diverged "
            "rather than whether they earn differently. One replay of one "
            "market is still a weak instrument — |t| under 2 is not evidence of "
            "a difference, and it is not evidence of sameness either.",
        )
        builder.table(
            rows,
            [
                "Against",
                "Mean per-tick earnings difference",
                "SD",
                "Final gap",
                "t",
                "Reads as",
            ],
        )

    builder.markdown(
        "_A variant that scores better here has not been shown to make money: "
        "the fill model is optimistic, one market is one sample, and the "
        "take-profit that never fills is marked to the close rather than to "
        "what it would cost to get out. What replay can establish is the "
        "negative — that a variant is **not** distinguishable from its control, "
        "which is the claim this agent has never been able to test._"
    )
    await builder.save()

    lines = [
        f"pair: {config.trading_pair} ({config.interval})",
        f"candles: {len(candles)} in {len(per_window)} window(s), "
        f"ticks each: {results[0].ticks if results else 0}",
        f"median range: {market_range:.2f} bp, fee: {fee:.2f} bp",
    ]
    for s in summaries:
        lines.append(
            f"{s['variant']}: net {s['net']:+.4f}, {s['fills']} fills, "
            f"{s['round_trips']} round trips, {s['applies']} applies, "
            f"spread ×{s['mean_spread_mult']:.2f}, size ×{s['mean_size_mult']:.2f}, "
            f"{s['one_sided']} one-sided"
        )
    if len(results) > 1:
        for position, name in enumerate(names[1:], start=1):
            if len(per_window) > 1:
                stats = pooled_stats(
                    [
                        (window[0].equity_curve, window[position].equity_curve)
                        for window in per_window
                    ]
                )
                lines.append(
                    f"{names[0]} vs {name}: pooled per-tick {stats['mean_diff']:+.5f}, "
                    f"t {stats['t']:+.2f}, won {stats['led']}/{stats['windows']} windows"
                )
            else:
                stats = paired_stats(
                    results[0].equity_curve, results[position].equity_curve
                )
                lines.append(
                    f"{names[0]} vs {name}: mean per-tick earnings "
                    f"{stats['mean_diff']:+.5f}, final gap {stats['final_gap']:+.4f}, "
                    f"t {stats['t']:+.2f}"
                )
    return "\n".join(lines)
