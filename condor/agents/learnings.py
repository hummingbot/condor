"""Agent learnings — one curated agent-level file (§7.1).

The RunStore replaces journals; learnings survive as explicit agent memory:
``agents/{slug}/learnings.md``, a flat timestamped bullet list appended via
an explicit tool call. The Phase-4 simplification drops the old category /
promotion / fuzzy-dedupe machinery — curation is the agent's job now.
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


def append_learning(agent_dir: Path, text: str) -> None:
    """Append one timestamped learning, keeping the newest MAX_LEARNINGS."""
    text = " ".join(str(text).split())
    if not text:
        return
    path = learnings_path(agent_dir)
    with _file_lock(path):
        lines = []
        if path.exists():
            lines = [l for l in path.read_text().splitlines() if l.startswith("- ")]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        lines.append(f"- [{now}] {text}")
        if len(lines) > MAX_LEARNINGS:
            lines = lines[-MAX_LEARNINGS:]
        tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
        tmp.write_text(_HEADER + "\n".join(lines) + "\n")
        tmp.replace(path)
