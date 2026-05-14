"""Condor - Telegram bot for Hummingbot trading."""


def _patch_plotly_templates() -> None:
    """Fix Plotly 6+ incompatibility with built-in templates.

    Plotly 6 removed the 'ticks' property from colorbar objects,
    but built-in templates like 'plotly_dark' still reference it,
    causing ValueError on any figure that uses them.
    """
    try:
        import plotly.io as pio
    except ImportError:
        return

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


_patch_plotly_templates()
