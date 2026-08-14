"""Tests for the shared atomic-write helper (ARCH-148).

Seven copies of tmpfile+os.replace were folded into :mod:`condor.fsutil`; these
pin the three properties every one of them was written to guarantee — the new
contents land, a failure mid-write leaves the previous file untouched, and no
temp file survives either outcome — plus the hardening the merged helper adds.
"""

from __future__ import annotations

import json
import os
import stat
import threading

import pytest

from condor.fsutil import atomic_write_bytes, atomic_write_json, atomic_write_text


def _tmp_files(directory):
    return sorted(p.name for p in directory.glob("*.tmp"))


# ── Success path ──


def test_replaces_existing_file(tmp_path):
    target = tmp_path / "state.txt"
    target.write_text("old")

    atomic_write_text(target, "new")

    assert target.read_text() == "new"
    assert _tmp_files(tmp_path) == []


def test_creates_file_and_parent_dirs(tmp_path):
    target = tmp_path / "deep" / "nested" / "state.txt"

    atomic_write_text(target, "hello")

    assert target.read_text() == "hello"
    assert _tmp_files(target.parent) == []


def test_writes_utf8_regardless_of_locale(tmp_path):
    target = tmp_path / "memo.md"

    atomic_write_text(target, "café ☕")

    assert target.read_bytes() == "café ☕".encode("utf-8")


def test_bytes_and_json_round_trip(tmp_path):
    blob = tmp_path / "blob.bin"
    atomic_write_bytes(blob, b"\x00\xff\x10")
    assert blob.read_bytes() == b"\x00\xff\x10"

    index = tmp_path / "index.json"
    atomic_write_json(index, [{"id": "a"}], indent=2)
    assert json.loads(index.read_text(encoding="utf-8")) == [{"id": "a"}]
    # json kwargs are forwarded verbatim, so callers keep their exact bytes.
    assert index.read_text(encoding="utf-8").startswith("[\n  {")


def test_fsync_can_be_disabled(tmp_path):
    target = tmp_path / "hot.json"

    atomic_write_json(target, {"tick": 1}, fsync=False)

    assert json.loads(target.read_text(encoding="utf-8")) == {"tick": 1}
    assert _tmp_files(tmp_path) == []


# ── Failure path ──


def test_failure_mid_write_leaves_original_intact(tmp_path, monkeypatch):
    target = tmp_path / "state.txt"
    target.write_text("original")

    real_replace = os.replace

    def boom(src, dst):
        # Fail exactly at publication: the temp file is fully written by now,
        # which is the worst moment for the previous contents.
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(target, "replacement")
    monkeypatch.setattr(os, "replace", real_replace)

    assert target.read_text() == "original"
    assert _tmp_files(tmp_path) == []


def test_failure_leaves_no_tmp_and_no_target_when_new(tmp_path, monkeypatch):
    target = tmp_path / "brand-new.txt"

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(target, "content")

    assert not target.exists()
    assert _tmp_files(tmp_path) == []


def test_unserializable_json_never_touches_the_filesystem(tmp_path):
    target = tmp_path / "index.json"
    target.write_text("[]")

    with pytest.raises(TypeError):
        atomic_write_json(target, {"fn": object()})

    assert target.read_text() == "[]"
    assert _tmp_files(tmp_path) == []


def test_keyboard_interrupt_cleans_up_tmp(tmp_path, monkeypatch):
    """``except Exception`` would leak the temp file here; ``BaseException`` does not."""
    target = tmp_path / "state.txt"
    target.write_text("original")

    def interrupt(src, dst):
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", interrupt)
    with pytest.raises(KeyboardInterrupt):
        atomic_write_bytes(target, b"replacement")

    assert target.read_text() == "original"
    assert _tmp_files(tmp_path) == []


# ── Hardening the merged helper adds ──


def test_tmp_name_is_unique_per_writer(tmp_path, monkeypatch):
    """A fixed temp name would let two writers tear each other's file."""
    seen: list[str] = []
    target = tmp_path / "status.json"
    real_replace = os.replace

    def spy(src, dst):
        seen.append(os.path.basename(src))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    atomic_write_text(target, "a")
    atomic_write_text(target, "b")

    assert len(seen) == 2
    assert seen[0] != seen[1]
    # Hidden and target-derived, so a listing of the directory never picks a
    # half-written file up and a stray one is traceable to its target.
    assert all(name.startswith(".status.json.") for name in seen)
    assert all(name.endswith(".tmp") for name in seen)


def test_concurrent_writers_never_publish_a_torn_file(tmp_path):
    target = tmp_path / "shared.txt"
    payloads = [chr(ord("a") + i) * 20_000 for i in range(8)]
    barrier = threading.Barrier(len(payloads))

    def writer(text):
        barrier.wait()
        for _ in range(10):
            atomic_write_text(target, text)

    threads = [threading.Thread(target=writer, args=(p,)) for p in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    survivor = target.read_text()
    assert survivor in payloads  # exactly one writer's payload, not a blend
    assert _tmp_files(tmp_path) == []


def test_new_file_is_private_and_existing_mode_is_preserved(tmp_path):
    fresh = tmp_path / "fresh.txt"
    atomic_write_text(fresh, "x")
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o600

    loose = tmp_path / "loose.txt"
    loose.write_text("x")
    loose.chmod(0o644)
    atomic_write_text(loose, "y")
    # Rewriting must not silently tighten a file someone widened on purpose.
    assert stat.S_IMODE(loose.stat().st_mode) == 0o644
