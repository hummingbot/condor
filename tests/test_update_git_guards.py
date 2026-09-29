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


# ── A branch that was never pushed is not an unreachable remote ──


@pytest.fixture
def cloned(tmp_path):
    """A checkout with a real ``origin`` that answers — a local bare repo."""
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "-q", "-b", "main", cwd=seed)
    (seed / "f.txt").write_text("1\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-qm", "c0", cwd=seed)
    _git("clone", "-q", "--bare", str(seed), str(origin), cwd=tmp_path)

    work = tmp_path / "work"
    _git("clone", "-q", str(origin), str(work), cwd=tmp_path)
    return work


@pytest.mark.asyncio
async def test_a_local_only_branch_is_named_as_such_not_blamed_on_the_network(cloned):
    """The ordinary state of anyone on a feature branch they have not pushed.

    git exits 128 both for "no such ref upstream" and for "cannot reach the
    remote", so the guard reported an unpushed branch as an unreachable
    remote — and sent the reader to debug a network that was fine.
    """
    _git("checkout", "-q", "-b", "feat/mine", cwd=cloned)

    component = components.Component("condor", "Condor", str(cloned))
    blocks = await components.repo_blocks(component)

    assert [b.code for b in blocks] == ["no-upstream-branch"]
    message = blocks[0].message
    assert "feat/mine" in message
    assert "unreachable" not in message.lower()
    # And it says what to do about it, naming the branch that does track.
    assert "main" in message


@pytest.mark.asyncio
async def test_a_tracked_branch_still_passes_the_guard(cloned):
    """The fix must not turn a healthy checkout into a blocked one."""
    component = components.Component("condor", "Condor", str(cloned))
    assert await components.repo_blocks(component) == []


@pytest.mark.asyncio
async def test_the_status_card_says_why_it_cannot_compare(cloned):
    """``Failed to fetch from remote`` was the only thing the card ever said."""
    _git("checkout", "-q", "-b", "feat/mine", cwd=cloned)

    info = await updater.check_for_updates(repo_dir=str(cloned))

    assert info["error"] and "not on origin" in info["error"]


def test_a_genuine_transport_failure_is_not_mistaken_for_a_missing_branch():
    assert updater.is_missing_remote_ref("fatal: couldn't find remote ref feat/x")
    assert updater.is_missing_remote_ref("fatal: could not find remote ref feat/x")
    assert not updater.is_missing_remote_ref(
        "fatal: unable to access 'https://...': Could not resolve host: github.com"
    )
    assert not updater.is_missing_remote_ref("")


@pytest.mark.asyncio
async def test_a_default_branch_with_a_slash_keeps_its_prefix(cloned, tmp_path):
    """`rsplit("/", 1)` on the ref silently renames `fix/x` to `x`.

    Branch names contain slashes routinely. Taking the last segment made
    `image_tracks_this_checkout` compare `fix/x != x` and skip the image with
    a wrong reason, and put a branch that does not exist into the advice
    `no-upstream-branch` prints.
    """
    _git("checkout", "-q", "-b", "fix/slashed", cwd=cloned)
    _git("push", "-q", "origin", "fix/slashed", cwd=cloned)
    # Point the remote's HEAD at the slashed branch, as `remote set-head` does.
    _git(
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
        "refs/remotes/origin/fix/slashed",
        cwd=cloned,
    )

    assert await updater.remote_default_branch(str(cloned)) == "fix/slashed"


@pytest.mark.asyncio
async def test_no_remote_head_is_unknown_not_a_guess(cloned):
    _git("symbolic-ref", "-d", "refs/remotes/origin/HEAD", cwd=cloned)
    assert await updater.remote_default_branch(str(cloned)) is None
