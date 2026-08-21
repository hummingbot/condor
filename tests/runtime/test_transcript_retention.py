"""Retention for the conversation transcript (PERF-170).

``transcript.jsonl`` was append-only with no sweep: PERF-138 made reading it
cheap, but the file itself grew for the life of the conversation. It is now
bounded in bytes, and the overflow is *moved* to ``transcript_archive.jsonl``
rather than deleted — the same bargain PERF-136 struck for the agent journal,
and a stricter one here, because this file is the user's own chat.

Four properties, and the last three matter more than the first: the file is
bounded, nothing is lost, no crash window can cost a turn, and a resumed
session is handed exactly the replay it would have been handed untrimmed.
"""

import json

import pytest

from condor.runtime import conversations
from condor.runtime.conversations import (
    ARCHIVE_MARKER_KIND,
    REPLAY_HEADER,
    REPLAY_OMITTED,
    TRANSCRIPT_ARCHIVE_FILENAME,
    TRANSCRIPT_FILENAME,
    TurnEntry,
    _is_archive_marker,
    _keep_bytes,
    append_turn,
    delete_conversation,
    new_conversation,
    read_transcript,
    replay_context,
)
from condor.runtime.keys import WEB

USER = 42

# Small enough that a handful of turns crosses the bound, and far enough apart
# that the trim still has real work to do on each crossing.
MAX_BYTES = 4000
KEEP_BYTES = 1000
REPLAY_CHARS = 200


@pytest.fixture
def conv_root(isolated_conversation_root):
    """The throwaway store root (see ``conftest.py``)."""
    return isolated_conversation_root


@pytest.fixture
def small_retention(monkeypatch):
    """Shrink the whole policy so a test can cross it in a few turns.

    ``REPLAY_MAX_CHARS`` comes down with it: ``_keep_bytes()`` floors the kept
    tail at a multiple of the replay budget, so leaving the real 6000 here
    would put the floor above ``MAX_BYTES`` and no trim would ever fire.
    """
    monkeypatch.setattr(conversations, "TRANSCRIPT_MAX_BYTES", MAX_BYTES)
    monkeypatch.setattr(conversations, "TRANSCRIPT_KEEP_BYTES", KEEP_BYTES)
    monkeypatch.setattr(conversations, "REPLAY_MAX_CHARS", REPLAY_CHARS)


def _talk(conv_id, turns, *, body=200):
    """Append ``turns`` user/assistant pairs of predictable, findable text."""
    for index in range(turns):
        append_turn(
            USER, conv_id, TurnEntry(role="user", text=f"q{index} " + "." * body)
        )
        append_turn(
            USER, conv_id, TurnEntry(role="assistant", text=f"a{index} " + "." * body)
        )


def _live(conv_root, conv_id):
    return conv_root / str(USER) / "conversations" / conv_id / TRANSCRIPT_FILENAME


def _archive(conv_root, conv_id):
    return (
        conv_root / str(USER) / "conversations" / conv_id / TRANSCRIPT_ARCHIVE_FILENAME
    )


# ── The file is bounded ──


def test_the_live_transcript_stays_bounded_over_many_turns(conv_root, small_retention):
    """Hundreds of turns, and the hot file never runs away."""
    meta = new_conversation(USER, WEB)
    sizes = []
    for _ in range(50):
        _talk(meta.id, 2)
        sizes.append(_live(conv_root, meta.id).stat().st_size)

    assert max(sizes) <= MAX_BYTES + 2000, f"unbounded: peaked at {max(sizes)}"
    # And it really did grow past the bound repeatedly rather than never
    # reaching it: the archive is where the difference went.
    assert _archive(conv_root, meta.id).stat().st_size > 10 * MAX_BYTES


def test_a_conversation_under_the_bound_is_never_touched(conv_root, small_retention):
    """No archive, no marker, no rewrite for the conversations people actually have."""
    meta = new_conversation(USER, WEB)
    _talk(meta.id, 3, body=20)

    assert not _archive(conv_root, meta.id).exists()
    assert not any(_is_archive_marker(t) for t in read_transcript(USER, meta.id))
    assert len(read_transcript(USER, meta.id, limit=0)) == 6


# ── Nothing is lost ──


def test_no_turn_is_destroyed_by_a_trim(conv_root, small_retention):
    """Every turn ever written comes back, in order, across many trims."""
    meta = new_conversation(USER, WEB)
    _talk(meta.id, 40)

    full = read_transcript(USER, meta.id, limit=0, include_archive=True)
    assert [t.text.split()[0] for t in full] == [
        label for index in range(40) for label in (f"q{index}", f"a{index}")
    ]


