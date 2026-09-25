"""PERF-079: the session snapshot listing is cached and reads only line 1.

Guards the two properties the cache has to keep at once: unchanged files are
never re-read, and every path that creates or removes a snapshot still shows up
in the very next listing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from condor.agents import sessions_index
from condor.agents.sessions_index import (
    list_runs,
    list_session_snapshots,
    list_sessions,
)

# Big enough that a whole-file read is unmistakable next to a line-1 read: real
# snapshots embed the system prompt plus 2000 chars per tool call.
BODY = "x" * 200_000


def _write_snapshot(d: Path, name: str, title: str, mtime: float) -> Path:
    path = d / name
    path.write_text(f"{title}\n\n## Executor State\n{BODY}\n")
    os.utime(path, (mtime, mtime))
    return path


@pytest.fixture(autouse=True)
def _clear_cache():
    sessions_index._snapshot_info_cache.clear()
    sessions_index._snapshot_count_cache.clear()
    yield
    sessions_index._snapshot_info_cache.clear()
    sessions_index._snapshot_count_cache.clear()


@pytest.fixture
def session(tmp_path: Path) -> Path:
    snaps = tmp_path / "snapshots"
    snaps.mkdir()
    _write_snapshot(snaps, "snapshot_1.md", "# Snapshot #1 — 2026-08-01 10:00:00", 1000)
    _write_snapshot(snaps, "snapshot_2.md", "# Snapshot #2 — 2026-08-01 11:00:00", 2000)
    _write_snapshot(snaps, "snapshot_3.md", "# Snapshot #3 — 2026-08-01 12:00:00", 3000)
    return tmp_path


def _count_opens(monkeypatch) -> list[Path]:
    """Record every snapshot file opened for reading."""
    opened: list[Path] = []
    real_open = Path.open

    def spy(self, *args, **kwargs):
        opened.append(self)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy)
    return opened


def test_fields_and_newest_first_ordering(session: Path):
    assert list_session_snapshots(session) == [
        {"tick": 3, "timestamp": "2026-08-01 12:00:00", "file": "snapshot_3.md"},
        {"tick": 2, "timestamp": "2026-08-01 11:00:00", "file": "snapshot_2.md"},
        {"tick": 1, "timestamp": "2026-08-01 10:00:00", "file": "snapshot_1.md"},
    ]


def test_legacy_runs_dir_fallback(tmp_path: Path):
    runs = tmp_path / "runs"
    runs.mkdir()
    _write_snapshot(runs, "run_7.md", "# Tick #7 — 2026-07-01 09:00:00", 1000)
    assert list_session_snapshots(tmp_path) == [
        {"tick": 7, "timestamp": "2026-07-01 09:00:00", "file": "run_7.md"}
    ]


def test_snapshots_dir_wins_over_legacy_runs(session: Path):
    runs = session / "runs"
    runs.mkdir()
    _write_snapshot(runs, "run_9.md", "# Tick #9 — 2026-07-01 09:00:00", 9000)
    assert [s["tick"] for s in list_session_snapshots(session)] == [3, 2, 1]


def test_missing_dirs_and_unparseable_names(tmp_path: Path):
    assert list_session_snapshots(tmp_path) == []
    snaps = tmp_path / "snapshots"
    snaps.mkdir()
    _write_snapshot(snaps, "notes.md", "# Notes", 1000)
    _write_snapshot(snaps, "snapshot_1.md", "# Snapshot #1 — ts", 2000)
    assert list_session_snapshots(tmp_path) == [
        {"tick": 1, "timestamp": "ts", "file": "snapshot_1.md"}
    ]


def test_title_without_timestamp_yields_empty_string(tmp_path: Path):
    snaps = tmp_path / "snapshots"
    snaps.mkdir()
    _write_snapshot(snaps, "snapshot_1.md", "# Snapshot #1", 1000)
    assert list_session_snapshots(tmp_path)[0]["timestamp"] == ""


def test_cold_read_stops_after_the_title_line(session: Path, monkeypatch):
    """The 200KB body must never be pulled in when line 1 carries the title."""
    reads: list[int] = []
    real_read = os.read

    def spy(fd, n):  # noqa: ANN001 - stdlib signature
        data = real_read(fd, n)
        reads.append(len(data))
        return data

    monkeypatch.setattr(os, "read", spy)
    list_session_snapshots(session)
    monkeypatch.undo()
    # Three ~200KB files; a line-1 read costs one buffer fill each, so anything
    # near the full 600KB means the bound was lost.
    assert sum(reads) < 3 * 32768


def test_title_below_line_one_still_parses(tmp_path: Path):
    """Fallback path: a file that does not open with the title is fully scanned."""
    snaps = tmp_path / "snapshots"
    snaps.mkdir()
    (snaps / "run_2.md").write_text(
        "<!-- legacy header -->\n\n# Tick #2 — 2026-01-02 03:04:05\n\nbody\n"
    )
    assert list_session_snapshots(tmp_path) == [
        {"tick": 2, "timestamp": "2026-01-02 03:04:05", "file": "run_2.md"}
    ]


def test_second_listing_reads_nothing(session: Path, monkeypatch):
    list_session_snapshots(session)
    opened = _count_opens(monkeypatch)
    assert [s["tick"] for s in list_session_snapshots(session)] == [3, 2, 1]
    assert opened == []


def test_new_snapshot_is_picked_up_without_reparsing_the_others(
    session: Path, monkeypatch
):
    list_session_snapshots(session)
    _write_snapshot(
        session / "snapshots",
        "snapshot_4.md",
        "# Snapshot #4 — 2026-08-01 13:00:00",
        4000,
    )
    opened = _count_opens(monkeypatch)
    result = list_session_snapshots(session)
    assert [s["tick"] for s in result] == [4, 3, 2, 1]
    assert result[0]["timestamp"] == "2026-08-01 13:00:00"
    assert [p.name for p in opened] == ["snapshot_4.md"]


def test_deleted_snapshot_disappears_and_is_evicted(session: Path):
    list_session_snapshots(session)
    gone = session / "snapshots" / "snapshot_1.md"
    gone.unlink()
    assert [s["tick"] for s in list_session_snapshots(session)] == [3, 2]
    assert gone not in sessions_index._snapshot_info_cache


def test_rewritten_snapshot_is_reparsed(session: Path):
    list_session_snapshots(session)
    _write_snapshot(
        session / "snapshots",
        "snapshot_2.md",
        "# Snapshot #2 — 2026-08-01 11:30:00",
        2500,
    )
    by_tick = {s["tick"]: s["timestamp"] for s in list_session_snapshots(session)}
    assert by_tick[2] == "2026-08-01 11:30:00"


def test_rewrite_at_same_mtime_is_caught_by_size(session: Path):
    list_session_snapshots(session)
    path = session / "snapshots" / "snapshot_3.md"
    path.write_text("# Snapshot #3 — 2026-08-01 12:45:00\n")
    os.utime(path, (3000, 3000))
    by_tick = {s["tick"]: s["timestamp"] for s in list_session_snapshots(session)}
    assert by_tick[3] == "2026-08-01 12:45:00"


def test_whole_session_removal_returns_empty(session: Path):
    list_session_snapshots(session)
    for f in (session / "snapshots").glob("*.md"):
        f.unlink()
    (session / "snapshots").rmdir()
    assert list_session_snapshots(session) == []


# ── PERF-686: the snapshot count is memoised on the directory's mtime ──


@pytest.fixture
def strategy(tmp_path: Path) -> Path:
    session_dir = tmp_path / "sessions" / "session_1"
    snaps = session_dir / "snapshots"
    snaps.mkdir(parents=True)
    (session_dir / "journal.md").write_text("# Journal\n")
    for n in (1, 2, 3):
        _write_snapshot(snaps, f"snapshot_{n}.md", f"# Snapshot #{n}", 1000 + n)
    return tmp_path


def _snap_dir(strategy: Path) -> Path:
    return strategy / "sessions" / "session_1" / "snapshots"


def _bump_mtime(d: Path) -> None:
    later = d.stat().st_mtime + 10
    os.utime(d, (later, later))


def _counts(strategy: Path) -> tuple[int, int]:
    (session_row,) = list_sessions(strategy)
    (run_row,) = list_runs(strategy, "k")
    return session_row["snapshot_count"], run_row["snapshot_count"]


def test_a_second_count_of_an_unchanged_snapshot_dir_enumerates_nothing(
    strategy: Path, monkeypatch
):
    assert list_sessions(strategy)[0]["snapshot_count"] == 3
    snap_dir = _snap_dir(strategy)
    enumerated: list[str] = []
    real_scandir = os.scandir
    real_glob = Path.glob

    def scandir_spy(path="."):
        if Path(path) == snap_dir:
            enumerated.append("scandir")
        return real_scandir(path)

    def glob_spy(self, *args, **kwargs):
        if self == snap_dir:
            enumerated.append("glob")
        return real_glob(self, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", scandir_spy)
    monkeypatch.setattr(Path, "glob", glob_spy)

    assert _counts(strategy) == (3, 3)
    assert enumerated == []


def test_a_new_snapshot_bumps_the_count(strategy: Path):
    assert _counts(strategy) == (3, 3)
    snap_dir = _snap_dir(strategy)
    _write_snapshot(snap_dir, "snapshot_4.md", "# Snapshot #4", 1004)
    _bump_mtime(snap_dir)
    assert _counts(strategy) == (4, 4)


def test_a_deleted_snapshot_lowers_the_count(strategy: Path):
    assert _counts(strategy) == (3, 3)
    snap_dir = _snap_dir(strategy)
    (snap_dir / "snapshot_1.md").unlink()
    _bump_mtime(snap_dir)
    assert _counts(strategy) == (2, 2)


def test_legacy_runs_dir_still_counts(tmp_path: Path):
    session_dir = tmp_path / "sessions" / "session_1"
    runs = session_dir / "runs"
    runs.mkdir(parents=True)
    (session_dir / "journal.md").write_text("# Journal\n")
    _write_snapshot(runs, "run_1.md", "# Run #1", 1000)
    assert _counts(tmp_path) == (1, 1)


def test_a_removed_snapshot_dir_counts_zero_and_is_evicted(strategy: Path):
    import shutil

    assert _counts(strategy) == (3, 3)
    snap_dir = _snap_dir(strategy)
    shutil.rmtree(snap_dir)
    assert _counts(strategy) == (0, 0)
    assert snap_dir not in sessions_index._snapshot_count_cache
