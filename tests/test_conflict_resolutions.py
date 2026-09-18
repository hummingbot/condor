"""A hand-edited playbook no longer has to be destroyed to update (C12, S2).

FEAT-115 redirects writes made *through the product* into the gitignored local
root. It does nothing about a text editor — and these are markdown files sitting
in a git checkout, which is exactly what people open in one. Such an edit blocks
the update with a `dirty-conflict`, and both escapes lost something: `discard`
destroyed the work outright (while reporting it as a *restoration*), and `stash`
parked it behind a shell command the Local user was never asked to open.
"""

from __future__ import annotations

import subprocess

import pytest

from condor.updates import components
from utils import updater

ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "PATH": "/usr/bin:/bin",
}


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=ENV)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    d = tmp_path / "repo"
    (d / "agents" / "scout").mkdir(parents=True)
    (d / "agents" / "scout" / "AGENT.md").write_text("# Scout v1\nNever on Sundays.\n")
    _git("init", "-q", "-b", "main", cwd=d)
    _git("add", "-A", cwd=d)
    _git("commit", "-qm", "v1", cwd=d)
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path / "local"))
    return d


# ── which conflicts can be kept ──


def test_only_agent_content_can_be_kept():
    keep = components.is_forkable
    assert keep(components.CONDOR, "agents/scout/AGENT.md") is True
    assert keep(components.CONDOR, "agents/scout/skills/recon/SKILL.md") is True
    # No local layer, so "keeping" it would be a quieter way of losing it.
    assert keep(components.CONDOR, "main.py") is False
    assert keep(components.CONDOR, "agents/_shared/skills/x/SKILL.md") is False
    assert keep(components.HUMMINGBOT_API, "agents/scout/AGENT.md") is False


def test_keep_mine_is_offered_only_when_every_path_can_take_it():
    """A mixed set would read as "keep all of this" and quietly reset the rest."""
    all_agents = components._resolutions_for(
        components.CONDOR, ["agents/scout/AGENT.md", "agents/keeper/AGENT.md"]
    )
    assert all_agents[0] == "keep-mine"

    mixed = components._resolutions_for(
        components.CONDOR, ["agents/scout/AGENT.md", "main.py"]
    )
    assert "keep-mine" not in mixed
    assert mixed == ["discard", "stash", "cancel"]


# ── keeping it ──


@pytest.mark.asyncio
async def test_keep_mine_preserves_the_edit_and_frees_the_update(repo, tmp_path):
    edited = repo / "agents" / "scout" / "AGENT.md"
    edited.write_text("# Scout v1 + TWO HOURS OF MY WORK\n")

    ok, message = await updater.move_to_local_root(str(repo), ["agents/scout/AGENT.md"])
    assert ok, message

    # The work survives, where the product would have put it.
    kept = tmp_path / "local" / "scout" / "AGENT.md"
    assert kept.read_text() == "# Scout v1 + TWO HOURS OF MY WORK\n"

    # The tracked file is back to HEAD, so the fast-forward is unobstructed.
    assert "TWO HOURS" not in edited.read_text()
    status = subprocess.run(
        ["git", "status", "--short"], cwd=repo, capture_output=True, text=True
    )
    assert status.stdout.strip() == ""


@pytest.mark.asyncio
async def test_the_discard_message_says_what_was_lost(repo):
    """It used to report a destructive act as "Restored 1 tracked file(s)"."""
    (repo / "agents" / "scout" / "AGENT.md").write_text("my work\n")

    ok, message = await updater.discard_paths(str(repo), ["agents/scout/AGENT.md"])
    assert ok
    assert "Discarded" in message
    assert "Restored" not in message


# ── and getting stashed work back ──


@pytest.mark.asyncio
async def test_a_clean_stash_pops_itself(repo):
    (repo / "agents" / "scout" / "AGENT.md").write_text("my work\n")
    ok, _ = await updater.stash_paths(str(repo), ["agents/scout/AGENT.md"])
    assert ok
    assert "my work" not in (repo / "agents" / "scout" / "AGENT.md").read_text()

    ok, message = await updater.stash_pop(str(repo))
    assert ok, message
    assert (repo / "agents" / "scout" / "AGENT.md").read_text() == "my work\n"


@pytest.mark.asyncio
async def test_popping_nothing_is_not_an_error(repo):
    ok, message = await updater.stash_pop(str(repo))
    assert ok
    assert "Nothing of ours" in message


@pytest.mark.asyncio
async def test_a_stash_we_did_not_make_is_left_alone(repo):
    """Someone else's stash is not ours to pop."""
    (repo / "agents" / "scout" / "AGENT.md").write_text("their work\n")
    subprocess.run(
        ["git", "stash", "push", "-m", "someone else's"],
        cwd=repo,
        check=True,
        capture_output=True,
        env=ENV,
    )

    ok, message = await updater.stash_pop(str(repo))
    assert ok
    assert "Nothing of ours" in message
    # Still there, untouched.
    listing = subprocess.run(
        ["git", "stash", "list"], cwd=repo, capture_output=True, text=True
    )
    assert "someone else's" in listing.stdout
