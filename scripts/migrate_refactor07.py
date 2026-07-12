"""One-shot migration for refactor-07: session as the stateful primitive.

- Backs up ``agents/`` to ``agents.pre-07.bak/`` (refuses to run if present).
- ``kind: delegation`` sessions → flat ``delegations/{date}-dK.md`` transcripts
  (K renumbered per agent in start order — delegations get their own namespace;
  the legacy session id is recorded in the file).
- ``kind: consult`` and ``kind: curation`` sessions → dropped (consults were
  never meant to persist; curation is removed — its audit trail is git).
- Surviving (tick) sessions: NEVER renumbered (``controller_id`` identity);
  the ``kind:`` field is dropped from their meta.yml.
- ``experiments/experiment_N.md`` → ``experiments/{date}-eN.md`` (numbers
  preserved — they are the ``{slug}_eN`` ids; date from the snapshot header).

Run once from the repo root: ``uv run python scripts/migrate_refactor07.py``
"""

from __future__ import annotations

import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
AGENTS = ROOT / "agents"
BACKUP = ROOT / "agents.pre-07.bak"


def _read_meta(session_dir: Path) -> dict:
    p = session_dir / "meta.yml"
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}


def _date_of(meta: dict, session_dir: Path) -> str:
    for key in ("started_at", "ended_at"):
        v = str(meta.get(key, ""))
        if re.match(r"^\d{4}-\d{2}-\d{2}", v):
            return v[:10]
    return datetime.fromtimestamp(
        session_dir.stat().st_mtime, tz=timezone.utc
    ).strftime("%Y-%m-%d")


def _migrate_delegation(agent_dir: Path, session_dir: Path, meta: dict, num: int) -> None:
    """Rewrite a delegation session as a flat transcript file."""
    slug = agent_dir.name
    date = _date_of(meta, session_dir)
    dele_dir = agent_dir / "delegations"
    dele_dir.mkdir(exist_ok=True)

    old_transcript = session_dir / "transcript.md"
    body = old_transcript.read_text() if old_transcript.exists() else ""
    # Strip the old "# Delegation {slug}_{N}" title + header list; we write a
    # fresh header in the new format and keep everything from "## Task" on.
    m = re.search(r"^## Task", body, re.MULTILINE)
    tail = body[m.start():] if m else (body or "## Task\n\n(transcript missing)\n")

    task_id = f"{slug}-d{num}"
    risk = meta.get("risk_limits")
    import json

    content = (
        f"# Delegation {task_id}\n\n"
        f"- **Status:** {meta.get('status', 'done')}\n"
        f"- **Agent:** {slug}\n"
        f"- **Server:** -\n"
        f"- **Risk limits:** {json.dumps(risk) if risk else '-'}\n"
        f"- **Started:** {meta.get('started_at', '-') or '-'}\n"
        f"- **Ended:** {meta.get('ended_at', '-') or '-'}\n"
        f"- **Legacy session id:** {slug}_{session_dir.name.split('_')[1]}"
        + (f" (task id {meta['legacy_task_id']})" if meta.get("legacy_task_id") else "")
        + "\n\n"
        + tail
    )
    path = dele_dir / f"{date}-d{num}.md"
    path.write_text(content)
    shutil.rmtree(session_dir)
    print(f"  delegation session_{session_dir.name.split('_')[1]} -> {path.relative_to(agent_dir)}")


def migrate_agent(agent_dir: Path) -> None:
    print(f"{agent_dir.name}:")
    sessions_dir = agent_dir / "sessions"
    delegations: list[tuple[str, int, Path, dict]] = []
    if sessions_dir.exists():
        for d in sorted(
            (d for d in sessions_dir.iterdir() if d.is_dir() and d.name.startswith("session_")),
            key=lambda d: int(d.name.split("_", 1)[1]),
        ):
            meta = _read_meta(d)
            kind = meta.get("kind", "tick_loop")
            if kind == "delegation":
                delegations.append((meta.get("started_at", ""), int(d.name.split("_", 1)[1]), d, meta))
            elif kind in ("consult", "curation"):
                shutil.rmtree(d)
                print(f"  dropped {kind} {d.name}")
            else:
                # Surviving session — drop the kind field, never renumber.
                if "kind" in meta:
                    del meta["kind"]
                    (d / "meta.yml").write_text(yaml.safe_dump(meta, sort_keys=False))
                    print(f"  kept session {d.name} (kind field dropped)")

    # Delegations renumber into their own namespace, in start order.
    delegations.sort(key=lambda t: (t[0], t[1]))
    for new_num, (_, _old, d, meta) in enumerate(delegations, start=1):
        _migrate_delegation(agent_dir, d, meta, new_num)

    # Experiments: experiment_N.md -> {date}-eN.md (numbers preserved).
    exp_dir = agent_dir / "experiments"
    if exp_dir.exists():
        for f in sorted(exp_dir.glob("experiment_*.md")):
            m = re.match(r"experiment_(\d+)\.md", f.name)
            if not m:
                continue
            content = f.read_text(errors="replace")
            ts = re.search(r"^# Experiment #\d+ — (\d{4}-\d{2}-\d{2})", content, re.MULTILINE)
            date = ts.group(1) if ts else datetime.fromtimestamp(
                f.stat().st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%d")
            new = exp_dir / f"{date}-e{m.group(1)}.md"
            f.rename(new)
            print(f"  experiment {f.name} -> {new.name}")


def main() -> None:
    if not AGENTS.exists():
        sys.exit("no agents/ directory found — run from the repo root")
    if BACKUP.exists():
        sys.exit(f"{BACKUP} already exists — remove it first (refusing to overwrite a backup)")
    shutil.copytree(AGENTS, BACKUP)
    print(f"backup: {BACKUP}\n")
    for agent_dir in sorted(AGENTS.iterdir()):
        if agent_dir.is_dir() and not agent_dir.name.startswith("_"):
            migrate_agent(agent_dir)
    print("\ndone.")


if __name__ == "__main__":
    main()
