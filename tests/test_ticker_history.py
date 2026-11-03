"""Tests for FEAT-053: the ticker-pool price history behind the change column.

Three things have to hold for the column to be trustworthy. The ring stays
bounded and hourly, so the file cannot grow without limit and a 60s poll cannot
turn "24h ago" into "one minute ago". :func:`reference` reports the age it
actually found rather than the age it was aiming for, including after a gap in
the ring. And the recorder never reaches for a client — the whole design rests
on it reading a pool the SDS already fetched.
"""

import ast
import json
from pathlib import Path

import pytest

from condor import paths, ticker_history


@pytest.fixture(autouse=True)
def _fresh_rings():
    """The rings are process-global; the runtime root is per-test."""
    ticker_history.reset()
    yield
    ticker_history.reset()


def pool(**prices: float) -> dict:
    """A TICKER_POOL value carrying one connector's pairs at these prices."""
    return {
        "connectors": {
            "binance": {
                pair.replace("_", "-"): {
                    "price": price,
                    "base_volume": 1.0,
                    "quote_volume": price,
                }
                for pair, price in prices.items()
            }
        },
        "prices": {},
        "updated_at": {"binance": 0.0},
    }


HOUR = ticker_history.SNAPSHOT_INTERVAL_S


# ── The ring ──


def test_records_at_most_one_snapshot_an_hour():
    """A 60s poll must not fill the ring in half an hour."""
    assert ticker_history.record("srv", pool(BTC_USDT=100.0), now=0.0) is True
    for minute in range(1, 60):
        assert (
            ticker_history.record("srv", pool(BTC_USDT=100.0), now=minute * 60.0)
            is False
        )
    assert ticker_history.record("srv", pool(BTC_USDT=100.0), now=HOUR) is True
    assert len(ticker_history._ring("srv")) == 2


def test_ring_is_trimmed_to_its_size():
    for i in range(ticker_history.RING_SIZE + 10):
        ticker_history.record("srv", pool(BTC_USDT=float(i + 1)), now=i * HOUR)

    ring = ticker_history._ring("srv")
    assert len(ring) == ticker_history.RING_SIZE
    # The oldest survivors are the newest writes, in order.
    assert ring[0]["t"] < ring[-1]["t"]
    assert ring[-1]["connectors"]["binance"]["BTC-USDT"] == float(
        ticker_history.RING_SIZE + 10
    )


def test_an_empty_pool_is_not_recorded_as_a_market_worth_zero():
    assert ticker_history.record("srv", {"connectors": {}}, now=0.0) is False
    assert ticker_history.record("srv", {}, now=0.0) is False
    # A pair quoted at zero is no reference either — it would read as -100%.
    assert ticker_history.record("srv", pool(BTC_USDT=0.0), now=0.0) is False
    assert ticker_history._ring("srv") == []


def test_snapshot_survives_a_restart():
    ticker_history.record("srv", pool(BTC_USDT=100.0), now=0.0)
    ticker_history.reset()  # a new process, same disk

    ref = ticker_history.reference("srv", now=HOUR)
    assert ref is not None
    prices, age = ref
    assert prices["binance"]["BTC-USDT"] == 100.0
    assert age == HOUR


def test_a_corrupt_file_costs_the_history_not_the_process():
    path = paths.state_dir("ticker_history") / "srv.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json at all")

    assert ticker_history.reference("srv") is None
    # And it recovers: the next poll starts a fresh ring.
    assert ticker_history.record("srv", pool(BTC_USDT=100.0), now=0.0) is True
    assert json.loads(Path(path).read_text())[0]["connectors"]["binance"] == {
        "BTC-USDT": 100.0
    }


def test_entries_that_are_not_snapshots_are_dropped():
    path = paths.state_dir("ticker_history") / "srv.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([{"t": 1.0, "connectors": {}}, "junk", {"t": "later"}]))

    assert len(ticker_history._ring("srv")) == 1


# ── The reference ──


def test_reference_is_the_snapshot_closest_to_24h_ago():
    now = 40 * HOUR
    for i in range(30):
        ticker_history.record("srv", pool(BTC_USDT=float(i)), now=i * HOUR)

    prices, age = ticker_history.reference("srv", now=now)
    assert age == ticker_history.TARGET_WINDOW_S
    assert prices["binance"]["BTC-USDT"] == 16.0  # the snapshot at T-24h


def test_a_gap_in_the_ring_is_reported_as_the_age_it_really_is():
    """After downtime the closest reference may be 40h old — and says 40h."""
    ticker_history.record("srv", pool(BTC_USDT=100.0), now=0.0)
    ticker_history.record("srv", pool(BTC_USDT=150.0), now=40 * HOUR)

    prices, age = ticker_history.reference("srv", now=40 * HOUR + 60)
    assert prices["binance"]["BTC-USDT"] == 100.0
    assert age == pytest.approx(40 * HOUR + 60)


def test_no_reference_at_all_when_the_ring_is_empty():
    assert ticker_history.reference("srv", now=0.0) is None


def test_a_reference_younger_than_the_floor_is_no_reference():
    """A fresh install must not report a whole venue at 0.00% over 12 seconds."""
    ticker_history.record("srv", pool(BTC_USDT=100.0), now=0.0)

    assert ticker_history.reference("srv", now=12.0) is None
    assert ticker_history.reference("srv", now=ticker_history.MIN_WINDOW_S) is not None


# ── The listener ──


def test_the_listener_only_records_ticker_pool_writes():
    from condor.server_data_service import CacheKey, ServerDataType

    ticker_history._on_cache_write(
        CacheKey.make("srv", ServerDataType.PORTFOLIO), {"anything": 1}
    )
    assert ticker_history._ring("srv") == []

    ticker_history._on_cache_write(
        CacheKey.make("srv", ServerDataType.TICKER_POOL), pool(BTC_USDT=100.0)
    )
    assert len(ticker_history._ring("srv")) == 1


def test_recording_never_reaches_for_a_client():
    """The whole design rests on this: history is free, or it is not worth it.

    Asserted on the module's syntax tree rather than by mocking a client,
    because the claim is that there is no call site at all — a mock only proves
    the one path a test happened to walk.
    """
    tree = ast.parse(Path(ticker_history.__file__).read_text())

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
            imported.update(a.name for a in node.names)
    assert not imported & {"aiohttp", "requests", "httpx", "get_client"}

    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "get_client" not in names
    # Nothing here awaits: the recorder runs inline on a sync cache-write hook.
    assert not any(
        isinstance(n, (ast.Await, ast.AsyncFunctionDef)) for n in ast.walk(tree)
    )


def test_install_listener_is_idempotent(monkeypatch):
    from condor.server_data_service import get_server_data_service

    sds = get_server_data_service()
    before = len(sds._listeners)
    monkeypatch.setattr(ticker_history, "_listener_installed", False)

    ticker_history.install_listener()
    ticker_history.install_listener()
    try:
        assert len(sds._listeners) == before + 1
    finally:
        sds.remove_listener(ticker_history._on_cache_write)
