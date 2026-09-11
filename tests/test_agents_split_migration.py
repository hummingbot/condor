"""The boot that moves an install off the tracked agent tree (FEAT-115, v2).

A self-hoster pulls and restarts; nobody tells them to run a script. So the
transition from one agent root to two is a boot migration with the same
discipline as FEAT-051's: every step independently idempotent, a destination
never overwritten, the marker written last.

What is pinned here is the three steps and the two ways they can be asked to do
nothing — a tree that is not a git checkout, and a second boot.

Each test builds a throwaway git repo whose ``agents/`` is the shipped library,
because step 2's whole definition of "stock" is ``git ls-files``.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from condor import paths
from condor.migrations import (
    MARKER_FILENAME,
    MARKER_V2_FILENAME,
    MARKER_V3_FILENAME,
    ensure_migrated,
)


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def _write(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")
    return path


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A checkout whose ``agents/`` holds one committed agent and one strategy."""
    root = tmp_path / "repo"
    agents = root / "agents"
    _write(
        agents / "scout" / "AGENT.md",
        "---\nname: Scout\ndescription: shipped\n---\n\nShip.\n",
    )
    _write(
        agents / "scout" / "strategies" / "grid" / "strategy.md",
        "---\nname: Grid\n---\n\ntick\n",
    )

    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "library")

    monkeypatch.setenv(paths.STOCK_AGENTS_ROOT_ENV, str(agents))
    monkeypatch.setenv(paths.AGENTS_ROOT_ENV, str(tmp_path / "local"))
    monkeypatch.setenv(paths.RUNTIME_ROOT_ENV, str(tmp_path / "runtime"))
    return root


def _dirty(repo) -> str:
    return _git(repo, "status", "--porcelain", "--", "agents").strip()


# ── Step 1: the gitignored output of running ──


def test_runtime_artefacts_move_out_from_under_the_library(repo):
    agents = repo / "agents"
    local = paths.local_agents_root()

    _write(agents / "scout" / "store" / "user_7" / "audit.log", "ran")
    _write(agents / "scout" / "mutes.yml", "skills: [recon]\n")
    _write(agents / "scout" / "proposals" / "p.md", "proposed")
    strategy = agents / "scout" / "strategies" / "grid"
    _write(strategy / "learnings.md", "learned")
    _write(strategy / "sessions" / "session_1" / "journal.md", "ticked")
    _write(strategy / "config.yml", "interval: 60\n")

    report = ensure_migrated()

    assert report.agent_artefacts == 6
    assert (local / "scout" / "store" / "user_7" / "audit.log").read_text() == "ran"
    assert (local / "scout" / "mutes.yml").exists()
    assert (local / "scout" / "proposals" / "p.md").exists()
    grid = local / "scout" / "strategies" / "grid"
    assert (grid / "learnings.md").read_text() == "learned"
    assert (grid / "sessions" / "session_1" / "journal.md").exists()
    assert (grid / "config.yml").exists()
    # The library itself is exactly as it was committed.
    assert not (agents / "scout" / "store").exists()
    assert (agents / "scout" / "strategies" / "grid" / "strategy.md").exists()
    assert _dirty(repo) == ""


# ── Step 2: an agent git tracks nothing of ──


def test_an_excluded_agent_moves_whole_and_is_still_listed(repo):
    """``.git/info/exclude``d agents are the hand-maintained split this replaces."""
    from condor.memory.paths import iter_agent_slugs

    agents = repo / "agents"
    _write(agents / "brigado" / "AGENT.md", "---\nname: Brigado\n---\n\nBRL.\n")
    _write(agents / "brigado" / "skills" / "lp" / "SKILL.md", "---\nname: lp\n---\n\nX")
    _write(repo / ".git" / "info" / "exclude", "agents/brigado/\n")

    report = ensure_migrated()

    assert report.agent_dirs == 1
    local = paths.local_agents_root()
    assert (local / "brigado" / "AGENT.md").read_text().endswith("BRL.\n")
    assert (local / "brigado" / "skills" / "lp" / "SKILL.md").exists()
    assert not (agents / "brigado").exists()
    # Moved, not lost: the registry is the union of the two roots.
    assert set(iter_agent_slugs()) == {"brigado", "scout"}
    assert _dirty(repo) == ""


def test_a_tracked_agent_is_left_where_it_is(repo):
    ensure_migrated()

    assert (repo / "agents" / "scout" / "AGENT.md").exists()
    assert not (paths.local_agents_root() / "scout" / "AGENT.md").exists()


