"""``import condor`` no longer drags Plotly into every process (OP-11)."""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _run(code: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_importing_condor_paths_does_not_import_plotly():
    """What each MCP server and the doctor do on the way up."""
    out = _run(
        "import sys, condor.paths, condor.fsutil; "
        "print(any(m == 'plotly' or m.startswith('plotly.') for m in sys.modules))"
    )
    assert out == "False"


def test_the_patch_is_applied_where_a_report_takes_a_figure():
    out = _run(
        "import plotly.graph_objects as go, plotly.io as pio\n"
        "from condor import plotly_compat\n"
        "from condor.reports import ReportBuilder\n"
        "assert plotly_compat._patched is False\n"
        "ReportBuilder('t').plotly(go.Figure(go.Scatter(x=[1, 2], y=[1, 2])))\n"
        "def has_ticks(o):\n"
        "    if isinstance(o, dict):\n"
        "        return 'ticks' in o or any(has_ticks(v) for v in o.values())\n"
        "    if isinstance(o, (list, tuple)):\n"
        "        return any(has_ticks(v) for v in o)\n"
        "    return False\n"
        "print(plotly_compat._patched, "
        "has_ticks(pio.templates['plotly_dark'].to_plotly_json()))\n"
    )
    assert out == "True False"


def test_the_patch_is_idempotent_and_dark_figures_still_build():
    import plotly.graph_objects as go

    from condor.plotly_compat import ensure_plotly_templates_patched

    ensure_plotly_templates_patched()
    ensure_plotly_templates_patched()
    fig = go.Figure(go.Heatmap(z=[[1, 2], [3, 4]]), layout={"template": "plotly_dark"})
    assert fig.to_json()
