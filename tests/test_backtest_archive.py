"""FEAT-075: the archive is a tiered index — summaries list, payloads expire.

A saved backtest used to be one object, so listing 22 of them read 1 GB off
disk every five seconds. These pin the split: the summary tier answers every
listing question without opening a payload, the payload tier is gzipped and
prunable, and a run whose payload is gone is still a first-class, rankable
record rather than a phantom.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import time

import pytest

from condor.backtest_store import BacktestStore
from tests.conftest import load_shared_routine

# Loaded at import time on purpose: the autouse isolation fixture repoints
# ``$CONDOR_AGENTS_ROOT`` at a tmp dir, so a routine imported *inside* a test
# would be looked up under a directory that has no routines in it.
_compare = load_shared_routine("backtest_compare")
_chart = load_shared_routine("backtest_chart")


def _envelope(*, status="completed", net_pnl=12.5, completed_at=None, **config):
    # Recent by default: retention is measured against the wall clock, so a
    # fixture pinned to a literal epoch would silently be "expired".
    completed_at = time.time() - 3600 if completed_at is None else completed_at
    inner = {
        "id": config.pop("config_id", "ema_btc"),
        "controller_name": "pmm_simple",
        "trading_pair": config.pop("trading_pair", "BTC-USDT"),
        "connector_name": "binance",
    }
    return {
        "status": status,
        "created_at": completed_at - 100,
        "completed_at": completed_at,
        "config": {
            "config": inner,
            "start_time": 1_751_328_000,
            "end_time": 1_759_104_000,
            "backtesting_resolution": "1m",
            "trade_cost": 0.0002,
            **config,
        },
        "result": {
            "results": {"net_pnl_quote": net_pnl, "sharpe_ratio": 1.4},
            "processed_data": {"close": {str(i): i for i in range(500)}},
            "pnl_timeseries": [{"timestamp": 1_751_328_000, "total_pnl": 0.0}],
            "executors": [],
        },
    }


@pytest.fixture
def store(tmp_path) -> BacktestStore:
    store = BacktestStore(data_dir=tmp_path / "backtests")
    # A fresh store prunes on its first save (see the throttle test below).
    # Silence that here so the retention tests exercise prune_payloads itself
    # rather than the incidental sweep a save happens to trigger.
    store._meta["last_pruned"] = time.time()
    return store


# ── the summary tier ──────────────────────────────────────────────────────────


def test_a_saved_envelope_produces_the_documented_summary(store):
    finished = time.time() - 60
    store.save_result("local", "task-1", _envelope(completed_at=finished))

    summary = store.get_summary("task-1")
    assert summary["task_id"] == "task-1"
    assert summary["server"] == "local"
    assert summary["status"] == "completed"
    assert summary["config_id"] == "ema_btc"
    assert summary["controller"] == "pmm_simple"
    assert summary["trading_pair"] == "BTC-USDT"
    assert summary["connector"] == "binance"
    assert summary["resolution"] == "1m"
    assert summary["trade_cost"] == 0.0002
    assert summary["start_time"] == 1_751_328_000
    assert summary["completed_at"] == finished
    assert summary["metrics"]["net_pnl_quote"] == 12.5
    assert summary["has_payload"] is True
    # The whole point of the tier: what listing ships is kilobytes, not megabytes.
    assert "processed_data" not in json.dumps(summary)


def test_listing_never_opens_a_payload(store, monkeypatch):
    """The 1 GB poll, pinned: reading a payload during a list is the defect."""
    store.save_result("local", "task-1", _envelope())
    store.save_result("local", "task-2", _envelope())

    def boom(_task_id):
        raise AssertionError("list_summaries must not read a payload")

    monkeypatch.setattr(store, "_read_payload", boom)
    assert len(store.list_summaries()) == 2
    assert len(store.list_summaries("local")) == 2


def test_summaries_span_servers_and_filter_by_one(store):
    store.save_result("local", "task-1", _envelope())
    store.save_result("brigado_2", "task-2", _envelope())

    assert {s["task_id"] for s in store.list_summaries()} == {"task-1", "task-2"}
    assert [s["task_id"] for s in store.list_summaries("local")] == ["task-1"]
    assert [s["task_id"] for s in store.list_summaries("brigado_2")] == ["task-2"]


def test_summaries_are_ordered_by_completion_not_by_mtime(store):
    """Ordering has to survive the payload it used to be read from."""
    store.save_result("local", "old", _envelope(completed_at=time.time() - 86400))
    store.save_result("local", "new", _envelope(completed_at=time.time()))

    assert [s["task_id"] for s in store.list_summaries()] == ["new", "old"]


def test_a_failed_task_still_gets_a_summary(store):
    store.save_result("local", "task-x", {"status": "failed", "error": "boom"})

    summary = store.get_summary("task-x")
    assert summary["status"] == "failed"
    assert summary["error"] == "boom"
    assert summary["metrics"] == {}


def test_a_malformed_config_summarizes_to_empty_fields_not_an_exception(store):
    store.save_result("local", "task-x", {"status": "completed", "config": "nonsense"})

    assert store.get_summary("task-x")["config_id"] == ""


def test_resolve_task_id_handles_exact_prefix_ambiguous_and_unknown(store):
    store.save_result("local", "abcdef", _envelope())
    store.save_result("local", "abcxyz", _envelope())

    assert store.resolve_task_id("abcdef") == "abcdef"
    assert store.resolve_task_id("abcd") == "abcdef"
    assert store.resolve_task_id("abc") == ["abcdef", "abcxyz"]
    assert store.resolve_task_id("zzz") is None


# ── the payload tier ──────────────────────────────────────────────────────────


def test_payload_round_trips_through_gzip(store):
    store.save_result("local", "task-1", _envelope())

    path = store._dir / "task-1.json.gz"
    assert path.exists()
    assert json.loads(gzip.decompress(path.read_bytes()))["status"] == "completed"
    assert store.get_result("task-1")["result"]["results"]["net_pnl_quote"] == 12.5


def test_compression_is_materially_smaller_than_the_json(store):
    envelope = _envelope()
    store.save_result("local", "task-1", envelope)

    raw = len(json.dumps({"server": "local", **envelope}).encode())
    assert (store._dir / "task-1.json.gz").stat().st_size < raw / 2


def test_a_legacy_uncompressed_payload_still_reads(store):
    """A v1 file on disk keeps answering until the migration rewrites it."""
    (store._dir / "task-1.json").write_text(
        json.dumps({"server": "local", **_envelope()}), encoding="utf-8"
    )
    store._index["task-1"] = {"task_id": "task-1", "server": "local"}

    assert store.get_result("task-1")["status"] == "completed"


# ── retention ─────────────────────────────────────────────────────────────────


def test_an_expired_payload_is_deleted_and_its_summary_survives(store):
    old = time.time() - 40 * 86400
    store.save_result("local", "old", _envelope(completed_at=old))
    store.save_result("local", "fresh", _envelope(completed_at=time.time()))

    assert store.prune_payloads(30) == 1
    assert not (store._dir / "old.json.gz").exists()
    assert (store._dir / "fresh.json.gz").exists()

    summary = store.get_summary("old")
    assert summary["has_payload"] is False
    assert summary["metrics"]["net_pnl_quote"] == 12.5, "metrics outlive the payload"
    assert store.has_payload("old") is False
    assert store.get_result("old") is None, "no payload"
    assert store.get_summary("old") is not None, "but not an unknown task"
    assert [s["task_id"] for s in store.list_summaries()] == ["fresh", "old"]


def test_pruning_is_disabled_by_a_zero_retention(store):
    store.save_result("local", "old", _envelope(completed_at=time.time() - 400 * 86400))

    assert store.prune_payloads(0) == 0
    assert store.has_payload("old") is True


def test_a_save_sweeps_at_most_once_a_day(tmp_path):
    """The throttle: retention needs no scheduler, only a cheap index check."""
    store = BacktestStore(data_dir=tmp_path / "backtests")
    store.save_result("local", "old", _envelope(completed_at=time.time() - 90 * 86400))
    assert store.has_payload("old") is False, "the first save of a boot sweeps"

    store.save_result("local", "old2", _envelope(completed_at=time.time() - 90 * 86400))
    assert store.has_payload("old2") is True, "the next save is inside the throttle"


def test_the_retention_env_override_is_honoured(store, monkeypatch):
    store.save_result("local", "old", _envelope(completed_at=time.time() - 3 * 86400))

    monkeypatch.setenv("CONDOR_BACKTEST_RETENTION_DAYS", "1")
    assert store.prune_payloads() == 1
    assert store.has_payload("old") is False


# ── the v1 → v2 migration ─────────────────────────────────────────────────────


def _write_v1(store, task_id: str, envelope: dict, server: str = "local") -> None:
    (store._dir / f"{task_id}.json").write_text(
        json.dumps({"server": server, **envelope}), encoding="utf-8"
    )
    index = (
        json.loads(store._index_path.read_text(encoding="utf-8"))
        if (store._index_path.exists())
        else {}
    )
    index[task_id] = {"server": server, "config": ""}
    store._index_path.write_text(json.dumps(index), encoding="utf-8")


def test_a_v1_store_lists_degraded_until_it_is_migrated(tmp_path):
    seed = BacktestStore(data_dir=tmp_path / "backtests")
    _write_v1(seed, "task-1", _envelope())

    store = BacktestStore(data_dir=tmp_path / "backtests")
    assert store.migrated is False
    summary = store.get_summary("task-1")
    assert summary["status"] == "unknown", "an unindexed run must not claim metrics"
    assert summary["metrics"] == {}
    # It is still addressable and still readable, just not yet described.
    assert store.resolve_task_id("task") == "task-1"
    assert store.get_result("task-1")["status"] == "completed"


def test_migration_derives_summaries_compresses_and_drops_the_originals(tmp_path):
    seed = BacktestStore(data_dir=tmp_path / "backtests")
    _write_v1(seed, "task-1", _envelope())

    store = BacktestStore(data_dir=tmp_path / "backtests")
    assert store.migrate() == 1
    assert store.migrated is True
    assert not (store._dir / "task-1.json").exists()
    assert (store._dir / "task-1.json.gz").exists()
    assert store.get_summary("task-1")["metrics"]["net_pnl_quote"] == 12.5
    assert store.get_result("task-1")["status"] == "completed"
    # Idempotent: a second pass has nothing left to convert.
    assert store.migrate() == 0


def test_migration_deletes_an_expired_payload_instead_of_compressing_it(tmp_path):
    seed = BacktestStore(data_dir=tmp_path / "backtests")
    _write_v1(seed, "old", _envelope(completed_at=time.time() - 90 * 86400))

    store = BacktestStore(data_dir=tmp_path / "backtests")
    store.migrate()

    assert not (store._dir / "old.json").exists()
    assert not (store._dir / "old.json.gz").exists()
    summary = store.get_summary("old")
    assert summary["has_payload"] is False
    assert summary["metrics"]["net_pnl_quote"] == 12.5, "the parse is what we kept"


def test_a_corrupt_v1_file_is_skipped_not_fatal(tmp_path):
    seed = BacktestStore(data_dir=tmp_path / "backtests")
    _write_v1(seed, "good", _envelope())
    (seed._dir / "bad.json").write_text("{not json", encoding="utf-8")

    store = BacktestStore(data_dir=tmp_path / "backtests")
    assert store.migrate() == 1
    assert store.get_summary("good")["status"] == "completed"


# ── the seam ──────────────────────────────────────────────────────────────────


def test_no_module_outside_the_store_reaches_into_its_privates():
    """Three call sites used to hand-roll the lookup this store now exposes.

    A private reach is how a store grows a second, drifting implementation of
    its own index; ``resolve_task_id``/``list_summaries``/``has_payload`` exist
    precisely so nobody needs one. Tests are exempt: they assert the on-disk
    shape on purpose.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    # self._index is a store minding its own index (code_runs has one too);
    # what this forbids is *another* object's.
    pattern = re.compile(r"(?<!self)\._index\b|(?<!self)\._task_path\b")
    offenders = []
    for path in list(root.glob("condor/**/*.py")) + list(
        root.glob("agents/**/routines/*.py")
    ):
        if path.name == "backtest_store.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "backtest_store" not in text and "get_backtest_store" not in text:
            continue
        if pattern.search(text):
            offenders.append(str(path.relative_to(root)))

    assert offenders == []


