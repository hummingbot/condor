"""``condor agents`` — the agent roster, from the specs on disk.

File-backed (works with the server down); live-run counts are overlaid when
the socket answers. Bare verb lists agents last-modified first; with a slug,
shows one spec summary + hashes.
"""

from collections import Counter
from datetime import datetime
from typing import Optional

import typer

from condor.cli.commands._common import try_control
from condor.cli.output import ExitCode, emit, fail, json_option, render_kv, render_table

COLUMNS = ["slug", "model", "denomination", "schedule", "live", "modified"]


def _live_counts() -> Optional[Counter]:
    live = try_control("agent.list")
    if live is None:
        return None
    return Counter(i.get("agent_slug", "") for i in live.get("agents", []))


def agents(
    slug: Optional[str] = typer.Argument(None, help="Agent slug for a spec summary."),
    as_json: bool = json_option(),
) -> None:
    """List agents (last-modified first) or show one agent's spec summary."""
    from condor.agents.agent import AgentStore, agents_data_root
    from condor.agents.learnings import read_learnings
    from condor.agents.runstore import get_run_store
    from condor.agents.spec import source_spec_hash

    store = AgentStore()
    root = agents_data_root()

    if slug:
        agent = store.get(slug)
        if agent is None:
            fail(f"no agent '{slug}'", ExitCode.NOT_FOUND)
        live = _live_counts()
        learnings = read_learnings(root / slug)
        n_learnings = sum(1 for line in learnings.splitlines() if line.startswith("- "))
        n_runs = len(list(get_run_store().runs_dir(slug).glob("*.jsonl")))
        record = {
            "slug": agent.slug,
            "model": agent.agent_key,
            "denomination": agent.denomination or "",
            "schedule": (agent.schedule or {}).get("cron", ""),
            "risk_limits": agent.risk_limits or {},
            "spec_hash": source_spec_hash(store.source_text(slug)),
            "learnings": n_learnings,
            "runs": n_runs,
            "live_runs": live.get(slug, 0) if live is not None else "server down",
            "tombstoned": (root / slug / "TOMBSTONE.yml").exists(),
        }
        emit(record, render_kv(record, title=f"agent {slug}"), as_json)
        return

    live = _live_counts()
    rows = []
    for agent in store.list_all():
        spec_path = root / agent.slug / "AGENT.md"
        mtime = spec_path.stat().st_mtime if spec_path.exists() else 0
        rows.append(
            {
                "_mtime": mtime,
                "slug": agent.slug
                + (
                    " (tombstoned)"
                    if (root / agent.slug / "TOMBSTONE.yml").exists()
                    else ""
                ),
                "model": agent.agent_key,
                "denomination": agent.denomination or "",
                "schedule": (agent.schedule or {}).get("cron", ""),
                "live": live.get(agent.slug, 0) if live is not None else "-",
                "modified": (
                    datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                    if mtime
                    else ""
                ),
            }
        )
    rows.sort(key=lambda r: r["_mtime"], reverse=True)
    for r in rows:
        r.pop("_mtime")
    emit({"agents": rows}, render_table(rows, columns=COLUMNS, title="agents"), as_json)
