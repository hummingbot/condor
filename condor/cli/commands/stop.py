"""``condor stop`` — harness-independent stop, straight over the control socket.

The escape hatch for a wedged chat client (docs/agent-history-comparison.md
§9.3): when the harness UI hangs, the engine keeps trading; this reaches it
from any terminal. Takes a run ULID, an agent slug (stops all its live runs),
or nothing (stops every live run).
"""

from typing import Optional

import typer

from condor.cli.commands._common import control
from condor.cli.output import echo, emit, json_option


def stop(
    target: Optional[str] = typer.Argument(
        None, help="Run ULID or agent slug; omit to stop ALL live runs."
    ),
    close: bool = typer.Option(
        False,
        "--close",
        help="Also close the runs' remaining owned inventory (default: detach).",
    ),
    as_json: bool = json_option(),
) -> None:
    """Stop live runs (works even when the chat harness is wedged)."""
    from condor.agents.runstore import is_run_id

    if target and is_run_id(target):
        targets = [(target, "")]
    else:
        live = control("agent.list")
        targets = [
            (i["agent_id"], i.get("agent_slug", ""))
            for i in live.get("agents", [])
            if not target or i.get("agent_slug") == target
        ]
        if not targets:
            scope = f" for {target!r}" if target else ""
            echo(f"no live runs{scope}")
            return

    results = []
    for run_id, slug in targets:
        result = control(
            "agent.verb",
            {"slug": slug, "verb": "stop", "agent_id": run_id, "close": close},
            timeout=120,
        )
        results.append({"run_id": run_id, **(result or {})})

    markdown = "\n".join(
        f"- {r['run_id']}: "
        + ", ".join(f"{k}={v}" for k, v in r.items() if k != "run_id")
        for r in results
    )
    emit({"stopped": results}, markdown, as_json)