# ── the routines that read the store ──────────────────────────────────────────


def test_compare_ranks_a_run_whose_payload_was_pruned(store):
    """Metrics outlive candles, so a pruned run still ranks — it just has no curve."""
    compare = _compare
    store.save_result("local", "kept", _envelope(net_pnl=5.0))
    store.save_result("local", "gone", _envelope(net_pnl=9.0))
    store._unlink_payload("gone")
    store._index["gone"]["has_payload"] = False

    runs = [asyncio.run(compare._load_run(store, tid)) for tid in ("kept", "gone")]
    assert all(r is not None for r in runs)
    assert [r.metrics["net_pnl_quote"] for r in runs] == [5.0, 9.0]
    assert runs[0].curve, "the kept run still draws"
    assert runs[1].curve == [], "the pruned one ranks without a line"


def test_compare_reads_a_metric_without_opening_a_payload(store, monkeypatch):
    """The number was already in the index; opening 20 MB for it was the waste."""
    compare = _compare
    store.save_result("local", "kept", _envelope())
    store._index["kept"]["has_payload"] = False  # stand in for "no curve wanted"

    def boom(_task_id):
        raise AssertionError("ranking must not open a payload")

    monkeypatch.setattr(store, "_read_payload", boom)
    run = asyncio.run(compare._load_run(store, "kept"))
    assert run.metrics["net_pnl_quote"] == 12.5
    assert compare._latest_ids(store, 3, "local") == ["kept"]


