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
    """`* text=auto` normalizes on commit; this catches one that slipped in.

    Asked of the *index*, not the working tree. `text=auto` guarantees LF in
    what git stores, and says nothing about what it materializes: a checkout
    with `core.autocrlf=true` — the Windows default, and inherited by a WSL2
    clone under /mnt/c — writes CRLF into the working tree for exactly the
    files this is meant to pass. Reading bytes off disk there fails every
    ordinary `.py` and `.tsx` and reports it as a portability defect, which is
    the opposite of what this test is for.

    `git ls-files --eol` reports both, as `i/<eol> w/<eol>`. `i/crlf` is a
    file committed with CRLF, which is the thing worth catching.
    """
    proc = subprocess.run(
        ["git", "ls-files", "--eol", "-z"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    offenders = []
    for entry in proc.stdout.split("\0"):
        if not entry.strip():
            continue
        # "i/crlf  w/crlf  attr/            \tpath/to/file"
        fields, _, rel = entry.partition("\t")
        index_eol = fields.split()[0] if fields.split() else ""
        if index_eol == "i/crlf":
            offenders.append(rel)
    assert not offenders, f"CRLF committed in: {offenders}"