# ── Step 3: an operator's edit to a tracked file ──


def test_a_local_edit_is_hoisted_stamped_and_the_checkout_restored(repo):
    from condor.layering import FORKED_FROM_KEY

    agent_md = repo / "agents" / "scout" / "AGENT.md"
    agent_md.write_text("---\nname: Scout\ndescription: mine\n---\n\nMy Scout.\n")

    report = ensure_migrated()

    assert report.agent_forks == 1
    # The edit is still in force, because local shadows stock...
    local = paths.local_agents_root() / "scout" / "AGENT.md"
    assert "My Scout." in local.read_text()
    assert FORKED_FROM_KEY in local.read_text()
    # ...and the tracked tree is back at HEAD, so the next update is boring.
    assert "Ship." in agent_md.read_text()
    assert _dirty(repo) == ""


def test_a_staged_edit_is_hoisted_too(repo):
    strategy_md = repo / "agents" / "scout" / "strategies" / "grid" / "strategy.md"
    strategy_md.write_text("---\nname: Grid\n---\n\nmy tick\n")
    _git(repo, "add", "--", "agents/scout/strategies/grid/strategy.md")

    report = ensure_migrated()

    assert report.agent_forks == 1
    hoisted = (
        paths.local_agents_root() / "scout" / "strategies" / "grid" / "strategy.md"
    )
    assert "my tick" in hoisted.read_text()
    assert _dirty(repo) == ""


def test_an_existing_local_copy_is_never_overwritten(repo):
    _write(
        paths.local_agents_root() / "scout" / "AGENT.md",
        "---\nname: Scout\n---\n\nAlready mine.\n",
    )
    (repo / "agents" / "scout" / "AGENT.md").write_text(
        "---\nname: Scout\n---\n\nEdited later.\n"
    )

    report = ensure_migrated()

    assert report.agent_forks == 0
    assert (
        "Already mine."
        in (paths.local_agents_root() / "scout" / "AGENT.md").read_text()
    )


# ── Doing nothing, in the two ways it is asked to ──


def test_a_non_git_tree_keeps_its_library_whole(tmp_path, monkeypatch):
    agents = tmp_path / "agents"
    _write(agents / "scout" / "AGENT.md", "---\nname: Scout\n---\n\nShip.\n")
    _write(agents / "scout" / "store" / "user_7" / "audit.log", "ran")
    monkeypatch.setenv(paths.STOCK_AGENTS_ROOT_ENV, str(agents))
    monkeypatch.setenv(paths.AGENTS_ROOT_ENV, str(tmp_path / "local"))
    monkeypatch.setenv(paths.RUNTIME_ROOT_ENV, str(tmp_path / "runtime"))

    report = ensure_migrated()

    # Step 1 still runs — it needs no git at all...
    assert report.agent_artefacts == 1
    # ...and steps 2-3 do not, because without git there is no way to tell stock
    # from local and no update conflict to solve either.
    assert report.agent_dirs == 0 and report.agent_forks == 0
    assert (agents / "scout" / "AGENT.md").exists()


def test_booting_twice_changes_nothing(repo):
    _write(repo / "agents" / "scout" / "mutes.yml", "skills: []\n")
    (repo / "agents" / "scout" / "AGENT.md").write_text(
        "---\nname: Scout\n---\n\nMine.\n"
    )
    first = ensure_migrated()
    assert first.total

    second = ensure_migrated()

    assert second.total == 0
    assert (paths.runtime_root() / MARKER_V2_FILENAME).is_file()


def test_an_interrupted_run_finishes_on_the_next_boot(repo):
    """The marker is a fast path, never the correctness condition."""
    _write(repo / "agents" / "scout" / "proposals" / "p.md", "proposed")
    ensure_migrated()

    # A second artefact appears without the marker being cleared: it is only
    # after the marker is removed that the next boot picks it up, which is what
    # "written last" buys — a run that died before the marker retries in full.
    _write(repo / "agents" / "scout" / "mutes.yml", "skills: []\n")
    (paths.runtime_root() / MARKER_V2_FILENAME).unlink()

    report = ensure_migrated()

    assert report.agent_artefacts == 1
    assert (paths.local_agents_root() / "scout" / "mutes.yml").exists()