def test_compare_orders_by_completion_across_servers(store):
    compare = _compare
    store.save_result("local", "older", _envelope(completed_at=time.time() - 7200))
    store.save_result("brigado_2", "newer", _envelope(completed_at=time.time()))

    assert compare._latest_ids(store, 2, None) == ["newer", "older"]
    # A chat pointed at a server with nothing saved still gets an answer.
    assert compare._latest_ids(store, 2, "moneymaker") == ["newer", "older"]


def test_chart_says_expired_rather_than_not_found(store, monkeypatch):
    """The new state, honestly rendered: a phantom "no saved backtest" is worse."""
    import condor.backtest_store as store_mod

    monkeypatch.setattr(store_mod, "get_backtest_store", lambda: store)
    store.save_result("local", "8e44f514", _envelope())
    store._unlink_payload("8e44f514")
    store._index["8e44f514"]["has_payload"] = False

    message = asyncio.run(_chart._load_saved_task("8e44"))
    assert isinstance(message, str)
    assert "expired" in message
    assert "No saved backtest" not in message
    assert _chart._is_saved("8e44") is False, "a pruned run must ask the server once"

    store.save_result("local", "aaaa1111", _envelope())
    assert _chart._is_saved("aaaa") is True
    assert asyncio.run(_chart._load_saved_task("zzzz")).startswith("No saved backtest")