def test_the_archive_holds_exactly_what_the_live_file_gave_up(
    conv_root, small_retention
):
    """live + archive == the whole history, with no turn in both and none in neither."""
    meta = new_conversation(USER, WEB)
    _talk(meta.id, 30)

    live = [t.text for t in read_transcript(USER, meta.id, limit=0) if t.text]
    archived = [t.text for t in conversations._read_turns(_archive(conv_root, meta.id))]
    marker = [
        t for t in read_transcript(USER, meta.id, limit=0) if _is_archive_marker(t)
    ]

    assert len(marker) == 1, "one marker, folded forward, never a stack of them"
    assert set(archived).isdisjoint(set(live) - {marker[0].text})
    assert len(archived) + len(live) - 1 == 60


def test_the_live_file_says_how_much_it_no_longer_holds(conv_root, small_retention):
    """The marker names the sidecar and the running total, so nothing lies."""
    meta = new_conversation(USER, WEB)
    _talk(meta.id, 30)

    turns = read_transcript(USER, meta.id, limit=0)
    marker = next(t for t in turns if _is_archive_marker(t))
    archived = conversations._read_turns(_archive(conv_root, meta.id))

    assert marker.kind == ARCHIVE_MARKER_KIND
    assert TRANSCRIPT_ARCHIVE_FILENAME in marker.text
    assert marker.text.startswith(f"{len(archived)} older turns moved to")
    assert turns[0] is turns[0] and _is_archive_marker(turns[0]), "at the head"


def test_the_full_read_drops_the_marker_because_nothing_is_missing(
    conv_root, small_retention
):
    """``include_archive`` is the complete record, so there is no boundary to mark."""
    meta = new_conversation(USER, WEB)
    _talk(meta.id, 30)

    assert any(_is_archive_marker(t) for t in read_transcript(USER, meta.id, limit=0))
    for turns in (
        read_transcript(USER, meta.id, limit=0, include_archive=True),
        read_transcript(USER, meta.id, limit=500, include_archive=True),
    ):
        assert not any(_is_archive_marker(t) for t in turns)


def test_a_bounded_tail_reaches_back_into_the_archive(conv_root, small_retention):
    """A tail longer than the live file continues into the sidecar, in order."""
    meta = new_conversation(USER, WEB)
    _talk(meta.id, 30)

    live_turns = len(read_transcript(USER, meta.id, limit=0))
    deep = read_transcript(USER, meta.id, limit=live_turns + 10, include_archive=True)

    # The marker is filtered before the bound is applied, so a caller asking
    # for N turns gets N real ones rather than N-1 and a signpost.
    assert len(deep) == live_turns + 10
    full = read_transcript(USER, meta.id, limit=0, include_archive=True)
    assert [t.text for t in deep] == [t.text for t in full[-len(deep) :]]


def test_deleting_a_conversation_takes_the_archive_with_it(conv_root, small_retention):
    """Forgetting a chat still forgets all of it — the sidecar is not a leak."""
    meta = new_conversation(USER, WEB)
    _talk(meta.id, 30)
    assert _archive(conv_root, meta.id).exists()

    assert delete_conversation(USER, meta.id) is True
    assert not _archive(conv_root, meta.id).exists()


# ── No crash window costs a turn ──


def test_a_crash_publishing_the_trimmed_file_loses_nothing(
    conv_root, small_retention, monkeypatch
):
    """The rewrite is atomic: a failed publish leaves the whole old file."""

    def boom(*args, **kwargs):
        raise OSError("crash mid-write")

    monkeypatch.setattr(conversations, "atomic_write_bytes", boom)
    meta = new_conversation(USER, WEB)
    _talk(meta.id, 20)  # must not raise: retention never breaks a live prompt

    # The trim never landed, so the live file is over the bound — and still
    # holds every single turn. Over-large beats truncated.
    assert _live(conv_root, meta.id).stat().st_size > MAX_BYTES
    assert [t.text.split()[0] for t in read_transcript(USER, meta.id, limit=0)] == [
        label for index in range(20) for label in (f"q{index}", f"a{index}")
    ]


