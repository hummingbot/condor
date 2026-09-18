"""The checkout has to work on macOS and Windows too (P5, P2).

Condor is developed on Linux, where the filesystem is case-sensitive and line
endings are LF. Both of the other supported hosts differ, and both differences
are silent: a case-collision makes a clean clone impossible on APFS or NTFS, and
an unmanaged line ending changes the bytes `content_digest` hashes.

These are cheap assertions against the tree itself, so a commit that breaks
either one fails here rather than on someone's laptop.
"""

from __future__ import annotations

import subprocess
from collections import defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def test_no_two_tracked_paths_differ_only_in_case():
    """macOS (APFS) and Windows (NTFS) are case-insensitive by default.

    Two paths differing only in case are one file there, so a clone silently
    loses one of them — and the fork layering, which resolves local-before-stock
    per item, then resolves unpredictably.
    """
    by_lower: dict[str, list[str]] = defaultdict(list)
    for path in _tracked_files():
        by_lower[path.lower()].append(path)

    collisions = {k: v for k, v in by_lower.items() if len(v) > 1}
    assert not collisions, f"paths differing only in case: {collisions}"


def test_agent_content_is_pinned_to_lf():
    """`content_digest` hashes these files, so their bytes must not vary by host."""
    attributes = (_REPO_ROOT / ".gitattributes").read_text()
    assert "* text=auto" in attributes
    for pattern in ("*.md text eol=lf", "*.sh text eol=lf"):
        assert pattern in attributes, f"{pattern} is not pinned"


def test_no_tracked_file_has_crlf_endings():
    """`* text=auto` normalizes on commit; this catches one that slipped in."""
    offenders = []
    for rel in _tracked_files():
        path = _REPO_ROOT / rel
        # Binary files have no line endings to normalize; .gitattributes marks
        # them, and this list mirrors it.
        if not path.is_file() or path.suffix in {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".ico",
            ".woff",
            ".woff2",
        }:
            continue
        try:
            if b"\r\n" in path.read_bytes():
                offenders.append(rel)
        except OSError:
            continue
    assert not offenders, f"CRLF in tracked files: {offenders}"
