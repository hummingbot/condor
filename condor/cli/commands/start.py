"""``condor start`` — start a session or experiment from the terminal.

Parity with the ``run_agent`` MCP tool; completes the hbot-style start/stop
pair. One-shot verb (hbot rule 5): agents and scripts don't need the
intermediate steps.
"""

from typing import Optional

import typer

from condor.cli.commands._common import control
from condor.cli.output import emit, json_option, render_kv


def start(
    slug: str = typer.Argument(..., help="Agent slug (see `condor agents`)."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Run as an experiment (no real orders; user synonym: dry run).",
    ),
    max_ticks: Optional[int] = typer.Option(
        None, "--max-ticks", help="Bound the run to N ticks."
    ),
    context: str = typer.Option(
        "", "--context", help="Trading context injected into the run."
    ),
    as_json: bool = json_option(),
) -> None:
    """Start an agent session (--dry-run for an experiment)."""
    config: dict = {}
    if dry_run:
        config["dry_run"] = True
    if max_ticks is not None:
        config["max_ticks"] = max_ticks

    result = control(
        "agent.start",
        {"slug": slug, "config": config, "trading_context": context},
        timeout=120,
    )
    record = {"agent": slug, **(result or {})}
    emit(record, render_kv(record, title="start"), as_json)
