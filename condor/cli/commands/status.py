"""``condor status`` — one glance at the whole system.

Server up (socket ping timed), live runs, scheduler next-fires, open
executors/exposure per agent, pending approvals. Pure local compute + socket
round-trips — never a model call (the Hermes bar). Like ``hbot status``, this
is a poll: "is anything running?" is a valid question with a valid answer, so
a down server is SUCCESS (exit 0, server=down), not an error.
"""

import time
from datetime import datetime, timezone

from condor.cli.commands._common import try_control
from condor.cli.output import echo, emit, json_option, render_kv, render_table

PING_TIMEOUT = 5.0


def _live_run_rows(agents: list[dict]) -> list[dict]:
    from condor.agents.runstore import ulid_datetime

    rows = []
    for i in agents:
        run_id = i.get("agent_id", "")
        try:
            started = ulid_datetime(run_id).strftime("%Y-%m-%d %H:%M")
        except Exception:
            started = ""
        rows.append(
            {
                "agent": i.get("agent_slug", ""),
                "run": run_id,
                "seq": i.get("session_num"),
                "status": i.get("status", ""),
                "ticks": i.get("tick_count"),
                "pnl": i.get("daily_pnl"),
                "exposure": i.get("total_exposure"),
                "open_execs": i.get("open_executors"),
                "started": started,
            }
        )
    return rows


def _next_fires(limit: int = 10) -> list[dict]:
    """Scheduler next-fires, computed the same way the scheduler will:
    agent-spec schedules + durable routine schedules → cron.next_fire."""
    from condor.agents.agent import AgentStore
    from condor.agents.cron import next_fire
    from condor.agents.scheduler import RoutineScheduleStore

    now = datetime.now(timezone.utc)
    rows = []
    for agent in AgentStore().list_all():
        sched = agent.schedule or {}
        if not sched.get("cron"):
            continue
        try:
            fire = next_fire(sched["cron"], sched.get("tz", "UTC"), now)
            rows.append(
                {
                    "source": f"agent {agent.slug}",
                    "cron": sched["cron"],
                    "next_fire": fire.isoformat(timespec="minutes"),
                }
            )
        except Exception as e:
            rows.append(
                {
                    "source": f"agent {agent.slug}",
                    "cron": sched.get("cron", ""),
                    "next_fire": f"error: {e}",
                }
            )
    for schedule_id, entry in RoutineScheduleStore().load().items():
        if entry.get("disabled"):
            continue
        try:
            fire = next_fire(entry["cron"], entry.get("tz", "UTC"), now)
            rows.append(
                {
                    "source": f"routine {entry.get('routine', schedule_id)}",
                    "cron": entry["cron"],
                    "next_fire": fire.isoformat(timespec="minutes"),
                }
            )
        except Exception as e:
            rows.append(
                {
                    "source": f"routine {entry.get('routine', schedule_id)}",
                    "cron": entry.get("cron", ""),
                    "next_fire": f"error: {e}",
                }
            )
    rows.sort(key=lambda r: r["next_fire"])
    return rows[:limit]


def status(as_json: bool = json_option()) -> None:
    """Show server, live runs, next scheduled fires, and pending approvals."""
    from condor.cli.commands._common import probe_control

    t0 = time.monotonic()
    state = probe_control(timeout=PING_TIMEOUT)
    ping_ms = round((time.monotonic() - t0) * 1000)
    up = state == "up"

    agents = (try_control("agent.list") or {}).get("agents", []) if up else []
    approvals = (try_control("approval.list") or {}).get("approvals", []) if up else []
    fires = _next_fires()

    server_lines = {
        "up": f"up ({ping_ms}ms socket round-trip)",
        "down": "down — start with `condor serve`",
        "blocked": "unknown — this shell is SANDBOXED (unix-socket connect "
        "denied, e.g. Codex): the server may well be up. Check via the "
        "Condor MCP tools (they run unsandboxed) or re-run this command "
        "with sandbox escalation/approval.",
    }
    summary = {
        "server": server_lines[state],
        "live_runs": len(agents),
        "pending_approvals": len(approvals),
    }
    if state == "down":
        summary["note"] = "scheduler and executors run inside `condor serve`"

    run_rows = _live_run_rows(agents)
    payload = {
        "server_up": up,
        "server_state": state,  # "up" | "down" | "blocked" (sandboxed shell)
        "socket_ms": ping_ms if up else None,
        "live_runs": agents,
        "pending_approvals": approvals,
        "next_fires": fires,
    }
    if as_json:
        emit(payload, "", True)
        return

    echo(render_kv(summary, title="status"))
    if run_rows:
        echo(
            "\n"
            + render_table(
                run_rows,
                columns=[
                    "agent",
                    "run",
                    "seq",
                    "status",
                    "ticks",
                    "pnl",
                    "exposure",
                    "open_execs",
                    "started",
                ],
                title="live runs",
            )
        )
    if approvals:
        rows = [
            {
                "id": a.get("approval_id", ""),
                "agent": a.get("agent_slug", ""),
                "summary": a.get("summary", ""),
            }
            for a in approvals
        ]
        echo(
            "\n"
            + render_table(
                rows,
                columns=["id", "agent", "summary"],
                title="pending approvals (resolve via chat/dashboard)",
            )
        )
    if fires:
        echo(
            "\n"
            + render_table(
                fires,
                columns=["source", "cron", "next_fire"],
                title="next scheduled fires",
            )
        )
