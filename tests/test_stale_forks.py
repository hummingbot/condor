"""A fork stops receiving upstream changes — and now says so (C11).

FEAT-115 solved the loud failure: a customized playbook no longer blocks an
update. It introduced the quiet one. ``fork_path`` stamps the copy with
``forked_from`` and nothing ever read the stamp back, so the update succeeded,
upstream's rewrite landed in the shipped tree, and the agent went on reading the
local copy — for ever, with no notice, on files that carry trading rules.

These tests pin the comparison that was missing. They do not pin a merge: local
still wins. What is asserted is that the operator is *told*.
"""

from __future__ import annotations

import pytest

from condor.layering import (
    FORKED_FROM_KEY,
    content_digest,
    fork_if_stock,
    stale_forks,
    write_preserving_stamp,
)
from condor.memory.paths import agent_home_layers, stock_agent_home

AGENT_MD = "---\nname: {name}\n---\n\n{body}\n"


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")
    return path


@pytest.fixture
def shipped():
    """One shipped playbook carrying a rule upstream will later tighten."""
    return _write(
        stock_agent_home("scout") / "AGENT.md",
        AGENT_MD.format(name="Scout", body="Never trade on Sundays."),
    )


def test_an_untouched_install_has_no_stale_forks(shipped):
    assert stale_forks("scout") == []


def test_a_fork_whose_stock_file_never_moved_is_not_stale(shipped):
    fork_if_stock("scout", "AGENT.md")
    assert stale_forks("scout") == []


def test_a_fork_is_reported_once_upstream_rewrites_the_stock_file(shipped):
    """The whole scenario, end to end: fork, then an update rewrites stock."""
    target = fork_if_stock("scout", "AGENT.md")
    write_preserving_stamp(
        target, AGENT_MD.format(name="Scout", body="MY EDIT: also never Mondays.")
    )
    assert stale_forks("scout") == [], "nothing has changed upstream yet"

    # What an update does to the shipped tree.
    shipped.write_text(
        AGENT_MD.format(
            name="Scout", body="UPSTREAM: never trade on Sundays OR holidays."
        ),
        "utf-8",
    )

    stale = stale_forks("scout")
    assert len(stale) == 1
    (fork,) = stale
    assert fork.rel == "AGENT.md"
    assert fork.retired is False
    assert fork.stock_digest == content_digest(shipped)
    assert fork.forked_from != fork.stock_digest

    # And the agent is still reading the local copy — the report is the fix,
    # not a silent takeover.
    assert "MY EDIT" in target.read_text("utf-8")


def test_a_retired_agent_is_reported_as_retired(shipped):
    """Upstream withdrawing a playbook does not propagate, so it must be said."""
    fork_if_stock("scout", "AGENT.md")
    shipped.unlink()

    stale = stale_forks("scout")
    assert len(stale) == 1
    assert stale[0].retired is True
    assert stale[0].stock_digest is None


def test_a_file_the_operator_authored_is_never_stale(shipped):
    """No stamp means it was never a fork, so there is nothing to diverge from."""
    local, _ = agent_home_layers("scout")
    _write(local / "MINE.md", AGENT_MD.format(name="mine", body="All my own work."))
    assert stale_forks("scout") == []


def test_a_legacy_crlf_stamp_is_not_reported_stale(shipped):
    """Stamps written before newline normalization must not all fire at once.

    A stamp taken from a CRLF working tree holds the raw-bytes hash. After
    normalization that compares as changed, which would report every such fork
    stale for a reason that has nothing to do with upstream.
    """
    import hashlib

    target = fork_if_stock("scout", "AGENT.md")
    shipped.write_bytes(shipped.read_bytes().replace(b"\n", b"\r\n"))
    legacy = f"sha256:{hashlib.sha256(shipped.read_bytes()).hexdigest()[:12]}"

    text = target.read_text("utf-8")
    target.write_text(
        text.replace(
            [ln for ln in text.splitlines() if ln.startswith(FORKED_FROM_KEY)][0],
            f"{FORKED_FROM_KEY}: {legacy}",
        ),
        "utf-8",
    )
    assert stale_forks("scout") == []
