"""Rebalance-range math ported from hummingbot-api's lp_rebalancer controller.

Pure functions, no I/O: given a current price and a range policy, produce
position bounds, deposit amounts, and auto-close limit prices. Source:
bots/controllers/generic/lp_rebalancer/lp_rebalancer.py (_calculate_price_bounds,
_calculate_amounts, _validate_and_clamp_bounds, _determine_side_from_price,
_create_executor_config limit-price derivation).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional


class Side(str, Enum):
    BUY = "BUY"      # position below price: all quote
    SELL = "SELL"    # position above price: all base
    RANGE = "RANGE"  # centered on price: split


@dataclass
class RangePolicy:
    position_width_pct: Decimal            # total width of the range, in %
    position_offset_pct: Decimal = Decimal("0")  # >=0: gap from price; <0: overlap into range
    rebalance_threshold_pct: Decimal = Decimal("1")  # limit-price buffer beyond bounds, in %
    buy_price_min: Optional[Decimal] = None
    buy_price_max: Optional[Decimal] = None
    sell_price_min: Optional[Decimal] = None
    sell_price_max: Optional[Decimal] = None


@dataclass
class PlannedPosition:
    side: Side
    lower_price: Decimal
    upper_price: Decimal
    base_amount: Decimal
    quote_amount: Decimal
    lower_limit_price: Decimal
    upper_limit_price: Decimal


def calculate_price_bounds(
    side: Side, current_price: Decimal, policy: RangePolicy
) -> tuple[Decimal, Decimal]:
    width = policy.position_width_pct / Decimal("100")
    offset = policy.position_offset_pct / Decimal("100")

    if side == Side.RANGE:
        half_width = width / Decimal("2")
        return current_price * (Decimal("1") - half_width), current_price * (Decimal("1") + half_width)

    if side == Side.BUY:
        upper = min(current_price, policy.buy_price_max) if policy.buy_price_max else current_price
        upper = upper * (Decimal("1") - offset)
        return upper * (Decimal("1") - width), upper

    # SELL
    lower = max(current_price, policy.sell_price_min) if policy.sell_price_min else current_price
    lower = lower * (Decimal("1") + offset)
    return lower, lower * (Decimal("1") + width)


def calculate_amounts(
    side: Side,
    current_price: Decimal,
    total_amount_quote: Decimal,
    policy: RangePolicy,
) -> tuple[Decimal, Decimal]:
    """Returns (base_amount, quote_amount) for the deposit."""
    total = total_amount_quote
    offset = policy.position_offset_pct

    if side == Side.RANGE:
        quote_amt = total / Decimal("2")
        return quote_amt / current_price, quote_amt

    if offset >= 0:
        # Out-of-range: single-sided allocation
        if side == Side.BUY:
            return Decimal("0"), total
        return total / current_price, Decimal("0")

    # In-range (offset < 0): proportional split by where price sits in the range
    lower_price, upper_price = calculate_price_bounds(side, current_price, policy)
    price_range = upper_price - lower_price

    if price_range <= 0 or current_price <= lower_price:
        if side == Side.BUY:
            return Decimal("0"), total
        return total / current_price, Decimal("0")
    if current_price >= upper_price:
        if side == Side.SELL:
            return total / current_price, Decimal("0")
        return Decimal("0"), total

    price_ratio = (current_price - lower_price) / price_range
    quote_amt = total * price_ratio
    base_amt = (total * (Decimal("1") - price_ratio)) / current_price
    return base_amt, quote_amt


def validate_and_clamp_bounds(
    lower_price: Decimal,
    upper_price: Decimal,
    side: Side,
    current_price: Decimal,
    policy: RangePolicy,
) -> tuple[Optional[Decimal], Optional[Decimal], Optional[Side]]:
    """Clamp bounds to price limits; try the opposite side if both exceed.

    Returns (lower, upper, side) or (None, None, None) when no valid
    position is possible at the current price.
    """
    if side == Side.RANGE:
        return lower_price, upper_price, side

    min_limit, max_limit = _limits_for(side, policy)
    lower_exceeds = min_limit is not None and lower_price < min_limit
    upper_exceeds = max_limit is not None and upper_price > max_limit

    if not lower_exceeds and not upper_exceeds:
        return lower_price, upper_price, side

    if lower_exceeds and upper_exceeds:
        opposite = Side.SELL if side == Side.BUY else Side.BUY
        opp_lower, opp_upper = calculate_price_bounds(opposite, current_price, policy)
        opp_min, opp_max = _limits_for(opposite, policy)
        opp_lower_exceeds = opp_min is not None and opp_lower < opp_min
        opp_upper_exceeds = opp_max is not None and opp_upper > opp_max

        if not opp_lower_exceeds and not opp_upper_exceeds:
            return opp_lower, opp_upper, opposite
        if opp_lower_exceeds and not opp_upper_exceeds:
            return opp_min, opp_upper, opposite
        if not opp_lower_exceeds and opp_upper_exceeds:
            return opp_lower, opp_max, opposite
        return None, None, None

    if lower_exceeds:
        return min_limit, upper_price, side
    return lower_price, max_limit, side


def determine_side_from_price(current_price: Decimal, policy: RangePolicy) -> Side:
    buy_mid = None
    sell_mid = None
    if policy.buy_price_min and policy.buy_price_max:
        buy_mid = (policy.buy_price_min + policy.buy_price_max) / 2
    if policy.sell_price_min and policy.sell_price_max:
        sell_mid = (policy.sell_price_min + policy.sell_price_max) / 2

    if buy_mid and sell_mid:
        if current_price <= buy_mid:
            return Side.BUY
        if current_price >= sell_mid:
            return Side.SELL
        return Side.BUY if (current_price - buy_mid) < (sell_mid - current_price) else Side.SELL
    if buy_mid:
        return Side.BUY
    if sell_mid:
        return Side.SELL
    return Side.BUY


def plan_position(
    side: Side,
    current_price: Decimal,
    total_amount_quote: Decimal,
    policy: RangePolicy,
) -> Optional[PlannedPosition]:
    """Full planning pipeline: bounds -> clamp -> amounts -> limit prices.

    Returns None when no valid position is possible (both sides out of limits).
    """
    if current_price <= 0:
        raise ValueError(f"invalid current_price: {current_price}")

    lower, upper = calculate_price_bounds(side, current_price, policy)
    lower, upper, final_side = validate_and_clamp_bounds(lower, upper, side, current_price, policy)
    if lower is None or final_side is None or lower >= upper:
        return None

    base_amt, quote_amt = calculate_amounts(final_side, current_price, total_amount_quote, policy)

    threshold = policy.rebalance_threshold_pct / Decimal("100")
    return PlannedPosition(
        side=final_side,
        lower_price=lower,
        upper_price=upper,
        base_amount=base_amt,
        quote_amount=quote_amt,
        lower_limit_price=lower * (Decimal("1") - threshold),
        upper_limit_price=upper * (Decimal("1") + threshold),
    )


def _limits_for(side: Side, policy: RangePolicy) -> tuple[Optional[Decimal], Optional[Decimal]]:
    if side == Side.BUY:
        return policy.buy_price_min, policy.buy_price_max
    return policy.sell_price_min, policy.sell_price_max
