"""Price snapshots of the ticker pool, so a CLOB market list can show change.

The Hummingbot API has no change field anywhere on the CLOB path: ``/market-data
/tickers`` answers ``price``, ``base_volume``, ``quote_volume`` and a poll
``timestamp``, and candles are strictly one pair per request *and* register a
live feed on the API side — so "the 24h change of every pair on this venue,
sortable" cannot be asked upstream at all.

It can be measured here instead. ``ServerDataType.TICKER_POOL`` is in the SDS
auto-subscribe core list, so every configured server's whole ticker pool is
already refreshed once a minute from process start, dashboard open or not. This
module is a listener on that write plus one JSON file per server: an hourly ring
of ``{pair: price}`` snapshots, from which the closest thing to a 24h-old
reference can be read back.

The honesty rule is the point of the shape. A change number whose window the
reader cannot see is worse than no number, so :func:`reference` returns the
snapshot it actually found **and its true age**, never a promise of 24h. One
hour after a fresh install the window is an hour and the column says so; after
downtime it may be 40h and the column says that; with nothing to compare
against the caller gets ``None`` and renders a dash.

Only prices are stored. Volumes are already 24h rolling and need no reference.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from condor import paths
from condor.fsutil import atomic_write_json

logger = logging.getLogger(__name__)

# One snapshot an hour, kept 25 deep: in steady state there is always an entry
# at least 24h old, and the closest one to T-24h is within half an hour of it.
SNAPSHOT_INTERVAL_S = 3600.0
RING_SIZE = 25
TARGET_WINDOW_S = 86400.0

# A reference younger than this is not a measurement. Without the floor, the
# first minutes of a fresh install would report a whole venue at 0.00% over a
# window of seconds — which is precisely the "number nobody can trust" this
# module exists to avoid. Below it, callers get None and render a dash.
MIN_WINDOW_S = 300.0

# server -> snapshots, oldest first: [{"t": float, "connectors": {c: {pair: price}}}]
_rings: dict[str, list[dict[str, Any]]] = {}
_loaded: set[str] = set()
_listener_installed = False


def _path(server: str) -> Path:
    return paths.state_dir("ticker_history") / f"{paths.safe_id(server)}.json"


def reset() -> None:
    """Drop the in-memory rings, so the next read comes off disk.

    The rings are process-global while the runtime root is per-test, so a suite
    that does not reset would read the previous test's prices out of memory.
    """
    _rings.clear()
    _loaded.clear()


def _ring(server: str) -> list[dict[str, Any]]:
    """This server's snapshots, read from disk once per process."""
    if server in _loaded:
        return _rings.setdefault(server, [])

    _loaded.add(server)
    snapshots: list[dict[str, Any]] = []
    try:
        raw = json.loads(_path(server).read_text())
        if isinstance(raw, list):
            snapshots = [
                s
                for s in raw
                if isinstance(s, dict)
                and isinstance(s.get("t"), (int, float))
                and isinstance(s.get("connectors"), dict)
            ]
            snapshots.sort(key=lambda s: s["t"])
    except FileNotFoundError:
        pass
    except (OSError, ValueError, paths.UnsafeIdError) as exc:
        # A half-written or hand-edited file costs the history, not the process:
        # the ring refills from the next poll.
        logger.warning(
            "Ticker history for '%s' unreadable, starting over: %s", server, exc
        )

    _rings[server] = snapshots
    return snapshots


def _prices(pool: dict[str, Any]) -> dict[str, dict[str, float]]:
    """``{connector: {pair: price}}`` from a TICKER_POOL value, prices only."""
    out: dict[str, dict[str, float]] = {}
    for connector, tickers in (pool.get("connectors") or {}).items():
        if not isinstance(tickers, dict):
            continue
        prices = {}
        for pair, t in tickers.items():
            price = t.get("price") if isinstance(t, dict) else None
            if isinstance(price, (int, float)) and price > 0:
                prices[pair] = float(price)
        if prices:
            out[connector] = prices
    return out


def record(server: str, pool: dict[str, Any], now: float | None = None) -> bool:
    """Snapshot ``pool``'s prices if the newest entry is an hour old.

    Reads nothing but the dict it is handed — the pool has already been fetched
    by the SDS, so recording history costs zero upstream requests.

    Returns True when a snapshot was written.
    """
    if not isinstance(pool, dict):
        return False
    now = time.time() if now is None else now

    prices = _prices(pool)
    if not prices:
        # An empty pool is a blip, not a market where everything is worth zero.
        return False

    ring = _ring(server)
    if ring and now - ring[-1]["t"] < SNAPSHOT_INTERVAL_S:
        return False

    ring.append({"t": now, "connectors": prices})
    del ring[:-RING_SIZE]

    try:
        atomic_write_json(_path(server), ring)
    except (OSError, paths.UnsafeIdError) as exc:
        # The ring stays in memory and is useful for as long as the process
        # lives; only its restart cushion is lost.
        logger.warning("Could not persist ticker history for '%s': %s", server, exc)
    return True


def reference(
    server: str, now: float | None = None
) -> tuple[dict[str, dict[str, float]], float] | None:
    """The snapshot closest to 24h ago and the window it actually measures.

    Returns ``({connector: {pair: price}}, age_seconds)``, or None when there is
    no snapshot older than :data:`MIN_WINDOW_S` to compare against.
    """
    now = time.time() if now is None else now
    ring = _ring(server)
    if not ring:
        return None

    target = now - TARGET_WINDOW_S
    best = min(ring, key=lambda s: abs(s["t"] - target))
    age = now - best["t"]
    if age < MIN_WINDOW_S:
        return None
    return best["connectors"], age


def _on_cache_write(key: Any, value: Any) -> None:
    """SDS listener: record every ticker-pool write, ignore everything else."""
    from condor.server_data_service import ServerDataType

    if key.data_type is ServerDataType.TICKER_POOL and isinstance(value, dict):
        record(key.server, value)


def install_listener() -> None:
    """Start recording, for every server the SDS polls. Idempotent."""
    global _listener_installed
    if _listener_installed:
        return

    from condor.server_data_service import get_server_data_service

    get_server_data_service().add_listener(_on_cache_write)
    _listener_installed = True
    logger.info("Ticker history recording enabled")
