"""Post-stop-loss token cooldown — a code-enforced re-entry blacklist.

The memecoin scalper's prompt says "blacklist any token that hit the hard stop",
but an LLM does not reliably hold that across ticks — in production it repeatedly
re-bought tokens it had *just* been stopped out of (falling-knife re-entries that
booked compounding losses). This enforces the rule in code: a token a stop_loss
closed for this agent is off-limits for a cooldown window, checked at
position_spot create (the only create path is the manage_executors tool), so the
model cannot re-enter it regardless of what it decides.

Derived from the executor store's own history — no writer, no extra state: a
recent CLOSED position_spot with close_type=stop_loss for the same base_token is
the blacklist signal.
"""

from __future__ import annotations

import os
import time
from typing import Optional

# Default cooldown after a hard stop before the same token may be re-entered.
# Long enough to skip the immediate falling-knife (re-entries were minutes apart),
# short enough that a genuinely re-pumping token isn't banned forever.
DEFAULT_COOLDOWN_HOURS = float(os.environ.get("CONDOR_STOP_COOLDOWN_HOURS", "4"))


def stopped_out_reason(
    agent_slug: str,
    base_token: str,
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
    store=None,
) -> Optional[str]:
    """Reason string if ``base_token`` was stop-loss-closed for ``agent_slug``
    within ``cooldown_hours`` (a new entry must be blocked); else ``None``.

    Uses opened_at as the timestamp — spot positions are short-lived (minutes),
    so open-time ≈ close-time. An unknown timestamp fails safe (treated recent)."""
    if not agent_slug or not base_token:
        return None
    cutoff = time.time() - cooldown_hours * 3600
    try:
        if store is None:
            from condor.executors.log import ExecutorLog

            store = ExecutorLog()
        records = store.load_by_slug(agent_slug)
    except Exception:
        return None
    for r in records:
        if r.type != "position_spot":
            continue
        if r.config.get("base_token") != base_token:
            continue
        if r.state.get("close_type") != "stop_loss":
            continue
        opened = r.state.get("opened_at")
        if opened is None or float(opened) >= cutoff:
            return (
                f"{base_token[:12]} was stopped out within {cooldown_hours:g}h — "
                "cooldown blacklist, no re-entry"
            )
    return None
