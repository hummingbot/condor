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


# ── S2: a stash that cannot be replayed must leave the tree usable ──


@pytest.fixture
def stashed(repo, tmp_path):
    """The main line of the `stash` resolution, set up exactly as it happens.

    `stash` is offered as a way out of `dirty-conflict`, and that block means
    the incoming commits touch the file being parked — so the replay conflicts
    in the ordinary case, not a rare one.
    """
    agent = repo / "agents" / "scout" / "AGENT.md"
    origin = tmp_path / "origin.git"
    _git("clone", "-q", "--bare", str(repo), str(origin), cwd=tmp_path)
    _git("remote", "add", "origin", str(origin), cwd=repo)
    _git("fetch", "-q", "origin", cwd=repo)
    _git("branch", "-q", "--set-upstream-to=origin/main", "main", cwd=repo)

    # Upstream rewrites the same file.
    work = tmp_path / "work"
    _git("clone", "-q", str(origin), str(work), cwd=tmp_path)
    (work / "agents" / "scout" / "AGENT.md").write_text("# Scout v2\nUPSTREAM rule.\n")
    _git("commit", "-aqm", "v2", cwd=work)
    _git("push", "-q", "origin", "main", cwd=work)

    # The operator's own edit, and unrelated uncommitted work elsewhere — the
    # runtime state these checkouts are always in.
    agent.write_text("# Scout v1\nNever on Sundays.\nMY EDIT.\n")
    bystander = repo / "runtime-notes.txt"
    bystander.write_text("do not lose me\n")
    return repo, agent, bystander


@pytest.mark.asyncio
async def test_a_stash_that_cannot_be_replayed_leaves_no_conflict_markers(stashed):
    """`git stash pop` wrote the markers into the file and kept the stash.

    The update then reported success over a checkout holding a half-merged
    agent playbook, an unmerged index, and advice — "resolve it by hand with
    git stash pop" — that errors with "needs merge" when followed.
    """
    repo, agent, bystander = stashed

    ok, _ = await updater.stash_paths(str(repo), ["agents/scout/AGENT.md"])
    assert ok
    ok, _ = await updater.fast_forward(str(repo))
    assert ok

    restored, message = await updater.stash_pop(str(repo))

    assert restored is False, "it genuinely could not be replayed"
    text = agent.read_text()
    assert "<<<<<<<" not in text and ">>>>>>>" not in text, text
    assert text == "# Scout v2\nUPSTREAM rule.\n", "the tree is the update's own"

    unmerged = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=U"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=ENV,
    ).stdout
    assert unmerged.strip() == "", f"index left unmerged: {unmerged}"

    # The work survives, and the message points at where.
    assert await updater.has_update_stash(str(repo)) is True
    assert "stash@{0}" in message and "nothing is half-merged" in message

    # And the abort did not reach past the stash's own paths.
    assert bystander.read_text() == "do not lose me\n"


@pytest.mark.asyncio
async def test_a_stash_that_replays_cleanly_is_dropped(stashed, tmp_path):
    """The common case still completes — and does not leave the entry behind."""
    repo, agent, _ = stashed
    # Drop the edit that collides, so the fast-forward is unobstructed, and
    # park something the update does not touch instead.
    _git("checkout", "--", "agents/scout/AGENT.md", cwd=repo)
    other = repo / "agents" / "scout" / "notes.md"
    other.write_text("my notes\n")

    ok, _ = await updater.stash_paths(str(repo), ["agents/scout/notes.md"])
    assert ok
    moved, output = await updater.fast_forward(str(repo))
    assert moved, output

    restored, message = await updater.stash_pop(str(repo))

    assert restored is True, message
    assert other.read_text() == "my notes\n"
    assert await updater.has_update_stash(str(repo)) is False, "the entry was kept"


@pytest.mark.asyncio
async def test_nothing_of_ours_stashed_is_not_a_failure(repo):
    assert await updater.stash_pop(str(repo)) == (
        True,
        "Nothing of ours was stashed.",
    )


