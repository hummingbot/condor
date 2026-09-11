"""Two-tier persistence for a finished run's sampled PnL history.

A finished run's history is *immutable* in exactly the way an archived sqlite is
immutable: the bot has stopped, the last dump has landed, and nothing that
happens later can change what the curve was. So it is walked once and read from
disk for ever after — which is the whole reason the Terminated population can
draw the same chart the live fleet draws without paying 4 MB and 18 s of
upstream every time the reader clicks a run (FEAT-089).

The split is the one :mod:`condor.backtest_store` already makes, one size class
smaller:

* The **index entry** — ~300 bytes of provenance: which run, over what window,
  which controllers, how many points and where they came from. It is what makes
  a listing cheap, it answers "do we have this" without opening anything, and
  it must outlive the payload. It lives in ``_index.json``.
* The **payload** — the points themselves, one array of six floats per sample,
  keyed by controller. ~30 KB gzipped for a three-day, three-controller run;
  all 137 archived runs of a real server come to about 4 MB. It is read only
  when someone opens that run. It lives in ``<key>.json.gz``.

Two rules follow from immutability, and they are the reason this store has no
TTL and no invalidation:

* **Eligibility, not expiry.** An entry is written only for a run that has
  stopped *and* has been stopped long enough for its final dump to have landed
  (:data:`SETTLE_SEC`). A run that fails the test is served live and not
  written at all. There is nothing to expire, because there is nothing that can
  change.
* **Retention on the payload alone.** Index entries are kept for ever; payloads
  are pruned past :data:`DEFAULT_RETENTION_DAYS` and rebuilt on demand. Same
  split, same reasoning, as the backtest archive: the cheap durable fact
  survives, the expensive reproducible one does not have to.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from condor import paths
from condor.fsutil import atomic_write_bytes, atomic_write_json

logger = logging.getLogger(__name__)

#: How long a payload is kept. Index entries are never pruned. ``0`` disables
#: pruning entirely.
DEFAULT_RETENTION_DAYS = 60

#: How long after a run's stop time its history is considered settled.
#:
#: Two sampling intervals. The bot writes its final performance dump on the way
#: down, and the sampler buckets at five minutes, so a run cached the instant it
#: stopped could miss its own last bucket — and being immutable, it would miss
#: it for ever. Waiting two buckets costs a live fetch for ten minutes and buys
#: a cache entry that is never wrong.
SETTLE_SEC = 600

#: How often :meth:`RunHistoryStore.put` bothers to look for prunable payloads.
_PRUNE_INTERVAL = 86_400

_GZIP_LEVEL = 6


def retention_days() -> int:
    """Payload retention in days, honouring ``CONDOR_RUN_HISTORY_RETENTION_DAYS``.

    Read per call rather than at import, so the override stays observable to a
    test and to an operator who edits ``.env``.
    """
    raw = os.getenv("CONDOR_RUN_HISTORY_RETENTION_DAYS")
    if raw is None or raw.strip() == "":
        return DEFAULT_RETENTION_DAYS
    try:
        return max(0, int(float(raw)))
    except ValueError:
        logger.warning("Ignoring unreadable CONDOR_RUN_HISTORY_RETENTION_DAYS=%r", raw)
        return DEFAULT_RETENTION_DAYS


@dataclass
class RunHistoryEntry:
    """What we know about one cached run, without opening its payload."""

    server: str
    bot_name: str
    deployed_at: str
    stopped_at: str
    #: ``controller_id -> {"connector": ..., "trading_pair": ...}``.
    #:
    #: Carried in the index because it is what the *fold* needs, not what the
    #: chart needs: a leaf with no pair is converted as though its quote were
    #: dollars, and on a BRL fleet that overstates every figure by the whole
    #: rate. Cheap enough to keep beside the provenance.
    controllers: dict[str, dict[str, str]] = field(default_factory=dict)
    #: Total points across every controller.
    points: int = 0
    #: The upstream sampling interval the rows were actually **fetched** at —
    #: provenance, not shape. It is ``pick_interval`` of the run's span (the
    #: per-controller walk deliberately goes coarse for a long run; see the
    #: note in :mod:`condor.fetchers.run_history`), or ``5m`` when the run
    #: declared no controller ids and the walk had no id to bind.
    #:
    #: It is *not* the spacing of the points below it: those are thinned to
    #: ``HISTORY_POINT_BUDGET`` by time bucket, which lands on no rung of the
    #: ladder and is independent of what was asked for.
    interval: str = "5m"
    built_at: float = 0.0
    #: ``"snapshots"`` | ``"archive"`` | ``"none"`` — which source the curve
    #: came from. Discovered per run, never assumed: the snapshot table's
    #: retention floor is a property of the deployment, not of this code.
    source: str = "snapshots"
    #: False once the payload has been pruned. The entry stays, so a listing
    #: still knows the run and a reader can ask for it to be rebuilt.
    has_payload: bool = True


def is_settled(stopped_at: str | None, now: float | None = None) -> bool:
    """Whether this run's history can be trusted not to change again.

    A run with no stop time is still live and never settles. One that stopped
    within :data:`SETTLE_SEC` may not have written its final dump yet, and an
    immutable cache written a moment too early is wrong for ever.
    """
    if not stopped_at:
        return False
    stopped = _epoch(stopped_at)
    if stopped is None:
        return False
    return (time.time() if now is None else now) - stopped > SETTLE_SEC


def _epoch(value: str) -> float | None:
    """An ISO-8601 instant as epoch seconds, or ``None`` when unreadable."""
    from datetime import datetime, timezone

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _safe(value: str) -> str:
    """One path segment out of an arbitrary identifier.

    Sanitizes rather than refusing, unlike :func:`condor.paths.safe_id`, and the
    difference is deliberate: these are *upstream* strings — a bot name, an ISO
    timestamp with colons and a plus in it — not ids this deployment minted, so
    refusing them would mean refusing to cache a legitimately named run. The
    result is only ever a filename inside this store's own directory, and the
    key it is derived from stays in the index verbatim.
    """
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in str(value))[:120]


def run_key(server: str, bot_name: str, deployed_at: str) -> str:
    """The identity of one run.

    A bot name is reused across runs, so the deploy time is what tells two
    apart — the same reasoning the browser's own leaf ids use.
    """
    return f"{_safe(server)}__{_safe(bot_name)}__{_safe(deployed_at)}"


class RunHistoryStore:
    """Persist finished runs' sampled histories as an index plus gzipped payloads."""

    def __init__(self, data_dir: Path | None = None) -> None:
        # Resolved per instance rather than as a default argument, so
        # ``$CONDOR_DATA_DIR`` stays observable after import (see
        # ``condor.paths``).
        self._dir = paths.run_history_dir() if data_dir is None else Path(data_dir)
        self._index_path = self._dir / "_index.json"
        self._index: dict[str, RunHistoryEntry] = {}
        self._meta: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._load_index()

    # -- public API --

    def get_entry(self, key: str) -> RunHistoryEntry | None:
        """What we know about this run without opening anything."""
        with self._lock:
            entry = self._index.get(key)
            return RunHistoryEntry(**asdict(entry)) if entry else None

    def get(self, key: str) -> tuple[RunHistoryEntry, dict[str, list]] | None:
        """The entry and its series, or ``None`` when there is no payload.

        ``None`` means "nothing to draw", which is *not* the same as "unknown
        run": a pruned run still has an entry. A caller that needs to tell them
        apart asks :meth:`get_entry`.
        """
        entry = self.get_entry(key)
        if entry is None or not entry.has_payload:
            return None
        series = self._read_payload(key)
        if series is None:
            # The file went missing under us — pruned by hand, or a failed
            # write. Say so in the index rather than answering None for ever
            # with an entry that claims otherwise.
            with self._lock:
                stored = self._index.get(key)
                if stored is not None and stored.has_payload:
                    stored.has_payload = False
                    self._persist_index()
            return None
        return entry, series

    def put(self, key: str, entry: RunHistoryEntry, series: dict[str, list]) -> None:
        """Persist one run's history. The payload is written before the entry.

        In that order because the entry is the claim that the payload exists: a
        crash between the two leaves a file nothing points at, which the next
        write replaces, rather than an index promising a file that is not there.
        """
        entry.built_at = entry.built_at or time.time()
        entry.has_payload = True
        self._write_payload(key, series)
        with self._lock:
            self._index[key] = entry
            self._persist_index()
        self._maybe_prune()

    def list_entries(self, server: str | None = None) -> list[RunHistoryEntry]:
        """Every entry, newest run first. Never opens a payload."""
        with self._lock:
            entries = [RunHistoryEntry(**asdict(e)) for e in self._index.values()]
        if server:
            entries = [e for e in entries if e.server == server]
        entries.sort(key=lambda e: e.deployed_at, reverse=True)
        return entries

    def prune_payloads(self, max_age_days: int | None = None) -> int:
        """Delete payloads for runs that stopped before the cutoff; keep entries.

        Returns how many were deleted. ``max_age_days=0`` is a no-op, which is
        how the retention override disables pruning.

        Aged on the run's **stop time**, not on the file's mtime: a run that
        finished a year ago is a year old however recently someone opened it,
        and mtime would keep the least interesting run alive simply because it
        was re-read.
        """
        max_age = retention_days() if max_age_days is None else max_age_days
        with self._lock:
            self._meta["last_pruned"] = time.time()
            self._persist_index()
        if max_age <= 0:
            return 0

        cutoff = time.time() - max_age * 86_400
        with self._lock:
            candidates = [
                (key, entry.stopped_at)
                for key, entry in self._index.items()
                if entry.has_payload
            ]

        deleted = 0
        for key, stopped_at in candidates:
            stopped = _epoch(stopped_at)
            if stopped is None or stopped >= cutoff:
                continue
            self._payload_path(key).unlink(missing_ok=True)
            with self._lock:
                stored = self._index.get(key)
                if stored is not None:
                    stored.has_payload = False
            deleted += 1

        if deleted:
            with self._lock:
                self._persist_index()
            logger.info(
                "Pruned %d run-history payload(s) for runs older than %dd",
                deleted,
                max_age,
            )
        return deleted

    def delete(self, key: str) -> bool:
        """Forget a run entirely — for a run whose record upstream is deleted."""
        with self._lock:
            if key not in self._index:
                return False
            del self._index[key]
            self._persist_index()
        self._payload_path(key).unlink(missing_ok=True)
        return True

    # -- internals --

    def _payload_path(self, key: str) -> Path:
        return self._dir / f"{_safe(key)}.json.gz"

    def _read_payload(self, key: str) -> dict[str, list] | None:
        path = self._payload_path(key)
        try:
            if not path.exists():
                return None
            raw = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        except Exception:
            logger.warning("Failed to read run-history payload %s", path)
            return None
        return raw if isinstance(raw, dict) else None

    def _write_payload(self, key: str, series: dict[str, list]) -> None:
        raw = json.dumps(series, separators=(",", ":")).encode("utf-8")
        atomic_write_bytes(self._payload_path(key), gzip.compress(raw, _GZIP_LEVEL))

    def _load_index(self) -> None:
        if not self._index_path.exists():
            return
        try:
            raw = json.loads(self._index_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Unreadable run-history index at %s", self._index_path)
            return
        if not isinstance(raw, dict):
            return
        self._meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
        entries = raw.get("entries")
        if not isinstance(entries, dict):
            return
        known = set(RunHistoryEntry.__dataclass_fields__)
        for key, value in entries.items():
            if not isinstance(value, dict):
                continue
            try:
                # Unknown keys are dropped rather than raising: an index written
                # by a later version must not stop this one from starting.
                self._index[key] = RunHistoryEntry(
                    **{k: v for k, v in value.items() if k in known}
                )
            except TypeError:
                logger.warning("Skipping malformed run-history entry %s", key)

    def _persist_index(self) -> None:
        atomic_write_json(
            self._index_path,
            {
                "meta": self._meta,
                "entries": {k: asdict(v) for k, v in self._index.items()},
            },
        )

    def _maybe_prune(self) -> None:
        last = self._meta.get("last_pruned", 0)
        if time.time() - (last or 0) < _PRUNE_INTERVAL:
            return
        try:
            self.prune_payloads()
        except Exception:
            logger.exception("Run-history prune failed")


_store: RunHistoryStore | None = None
_store_lock = threading.Lock()


def get_run_history_store() -> RunHistoryStore:
    """The process-wide store. Built on first use, so ``$CONDOR_DATA_DIR`` applies."""
    global _store
    with _store_lock:
        if _store is None:
            _store = RunHistoryStore()
        return _store


def reset_run_history_store() -> None:
    """Drop the singleton, so the next call re-resolves its directory. For tests."""
    global _store
    with _store_lock:
        _store = None
