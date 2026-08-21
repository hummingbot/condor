"""The boot migration onto ``.condor/`` (FEAT-051).

This moves a person's chat history, so the properties worth pinning are not
"it copies files" but the three that make it safe to run unattended on every
boot: it is idempotent, it finishes what an interrupted run started, and it
never writes over a destination that already exists.
"""

import json

import pytest

from condor import paths
from condor.migrations import MARKER_FILENAME, ensure_migrated


@pytest.fixture
def agents_root(tmp_path, monkeypatch):
    """A throwaway ``agents/`` tree — the *source* of the delegation step.

    Autouse-by-dependency through ``legacy``, and not optional: a migration test
    that left this pointing at the real tree would move the developer's own
    delegation records into a pytest tmp directory and prune what it emptied.
    Ask me how I know.
    """
    from condor.agents import agent as agent_module

    root = tmp_path / "agents"
    root.mkdir()
    monkeypatch.setattr(agent_module, "_DATA_ROOT", root)
    return root


@pytest.fixture
def legacy(tmp_path, monkeypatch, agents_root):
    """A pre-FEAT-051 install: the runtime store inside the Python package."""
    root = tmp_path / "package-runtime"
    monkeypatch.setattr(paths, "LEGACY_RUNTIME_ROOT", root)
    return root


def _seed_conversation(legacy, user_id, conv_id, *, turns=2, transcript=True):
    d = legacy / "conversations" / str(user_id) / conv_id
    d.mkdir(parents=True)
    (d / "meta.json").write_text(
        json.dumps({"id": conv_id, "user_id": user_id, "turn_count": turns})
    )
    if transcript:
        (d / "transcript.jsonl").write_text('{"role": "user", "text": "hi"}\n')
    return d


# ── the move ──


def test_conversations_land_under_their_owner(legacy, agents_root):
    _seed_conversation(legacy, 42, "abc123")

    report = ensure_migrated(agents_root)

    moved = paths.conversation_dir(42, "abc123")
    assert report.conversations == 1
    assert json.loads((moved / "meta.json").read_text())["id"] == "abc123"
    assert (moved / "transcript.jsonl").is_file()
    assert not (legacy / "conversations" / "42" / "abc123").exists()


def test_state_and_telemetry_move_too(legacy, agents_root):
    (legacy / "state" / "condor.scalper").mkdir(parents=True)
    (legacy / "state" / "condor.scalper" / "state.json").write_text('{"entries": {}}')
    (legacy / "telemetry").mkdir(parents=True)
    (legacy / "telemetry" / "outbox.jsonl").write_text("{}\n")

    report = ensure_migrated(agents_root)

    assert report.state == 1
    assert report.telemetry == 1
    assert (paths.state_dir("condor.scalper") / "state.json").is_file()
    assert (paths.telemetry_dir() / "outbox.jsonl").is_file()


def test_the_package_keeps_no_runtime_directory(legacy, agents_root):
    _seed_conversation(legacy, 42, "abc123")
    (legacy / "telemetry").mkdir(parents=True)
    (legacy / "telemetry" / "outbox.jsonl").write_text("{}\n")

    ensure_migrated(agents_root)

    assert not legacy.exists(), "condor/.runtime/ must be gone after a boot"


def test_a_live_telemetry_spool_at_the_destination_is_not_clobbered(
    legacy, agents_root
):
    """The destination can already exist: a process booted on the new build."""
    (legacy / "telemetry").mkdir(parents=True)
    (legacy / "telemetry" / "spool.1.jsonl").write_text("old\n")
    paths.telemetry_dir().mkdir(parents=True)
    (paths.telemetry_dir() / "spool.2.jsonl").write_text("live\n")

    ensure_migrated(agents_root)

    assert (paths.telemetry_dir() / "spool.1.jsonl").read_text() == "old\n"
    assert (paths.telemetry_dir() / "spool.2.jsonl").read_text() == "live\n"


# ── the deliberate drop ──


def test_an_empty_conversation_stub_is_dropped_not_moved(legacy, agents_root):
    """The 812 stubs a test suite wrote before there was a root to repoint."""
    _seed_conversation(legacy, 42, "stub", turns=0, transcript=False)
    _seed_conversation(legacy, 42, "real")

    report = ensure_migrated(agents_root)

    assert report.dropped_stubs == 1
    assert report.conversations == 1
    assert not paths.conversation_dir(42, "stub").exists()
    assert (paths.conversation_dir(42, "real") / "meta.json").is_file()


def test_a_transcript_with_no_counted_turns_is_still_migrated(legacy, agents_root):
    """Zero turns is only a stub when there is no transcript at all."""
    _seed_conversation(legacy, 42, "torn", turns=0, transcript=True)

    report = ensure_migrated(agents_root)

    assert report.dropped_stubs == 0
    assert (paths.conversation_dir(42, "torn") / "transcript.jsonl").is_file()


def test_an_unreadable_meta_is_kept(legacy, agents_root):
    d = legacy / "conversations" / "42" / "broken"
    d.mkdir(parents=True)
    (d / "meta.json").write_text("{not json")

    report = ensure_migrated(agents_root)

    assert report.dropped_stubs == 0
    assert (paths.conversation_dir(42, "broken") / "meta.json").is_file()


# ── running it more than once ──


def test_booting_twice_changes_nothing(legacy, agents_root):
    _seed_conversation(legacy, 42, "abc123")
    ensure_migrated(agents_root)
    before = (paths.conversation_dir(42, "abc123") / "meta.json").read_text()

    second = ensure_migrated(agents_root)

    assert second.total == 0
    assert (paths.conversation_dir(42, "abc123") / "meta.json").read_text() == before


