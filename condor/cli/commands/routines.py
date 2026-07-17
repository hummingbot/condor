"""``condor routines`` — list routines, run one, inspect schedules.

The run/schedule mutations the dashboard exposes today, from the terminal.
Routine AUTHORING stays with the routine_builder agent — this only runs what
already exists.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

import typer

from condor.cli.output import (
    ExitCode,
    echo,
    emit,
    fail,
    json_option,
    render_kv,
    render_table,
)

LIST_COLUMNS = ["name", "category", "source", "continuous", "reports", "description"]
SCHEDULE_COLUMNS = [
    "id",
    "routine",
    "agent",
    "cron",
    "tz",
    "last_fired",
    "next_fire",
    "disabled",
]


def _list(as_json: bool) -> None:
    from condor.routine_store import get_routine_store

    routines = get_routine_store().list_routines()
    routines.sort(key=lambda r: r.get("last_modified") or 0, reverse=True)
    rows = [
        {
            "name": r.get("name", ""),
            "category": r.get("category", ""),
            "source": r.get("source", ""),
            "continuous": r.get("is_continuous", False),
            "reports": r.get("report_count"),
            "description": (r.get("description") or "")[:60],
        }
        for r in routines
    ]
    emit(
        {"routines": routines},
        render_table(rows, columns=LIST_COLUMNS, title="routines"),
        as_json,
    )


def _run(
    name: Optional[str], config_json: str, agent: str, timeout_s: int, as_json: bool
) -> None:
    from condor.routines_worker import run_routine_in_worker

    if not name:
        fail("usage: condor routines run <name>", ExitCode.CONFIG_ERROR)
    try:
        config = json.loads(config_json) if config_json else {}
    except json.JSONDecodeError as e:
        fail(f"--config is not valid JSON: {e}", ExitCode.CONFIG_ERROR)
    if not isinstance(config, dict):
        fail("--config must be a JSON object", ExitCode.CONFIG_ERROR)

    result = asyncio.run(
        run_routine_in_worker(
            name, config, agent_slug=agent, attribute=agent, timeout_s=timeout_s
        )
    )
    if isinstance(result, dict) and result.get("error"):
        fail(f"routine {name!r}: {result['error']}", ExitCode.ERROR)
    record = {
        "routine": name,
        **(result if isinstance(result, dict) else {"result": result}),
    }
    emit(record, render_kv(record, title=f"routine {name}"), as_json)


def _schedules(as_json: bool) -> None:
    from condor.agents.cron import next_fire
    from condor.agents.scheduler import RoutineScheduleStore

    now = datetime.now(timezone.utc)
    entries = RoutineScheduleStore().load()
    rows = []
    for schedule_id, entry in entries.items():
        try:
            upcoming = next_fire(entry["cron"], entry.get("tz", "UTC"), now)
            upcoming_s = upcoming.isoformat(timespec="minutes")
        except Exception as e:
            upcoming_s = f"error: {e}"
        rows.append(
            {
                "id": schedule_id,
                "routine": entry.get("routine", ""),
                "agent": entry.get("agent_slug", ""),
                "cron": entry.get("cron", ""),
                "tz": entry.get("tz", "UTC"),
                "last_fired": entry.get("last_fired", ""),
                "next_fire": upcoming_s,
                "disabled": entry.get("disabled", False),
            }
        )
    rows.sort(key=lambda r: r["next_fire"])
    emit(
        {"schedules": entries},
        render_table(rows, columns=SCHEDULE_COLUMNS, title="routine schedules"),
        as_json,
    )


def routines(
    action: Optional[str] = typer.Argument(
        None, help="run <name> | schedules; omit to list routines."
    ),
    name: Optional[str] = typer.Argument(None, help="Routine name (with `run`)."),
    config_json: str = typer.Option(
        "", "--config", help="JSON object of routine config (with `run`)."
    ),
    agent: str = typer.Option(
        "", "--agent", help="Run under an agent's attribution (agent-scoped routines)."
    ),
    timeout_s: int = typer.Option(120, "--timeout", help="Worker timeout in seconds."),
    as_json: bool = json_option(),
) -> None:
    """List routines (last-modified first), run one, or show schedules."""
    if action is None:
        _list(as_json)
    elif action == "run":
        _run(name, config_json, agent, timeout_s, as_json)
    elif action == "schedules":
        _schedules(as_json)
    else:
        fail(f"unknown action {action!r} (run | schedules)", ExitCode.CONFIG_ERROR)
