"""CORR-160: ``BacktestStore`` publishes whole files, never torn ones.

Both write sites used to derive their temp path as ``path.with_suffix(".tmp")``
— a *fixed* sibling name. Two writers racing on the same task (a re-run
overwriting a result, or two servers persisting into the shared index) picked
the identical temp path, one truncated the other's partial write, and
``os.replace`` published the blend. These pin the properties the shared
``condor.fsutil`` writer gives both sites instead: a unique temp file per
writer, no litter left behind, and UTF-8 on disk regardless of locale.
"""

from __future__ import annotations

import gzip
import json
import os
import threading

from condor.backtest_store import BacktestStore


def _tmp_files(directory):
    return sorted(p.name for p in directory.glob("*.tmp"))


def _store(tmp_path) -> BacktestStore:
    return BacktestStore(data_dir=tmp_path / "backtests")


def test_result_file_survives_concurrent_writers_intact(tmp_path):
    """Eight threads rewrite one task; the survivor is one writer's payload."""
    store = _store(tmp_path)
    # Big enough that a single write is many filesystem blocks, so a shared
    # temp file would reliably tear rather than race by luck.
    payloads = [
        {"server": f"srv{i}", "blob": chr(ord("a") + i) * 40_000} for i in range(8)
    ]
    barrier = threading.Barrier(len(payloads))

    def writer(payload):
        barrier.wait()
        for _ in range(10):
            store.save_result(payload["server"], "task-1", payload)

    threads = [threading.Thread(target=writer, args=(p,)) for p in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    survivor = store.get_result("task-1")
    assert survivor is not None  # parsed as JSON at all, i.e. not truncated
    assert {"server": survivor["server"], "blob": survivor["blob"]} in payloads
    assert _tmp_files(store._dir) == []


def test_index_survives_concurrent_writers_intact(tmp_path):
    """The index is the shared file: every writer persists it on every save."""
    store = _store(tmp_path)
    barrier = threading.Barrier(8)

    def writer(n: int):
        barrier.wait()
        for i in range(10):
            store.save_result(
                f"srv{n}", f"task-{n}-{i}", {"config": {"id": "c" * 5_000}}
            )

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # A torn index is unparseable; a reload that has to rebuild would mask it,
    # so read the bytes directly.
    on_disk = json.loads(store._index_path.read_text(encoding="utf-8"))
    assert isinstance(on_disk, dict) and on_disk.get("tasks")
    assert all(isinstance(v, dict) and "server" in v for v in on_disk["tasks"].values())
    assert _tmp_files(store._dir) == []


def test_writers_never_share_a_temp_path(tmp_path, monkeypatch):
    """The defect itself: a fixed sibling name is what let two writers collide."""
    store = _store(tmp_path)
    seen: list[str] = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(os.path.basename(src))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    store.save_result("srv", "task-1", {"n": 1})
    store.save_result("srv", "task-1", {"n": 2})

    assert len(seen) == len(set(seen))
    assert not any(
        name == "task-1.json.gz.tmp" or name == "_index.tmp" for name in seen
    )


def test_round_trip_is_utf8_regardless_of_locale(tmp_path):
    store = _store(tmp_path)
    store.save_result("srv", "task-1", {"note": "café ☕"})

    assert store.get_result("task-1")["note"] == "café ☕"
    # Decoded explicitly as UTF-8, not as whatever the reader's locale is.
    raw = gzip.decompress((store._dir / "task-1.json.gz").read_bytes()).decode("utf-8")
    assert json.loads(raw)["note"] == "café ☕"


def test_delete_still_removes_file_and_index_entry(tmp_path):
    store = _store(tmp_path)
    store.save_result("srv", "task-1", {"n": 1})

    assert store.delete_result("task-1") is True
    assert store.get_result("task-1") is None
    assert not (store._dir / "task-1.json.gz").exists()
    assert json.loads(store._index_path.read_text(encoding="utf-8"))["tasks"] == {}