def test_an_interrupted_run_finishes_on_the_next_boot(legacy, agents_root):
    """The marker is a fast path, not the correctness condition."""
    _seed_conversation(legacy, 42, "first")
    ensure_migrated(agents_root)
    assert (paths.runtime_root() / MARKER_FILENAME).is_file()

    # A crash before the marker: the marker is gone, one record never moved.
    (paths.runtime_root() / MARKER_FILENAME).unlink()
    _seed_conversation(legacy, 42, "second")

    report = ensure_migrated(agents_root)

    assert report.conversations == 1
    assert (paths.conversation_dir(42, "second") / "meta.json").is_file()
    assert (paths.conversation_dir(42, "first") / "meta.json").is_file()


def test_an_existing_record_is_never_overwritten(legacy, agents_root):
    _seed_conversation(legacy, 42, "abc123")
    existing = paths.conversation_dir(42, "abc123")
    existing.mkdir(parents=True)
    (existing / "meta.json").write_text('{"id": "abc123", "turn_count": 99}')

    report = ensure_migrated(agents_root)

    assert report.conversations == 0
    assert report.skipped == 1
    assert json.loads((existing / "meta.json").read_text())["turn_count"] == 99


def test_nothing_to_migrate_is_not_an_error(legacy, agents_root):
    report = ensure_migrated(agents_root)

    assert report.total == 0
    assert (paths.runtime_root() / MARKER_FILENAME).is_file()


def test_the_marker_short_circuits_a_migrated_install(legacy, agents_root):
    paths.runtime_root().mkdir(parents=True)
    (paths.runtime_root() / MARKER_FILENAME).write_text("FEAT-051\n")
    _seed_conversation(legacy, 42, "late")

    report = ensure_migrated(agents_root)

    assert report.total == 0
    assert (legacy / "conversations" / "42" / "late").exists()


def test_a_directory_name_that_is_not_an_id_is_left_alone(legacy, agents_root):
    """Refuse, never sanitize — the same rule the store itself applies."""
    stray = legacy / "conversations" / "not an id!"
    stray.mkdir(parents=True)
    _seed_conversation(legacy, 42, "abc123")

    report = ensure_migrated(agents_root)

    assert report.conversations == 1
    assert stray.is_dir()


# ── delegations, re-keyed by the user who asked ──


def _seed_delegation(agents_root, slug, task_id, *, user_id=7, events=True):
    d = agents_root / slug / "delegations"
    d.mkdir(parents=True, exist_ok=True)
    status = {"state": "done", "task_id": task_id, "agent_slug": slug}
    if user_id:
        status["user_id"] = user_id
    (d / f"{task_id}.status.json").write_text(json.dumps(status))
    (d / f"{task_id}.md").write_text(f"# Delegation {task_id}\n\n- **Status:** done\n")
    if events:
        (d / f"{task_id}.events.json").write_text('{"events": []}')
    return d


def test_a_delegation_moves_under_the_user_who_asked(legacy, agents_root):
    _seed_delegation(agents_root, "scout", "scout-delegate-abc")

    report = ensure_migrated(agents_root)

    record = paths.delegation_dir(7, "scout-delegate-abc")
    assert report.delegations == 1
    assert json.loads((record / "status.json").read_text())["task_id"] == (
        "scout-delegate-abc"
    )
    assert (record / "transcript.md").is_file()
    assert (record / "events.json").is_file()
    assert not (agents_root / "scout" / "delegations").exists()


def test_an_unowned_record_is_left_where_it_is(legacy, agents_root):
    """It belongs to nobody, so there is no user directory to file it under."""
    source = _seed_delegation(agents_root, "scout", "scout-delegate-old", user_id=0)

    report = ensure_migrated(agents_root)

    assert report.delegations == 0
    assert (source / "scout-delegate-old.status.json").is_file()
    assert (source / "scout-delegate-old.md").is_file()


def test_a_record_without_a_sidecar_still_moves(legacy, agents_root):
    _seed_delegation(agents_root, "scout", "scout-delegate-thin", events=False)

    ensure_migrated(agents_root)

    record = paths.delegation_dir(7, "scout-delegate-thin")
    assert (record / "status.json").is_file()
    assert not (record / "events.json").exists()


def test_a_delegation_already_migrated_is_not_touched(legacy, agents_root):
    _seed_delegation(agents_root, "scout", "scout-delegate-abc")
    record = paths.delegation_dir(7, "scout-delegate-abc")
    record.mkdir(parents=True)
    (record / "status.json").write_text('{"state": "stopped"}')

    report = ensure_migrated(agents_root)

    assert report.delegations == 0
    assert json.loads((record / "status.json").read_text())["state"] == "stopped"


def test_delegations_and_conversations_land_in_one_directory(legacy, agents_root):
    """The whole point: a person's footprint is one subtree."""
    _seed_conversation(legacy, 7, "conv1")
    _seed_delegation(agents_root, "scout", "scout-delegate-abc")

    ensure_migrated(agents_root)

    home = paths.user_dir(7)
    assert sorted(p.name for p in home.iterdir()) == ["conversations", "delegations"]


def test_the_migrated_record_reads_back_through_delegation_history(legacy, agents_root):
    from condor.agents.delegation_history import list_history, read_history

    _seed_delegation(agents_root, "scout", "scout-delegate-abc")

    ensure_migrated(agents_root)

    record = read_history(7, "scout-delegate-abc")
    assert record is not None
    assert record["agent"] == "scout"
    assert [r["task_id"] for r in list_history(user_id=7)] == ["scout-delegate-abc"]