def test_an_iso_timestamp_is_a_real_clock(store):
    """The API server writes ISO-8601, not epoch seconds.

    Reducing both to one clock is what makes ordering and retention work at
    all: a stamp that parsed to nothing would sort every run equally and leave
    expiry to file mtimes forever.
    """
    envelope = _envelope()
    envelope["created_at"] = "2026-08-24T18:11:04.300751+00:00"
    envelope["completed_at"] = "2026-08-24T18:11:04.484718+00:00"
    store.save_result("local", "iso", envelope)

    assert store.get_summary("iso")["completed_at"] == pytest.approx(
        1_787_595_064.48, abs=1
    )

    # Naive stamps are read as UTC, not as the reader's local zone.
    naive = _envelope()
    naive["completed_at"] = "2026-08-24T18:11:04"
    store.save_result("local", "naive", naive)
    assert store.get_summary("naive")["completed_at"] == pytest.approx(
        store.get_summary("iso")["completed_at"], abs=1
    )


def test_migration_drops_a_v1_entry_whose_file_is_gone(tmp_path):
    """An index row that can never be described is not a run, it is a ghost."""
    seed = BacktestStore(data_dir=tmp_path / "backtests")
    _write_v1(seed, "real", _envelope())
    seed._index_path.write_text(
        json.dumps(
            {
                "real": {"server": "local", "config": ""},
                "ghost": {"server": "local", "config": ""},
            }
        ),
        encoding="utf-8",
    )

    store = BacktestStore(data_dir=tmp_path / "backtests")
    assert store.get_summary("ghost")["status"] == "unknown"
    store.migrate()

    assert store.get_summary("ghost") is None
    assert store.get_summary("real")["status"] == "completed"
