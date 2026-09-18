"""Two things the shared git machinery could not see (S1, S3).

Both fire once per checkout, so both components inherit them.
"""

from __future__ import annotations

import subprocess

import pytest

from condor.updates import components
from utils import updater


def _git(*args, cwd):
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": "/usr/bin:/bin",
        },
    )


@pytest.fixture
def repo(tmp_path):
    """A throwaway checkout with three commits."""
    d = tmp_path / "repo"
    d.mkdir()
    _git("init", "-q", "-b", "main", cwd=d)
    for i in range(3):
        (d / "f.txt").write_text(f"{i}\n")
        _git("add", "-A", cwd=d)
        _git("commit", "-qm", f"c{i}", cwd=d)
    return d


@pytest.mark.asyncio
async def test_an_attached_checkout_is_not_detached(repo):
    assert await updater.is_detached(str(repo)) is False


@pytest.mark.asyncio
async def test_a_detached_checkout_is_detected(repo):
    """``--abbrev-ref`` cannot answer this — it returns the string ``HEAD``."""
    _git("checkout", "-q", "--detach", "HEAD~2", cwd=repo)

    assert await updater.get_current_branch(str(repo)) == "HEAD"
    assert await updater.is_detached(str(repo)) is True


@pytest.mark.asyncio
async def test_a_detached_checkout_blocks_the_update(repo, monkeypatch):
    """And it blocks before anything reads a branch name or fetches."""
    _git("checkout", "-q", "--detach", "HEAD~2", cwd=repo)
    component = components.Component(
        key="condor", name="Condor", repo_dir=str(repo), service=None
    )

    async def _never(*a, **k):  # pragma: no cover - asserts it is not reached
        raise AssertionError("fetched despite a detached HEAD")

    monkeypatch.setattr(updater, "fetch", _never)

    blocks = await components.repo_blocks(component)
    assert [b.code for b in blocks] == ["detached-head"]
    assert blocks[0].resolutions == ["cancel"]
    # The message has to name the commit, or there is nothing to go back to.
    short = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert short in blocks[0].message


@pytest.mark.asyncio
async def test_a_stale_index_lock_gets_a_hint(repo):
    """The raw git error sends the reader hunting a process that is not there."""
    (repo / ".git" / "index.lock").touch()

    rc, out = await updater._run_git("add", "-A", repo_dir=str(repo))
    assert rc != 0
    assert "index.lock" in out
    assert "Stale git lock" in out
    assert str(repo) in out


@pytest.mark.asyncio
async def test_the_hint_is_not_appended_to_unrelated_failures(repo):
    rc, out = await updater._run_git("rev-parse", "nope-not-a-ref", repo_dir=str(repo))
    assert rc != 0
    assert "Stale git lock" not in out


@pytest.mark.asyncio
async def test_the_remote_default_branch_is_read_not_assumed(repo, tmp_path):
    """A clone knows its remote's default branch; a bare repo with no origin does not."""
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", str(repo), str(clone)], check=True, capture_output=True
    )
    assert await updater.remote_default_branch(str(clone)) == "main"
    # No origin at all -> "cannot tell", never a guess.
    assert await updater.remote_default_branch(str(repo)) is None
