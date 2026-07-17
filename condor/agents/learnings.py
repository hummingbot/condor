"""Agent learnings — one curated agent-level file (§7.1).

The RunStore replaces journals; learnings survive as explicit agent memory:
``agents/{slug}/learnings.md``, a flat timestamped bullet list appended via
an explicit tool call. The Phase-4 simplification drops the old category /
promotion / fuzzy-dedupe machinery — curation is the agent's job, and the
write API forces it (Hermes pattern, docs/insight-flow-simplification.md §7):
at the cap a plain append ERRORS instead of silently evicting the oldest
entry, and ``replaces=`` consolidates into an existing entry.
"""

from __future__ import annotations

import fcntl
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

MAX_LEARNINGS = 40

_HEADER = "# Learnings\n\n"


@contextmanager
def _file_lock(path: Path):
    """flock sidecar mutex: the engine process and MCP subprocess both
    append; unlocked read-modify-write loses whichever lands first."""
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def learnings_path(agent_dir: Path) -> Path:
    return Path(agent_dir) / "learnings.md"


def read_learnings(agent_dir: Path) -> str:
    """The bullet list (without the header); "" when none recorded."""
    path = learnings_path(agent_dir)
    if not path.exists():
        return ""
    lines = [l for l in path.read_text().splitlines() if l.startswith("- ")]
    return "\n".join(lines)


def append_learning(agent_dir: Path, text: str, replaces: str | None = None) -> None:
    """Append one timestamped learning, or consolidate into an existing one.

    ``replaces`` is a case-insensitive substring of exactly one existing
    entry; that entry is rewritten in place (fresh timestamp). A full list
    rejects plain appends with a loud error — consolidation is the way
    forward, never silent eviction.
    """
    text = " ".join(str(text).split())
    if not text:
        return
    path = learnings_path(agent_dir)
    with _file_lock(path):
        lines = []
        if path.exists():
            lines = [l for l in path.read_text().splitlines() if l.startswith("- ")]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        entry = f"- [{now}] {text}"
        if replaces:
            needle = " ".join(str(replaces).split()).lower()
            matches = [i for i, l in enumerate(lines) if needle in l.lower()]
            if not matches:
                raise ValueError(f"replaces={replaces!r} matches no learning")
            if len(matches) > 1:
                raise ValueError(
                    f"replaces={replaces!r} matches {len(matches)} learnings — "
                    "use a more distinctive substring"
                )
            lines[matches[0]] = entry
        else:
            if len(lines) >= MAX_LEARNINGS:
                raise ValueError(
                    f"learnings full ({MAX_LEARNINGS}) — consolidate instead: "
                    'pass replaces="<substring of an existing entry>" to merge '
                    "into it"
                )
            lines.append(entry)
        tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
        tmp.write_text(_HEADER + "\n".join(lines) + "\n")
        tmp.replace(path)
