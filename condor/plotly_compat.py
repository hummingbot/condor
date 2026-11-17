"""Plotly template compatibility, applied where figures are built.

This used to run from ``condor/__init__.py``, i.e. in every process that
imported anything under ``condor`` — including both MCP servers of every agent
session, ``condor.doctor`` and test collection — at about 130 ms of CPU each,
to fix charts none of them draw. It is now called by the processes and modules
that actually build figures: ``main()`` (handlers and routines draw with plotly
directly, in the bot process) and :mod:`condor.reports`.
"""

from __future__ import annotations

__all__ = ["ensure_plotly_templates_patched"]

_patched = False


def ensure_plotly_templates_patched() -> None:
    """Fix Plotly 6+ incompatibility with built-in templates. Idempotent.

    Plotly 6 removed the 'ticks' property from colorbar objects,
    but built-in templates like 'plotly_dark' still reference it,
    causing ValueError on any figure that uses them.
    """
    global _patched
    if _patched:
        return
    try:
        import plotly.io as pio
    except ImportError:
        return
    _patched = True

    def _strip_ticks(obj: object) -> None:
        if isinstance(obj, dict):
            obj.pop("ticks", None)
            for v in obj.values():
                _strip_ticks(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                _strip_ticks(v)

    for name in ("plotly_dark", "plotly_white", "plotly"):
        try:
            t = pio.templates[name]
            raw = t.to_plotly_json()
            _strip_ticks(raw)
            pio.templates[name] = raw
        except Exception:
            pass
