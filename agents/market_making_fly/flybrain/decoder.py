"""Decode spike counts into a quoting posture.

An engineered, fixed readout — like stonkfly's, it reads only spike counts and
the cells it reads are logged. Nothing here is a discovery of "market-making
neurons":

* ``trend_hz``   — mean DNp20 right rate minus mean left rate (stonkfly's
                   BUY/SELL cells). Sign → which way the reference price leans.
* ``arousal_hz`` — mean rate of the descending-neuron population. Higher →
                   tighter spreads and a larger share of the book quoted.
* ``gate``       — DNpe017 spikes ≥ 1, required for a trending call.
* ``valence_hz`` — mean MBON07 rate minus mean MBON11 rate: approach minus
                   avoidance. These are the cells the KC→MBON memory rule
                   writes to, so this is the only channel a P&L pulse can
                   reach. Higher → more of the book quoted. Without it the
                   dopamine loop changes synapses that change nothing.
* ``kc_spikes``  — Kenyon cell drive. Not a posture: a confidence test. A scene
                   that barely reaches the mushroom body leaves the other
                   channels reading noise, so the posture is marked unconfident
                   and the loop holds rather than applying it.

Channels are z-scored against a rolling per-pair baseline. Stonkfly's own run
proposed BUY six times out of six — a persistent turning bias of the circuit
became persistent buying. In market making that would be a persistent skew, so
the baseline is subtracted before thresholds are applied. ``center_bias=False``
keeps stonkfly's raw reading of the trend channel (2 Hz = one unit) as a
control; arousal has no natural zero and is always window-normalized.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Literal

Regime = Literal[
    "pause", "volatile", "trending_up", "trending_down", "quiet", "ranging"
]
REGIMES: tuple[str, ...] = (
    "pause",
    "volatile",
    "trending_up",
    "trending_down",
    "quiet",
    "ranging",
)

# stonkfly's DNp20 threshold: 2 Hz difference is one unit in raw mode.
RAW_TREND_UNIT_HZ = 2.0


@dataclass(frozen=True)
class DecoderSettings:
    window: int = 60
    warmup: int = 10
    z_regime: float = 1.0
    z_pause: float = 2.5
    # Negative: an aroused fly quotes *tighter*, not wider. Arousal is the
    # descending population's rate against its own recent average — nothing
    # ties it to volatility, so neither sign is derived from anything. This one
    # says an active market is one to lean into; +0.5 says it is one to back
    # away from, which is the textbook answer. Both are testable against each
    # other on the same market and neither has been.
    spread_gain: float = -0.5
    spread_min: float = 0.6
    spread_max: float = 2.5
    # And it quotes more of the book while it is active. Bounded the same way,
    # then clamped in build_config so an order never falls under the venue
    # minimum or the allocation over 1.
    size_gain: float = 0.5
    size_min: float = 0.6
    size_max: float = 2.5
    # What the fly has learned about scenes like this one, on the same scale as
    # arousal and added to it, so the size the fly commits carries both "the
    # market is active" and "this looked good last time".
    valence_gain: float = 0.5
    # A scene this far below the mushroom body's own recent drive is one the
    # fly effectively did not see; its z-scores are noise.
    z_kc_quiet: float = -1.5
    shift_gain_bps: float = 1.0
    max_shift_bps: float = 3.0
    center_bias: bool = True

    def __post_init__(self):
        if self.window < 2 or self.warmup < 1 or self.warmup > self.window:
            raise ValueError("window >= 2 and 1 <= warmup <= window required")
        if not 0 < self.z_regime < self.z_pause:
            raise ValueError("0 < z_regime < z_pause required")
        for lo, hi, what in (
            (self.spread_min, self.spread_max, "spread"),
            (self.size_min, self.size_max, "size"),
        ):
            if not 0 < lo <= 1 <= hi:
                raise ValueError(f"{what}_min <= 1 <= {what}_max required")
        for name in ("shift_gain_bps", "max_shift_bps"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        # The two arousal gains carry a direction, so they may be negative —
        # but not zero, which would mean the channel is read and discarded.
        if self.z_kc_quiet >= 0:
            raise ValueError("z_kc_quiet must be negative")
        for name in ("spread_gain", "size_gain", "valence_gain"):
            value = getattr(self, name)
            if not math.isfinite(value) or value == 0:
                raise ValueError(f"{name} must be finite and non-zero")


@dataclass(frozen=True)
class Channels:
    trend_hz: float
    arousal_hz: float
    gate_spikes: int
    valence_hz: float = 0.0
    kc_spikes: int = 0

    def __post_init__(self):
        for name in ("trend_hz", "arousal_hz", "valence_hz"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"Nonfinite channel {name}")
        if self.arousal_hz < 0 or self.gate_spikes < 0 or self.kc_spikes < 0:
            raise ValueError("Negative rate or spike count")


@dataclass
class Baseline:
    """Rolling per-pair history of each channel; persisted in state.json."""

    trend: list[float] = field(default_factory=list)
    arousal: list[float] = field(default_factory=list)
    valence: list[float] = field(default_factory=list)
    kc: list[float] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Baseline":
        if not data:
            return cls()
        return cls(
            trend=list(data["trend"]),
            arousal=list(data["arousal"]),
            valence=list(data["valence"]),
            kc=list(data["kc"]),
        )

    def to_dict(self) -> dict:
        return {
            "trend": list(self.trend),
            "arousal": list(self.arousal),
            "valence": list(self.valence),
            "kc": list(self.kc),
        }

    @property
    def count(self) -> int:
        return len(self.trend)

    def push(self, channels: Channels, window: int) -> None:
        self.trend.append(channels.trend_hz)
        self.arousal.append(channels.arousal_hz)
        self.valence.append(channels.valence_hz)
        self.kc.append(float(channels.kc_spikes))
        for history in (self.trend, self.arousal, self.valence, self.kc):
            del history[:-window]


def _z(value: float, history: list[float], center: bool) -> float:
    if len(history) < 2:
        return 0.0
    std = statistics.pstdev(history)
    if std < 1e-9:
        return 0.0
    mean = statistics.fmean(history) if center else 0.0
    return (value - mean) / std


@dataclass(frozen=True)
class Posture:
    regime: str
    spread_mult: float
    size_mult: float
    shift_bps: float
    trend_z: float
    arousal_z: float
    valence_z: float
    gate: bool
    warm: bool  # False while the baseline is still forming
    confident: bool = True  # False when the scene never reached the mushroom body

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Posture":
        return cls(**data)


NEUTRAL = Posture(
    regime="ranging",
    spread_mult=1.0,
    size_mult=1.0,
    shift_bps=0.0,
    trend_z=0.0,
    arousal_z=0.0,
    valence_z=0.0,
    gate=False,
    warm=False,
)


def classify(trend_z: float, arousal_z: float, gate: bool, s: DecoderSettings) -> str:
    """Regime in precedence order: pause > volatile > trending > quiet > ranging."""
    if arousal_z >= s.z_pause:
        return "pause"
    if arousal_z >= s.z_regime:
        return "volatile"
    if gate and trend_z >= s.z_regime:
        return "trending_up"
    if gate and trend_z <= -s.z_regime:
        return "trending_down"
    if arousal_z <= -s.z_regime:
        return "quiet"
    return "ranging"


def decode(channels: Channels, baseline: Baseline, s: DecoderSettings) -> Posture:
    """Push the observation into the baseline and return the posture.

    The baseline is mutated (that is the point: it is the rolling window)."""
    baseline.push(channels, s.window)
    if baseline.count < s.warmup:
        return NEUTRAL
    if s.center_bias:
        trend_z = _z(channels.trend_hz, baseline.trend, center=True)
    else:
        trend_z = channels.trend_hz / RAW_TREND_UNIT_HZ
    arousal_z = _z(channels.arousal_hz, baseline.arousal, center=True)
    valence_z = _z(channels.valence_hz, baseline.valence, center=True)
    kc_z = _z(float(channels.kc_spikes), baseline.kc, center=True)
    gate = channels.gate_spikes >= 1
    # No Kenyon drive at all, or far below what this pair usually produces: the
    # chart did not reach the mushroom body, so every z above is measuring the
    # network's own noise rather than the picture.
    confident = channels.kc_spikes > 0 and kc_z > s.z_kc_quiet
    regime = classify(trend_z, arousal_z, gate, s)
    spread_mult = min(s.spread_max, max(s.spread_min, 1 + s.spread_gain * arousal_z))
    size_mult = min(
        s.size_max,
        max(s.size_min, 1 + s.size_gain * arousal_z + s.valence_gain * valence_z),
    )
    shift = max(-s.max_shift_bps, min(s.max_shift_bps, s.shift_gain_bps * trend_z))
    if not gate:
        shift = 0.0  # no descending gate spike, no directional lean
    return Posture(
        regime=regime,
        spread_mult=round(spread_mult, 4),
        size_mult=round(size_mult, 4),
        shift_bps=round(shift, 3),
        trend_z=round(trend_z, 4),
        arousal_z=round(arousal_z, 4),
        valence_z=round(valence_z, 4),
        gate=gate,
        warm=True,
        confident=confident,
    )


@dataclass(frozen=True)
class Hysteresis:
    min_apply_interval_sec: float = 300.0
    spread_delta: float = 0.15
    shift_delta_bps: float = 0.5


def should_apply(
    previous: Posture | None,
    new: Posture,
    last_apply_ts: float | None,
    now: float,
    h: Hysteresis,
) -> tuple[bool, str]:
    """Rate-limit configuration changes to material posture moves."""
    if not new.confident:
        # Holding is the conservative act: the last applied config stays, and
        # the reason is recorded rather than a neutral posture being written
        # as though the fly had decided on one.
        return False, "kenyon drive below baseline — scene not seen"
    if previous is None:
        return True, "first posture"
    if last_apply_ts is not None and now - last_apply_ts < h.min_apply_interval_sec:
        return False, "apply cooldown"
    if new.regime != previous.regime:
        return True, f"regime {previous.regime} -> {new.regime}"
    if abs(new.spread_mult - previous.spread_mult) >= h.spread_delta:
        return True, f"spread_mult {previous.spread_mult} -> {new.spread_mult}"
    if abs(new.shift_bps - previous.shift_bps) >= h.shift_delta_bps:
        return True, f"shift_bps {previous.shift_bps} -> {new.shift_bps}"
    return False, "no material change"
