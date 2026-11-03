"""A finished run's history is cached once and never invalidated.

The store's whole justification is that a finished run cannot change, so the
tests that matter are the ones that pin *when that is true* and what survives
what:

* **eligibility** — a run is written only once its final dump can have landed.
  An immutable entry written a moment too early is wrong for ever, which is the
  one failure mode a cache with no TTL cannot recover from;
* **retention** — payloads age out, entries do not, and the age is the run's
  stop time rather than the file's mtime;
* **round trip** — what goes in comes back, across a restart, with the
  controller identities that keep a BRL run from being folded as dollars.
"""

import gzip
import json
import time

import pytest

from condor.run_history_store import (
    RunHistoryEntry,
    RunHistoryStore,
    is_settled,
    retention_days,
    run_key,
)

HOUR = 3600
NOW = time.time()


def iso(offset_sec: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(NOW + offset_sec, tz=timezone.utc).isoformat()


def entry(**over) -> RunHistoryEntry:
    base = dict(
        server="brigado",
        bot_name="ganjahro-toppnl-20260821-180502",
        deployed_at=iso(-100 * HOUR),
        stopped_at=iso(-10 * HOUR),
        controllers={"c1": {"connector": "binance", "trading_pair": "BTC-BRL"}},
        points=3,
        interval="5m",
        source="snapshots",
    )
    base.update(over)
    return RunHistoryEntry(**base)


SERIES = {"c1": [[1, 2.0, 3.0, 5.0, 100.0, 0.01], [2, 4.0, 1.0, 5.0, 200.0, 0.02]]}


@pytest.fixture(autouse=True)
def _no_background_pruning(monkeypatch):
    """Disable the write-time prune, so each test drives retention itself.

    ``put`` prunes on the first write of a process — which is the behaviour
    ``test_the_first_write_prunes_what_has_aged_out`` pins — and left on, it
    would silently delete the aged payloads the explicit-retention tests set up
    before those tests ever ran.
    """
    monkeypatch.setenv("CONDOR_RUN_HISTORY_RETENTION_DAYS", "0")


@pytest.fixture
def store(tmp_path):
    return RunHistoryStore(tmp_path / "run_history")


# ── Eligibility ──


def test_a_live_run_never_settles():
    assert is_settled(None, NOW) is False
    assert is_settled("", NOW) is False


def test_a_run_that_just_stopped_is_not_settled_yet():
    """The bot writes its final dump on the way down and the sampler buckets at
    five minutes. Cached the instant it stopped, the run would miss its own last
    bucket — and being immutable, it would miss it for ever."""
    assert is_settled(iso(-60), NOW) is False


def test_a_run_stopped_long_enough_ago_is_settled():
    assert is_settled(iso(-2 * HOUR), NOW) is True


def test_an_unreadable_stop_time_does_not_settle():
    assert is_settled("last tuesday", NOW) is False


# ── Round trip ──


def test_what_goes_in_comes_back(store):
    key = run_key("brigado", "gan", "2026-08-21T18:05:02+00:00")
    store.put(key, entry(), SERIES)

    got = store.get(key)
    assert got is not None
    read_entry, series = got
    assert series == SERIES
    assert read_entry.points == 3
    assert read_entry.interval == "5m"


def test_the_controllers_identities_survive_so_a_brl_run_is_not_folded_as_dollars(
    store,
):
    key = run_key("brigado", "gan", "d")
    store.put(key, entry(), SERIES)
    assert store.get_entry(key).controllers["c1"]["trading_pair"] == "BTC-BRL"


def test_the_cache_survives_a_restart(tmp_path):
    key = run_key("brigado", "gan", "d")
    RunHistoryStore(tmp_path / "rh").put(key, entry(), SERIES)

    # A different instance over the same directory: what a restart looks like.
    reopened = RunHistoryStore(tmp_path / "rh")
    got = reopened.get(key)
    assert got is not None and got[1] == SERIES


def test_an_unknown_run_is_none_rather_than_an_error(store):
    assert store.get("nobody") is None
    assert store.get_entry("nobody") is None


def test_the_payload_is_actually_compressed(store):
    key = run_key("brigado", "gan", "d")
    store.put(key, entry(), SERIES)
    path = next((store._dir).glob("*.json.gz"))
    assert json.loads(gzip.decompress(path.read_bytes()).decode()) == SERIES


def test_a_bot_name_with_awkward_characters_still_gets_a_file(store):
    """The key is built from upstream strings — an ISO timestamp has colons and
    a plus in it — so refusing them would mean refusing to cache a legitimately
    named run."""
    key = run_key("brigado", "bot/../etc", "2026-08-21T18:05:02+00:00")
    store.put(key, entry(), SERIES)
    assert store.get(key) is not None
    assert all(p.parent == store._dir for p in store._dir.glob("*.json.gz"))


# ── Retention ──


def test_an_old_payload_is_pruned_and_its_entry_is_not(store):
    old = run_key("brigado", "old", "d")
    fresh = run_key("brigado", "fresh", "d")
    store.put(old, entry(stopped_at=iso(-90 * 24 * HOUR)), SERIES)
    store.put(fresh, entry(stopped_at=iso(-1 * 24 * HOUR)), SERIES)

    assert store.prune_payloads(max_age_days=30) == 1

    # The expensive reproducible half is gone; the cheap durable half is not.
    assert store.get(old) is None
    assert store.get_entry(old) is not None
    assert store.get_entry(old).has_payload is False
    assert store.get_entry(old).points == 3
    assert store.get(fresh) is not None


def test_the_index_still_lists_a_pruned_run(store):
    key = run_key("brigado", "old", "d")
    store.put(key, entry(stopped_at=iso(-90 * 24 * HOUR)), SERIES)
    store.prune_payloads(max_age_days=30)

    assert [e.bot_name for e in store.list_entries()] == [
        "ganjahro-toppnl-20260821-180502"
    ]


def test_a_pruned_index_survives_a_restart(tmp_path):
    key = run_key("brigado", "old", "d")
    first = RunHistoryStore(tmp_path / "rh")
    first.put(key, entry(stopped_at=iso(-90 * 24 * HOUR)), SERIES)
    first.prune_payloads(max_age_days=30)

    reopened = RunHistoryStore(tmp_path / "rh")
    assert reopened.get_entry(key) is not None
    assert reopened.get_entry(key).has_payload is False


def test_retention_zero_prunes_nothing(store):
    key = run_key("brigado", "ancient", "d")
    store.put(key, entry(stopped_at=iso(-900 * 24 * HOUR)), SERIES)
    assert store.prune_payloads(max_age_days=0) == 0
    assert store.get(key) is not None


def test_the_first_write_prunes_what_has_aged_out(store, monkeypatch):
    """Retention needs no scheduler: the store sweeps once a day, on a write."""
    monkeypatch.setenv("CONDOR_RUN_HISTORY_RETENTION_DAYS", "30")
    store.put(
        run_key("brigado", "old", "d"), entry(stopped_at=iso(-90 * 24 * HOUR)), SERIES
    )
    store.put(
        run_key("brigado", "new", "d"), entry(stopped_at=iso(-1 * 24 * HOUR)), SERIES
    )

    assert store.get(run_key("brigado", "old", "d")) is None
    assert store.get(run_key("brigado", "new", "d")) is not None


def test_retention_is_read_from_the_environment_per_call(monkeypatch):
    monkeypatch.setenv("CONDOR_RUN_HISTORY_RETENTION_DAYS", "7")
    assert retention_days() == 7
    monkeypatch.setenv("CONDOR_RUN_HISTORY_RETENTION_DAYS", "nonsense")
    assert retention_days() == 60


def test_a_payload_that_vanished_is_recorded_as_gone_rather_than_re_read(store):
    key = run_key("brigado", "gan", "d")
    store.put(key, entry(), SERIES)
    next(store._dir.glob("*.json.gz")).unlink()

    assert store.get(key) is None
    assert store.get_entry(key).has_payload is False


# ── Deleting ──


def test_deleting_a_run_forgets_it_entirely(store):
    key = run_key("brigado", "gan", "d")
    store.put(key, entry(), SERIES)
    assert store.delete(key) is True
    assert store.get_entry(key) is None
    assert list(store._dir.glob("*.json.gz")) == []
    assert store.delete(key) is False


# ── The index itself ──


def test_an_index_from_a_later_version_does_not_stop_this_one_starting(tmp_path):
    d = tmp_path / "rh"
    d.mkdir(parents=True)
    (d / "_index.json").write_text(
        json.dumps(
            {
                "meta": {},
                "entries": {
                    "k": {
                        "server": "s",
                        "bot_name": "b",
                        "deployed_at": "d",
                        "stopped_at": "s",
                        "something_new": 42,
                    }
                },
            }
        )
    )
    assert RunHistoryStore(d).get_entry("k").bot_name == "b"


def test_an_unreadable_index_leaves_an_empty_store_rather_than_a_crash(tmp_path):
    d = tmp_path / "rh"
    d.mkdir(parents=True)
    (d / "_index.json").write_text("{not json")
    assert RunHistoryStore(d).list_entries() == []


def test_two_runs_of_one_bot_are_different_keys():
    """A bot name is reused across runs; the deploy time is what tells them
    apart."""
    assert run_key("s", "bot", "2026-08-01") != run_key("s", "bot", "2026-08-02")
    assert run_key("a", "bot", "d") != run_key("b", "bot", "d")