def test_a_crash_writing_the_archive_leaves_the_transcript_intact(
    conv_root, small_retention, monkeypatch
):
    """Nowhere safe to park the turns means nothing is dropped."""
    monkeypatch.setattr(
        conversations,
        "_append_archive",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("no sidecar")),
    )
    meta = new_conversation(USER, WEB)
    _talk(meta.id, 20)

    assert not _archive(conv_root, meta.id).exists()
    assert len(read_transcript(USER, meta.id, limit=0)) == 40


def test_the_trim_is_retried_on_the_next_append(
    conv_root, small_retention, monkeypatch
):
    """A window that failed is not a window that gave up."""
    calls = {"n": 0}
    real = conversations._append_archive

    def flaky(conv_dir, lines):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("transient")
        return real(conv_dir, lines)

    monkeypatch.setattr(conversations, "_append_archive", flaky)
    meta = new_conversation(USER, WEB)
    _talk(meta.id, 20)

    assert calls["n"] > 1
    assert _archive(conv_root, meta.id).exists()
    assert len(read_transcript(USER, meta.id, limit=0, include_archive=True)) == 40


def test_a_trim_publishes_only_whole_lines(conv_root, small_retention):
    """Every line of both files still parses as JSON after repeated trims."""
    meta = new_conversation(USER, WEB)
    _talk(meta.id, 40)

    for path in (_live(conv_root, meta.id), _archive(conv_root, meta.id)):
        raw = path.read_bytes()
        assert raw.endswith(b"\n"), f"{path.name} ends mid-line"
        for line in raw.split(b"\n"):
            if line.strip():
                json.loads(line.decode("utf-8"))


# ── The replay is unaffected ──


def test_the_kept_tail_can_always_fill_the_replay_budget(monkeypatch):
    """The floor holds even when the configured keep is absurdly small."""
    monkeypatch.setattr(conversations, "TRANSCRIPT_KEEP_BYTES", 1)
    monkeypatch.setattr(conversations, "REPLAY_MAX_CHARS", 6000)
    assert _keep_bytes() >= 6000


def test_replay_is_identical_with_and_without_retention(conv_root, monkeypatch):
    """The whole point: a trim cannot change what a resumed session is handed.

    Same turns, one conversation trimmed to the bone and one with retention
    effectively off. The replay walks back from the newest turn and stops at
    the budget, and the kept tail is always wider than that budget — so the two
    must agree byte for byte.
    """
    monkeypatch.setattr(conversations, "REPLAY_MAX_CHARS", REPLAY_CHARS)

    monkeypatch.setattr(conversations, "TRANSCRIPT_MAX_BYTES", 10**9)
    untrimmed = new_conversation(USER, WEB)
    _talk(untrimmed.id, 40)

    monkeypatch.setattr(conversations, "TRANSCRIPT_MAX_BYTES", MAX_BYTES)
    monkeypatch.setattr(conversations, "TRANSCRIPT_KEEP_BYTES", KEEP_BYTES)
    trimmed = new_conversation(USER, WEB)
    _talk(trimmed.id, 40)

    assert _archive(conv_root, trimmed.id).exists(), "the trimmed one really trimmed"
    assert not _archive(conv_root, untrimmed.id).exists()

    expected = replay_context(USER, untrimmed.id, max_chars=REPLAY_CHARS)
    assert replay_context(USER, trimmed.id, max_chars=REPLAY_CHARS) == expected
    assert expected.startswith(REPLAY_HEADER)
    assert "a39" in expected, "the newest turn survives, which is what replay is for"


def test_replay_stays_coherent_after_a_trim(conv_root, small_retention):
    """Header, newest turns, explicit boundary, and never over the budget."""
    meta = new_conversation(USER, WEB)
    _talk(meta.id, 40)

    replay = replay_context(USER, meta.id, max_chars=REPLAY_CHARS)

    assert replay.startswith(REPLAY_HEADER)
    assert replay.endswith(REPLAY_OMITTED)
    assert len(replay) <= REPLAY_CHARS
    assert "a39" in replay


def test_the_newest_turn_survives_a_turn_larger_than_the_whole_budget(
    conv_root, small_retention
):
    """A trim has to shrink the file, never empty it."""
    meta = new_conversation(USER, WEB)
    _talk(meta.id, 10)
    append_turn(USER, meta.id, TurnEntry(role="assistant", text="huge " + "z" * 50_000))

    turns = read_transcript(USER, meta.id, limit=0)
    assert turns[-1].text.startswith("huge ")
    assert replay_context(USER, meta.id, max_chars=REPLAY_CHARS).count("huge") == 1
    assert len(read_transcript(USER, meta.id, limit=0, include_archive=True)) == 21