def test_v2_runs_on_a_box_that_already_migrated_v1(repo):
    """The two markers are independent, or an existing install never splits."""
    from condor.migrations import MARKER_FILENAME

    runtime = paths.runtime_root()
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / MARKER_FILENAME).write_text("FEAT-051\n")
    _write(repo / "agents" / "scout" / "mutes.yml", "skills: []\n")

    report = ensure_migrated()

    assert report.agent_artefacts == 1
    assert (runtime / MARKER_V2_FILENAME).is_file()


# ── v3: the delegations v1 walked past ──


def _delegations(repo, slug):
    return repo / "agents" / slug / "delegations"


def test_v3_adopts_a_transcript_with_no_status(repo, tmp_path):
    """The shape v1 could not see: a glob on ``*.status.json`` never visits it."""
    _write(_delegations(repo, "scout") / "scout-delegate-abc.md", "# what it did\n")

    report = ensure_migrated(repo / "agents")

    assert report.stranded_delegations == 1
    record = tmp_path / "local" / "scout" / "delegations" / "scout-delegate-abc"
    assert record.joinpath("transcript.md").read_text("utf-8") == "# what it did\n"
    # And the tracked tree is clean, which is the point of the sweep.
    assert not _delegations(repo, "scout").exists()


def test_v3_keys_a_record_that_names_a_user_by_its_user(repo, tmp_path):
    """Ownerless is the fallback, not the destination: a named user still wins.

    On a fresh install v1 gets to this record first and v3 never sees it. The
    branch under test is the one that matters on a box that has *already* run
    v1 -- where a status-bearing record can only be there because a v1 move was
    cut short -- so the earlier markers are in place and v3 runs alone.
    """
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / MARKER_FILENAME).write_text("FEAT-051\n")
    (runtime / MARKER_V2_FILENAME).write_text("FEAT-115\n")

    source = _delegations(repo, "scout")
    _write(source / "t1.status.json", json.dumps({"user_id": 42, "status": "done"}))
    _write(source / "t1.md", "transcript\n")

    report = ensure_migrated(repo / "agents")

    assert report.delegations == 0
    assert report.stranded_delegations == 1
    record = runtime / "users" / "42" / "delegations" / "t1"
    assert record.joinpath("status.json").is_file()
    assert record.joinpath("transcript.md").read_text("utf-8") == "transcript\n"


def test_v3_adopts_a_status_that_names_nobody(repo, tmp_path):
    """v1's "belongs to nobody; read in place, forever" — in place is now dirt."""
    source = _delegations(repo, "scout")
    _write(source / "t2.status.json", json.dumps({"status": "done"}))
    _write(source / "t2.events.json", "[]")

    report = ensure_migrated(repo / "agents")

    assert report.stranded_delegations == 1
    record = tmp_path / "local" / "scout" / "delegations" / "t2"
    assert record.joinpath("status.json").is_file()
    assert record.joinpath("events.json").read_text("utf-8") == "[]"
    assert not source.exists()


def test_v3_leaves_what_is_not_a_delegation_where_it_is(repo, tmp_path):
    """It empties the directory of delegations, not of an operator's own file."""
    source = _delegations(repo, "scout")
    _write(source / "notes.txt", "mine")
    _write(source / "t3.md", "transcript\n")

    report = ensure_migrated(repo / "agents")

    assert report.stranded_delegations == 1
    assert source.joinpath("notes.txt").read_text("utf-8") == "mine"


def test_v3_is_idempotent_and_marks_itself_done(repo, tmp_path):
    """Second boot moves nothing, and the marker is written last as ever."""
    _write(_delegations(repo, "scout") / "t4.md", "transcript\n")

    first = ensure_migrated(repo / "agents")
    assert (tmp_path / "runtime" / MARKER_V3_FILENAME).is_file()

    _write(_delegations(repo, "scout") / "t5.md", "ignored: the marker is written\n")
    second = ensure_migrated(repo / "agents")

    assert first.stranded_delegations == 1
    assert second.stranded_delegations == 0


def test_v3_never_overwrites_a_record_already_adopted(repo, tmp_path):
    """The module's standing rule: the worst outcome is a record left in place."""
    _write(_delegations(repo, "scout") / "t6.md", "the newer one\n")
    kept = tmp_path / "local" / "scout" / "delegations" / "t6" / "status.json"
    _write(kept, json.dumps({"status": "already here"}))

    report = ensure_migrated(repo / "agents")

    assert report.stranded_delegations == 0
    assert json.loads(kept.read_text("utf-8"))["status"] == "already here"
