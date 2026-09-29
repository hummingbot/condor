"""The chat agent stops writing into the tracked library (C6).

Every other assistant's authored content lands in the gitignored local root.
The chat's did not: its writable routines dir was the repo-root ``routines/``,
which git tracks and upstream actively maintains — 13 files, 48 commits touching
them over 60. So asking the chat agent to improve a shipped routine modified a
file upstream also modifies, and the next update touching it raised a
dirty-conflict whose escapes were lossy.

The carve-out that remains is load-bearing and these tests say so: FEAT-033
found that threading the slug through naively relocates the general *library*
and empties the catalog, silently, because a missing directory simply lists
nothing. Only the write target moves; the shipped root stays as the read
fallback.
"""

from __future__ import annotations

import subprocess

import pytest

from condor import migrations, paths
from condor.memory.paths import CHAT_SLUG
from routines.base import assistant_routines_dir, assistant_routines_dirs

# ── where writes land ──


@pytest.mark.parametrize("slug", [None, CHAT_SLUG])
def test_the_chat_writes_into_the_local_root(slug):
    """The whole point: `create_routine` can no longer touch the tracked tree."""
    target = assistant_routines_dir(slug)
    assert target == paths.local_agents_root() / CHAT_SLUG / "routines"
    # The thing that matters: it is not under the tracked tree any more.
    assert paths.stock_agents_root() not in target.parents
    assert not target.is_relative_to(paths._PROJECT_ROOT / "routines")


def test_a_specialist_is_unchanged():
    target = assistant_routines_dir("hyperliquid_expert")
    assert target == paths.local_agents_root() / "hyperliquid_expert" / "routines"


# ── and the catalog stays populated (the FEAT-033 regression) ──


@pytest.mark.parametrize("slug", [None, CHAT_SLUG])
def test_the_shipped_library_is_still_read(slug):
    """If this ever regresses, the catalog empties silently. Fail loudly instead."""
    dirs = assistant_routines_dirs(slug)
    assert len(dirs) == 2, "the chat lost a layer"
    assert dirs[0] == paths.local_agents_root() / CHAT_SLUG / "routines"

    shipped = dirs[1]
    assert shipped.name == "routines"
    assert shipped.is_dir(), "the shipped library is not where reads look"
    # The thing FEAT-033 broke: a populated catalog.
    assert list(shipped.glob("*.py")), "the shipped routine catalog reads as empty"


def test_local_shadows_shipped_not_the_other_way_round():
    dirs = assistant_routines_dirs(None)
    assert dirs.index(paths.local_agents_root() / CHAT_SLUG / "routines") == 0


# ── existing chat-authored routines come along ──


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A checkout with one shipped routine committed and one authored by the chat."""
    monkeypatch.setattr(paths, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(migrations.paths, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("CONDOR_RUNTIME_ROOT", str(tmp_path / ".condor"))

    library = tmp_path / "routines"
    library.mkdir()
    (library / "shipped.py").write_text("# upstream maintains this\n")
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin",
    }
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "v1"]):
        subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True, env=env
        )
    # What the chat agent wrote before this change: untracked, in the tracked dir.
    (library / "my_scanner.py").write_text("# written by the chat agent\n")
    return tmp_path


def test_a_chat_authored_routine_is_lifted_into_the_local_root(repo):
    report = migrations.MigrationReport()
    migrations._lift_chat_routines(report)

    assert report.chat_routines == 1
    lifted = paths.local_agents_root() / CHAT_SLUG / "routines" / "my_scanner.py"
    assert lifted.read_text() == "# written by the chat agent\n"
    assert not (repo / "routines" / "my_scanner.py").exists()


def test_a_tracked_routine_is_never_moved(repo):
    """Moving it would take the operator's edit out of the tree being updated.

    A tracked file is either shipped or a modification of something shipped, and
    ``keep-mine`` exists for exactly that case — it is the operator's choice,
    not the migration's.
    """
    (repo / "routines" / "shipped.py").write_text("# upstream, plus my edit\n")

    report = migrations.MigrationReport()
    migrations._lift_chat_routines(report)

    # The untracked one moved; the tracked one stayed exactly where it was.
    assert report.chat_routines == 1
    assert (
        repo / "routines" / "shipped.py"
    ).read_text() == "# upstream, plus my edit\n"
    assert not (
        paths.local_agents_root() / CHAT_SLUG / "routines" / "shipped.py"
    ).exists()


def test_the_lift_does_not_overwrite_something_already_there(repo):
    destination = paths.local_agents_root() / CHAT_SLUG / "routines"
    destination.mkdir(parents=True)
    (destination / "my_scanner.py").write_text("# the newer local one\n")

    report = migrations.MigrationReport()
    migrations._lift_chat_routines(report)

    assert report.chat_routines == 0
    assert (destination / "my_scanner.py").read_text() == "# the newer local one\n"


def test_the_lift_is_idempotent(repo):
    first = migrations.MigrationReport()
    migrations._lift_chat_routines(first)
    second = migrations.MigrationReport()
    migrations._lift_chat_routines(second)

    assert first.chat_routines == 1
    assert second.chat_routines == 0


# ── The chat's local layer is reported when upstream moves under it ──


def _chat_layers(tmp_path=None):
    from routines.base import assistant_routines_dirs

    return assistant_routines_dirs(None)


def test_an_improved_shipped_routine_is_reported_before_and_after(monkeypatch):
    """The scenario the local layer exists to create, and nothing reported it.

    FEAT-033 keeps the shipped library at the repo-root ``routines/`` and only
    the write target moved under the agent — so the generic scan looked for
    stock at ``<stock>/condor/routines``, found nothing, and filed the file as
    something the agent authored from scratch.
    """
    from condor.layering import all_stale_forks, locally_overridden

    local_dir, stock_dir = _chat_layers()
    shipped = stock_dir / "arb_check.py"
    original = shipped.read_bytes() if shipped.is_file() else None
    try:
        shipped.parent.mkdir(parents=True, exist_ok=True)
        shipped.write_text("# shipped\n", "utf-8")
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / "arb_check.py").write_text(
            "# shipped\n# my improvement\n", "utf-8"
        )

        # Before: the update is about to rewrite a file we have our own copy of.
        assert locally_overridden(["routines/arb_check.py"]) == [
            "routines/arb_check.py"
        ]

        # After: upstream's rewrite has landed and ours still wins.
        shipped.write_text("# UPSTREAM rewrote this\n", "utf-8")
        stale = [f for f in all_stale_forks() if f.rel == "routines/arb_check.py"]
        assert len(stale) == 1, "the chat's shadow of a shipped routine went unreported"
        assert stale[0].label == "condor/routines/arb_check.py"
        assert stale[0].unprovenanced is True, ".py can never carry a stamp"
    finally:
        if original is None:
            shipped.unlink(missing_ok=True)
        else:
            shipped.write_bytes(original)


def test_a_routine_the_chat_invented_is_not_reported():
    """No shipped counterpart means it was never a fork of anything."""
    from condor.layering import all_stale_forks

    local_dir, _ = _chat_layers()
    local_dir.mkdir(parents=True, exist_ok=True)
    (local_dir / "my_own_idea.py").write_text("# mine alone\n", "utf-8")

    assert [f for f in all_stale_forks() if f.rel.endswith("my_own_idea.py")] == []
