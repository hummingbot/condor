"""Turn a posture into a full ``pmm_mister`` config.

The base is a bounded default set; the posture multiplies the spreads and leans
them. Every money-relevant floor lives here, in code:

* no spread level closer to mid than the market's own maker fee, so a
  two-sided round trip at the floor at least pays for itself;
* ``take_profit`` never below ``2.2 ×`` the round-trip maker fee, and never
  below 4 bp (Market Making Expert's "TP must exceed round-trip fees" made
  mandatory);
* the reference-price lean is capped at half the first-level spread.

Inventory bands, allocation, leverage cap and the global stop loss are the
operator's, not the fly's to move.

The spec works on any CLOB market, spot or perp. What the venue decides —
whether leverage applies at all, and what a round trip costs — comes from
``venue.py``; what the fly decides is only ever the posture.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from flybrain import venue
from flybrain.decoder import REGIMES, Posture
from flybrain.naming import pair_names

# executor_refresh_time, buy/sell cooldown — Market Making Expert's table.
TIMING: dict[str, tuple[int, int]] = {
    "quiet": (20, 30),
    "ranging": (30, 60),
    "trending_up": (30, 60),
    "trending_down": (30, 60),
    "volatile": (60, 120),
    "pause": (60, 120),
}
assert set(TIMING) == set(REGIMES)

BPS = 1e-4
# How far above an exchange minimum an order must be sized to survive the
# venue's own rounding. 20 % covers a step rounded down on a three-decimal
# market near $150 and leaves room for the lean.
ORDER_SIZE_MARGIN = 1.2


@dataclass(frozen=True)
class MarketSpec:
    """What the operator settles once per deployment; the fly never changes it."""

    connector_name: str
    trading_pair: str  # BASE-QUOTE, or ISSUER:TOKEN-QUOTE on HIP-3
    total_amount_quote: float
    picked_spread_bps: float  # the observed spread for this market
    # Blank means "derive from the connector name"; see venue.resolve.
    market_type: str = ""
    # 1 on spot, where there is nothing to lever.
    leverage: int = 1
    # 0 means "use the venue default"; pass the exchange's real figure when
    # known, since the take-profit floor is derived from it.
    maker_fee_bps: float = 0.0
    portfolio_allocation: float = 0.2
    target_base_pct: float = 0.4
    min_base_pct: float = 0.3
    max_base_pct: float = 0.5
    max_active_executors_by_level: int = 2
    global_stop_loss: float = 0.02
    leverage_cap: int = 5
    # Exchange minimum per order. pmm_mister sizes one cycle as
    # total_amount_quote × portfolio_allocation, split across both sides and
    # every level, so a small book needs a larger allocation to clear it.
    min_order_notional: float = 10.0

    def __post_init__(self):
        pair_names(self.trading_pair)  # refuses anything unparseable
        resolved = venue.resolve(self.connector_name, self.market_type)
        object.__setattr__(self, "market_type", resolved)
        if not self.maker_fee_bps:
            object.__setattr__(
                self,
                "maker_fee_bps",
                venue.default_maker_fee_bps(self.connector_name, resolved),
            )
        for name in ("total_amount_quote", "picked_spread_bps", "maker_fee_bps"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.is_spot:
            if self.leverage != 1:
                raise ValueError(
                    f"{self.connector_name} is a spot market: leverage must be 1, "
                    f"got {self.leverage}"
                )
        elif not 1 <= self.leverage <= self.leverage_cap:
            raise ValueError(f"leverage must be within 1..{self.leverage_cap}")
        if not 0 < self.min_base_pct < self.target_base_pct < self.max_base_pct < 1:
            raise ValueError("0 < min_base < target_base < max_base < 1 required")
        if not 0 < self.portfolio_allocation <= 1:
            raise ValueError("portfolio_allocation must be in (0, 1]")
        if self.min_order_notional < 0:
            raise ValueError("min_order_notional must be >= 0")

    @property
    def is_spot(self) -> bool:
        return self.market_type == venue.SPOT

    @property
    def min_spread_bps(self) -> float:
        """The closest to mid a quote may sit: one maker fee.

        A buy at −f and a sell at +f capture exactly 2f, which is the round
        trip — break-even. Anything tighter loses money on every completed
        pair whatever the fly decodes, so it is the one width that is not the
        strategy's to choose. It was a fixed 3 bp, which is both too wide for
        a HIP-3 market at 1.3 bp and too tight for a spot book at 7.5, and it
        was a parameter nobody could set correctly without already knowing the
        fee the spec now carries.
        """
        return self.maker_fee_bps

    @property
    def order_notional(self) -> float:
        """Quote size of one order at neutral posture: one cycle's allocation
        over two sides and two levels."""
        return self.total_amount_quote * self.portfolio_allocation / 4

    def check_order_size(self) -> None:
        """Refuse a size the exchange will reject once it has been rounded.

        An order sized to exactly the minimum does not survive the round trip
        through the exchange's own precision: the controller converts the quote
        size to a base amount, rounds it down to the market's step, and the
        resulting notional lands *under* the minimum. XYZ:ORCL-USD quotes to
        three decimals near 148, so $10.00 became $9.94 and Hyperliquid
        rejected every order with "lower than minimum notional size". The
        margin is what a rounded-down step can cost plus the widest lean.
        """
        floor = self.min_order_notional * ORDER_SIZE_MARGIN
        if self.order_notional < floor:
            needed = floor * 4 / self.total_amount_quote
            raise ValueError(
                f"{self.trading_pair}: an order would be {self.order_notional:.2f} quote, "
                f"under the {floor:.2f} needed to clear a "
                f"{self.min_order_notional:.0f} minimum after rounding; raise "
                f"portfolio_allocation to at least {min(1.0, needed):.2f} or "
                "total_amount_quote"
            )


# The two geometry rules, as plain functions of what they actually depend on:
# a market's fee and its observed spread. The scanner needs them before a spec
# exists — it is deciding whether a market is worth quoting at all — and two
# copies of a money rule is one too many.
def take_profit_floor_bps(maker_fee_bps: float) -> float:
    """Where a position must close to have been worth opening, in bp."""
    return max(4.0, 2.2 * 2 * maker_fee_bps)


# The outer level must stay outside the inner one. The playbook's S+1 assumed
# a market quoting several bp; on a tight book — XYZ:DRAM-USD quotes 0.35 —
# S+1 lands inside max(2, S/2) and the ladder inverts, so the "outer" level
# fills first and the inventory ladder means nothing.
LEVEL_STEP_BPS = 1.0


def base_levels_from_spread(picked_spread_bps: float) -> tuple[float, float]:
    """HIP-3 playbook: level 1 ``max(2, S/2)`` bp, level 2 ``S+1`` bp, with the
    second never inside the first."""
    first = max(2.0, picked_spread_bps / 2)
    return first, max(picked_spread_bps + 1, first + LEVEL_STEP_BPS)


def take_profit_floor(spec: MarketSpec) -> float:
    return round(take_profit_floor_bps(spec.maker_fee_bps) * BPS, 8)


def base_levels_bps(spec: MarketSpec) -> tuple[float, float]:
    return base_levels_from_spread(spec.picked_spread_bps)


def _fmt(values: list[float]) -> str:
    return ",".join(f"{v:.6f}".rstrip("0").rstrip(".") for v in values)


def build_config(spec: MarketSpec, posture: Posture) -> dict:
    if posture.regime not in TIMING:
        raise ValueError(f"Unknown regime {posture.regime!r}")
    spec.check_order_size()
    l1, l2 = base_levels_bps(spec)
    levels = [l1 * posture.spread_mult, l2 * posture.spread_mult]
    shift = max(-levels[0] / 2, min(levels[0] / 2, posture.shift_bps))
    buy = [max(spec.min_spread_bps, lvl - shift) for lvl in levels]
    sell = [max(spec.min_spread_bps, lvl + shift) for lvl in levels]
    take_profit = max(take_profit_floor(spec), min(buy[0], sell[0]) * BPS)
    refresh, cooldown = TIMING[posture.regime]
    config = {
        "controller_type": "generic",
        "controller_name": "pmm_mister",
        "connector_name": spec.connector_name,
        "trading_pair": spec.trading_pair,
        "total_amount_quote": spec.total_amount_quote,
        "portfolio_allocation": spec.portfolio_allocation,
        "leverage": spec.leverage,
        "target_base_pct": spec.target_base_pct,
        "min_base_pct": spec.min_base_pct,
        "max_base_pct": spec.max_base_pct,
        "buy_spreads": _fmt([b * BPS for b in buy]),
        "sell_spreads": _fmt([s * BPS for s in sell]),
        "buy_amounts_pct": "1,1",
        "sell_amounts_pct": "1,1",
        "executor_refresh_time": refresh,
        "buy_cooldown_time": cooldown,
        "sell_cooldown_time": cooldown,
        "take_profit": round(take_profit, 8),
        "take_profit_order_type": 3,
        "open_order_type": 3,
        "max_active_executors_by_level": spec.max_active_executors_by_level,
        "global_sl_enabled": True,
        "global_stop_loss": spec.global_stop_loss,
        "manual_kill_switch": posture.regime == "pause",
    }
    if not spec.is_spot:
        # Only a perpetual has one; pmm_mister skips the check on spot.
        config["position_mode"] = "ONEWAY"
    return config


def config_diff(old: dict | None, new: dict) -> dict:
    """Fields whose value changed; the whole config when there was none."""
    if old is None:
        return dict(new)
    return {k: v for k, v in new.items() if old.get(k) != v}
