"""``condor runs`` — run history from the append-only streams.

File-backed: works with the server down (docs/cli-plan.md, principle 2). The
bare verb lists run metas by latest event (file mtime) — a live run being
ticked sorts above an older ended one. ``runs export <run_id>`` prints the
full markdown export (``render_run_markdown`` finally gets a CLI consumer).
"""

from datetime import datetime
from typing import Optional

import typer

from condor.cli.output import ExitCode, echo, emit, fail, json_option, render_table

COLUMNS = ["run", "agent", "seq", "kind", "status", "started", "ended", "events"]


def _fmt_ts(value) -> str:
    """Event timestamps are epoch floats (runstore emits time.time())."""
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(value)[:16]


def _export(run_id: Optional[str]) -> None:
    from condor.agents.exports import render_run_markdown
    from condor.agents.runstore import get_run_store, is_run_id

    if not run_id or not is_run_id(run_id):
        fail(
            "usage: condor runs export <run_id> (a 26-char ULID — see `condor runs`)",
            ExitCode.CONFIG_ERROR,
        )
    store = get_run_store()
    path = store.find_run_path(run_id)
    if path is None:
        fail(f"run '{run_id}' not found", ExitCode.NOT_FOUND)
    slug = store.slug_from_path(path)
    meta = store.run_meta(slug, run_id)
    events = store.read_events(slug, run_id)
    ticks = store.tick_files(slug, run_id)
    echo(render_run_markdown(meta, events, [text for _, text in ticks]))


def _list(slug: Optional[str], kind: Optional[str], limit: int, as_json: bool) -> None:
    from condor.agents.runstore import get_run_store

    store = get_run_store()
    slugs = [slug] if slug else store.agent_slugs_with_runs()
    if slug and slug not in store.agent_slugs_with_runs():
        fail(f"no runs for agent '{slug}' (see `condor agents`)", ExitCode.NOT_FOUND)

    # Latest event first == file mtime order, across all agents.
    entries = []
    for s in slugs:
        for path in store.all_run_paths(s):
            entries.append((path.stat().st_mtime, s, path.parent.name))
    entries.sort(reverse=True)

    metas = []
    for _, s, run_id in entries:
        meta = store.run_meta(s, run_id)
        if kind and meta.get("kind") != kind:
            continue
        metas.append(meta)
        if len(metas) >= limit:
            break

    rows = [
        {
            "run": m.get("run_id", ""),
            "agent": m.get("agent_slug", ""),
            "seq": m.get("display_seq"),
            "kind": m.get("kind", ""),
            "status": m.get("status", ""),
            "started": _fmt_ts(m.get("started_at")),
            "ended": _fmt_ts(m.get("ended_at")),
            "events": m.get("event_count"),
        }
        for m in metas
    ]
    emit({"runs": metas}, render_table(rows, columns=COLUMNS, title="runs"), as_json)


def runs(
    target: Optional[str] = typer.Argument(
        None, help="Agent slug to filter by, or `export` to print one run."
    ),
    run_id: Optional[str] = typer.Argument(None, help="Run ULID (with `export`)."),
    kind: Optional[str] = typer.Option(
        None,
        "--kind",
        help="Filter: session | scheduled.",
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="Rows to show."),
    as_json: bool = json_option(),
) -> None:
    """List runs (latest event first) or export one as markdown."""
    if target == "export":
        _export(run_id)
        return
    if run_id is not None:
        fail(
            "usage: condor runs [slug] | condor runs export <run_id>",
            ExitCode.CONFIG_ERROR,
        )
    _list(target, kind, limit, as_json)
