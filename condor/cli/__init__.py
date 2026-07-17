"""Condor command-line interface (``condor`` on PATH, or ``python -m condor.cli``).

One module per command under ``commands/``; the output contract (Markdown +
``--json`` + stable exit codes) lives in ``output.py``. See docs/cli-plan.md.
"""

from condor.cli.main import app, main

__all__ = ["app", "main"]
