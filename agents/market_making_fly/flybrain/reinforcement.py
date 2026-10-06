"""Turn a change in the fly's P&L into a dopamine pulse kind.

Stonkfly's rule, with the equity source changed: ``equity`` is the combined net
P&L of the ``pmm_mister`` controllers the fly is quoting for — realized plus
unrealized minus fees — so nothing else on the account can reward or punish it.

The pulse is binary above a deadband, not proportional, and tiny changes are
not accumulated. It is feedback about value between two observations; it is
not evidence that the last posture caused that change.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Literal

Kind = Literal["reward", "aversive", "none"]


def D(value) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("Boolean is not money")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("Nonfinite quantity")
    return number


def reinforcement(equity, anchor, deadband) -> tuple[Kind, Decimal]:
    """``(kind, delta)`` with ``delta = equity - anchor``."""
    delta = D(equity) - D(anchor)
    threshold = D(deadband)
    if threshold <= 0:
        raise ValueError("Positive reinforcement deadband required")
    if delta >= threshold:
        return "reward", delta
    if delta <= -threshold:
        return "aversive", delta
    return "none", delta


def controller_net(performance: dict) -> float:
    """``realized + unrealized`` in quote for one controller's performance
    block (the ``performance`` dict inside ``get_active_bots_status``). Fees
    are already netted into Hummingbot's realized figure."""
    realized = performance.get("realized_pnl_quote")
    unrealized = performance.get("unrealized_pnl_quote")
    if realized is None or unrealized is None:
        raise ValueError(
            "Controller performance lacks realized_pnl_quote/unrealized_pnl_quote"
        )
    total = float(realized) + float(unrealized)
    if not math.isfinite(total):
        raise ValueError("Nonfinite controller P&L")
    return total
