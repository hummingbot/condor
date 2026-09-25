"""The shipped agent library names no creator.

``agents/`` is the stock layer every install reads. A ``created_by`` in a
tracked ``AGENT.md`` / ``strategy.md`` is some developer's Telegram id: it
points to nobody on other installs, yet the admin-principal rule and the
legacy-restart owner fallback still act on it, and it publishes a personal
id. Stock files carry ``created_by: 0`` (or no key). CORR-705.

Local-layer items (``.condor/agents/``) keep their real creator; this only
checks what git tracks.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from condor.frontmatter import parse_frontmatter

REPO = Path(__file__).resolve().parent.parent


def _tracked_library_files() -> list[Path]:
    try:
        out = subprocess.run(
            [
                "git",
                "ls-files",
                "agents/**/AGENT.md",
                "agents/**/strategy.md",
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [REPO / line for line in out.splitlines() if line.strip()]


def _files_with_a_creator(paths: list[Path]) -> list[str]:
    offenders = []
    for path in paths:
        if not path.exists():  # deleted in the working tree, not yet staged
            continue
        meta, _ = parse_frontmatter(path.read_text())
        if meta.get("created_by", 0) not in (0, None):
            offenders.append(f"{path.name} in {path.parent}: {meta['created_by']}")
    return offenders


def test_no_tracked_agent_or_strategy_names_a_creator():
    paths = _tracked_library_files()
    assert paths, "git ls-files found no shipped agents"
    assert _files_with_a_creator(paths) == []


def test_the_scan_flags_a_non_zero_creator(tmp_path):
    stock = tmp_path / "strategy.md"
    stock.write_text("---\nname: s\ncreated_by: 0\n---\n\nbody\n")
    missing = tmp_path / "AGENT.md"
    missing.write_text("---\nname: a\n---\n\nbody\n")
    dev = tmp_path / "dev.md"
    dev.write_text("---\nname: d\ncreated_by: 481175164\n---\n\nbody\n")

    assert _files_with_a_creator([stock, missing]) == []
    assert len(_files_with_a_creator([stock, missing, dev])) == 1
