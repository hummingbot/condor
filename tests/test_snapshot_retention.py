"""Snapshot retention keeps the newest ticks, not the lexically greatest names (#235).

``snapshot_100.md`` sorts before ``snapshot_2.md`` as a string, so the old cleanup kept
ticks 2-9 and 16-99 and deleted 151-158 at tick 158 with a cap of 100. The cases cross
the digit-width boundary on purpose: that is where a lexical sort and a numeric one part.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from condor.agents import journal as journal_module
from condor.agents.journal import JournalManager


def _write_snapshots(journal: JournalManager, ticks) -> Path:
    snapshots_dir = journal._snapshots_dir
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    for tick in ticks:
        (snapshots_dir / f"snapshot_{tick}.md").write_text(f"# tick {tick}\n")
    return snapshots_dir


def _ticks_on_disk(snapshots_dir: Path) -> list[int]:
    ticks = []
    for p in snapshots_dir.glob("snapshot_*.md"):
        suffix = p.stem.split("_", 1)[1]
        if suffix.isdigit():
            ticks.append(int(suffix))
    return sorted(ticks)


def test_cleanup_keeps_the_newest_ticks_across_the_digit_boundary(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(journal_module, "MAX_SNAPSHOTS", 100)
    journal = JournalManager("test-agent", session_dir=tmp_path)
    snapshots_dir = _write_snapshots(journal, range(1, 103))

    journal._cleanup_old_snapshots()

    assert _ticks_on_disk(snapshots_dir) == list(range(3, 103))


def test_cleanup_at_the_reported_tick_keeps_59_to_158(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(journal_module, "MAX_SNAPSHOTS", 100)
    journal = JournalManager("test-agent", session_dir=tmp_path)
    snapshots_dir = _write_snapshots(journal, range(1, 159))

    journal._cleanup_old_snapshots()

    assert _ticks_on_disk(snapshots_dir) == list(range(59, 159))


def test_cleanup_under_the_cap_deletes_nothing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(journal_module, "MAX_SNAPSHOTS", 100)
    journal = JournalManager("test-agent", session_dir=tmp_path)
    snapshots_dir = _write_snapshots(journal, range(1, 101))

    journal._cleanup_old_snapshots()

    assert _ticks_on_disk(snapshots_dir) == list(range(1, 101))


def test_cleanup_leaves_a_file_without_a_tick_alone(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(journal_module, "MAX_SNAPSHOTS", 2)
    journal = JournalManager("test-agent", session_dir=tmp_path)
    snapshots_dir = _write_snapshots(journal, [1, 2, 3])
    stray = snapshots_dir / "snapshot_notes.md"
    stray.write_text("not a tick\n")

    journal._cleanup_old_snapshots()

    assert _ticks_on_disk(snapshots_dir) == [2, 3]
    assert stray.exists()
