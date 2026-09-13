"""Deterministic checks that can veto or halt, and never choose a posture.

A ``Veto`` means "keep the previous config this tick". A ``Halt`` means "stop
the bots and stop the fly until a human passes ``resume_reviewed``"; a
*financial* halt (loss stop, loss-rate breaker) cannot be cleared that way at
all — a new run directory is needed, exactly stonkfly's rule.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from flybrain.posture import MarketSpec, take_profit_floor

BPS = 1e-4


class Veto(Exception):
    pass


class Halt(Exception):
    def __init__(self, reason: str, financial: bool):
        super().__init__(reason)
        self.reason = reason
        self.financial = financial


@dataclass(frozen=True)
class GuardSettings:
    max_applies_per_day: int = 48
    apply_price_tolerance: float = 0.005
    max_loss_quote: float = 0.0  # 0 → 4 % of the combined total_amount_quote
    max_loss_per_volume_bps: float = 5.0
    loss_no_new_high_ticks: int = 25
    min_volume_for_loss_rate: float = 100.0
    max_apply_failures: int = 3
    closed_ticks_to_stop: int = 5

    def __post_init__(self):
        if self.max_applies_per_day < 1 or self.max_apply_failures < 1:
            raise ValueError("positive limits required")
        if not 0 < self.apply_price_tolerance <= 0.05:
            raise ValueError("apply_price_tolerance must be in (0, 0.05]")
        if self.max_loss_quote < 0 or self.max_loss_per_volume_bps <= 0:
            raise ValueError("loss limits must be positive")


@dataclass
class GuardState:
    """Persisted in state.json between ticks."""

    applies_day: str = ""
    applies_today: int = 0
    last_apply: dict[str, float] = field(default_factory=dict)  # per pair
    consecutive_failures: int = 0
    session_high_net: float = 0.0
    ticks_since_high: int = 0
    closed_ticks: dict[str, int] = field(default_factory=dict)
    halted: str | None = None
    halt_financial: bool = False

    @classmethod
    def from_dict(cls, data: dict | None) -> "GuardState":
        return cls(**data) if data else cls()

    def to_dict(self) -> dict:
        return asdict(self)


def _day(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")


def check_not_halted(state: GuardState) -> None:
    if state.halted:
        raise Halt(state.halted, state.halt_financial)


def resume(state: GuardState, reviewed: bool) -> None:
    """Clear a transient halt after review; refuse to clear a financial one."""
    if not state.halted:
        return
    if state.halt_financial:
        raise Halt(f"financial halt cannot be cleared by review: {state.halted}", True)
    if not reviewed:
        raise Halt(
            f"halted, pass resume_reviewed=true after review: {state.halted}", False
        )
    state.halted = None
    state.consecutive_failures = 0


def check_market_open(
    pair: str, book_open: bool, state: GuardState, s: GuardSettings
) -> bool:
    """Veto while a book is closed. Returns True when the pair has been closed
    for ``closed_ticks_to_stop`` ticks, meaning its bot should be stopped."""
    if book_open:
        state.closed_ticks[pair] = 0
        return False
    state.closed_ticks[pair] = state.closed_ticks.get(pair, 0) + 1
    if state.closed_ticks[pair] >= s.closed_ticks_to_stop:
        return True
    raise Veto(f"{pair}: book closed ({state.closed_ticks[pair]} ticks)")


def check_collateral(available_usd: float, required_usd: float) -> None:
    if not math.isfinite(available_usd) or not math.isfinite(required_usd):
        raise Veto("collateral figures are not finite")
    if available_usd < required_usd:
        raise Veto(
            f"available collateral {available_usd:.2f} < required {required_usd:.2f}"
        )


def _spreads(value: str) -> list[float]:
    return [float(x) for x in str(value).split(",") if x.strip()]


def check_config(config: dict, spec: MarketSpec) -> None:
    """Belt to ``posture.build_config``'s braces: refuse anything below the floors."""
    for key in ("buy_spreads", "sell_spreads"):
        for level in _spreads(config[key]):
            if level < spec.min_spread_bps * BPS:
                raise Veto(f"{key} level {level} below {spec.min_spread_bps} bp")
    if float(config["take_profit"]) < take_profit_floor(spec):
        raise Veto("take_profit below fee floor")
    if int(config["leverage"]) > spec.leverage_cap:
        raise Veto(f"leverage {config['leverage']} above cap {spec.leverage_cap}")
    if config["trading_pair"] != spec.trading_pair:
        raise Veto("config pair does not match market spec")
    if not config.get("global_sl_enabled"):
        raise Veto("global stop loss must stay enabled")


def check_apply_window(state: GuardState, now: float, s: GuardSettings) -> None:
    if state.applies_day != _day(now):
        state.applies_day = _day(now)
        state.applies_today = 0
    if state.applies_today >= s.max_applies_per_day:
        raise Veto("daily apply limit")


def check_price_move(observed_mid: float, fresh_mid: float, s: GuardSettings) -> None:
    if observed_mid <= 0 or fresh_mid <= 0:
        raise Veto("non-positive mid")
    if abs(fresh_mid - observed_mid) / observed_mid > s.apply_price_tolerance:
        raise Veto("price moved beyond neural observation tolerance")


def record_apply(
    state: GuardState, pair: str, now: float, ok: bool, s: GuardSettings
) -> None:
    state.applies_today += 1
    state.last_apply[pair] = now
    if ok:
        state.consecutive_failures = 0
        return
    state.consecutive_failures += 1
    if state.consecutive_failures >= s.max_apply_failures:
        state.halted = (
            f"{state.consecutive_failures} consecutive config update failures"
        )
        state.halt_financial = False
        raise Halt(state.halted, False)


def check_pnl(
    total_net: float,
    volume: float,
    state: GuardState,
    s: GuardSettings,
    max_loss_quote: float,
) -> None:
    """Loss stop and the HIP-3 loss-rate breaker. ``total_net`` must include
    unrealized P&L — realized alone can look fine while the open position bleeds."""
    if not math.isfinite(total_net) or not math.isfinite(volume):
        raise Veto("P&L figures are not finite")
    if total_net > state.session_high_net:
        state.session_high_net = total_net
        state.ticks_since_high = 0
    else:
        state.ticks_since_high += 1
    if total_net <= -max_loss_quote:
        state.halted = f"loss stop: net {total_net:.2f} <= -{max_loss_quote:.2f}"
        state.halt_financial = True
        raise Halt(state.halted, True)
    if volume >= s.min_volume_for_loss_rate:
        rate_bps = total_net / volume * 1e4
        if rate_bps <= -s.max_loss_per_volume_bps:
            state.halted = f"loss-rate breaker: {rate_bps:.1f} bp of volume"
            state.halt_financial = True
            raise Halt(state.halted, True)
    if (
        state.ticks_since_high >= s.loss_no_new_high_ticks
        and total_net < state.session_high_net
    ):
        state.halted = f"no new P&L high for {state.ticks_since_high} ticks"
        state.halt_financial = True
        raise Halt(state.halted, True)


def default_max_loss(specs: list[MarketSpec], s: GuardSettings) -> float:
    if s.max_loss_quote > 0:
        return s.max_loss_quote
    return 0.04 * sum(spec.total_amount_quote for spec in specs)