@pytest.mark.asyncio
async def test_work_left_in_a_stash_is_reported_by_the_next_preflight(
    stashed, monkeypatch
):
    """The stash outlived the run that made it and nothing mentioned it again.

    `git status` does not show stashes, the finished run scrolls away, and the
    next preflight only looks at the working tree — so parked work could sit
    there indefinitely with every surface reporting a healthy install.
    """
    repo, _, _ = stashed
    await updater.stash_paths(str(repo), ["agents/scout/AGENT.md"])

    monkeypatch.setattr(
        components,
        "_table",
        lambda: {
            components.HUMMINGBOT_API: components.Component(
                components.HUMMINGBOT_API,
                "Hummingbot API",
                str(repo / "no-such-api"),
                service="hummingbot-api",
            ),
            components.CONDOR: components.Component(
                components.CONDOR, "Condor", str(repo)
            ),
        },
    )
    preflight = await components.preflight([components.CONDOR])

    codes = [w.code for w in preflight.warnings]
    assert "stashed-work" in codes, codes
    message = next(w.message for w in preflight.warnings if w.code == "stashed-work")
    assert "condor /update" in message


@pytest.mark.asyncio
async def test_a_clean_checkout_gets_no_stash_warning(repo, monkeypatch):
    monkeypatch.setattr(
        components,
        "_table",
        lambda: {
            components.HUMMINGBOT_API: components.Component(
                components.HUMMINGBOT_API,
                "Hummingbot API",
                str(repo / "no-such-api"),
                service="hummingbot-api",
            ),
            components.CONDOR: components.Component(
                components.CONDOR, "Condor", str(repo)
            ),
        },
    )
    preflight = await components.preflight([components.CONDOR])
    assert "stashed-work" not in [w.code for w in preflight.warnings]


# ── Review findings on the resolutions themselves ──


@pytest.mark.asyncio
async def test_our_stash_is_found_by_name_not_by_position(stashed):
    """`stash apply`/`drop` default to `stash@{0}`, and the stack reorders.

    With an operator stash pushed after ours, "a condor stash exists
    somewhere" plus "act on the top one" applied *and dropped* theirs,
    restored unrelated work into the tree nobody asked for, left ours parked,
    and reported success.
    """
    repo, agent, bystander = stashed
    # Park something the update does not touch, so the replay applies cleanly
    # and the drop is reached — that is where the wrong entry was destroyed.
    _git("checkout", "--", "agents/scout/AGENT.md", cwd=repo)
    (repo / "agents" / "scout" / "notes.md").write_text("my notes\n")
    await updater.stash_paths(str(repo), ["agents/scout/notes.md"])

    bystander.write_text("operator's own edit\n")
    _git("stash", "push", "-u", "-m", "my wip", "--", bystander.name, cwd=repo)
    assert (
        await updater.update_stash_ref(str(repo)) == "stash@{1}"
    ), "ours is not on top"

    moved, output = await updater.fast_forward(str(repo))
    assert moved, output
    restored, message = await updater.stash_pop(str(repo))
    assert restored, message

    listing = subprocess.run(
        ["git", "stash", "list"], cwd=repo, capture_output=True, text=True, env=ENV
    ).stdout
    assert "my wip" in listing, "the operator's stash was consumed"
    assert "condor /update" not in listing, "ours should have been dropped"
    assert (repo / "agents" / "scout" / "notes.md").read_text() == "my notes\n"
    # Their work stayed parked rather than being dumped into the tree.
    assert not bystander.exists() or bystander.read_text() != "operator's own edit\n"


