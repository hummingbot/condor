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
def legacy(tmp_path, monkeypatch):
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


def test_conversations_land_under_their_owner(legacy):
    _seed_conversation(legacy, 42, "abc123")

    report = ensure_migrated()

    moved = paths.conversation_dir(42, "abc123")
    assert report.conversations == 1
    assert json.loads((moved / "meta.json").read_text())["id"] == "abc123"
    assert (moved / "transcript.jsonl").is_file()
    assert not (legacy / "conversations" / "42" / "abc123").exists()


def test_state_and_telemetry_move_too(legacy):
    (legacy / "state" / "condor.scalper").mkdir(parents=True)
    (legacy / "state" / "condor.scalper" / "state.json").write_text('{"entries": {}}')
    (legacy / "telemetry").mkdir(parents=True)
    (legacy / "telemetry" / "outbox.jsonl").write_text("{}\n")

    report = ensure_migrated()

    assert report.state == 1
    assert report.telemetry == 1
    assert (paths.state_dir("condor.scalper") / "state.json").is_file()
    assert (paths.telemetry_dir() / "outbox.jsonl").is_file()


def test_the_package_keeps_no_runtime_directory(legacy):
    _seed_conversation(legacy, 42, "abc123")
    (legacy / "telemetry").mkdir(parents=True)
    (legacy / "telemetry" / "outbox.jsonl").write_text("{}\n")

    ensure_migrated()

    assert not legacy.exists(), "condor/.runtime/ must be gone after a boot"


def test_a_live_telemetry_spool_at_the_destination_is_not_clobbered(legacy):
    """The destination can already exist: a process booted on the new build."""
    (legacy / "telemetry").mkdir(parents=True)
    (legacy / "telemetry" / "spool.1.jsonl").write_text("old\n")
    paths.telemetry_dir().mkdir(parents=True)
    (paths.telemetry_dir() / "spool.2.jsonl").write_text("live\n")

    ensure_migrated()

    assert (paths.telemetry_dir() / "spool.1.jsonl").read_text() == "old\n"
    assert (paths.telemetry_dir() / "spool.2.jsonl").read_text() == "live\n"


# ── the deliberate drop ──


def test_an_empty_conversation_stub_is_dropped_not_moved(legacy):
    """The 812 stubs a test suite wrote before there was a root to repoint."""
    _seed_conversation(legacy, 42, "stub", turns=0, transcript=False)
    _seed_conversation(legacy, 42, "real")

    report = ensure_migrated()

    assert report.dropped_stubs == 1
    assert report.conversations == 1
    assert not paths.conversation_dir(42, "stub").exists()
    assert (paths.conversation_dir(42, "real") / "meta.json").is_file()


def test_a_transcript_with_no_counted_turns_is_still_migrated(legacy):
    """Zero turns is only a stub when there is no transcript at all."""
    _seed_conversation(legacy, 42, "torn", turns=0, transcript=True)

    report = ensure_migrated()

    assert report.dropped_stubs == 0
    assert (paths.conversation_dir(42, "torn") / "transcript.jsonl").is_file()


def test_an_unreadable_meta_is_kept(legacy):
    d = legacy / "conversations" / "42" / "broken"
    d.mkdir(parents=True)
    (d / "meta.json").write_text("{not json")

    report = ensure_migrated()

    assert report.dropped_stubs == 0
    assert (paths.conversation_dir(42, "broken") / "meta.json").is_file()


# ── running it more than once ──


def test_booting_twice_changes_nothing(legacy):
    _seed_conversation(legacy, 42, "abc123")
    ensure_migrated()
    before = (paths.conversation_dir(42, "abc123") / "meta.json").read_text()

    second = ensure_migrated()

    assert second.total == 0
    assert (paths.conversation_dir(42, "abc123") / "meta.json").read_text() == before


def test_an_interrupted_run_finishes_on_the_next_boot(legacy):
    """The marker is a fast path, not the correctness condition."""
    _seed_conversation(legacy, 42, "first")
    ensure_migrated()
    assert (paths.runtime_root() / MARKER_FILENAME).is_file()

    # A crash before the marker: the marker is gone, one record never moved.
    (paths.runtime_root() / MARKER_FILENAME).unlink()
    _seed_conversation(legacy, 42, "second")

    report = ensure_migrated()

    assert report.conversations == 1
    assert (paths.conversation_dir(42, "second") / "meta.json").is_file()
    assert (paths.conversation_dir(42, "first") / "meta.json").is_file()


def test_an_existing_record_is_never_overwritten(legacy):
    _seed_conversation(legacy, 42, "abc123")
    existing = paths.conversation_dir(42, "abc123")
    existing.mkdir(parents=True)
    (existing / "meta.json").write_text('{"id": "abc123", "turn_count": 99}')

    report = ensure_migrated()

    assert report.conversations == 0
    assert report.skipped == 1
    assert json.loads((existing / "meta.json").read_text())["turn_count"] == 99


def test_nothing_to_migrate_is_not_an_error(legacy):
    report = ensure_migrated()

    assert report.total == 0
    assert (paths.runtime_root() / MARKER_FILENAME).is_file()


def test_the_marker_short_circuits_a_migrated_install(legacy):
    paths.runtime_root().mkdir(parents=True)
    (paths.runtime_root() / MARKER_FILENAME).write_text("FEAT-051\n")
    _seed_conversation(legacy, 42, "late")

    report = ensure_migrated()

    assert report.total == 0
    assert (legacy / "conversations" / "42" / "late").exists()


def test_a_directory_name_that_is_not_an_id_is_left_alone(legacy):
    """Refuse, never sanitize — the same rule the store itself applies."""
    stray = legacy / "conversations" / "not an id!"
    stray.mkdir(parents=True)
    _seed_conversation(legacy, 42, "abc123")

    report = ensure_migrated()

    assert report.conversations == 1
    assert stray.is_dir()
