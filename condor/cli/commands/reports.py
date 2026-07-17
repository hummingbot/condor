"""``condor reports`` — routine report access + scripting.

CLI = access + scripting; the read-only dashboard keeps the pretty rendering
(browsers render charts better than terminals). ``show`` prints the raw HTML
document to stdout; ``rm`` replaces the dashboard's report DELETE.
"""

import asyncio
from typing import Optional

import typer

from condor.cli.output import ExitCode, echo, emit, fail, json_option, render_table

COLUMNS = ["id", "title", "created_at", "source", "agent", "tags"]


def reports(
    action: Optional[str] = typer.Argument(
        None, help="show | rm; omit to list reports."
    ),
    report_id: Optional[str] = typer.Argument(None, help="Report id (with show/rm)."),
    limit: int = typer.Option(20, "--limit", "-n", help="Rows to list."),
    as_json: bool = json_option(),
) -> None:
    """List routine reports (newest first), print one, or delete one."""
    from condor.reports import (
        delete_report,
        get_report,
        get_report_raw_html,
        list_reports,
    )

    if action is None:
        entries, total = list_reports(limit=limit)
        rows = [
            {
                "id": e.get("id", ""),
                "title": e.get("title", ""),
                "created_at": (e.get("created_at") or "")[:16],
                "source": e.get("source_name") or e.get("source_type", ""),
                "agent": e.get("agent", ""),
                "tags": ", ".join(e.get("tags") or []),
            }
            for e in entries
        ]
        emit(
            {"reports": entries, "total": total},
            render_table(rows, columns=COLUMNS, title=f"reports ({total} total)"),
            as_json,
        )
        return

    if action not in ("show", "rm") or not report_id:
        fail("usage: condor reports [show <id> | rm <id>]", ExitCode.CONFIG_ERROR)

    if action == "show":
        found = get_report_raw_html(report_id)
        if found is None:
            fail(f"no report '{report_id}'", ExitCode.NOT_FOUND)
        raw_html, filename = found
        if as_json:
            emit({"id": report_id, "filename": filename, "html": raw_html}, "", True)
        else:
            echo(raw_html)
        return

    entry = get_report(report_id)
    if entry is None or not asyncio.run(delete_report(report_id)):
        fail(f"no report '{report_id}'", ExitCode.NOT_FOUND)
    echo(f"removed report {report_id} ({entry.get('title', '')})")
