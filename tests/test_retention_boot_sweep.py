"""ST-13: retention also runs at boot, not only when something new is saved.

Both stores hook their daily prune to their own writes, so a store nobody
writes to kept payloads past retention indefinitely. The boot-time
``migrate_backtest_archive`` was the natural sweep, but ``migrate()`` returned
early on an already-migrated store, before its prune.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import condor.backtest_store as bts
import condor.run_history_store as rhs
from condor.backtest_store import BacktestStore
from condor.run_history_store import RunHistoryEntry, RunHistoryStore

DAY = 86400


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _aged_backtest_store(tmp_path, monkeypatch) -> BacktestStore:
    """A migrated v2 store with one payload past retention and one inside it."""
    # Retention off while seeding: the first write of a store prunes at once.
    monkeypatch.setenv("CONDOR_BACKTEST_RETENTION_DAYS", "0")
    store = BacktestStore(data_dir=tmp_path / "backtests")
    store.save_result("srv", "old", {"completed_at": _iso(60), "config": {}})
    store.save_result("srv", "new", {"completed_at": _iso(1), "config": {}})
    assert store.get_result("old") is not None
    monkeypatch.setenv("CONDOR_BACKTEST_RETENTION_DAYS", "30")
    # The last prune check was over a day ago, as on an install whose last
    # backtest was weeks back.
    store._meta["last_pruned"] = time.time() - 2 * DAY
    store._persist_index()
    return BacktestStore(data_dir=tmp_path / "backtests")


def test_migrate_on_a_migrated_store_still_prunes_aged_payloads(tmp_path, monkeypatch):
    store = _aged_backtest_store(tmp_path, monkeypatch)
    assert store.migrated

    assert store.migrate() == 0

    assert store.get_result("old") is None
    assert not store._payload_path("old").exists()
    # The summary is the durable half and stays.
    assert store.get_summary("old") is not None
    assert store.get_summary("old")["has_payload"] is False
    assert store.get_result("new") is not None


def test_the_boot_sweep_is_rate_limited_like_the_write_time_one(tmp_path, monkeypatch):
    store = _aged_backtest_store(tmp_path, monkeypatch)
    store._meta["last_pruned"] = time.time()

    store.migrate()

    assert store.get_result("old") is not None


def test_the_boot_hook_sweeps_run_history_too(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_RUN_HISTORY_RETENTION_DAYS", "0")
    history = RunHistoryStore(tmp_path / "run_history")
    history.put(
        "old",
        RunHistoryEntry(
            server="s", bot_name="b", deployed_at=_iso(100), stopped_at=_iso(90)
        ),
        {"c": [[1, 2.0]]},
    )
    assert history.get("old") is not None
    monkeypatch.setenv("CONDOR_RUN_HISTORY_RETENTION_DAYS", "30")
    history._meta["last_pruned"] = time.time() - 2 * DAY

    monkeypatch.setattr(rhs, "_store", history)
    monkeypatch.setattr(bts, "_store", BacktestStore(data_dir=tmp_path / "backtests"))

    bts.migrate_backtest_archive()

    assert history.get("old") is None
    assert history.get_entry("old") is not None
    assert history.get_entry("old").has_payload is False
