"""Rendering guard for a figure that cannot legitimately be negative.

Traded volume is a sum of fills, so a negative one is never a measurement. It
is a signed quantity that leaked into a volume field, or a cumulative counter
differenced across a restart — the two defects [[CORR-731]] fixed at their
sources. What this module exists for is the next one.

Clamping at the point of display would keep every surface plausible and destroy
the only evidence that anything upstream is wrong, which is how a negative
volume survived long enough to be read off an agent's journal during a scored
race. So the renderers flag instead of clamp: the figure reads as a data error,
carries the offending value, and is logged.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

#: What an unusable volume renders as, in place of a currency figure.
VOLUME_DATA_ERROR = "data error"


def format_volume_usd(value: Any, *, source: str, decimals: int = 0) -> str:
    """``$1,234`` for a usable volume, a flagged data error for anything else.

    ``source`` names the surface for the log line; it is not rendered.
    """
    try:
        v = float(value or 0.0)
    except (TypeError, ValueError):
        log.warning("%s: volume is not a number: %r", source, value)
        return f"{VOLUME_DATA_ERROR} ({value!r})"
    if v < 0:
        log.warning(
            "%s: negative volume %r — rendering as a data error, not as a figure",
            source,
            v,
        )
        return f"{VOLUME_DATA_ERROR} ({v:,.{decimals}f})"
    return f"${v:,.{decimals}f}"
