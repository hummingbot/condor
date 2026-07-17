"""``condor performance`` — executor performance aggregates.

The CLI consumer the executor performance aggregation was missing
(docs/cli-plan.md migration map). File-backed: reads the append-only
executor logs directly, so it works with the server down. Numbers in the
terminal; curves stay in the read-only dashboard.
"""

from typing import Optional

import typer

from condor.cli.output import ExitCode, emit, fail, json_option, render_table

GROUP_KEYS = ("agent", "run", "strategy", "venue", "type")
COLUMNS = [
    "key",
    "open",
    "closed",
    "failed",
    "realized_pnl",
    "costs",
    "open_notional",
    "win_rate",
]


def performance(
    slug: Optional[str] = typer.Argument(None, help="Filter to one agent slug."),
    group_by: str = typer.Option(
        "agent", "--group-by", help=f"One of: {', '.join(GROUP_KEYS)}."
    ),
    as_json: bool = json_option(),
) -> None:
    """Show realized PnL, costs, and open notional per group."""
    from condor.executors.log import ExecutorLog
    from condor.executors.performance import aggregate_performance

    if group_by not in GROUP_KEYS:
        fail(
            f"--group-by must be one of {', '.join(GROUP_KEYS)}", ExitCode.CONFIG_ERROR
        )
    groups = aggregate_performance(ExecutorLog(), group_by=group_by, agent_slug=slug)
    rows = [
        {
            "key": g.get("key", ""),
            "open": g.get("open_count"),
            "closed": g.get("closed_count"),
            "failed": g.get("failed_count"),
            "realized_pnl": g.get("realized_pnl_quote"),
            "costs": g.get("costs_quote"),
            "open_notional": g.get("open_notional_quote"),
            "win_rate": g.get("win_rate"),
        }
        for g in groups
    ]
    emit(
        {"group_by": group_by, "groups": groups},
        render_table(rows, columns=COLUMNS, title=f"performance by {group_by}"),
        as_json,
    )
