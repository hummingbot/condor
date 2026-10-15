"""The shipped agent library names no creator.

``agents/`` is the stock layer every install reads. A ``created_by`` in a
tracked ``AGENT.md`` / ``loop.md`` is some developer's Telegram id: it
points to nobody on other installs, yet the admin-principal rule and the
legacy-restart owner fallback still act on it, and it publishes a personal
id. Stock files carry ``created_by: 0`` (or no key). CORR-705.

Local-layer items (``.condor/agents/``) keep their real creator; this only
checks what git tracks. ``publish_to_stock`` is the one path that writes here,
and it zeroes the field on the way in — see ``scrub_for_stock`` (CORR-721).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from condor.agents.agent import AGENT_MD
from condor.agents.strategy import LOOP_MD
from condor.frontmatter import parse_frontmatter

REPO = Path(__file__).resolve().parent.parent

# Taken from the modules that own the filenames, not restated, so a rename
# cannot drift the guard away from the files it is meant to scan: FEAT-128
# renamed ``strategy.md`` to ``loop.md`` and this pattern matched nothing for
# six tracked playbooks while the test went on passing (CORR-721).
LIBRARY_PATTERNS = (f"agents/**/{AGENT_MD}", f"agents/**/{LOOP_MD}")


def _tracked_library_files(patterns: tuple[str, ...] = LIBRARY_PATTERNS) -> list[Path]:
    """Every tracked file the patterns name — and no pattern may be vacuous."""
    found: list[Path] = []
    for pattern in patterns:
        try:
            out = subprocess.run(
                ["git", "ls-files", pattern],
                cwd=REPO,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError):
            pytest.skip("not a git checkout")
        matched = [REPO / line for line in out.splitlines() if line.strip()]
        assert matched, (
            f"'{pattern}' matched no tracked file — the guard would scan "
            "nothing for it. A rename emptied the pattern; point it at the "
            "new name."
        )
        found.extend(matched)
    return found


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


def test_the_scan_covers_both_the_agent_definition_and_the_loop_playbook():
    names = {path.name for path in _tracked_library_files()}
    assert names == {AGENT_MD, LOOP_MD}


def test_an_emptied_pattern_fails_instead_of_silently_scanning_nothing():
    """The exact shape of the CORR-721 regression: a stale pattern.

    ``agents/**/strategy.md`` is what FEAT-128's rename left behind.
    """
    with pytest.raises(AssertionError, match="matched no tracked file"):
        _tracked_library_files((f"agents/**/{AGENT_MD}", "agents/**/strategy.md"))


def test_the_scan_flags_a_non_zero_creator(tmp_path):
    stock = tmp_path / LOOP_MD
    stock.write_text("---\nname: s\ncreated_by: 0\n---\n\nbody\n")
    missing = tmp_path / AGENT_MD
    missing.write_text("---\nname: a\n---\n\nbody\n")
    dev = tmp_path / "dev.md"
    dev.write_text("---\nname: d\ncreated_by: 481175164\n---\n\nbody\n")

    assert _files_with_a_creator([stock, missing]) == []
    assert len(_files_with_a_creator([stock, missing, dev])) == 1