@pytest.mark.asyncio
async def test_keep_mine_refuses_to_overwrite_an_existing_local_copy(repo, tmp_path):
    """Edited through the product *and* in the checkout: two customizations.

    `shutil.move` replaced the first with the second and reported that the
    version was kept — one of them destroyed, silently.
    """
    from condor.paths import local_agents_root

    local = local_agents_root() / "scout"
    local.mkdir(parents=True, exist_ok=True)
    (local / "AGENT.md").write_text("MADE THROUGH THE PRODUCT\n")
    (repo / "agents" / "scout" / "AGENT.md").write_text("# Scout v1\nEditor tweak.\n")

    ok, message = await updater.move_to_local_root(str(repo), ["agents/scout/AGENT.md"])

    assert ok is False
    assert (local / "AGENT.md").read_text() == "MADE THROUGH THE PRODUCT\n"
    assert "already have your own" in message
    # And the checkout copy is still there to compare against.
    assert (repo / "agents" / "scout" / "AGENT.md").exists()


@pytest.mark.asyncio
async def test_keep_mine_on_an_untracked_file_reports_success(repo):
    """`keep-mine` is offered for an `untracked-conflict` too.

    `checkout HEAD -- <path>` fails on a path HEAD never had, so the whole
    resolution reported failure *after* moving the file. Nothing needs
    resetting for an untracked path — moving it was the resolution.
    """
    (repo / "agents" / "scout" / "invented.md").write_text("mine alone\n")

    ok, message = await updater.move_to_local_root(
        str(repo), ["agents/scout/invented.md"]
    )

    assert ok is True, message
    assert not (repo / "agents" / "scout" / "invented.md").exists()


# ── Second review pass: defects in the fixes above ──


@pytest.mark.asyncio
async def test_a_batch_move_checks_every_destination_before_moving_any(repo):
    """A clash found half way through left the earlier files already gone.

    The refusal said "nothing was moved" while the checkout showed a deletion
    nobody made, and the update stayed blocked.
    """
    from condor.paths import local_agents_root

    (repo / "agents" / "scout" / "second.md").write_text("shipped\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "two files", cwd=repo)

    clash = local_agents_root() / "scout" / "second.md"
    clash.parent.mkdir(parents=True, exist_ok=True)
    clash.write_text("MADE THROUGH THE PRODUCT\n")
    for name in ("AGENT.md", "second.md"):
        (repo / "agents" / "scout" / name).write_text("shipped\nmy edit\n")

    ok, message = await updater.move_to_local_root(
        str(repo), ["agents/scout/AGENT.md", "agents/scout/second.md"]
    )

    assert ok is False
    assert (repo / "agents" / "scout" / "AGENT.md").exists(), "moved despite refusing"
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=ENV,
    ).stdout
    assert " D " not in status and not status.startswith("D "), status
    assert "second.md" in message and "AGENT.md" not in message


@pytest.mark.asyncio
async def test_a_staged_addition_has_its_index_entry_cleared(repo):
    """`checkout HEAD --` cannot touch a path HEAD never had.

    The staged entry survived the move, so the incoming commit that adds the
    same path still had something to collide with — reported as a success.
    """
    (repo / "agents" / "scout" / "added.md").write_text("I staged this\n")
    _git("add", "agents/scout/added.md", cwd=repo)

    ok, message = await updater.move_to_local_root(str(repo), ["agents/scout/added.md"])

    assert ok is True, message
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=ENV,
    ).stdout.strip()
    assert status == "", f"index entry survived: {status!r}"


@pytest.mark.asyncio
async def test_the_abort_advice_names_our_stash_not_the_top_one(stashed):
    """Ours is not necessarily `stash@{0}` — anything pushed later sits above.

    The recovery line hardcoded `stash@{0}` and bare `git stash pop`, so
    following it restored somebody else's work: the same mistake the lookup
    had just been fixed to stop making.
    """
    repo, agent, bystander = stashed
    await updater.stash_paths(str(repo), ["agents/scout/AGENT.md"])
    bystander.write_text("operator's own edit\n")
    _git("stash", "push", "-u", "-m", "someone else", "--", bystander.name, cwd=repo)

    moved, output = await updater.fast_forward(str(repo))
    assert moved, output
    restored, message = await updater.stash_pop(str(repo))

    assert restored is False, "this replay is expected to conflict"
    assert "git stash pop stash@{1}" in message, message
    assert "stash@{0}" not in message
