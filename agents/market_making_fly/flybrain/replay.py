"""Replay the fly over recorded candles, with a maker fill model for its P&L.

Every experiment this agent needs is a comparison: does the memory rule change
anything, does real reinforcement beat shuffled, does a gain matter. Answering
any of them live costs a bot, an hour and real capital, and produces one sample
of a market that never repeats. So the same loop runs here over a fixed candle
series instead, where the only thing that differs between two runs is the
setting under test.

What is identical to the live loop: the frame the retina sees (``market_frame``
over the same sliding window), the brain (the same worker, the same seeded
network), the decoder, the posture geometry and the fee. What is simulated is
only the market's answer — whether a resting quote filled — and that is the
part to be honest about:

* a quote at price ``p`` fills if the next candle trades through it. There is
  no queue position, so every fill a price *touches* is assumed ours. Real
  fills are a fraction of that, and the fraction is worst exactly when the
  market is moving, which is when this model is most generous.
* a fill's take-profit closes when a later candle trades through it. The same
  optimism applies, and inventory that never reaches its take-profit is marked
  to the close, exactly as the live loop's unrealized P&L would be.
* adverse selection appears only through price: if the market runs, the model
  fills the losing side and holds the position, which is the real failure mode
  it does reproduce.

So replay P&L is an upper bound, not a forecast. It is useful because the bias
is the *same* for every variant, which is what a comparison needs.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import numpy as np
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
from flybrain.posture import BPS, MarketSpec, build_config, take_profit_floor
from flybrain.reinforcement import reinforcement

# A held position is marked to the candle close, like the live loop's
# unrealized P&L; a closed one has both fees taken out of it.
SIDES = ("buy", "sell")


@dataclass
class Lot:
    """One filled quote, still open."""

    side: str
    price: float
    amount: float  # base units
    take_profit: float  # fraction from entry


@dataclass
class Ledger:
    """Fills, closes and P&L for one replay, in quote units."""

    realized: float = 0.0
    fees: float = 0.0
    fills: int = 0
    round_trips: int = 0
    volume: float = 0.0
    open_lots: list[Lot] = field(default_factory=list)

    @property
    def inventory(self) -> float:
        return sum(l.amount if l.side == "buy" else -l.amount for l in self.open_lots)

    def equity(self, mark: float) -> float:
        """Realized less fees, plus the open inventory marked to ``mark``."""
        unrealized = sum(
            (
                (mark - l.price) * l.amount
                if l.side == "buy"
                else (l.price - mark) * l.amount
            )
            for l in self.open_lots
        )
        return self.realized - self.fees + unrealized


def _levels(config: dict, key: str) -> list[float]:
    return [float(x) for x in config[key].split(",")]


def quote_prices(config: dict, mid: float) -> dict[str, list[float]]:
    """Where this config rests its orders around ``mid``."""
    return {
        "buy": [mid * (1 - s) for s in _levels(config, "buy_spreads")],
        "sell": [mid * (1 + s) for s in _levels(config, "sell_spreads")],
    }


def step(
    ledger: Ledger,
    config: dict,
    spec: MarketSpec,
    candle: dict,
    mid: float,
    max_lots: int,
) -> None:
    """One candle against one config: close what reaches take-profit, then fill.

    Closes are settled before new fills so a lot cannot open and close inside
    the same candle, which the data cannot support: a candle says a price was
    touched, not in what order.
    """
    high, low = float(candle["high"]), float(candle["low"])
    fee = spec.maker_fee_bps * BPS

    still_open: list[Lot] = []
    for lot in ledger.open_lots:
        target = (
            lot.price * (1 + lot.take_profit)
            if lot.side == "buy"
            else lot.price * (1 - lot.take_profit)
        )
        reached = high >= target if lot.side == "buy" else low <= target
        if not reached:
            still_open.append(lot)
            continue
        gross = (
            (target - lot.price) * lot.amount
            if lot.side == "buy"
            else (lot.price - target) * lot.amount
        )
        ledger.realized += gross
        ledger.fees += target * lot.amount * fee  # the closing side
        ledger.volume += target * lot.amount
        ledger.round_trips += 1
    ledger.open_lots = still_open

    notional = float(config["total_amount_quote"]) * float(
        config["portfolio_allocation"]
    )
    per_order = notional / 4
    prices = quote_prices(config, mid)
    for side in SIDES:
        for price in prices[side]:
            if len(ledger.open_lots) >= max_lots:
                break
            touched = low <= price if side == "buy" else high >= price
            if not touched or price <= 0:
                continue
            amount = per_order / price
            ledger.open_lots.append(
                Lot(
                    side=side,
                    price=price,
                    amount=amount,
                    take_profit=float(config["take_profit"]),
                )
            )
            ledger.fills += 1
            ledger.fees += per_order * fee
            ledger.volume += per_order


@dataclass
class ReplayResult:
    variant: str
    ticks: int
    applies: int
    holds: int
    unconfident: int
    ledger: Ledger
    equity_curve: list[float]
    postures: list[Posture]

    def summary(self) -> dict:
        regimes: dict[str, int] = {}
        for p in self.postures:
            regimes[p.regime] = regimes.get(p.regime, 0) + 1
        spreads = [p.spread_mult for p in self.postures] or [0.0]
        sizes = [p.size_mult for p in self.postures] or [0.0]
        tps = [p.tp_mult for p in self.postures] or [0.0]
        return {
            "variant": self.variant,
            "ticks": self.ticks,
            "net": round(self.equity_curve[-1] if self.equity_curve else 0.0, 4),
            "realized": round(self.ledger.realized - self.ledger.fees, 4),
            "fees": round(self.ledger.fees, 4),
            "fills": self.ledger.fills,
            "round_trips": self.ledger.round_trips,
            "volume": round(self.ledger.volume, 2),
            "open_lots": len(self.ledger.open_lots),
            "applies": self.applies,
            "unconfident": self.unconfident,
            "mean_spread_mult": round(sum(spreads) / len(spreads), 3),
            "mean_size_mult": round(sum(sizes) / len(sizes), 3),
            "mean_tp_mult": round(sum(tps) / len(tps), 3),
            "regimes": regimes,
        }


def frames(
    pair: str, candles: list[dict], window: int
) -> list[tuple[np.ndarray, dict, float]]:
    """``(frame, candle, mid)`` per tick, over a sliding window of candles.

    The bid/ask drawn on the frame are the previous close plus and minus half
    the observed spread of that bar — the live loop draws the live book, and a
    candle series has no book. The tick's *decisions* are then applied to the
    following candle, so nothing is decided on a bar it can already see.
    """
    out = []
    for i in range(window, len(candles) - 1):
        history = candles[i - window : i]
        last = float(history[-1]["close"])
        half = max(
            last * 1e-5, (float(history[-1]["high"]) - float(history[-1]["low"])) / 200
        )
        frame = market_frame(pair, history, last - half, last + half, n_candles=window)
        out.append((frame, candles[i], last))
    return out


def replay(
    variant: str,
    pair: str,
    candles: list[dict],
    spec: MarketSpec,
    settings: DecoderSettings,
    observe,
    window: int = 72,
    deadband_bps: float = 1.0,
    shuffle_seed: int | None = None,
    neural_ms: float = 500.0,
) -> ReplayResult:
    """Walk the candles once, exactly as the live loop walks wall time.

    ``observe(frame, stimulus, neural_ms) -> dict`` is the brain, injected so a
    caller can hand in a fresh worker per variant — a brain that has already
    learned from one variant is not a control for the next.

    ``shuffle_seed`` replaces each pulse with a randomly signed one of the same
    magnitude. The fly still gets reinforced exactly as often and as hard; only
    the correspondence to its own P&L is destroyed. If that scores the same,
    what the synapses hold is not about this market.
    """
    # How many open positions the controller would tolerate, rather than a
    # number picked here: two levels a side, each allowed its own concurrent
    # executors. A cap set independently of the strategy quietly becomes the
    # thing under test — with a fixed 8, widening the take-profit blocked new
    # fills by leaving lots open, so the exit was measured through the cap.
    max_lots = spec.max_active_executors_by_level * 2 * len(SIDES)
    baseline = Baseline()
    ledger = Ledger()
    hysteresis = Hysteresis(min_apply_interval_sec=0.0)
    rng = random.Random(shuffle_seed) if shuffle_seed is not None else None
    deadband = deadband_bps * BPS * spec.total_amount_quote

    config = build_config(spec, decode(Channels(0.0, 0.0, 0), Baseline(), settings))
    previous: Posture | None = None
    anchor = 0.0
    applies = holds = unconfident = 0
    curve: list[float] = []
    postures: list[Posture] = []

    for tick, (frame, candle, mid) in enumerate(frames(pair, candles, window)):
        equity = ledger.equity(mid)
        kind, _ = reinforcement(equity, anchor, deadband)
        if rng is not None and kind != "none":
            kind = rng.choice(["reward", "aversive"])
        anchor = equity

        neural = observe(frame, kind, neural_ms)
        posture = decode(
            Channels(
                neural["trend_hz"],
                neural["arousal_hz"],
                neural["gate_spikes"],
                neural["valence_hz"],
                neural["kc_spikes"],
            ),
            baseline,
            settings,
        )
        postures.append(posture)
        if not posture.confident:
            unconfident += 1
        ok, _ = should_apply(previous, posture, None, float(tick), hysteresis)
        if ok:
            config = build_config(spec, posture)
            previous = posture
            applies += 1
        else:
            holds += 1

        step(ledger, config, spec, candle, mid, max_lots)
        curve.append(ledger.equity(float(candle["close"])))

    return ReplayResult(
        variant=variant,
        ticks=len(curve),
        applies=applies,
        holds=holds,
        unconfident=unconfident,
        ledger=ledger,
        equity_curve=curve,
        postures=postures,
    )


def paired_stats(a: list[float], b: list[float]) -> dict:
    """Do two runs earn differently per tick? Mean difference, sd, and t.

    On the *increments*, not the levels. An equity curve is cumulative: once
    two runs separate, every later tick inherits that gap, so a t-test on
    levels measures how long ago they diverged rather than whether they earn
    differently. Run on levels this returned |t| of 27 to 63 for variants whose
    final P&L differed by a few percent — a number that says nothing except
    that a random walk is autocorrelated.

    Even on increments this is a weak instrument, and it is reported as one:
    one replay of one market, with fills that assume a touched price was ours.
    A |t| under 2 is not evidence of a difference; it is also not evidence of
    sameness.
    """
    n = min(len(a), len(b))
    if n < 4:
        return {"n": 0, "mean_diff": 0.0, "sd": 0.0, "t": 0.0, "final_gap": 0.0}
    gains_a = [a[i] - a[i - 1] for i in range(1, n)]
    gains_b = [b[i] - b[i - 1] for i in range(1, n)]
    deltas = [x - y for x, y in zip(gains_a, gains_b)]
    k = len(deltas)
    mean = sum(deltas) / k
    var = sum((d - mean) ** 2 for d in deltas) / (k - 1)
    sd = math.sqrt(var)
    # A difference with no spread has no sampling variation, so t is undefined
    # rather than enormous — and floating point will not produce a clean zero:
    # the increments of a perfect ramp differ in the last bits, which took the
    # naive expression to 4e15. Below a hair of the scale it measures, the
    # answer is "read the mean, there is nothing here to test".
    scale = max(abs(mean), max((abs(d) for d in deltas), default=0.0))
    degenerate = sd <= scale * 1e-9
    return {
        "n": k,
        "mean_diff": mean,
        "sd": sd,
        "t": 0.0 if degenerate else mean / (sd / math.sqrt(k)),
        "final_gap": a[n - 1] - b[n - 1],
    }
