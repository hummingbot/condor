"""Fetch and aggregate Hummingbot bot performance by bot name.

Single source of truth for the "bot-by-name" PnL aggregation that wraps
``client.bot_orchestration.get_latest_controller_performance()`` and rolls up
the per-controller snapshots into one figure per ``bot_name``.

Used by:
- the web ``/bot-runs`` route (to enrich each run with its live PnL), and
- ``condor.agents.performance`` (to merge a controller-mode agent's bot PnL into
  the agent's reported performance).

The two PnL sources are disjoint by construction: bot controllers create
executors tagged with their own controller-config ids, never with an
``agent_id``, so this aggregate adds to the executor-by-``agent_id`` aggregate
without double counting.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter, OrderedDict
from functools import partial
from typing import Any, Iterable

from condor.fetchers._pagination import collect_pages
from condor.fetchers.executors import normalize_executor_side

logger = logging.getLogger(__name__)


def extract_snapshots(result: Any) -> list[dict]:
    """Normalize a controller-performance API response into a list of snapshot dicts."""
    if isinstance(result, list):
        return [s for s in result if isinstance(s, dict)]
    if isinstance(result, dict):
        data = result.get("data", result.get("snapshots", result.get("records", [])))
        if isinstance(data, list):
            return [s for s in data if isinstance(s, dict)]
        if isinstance(data, dict):
            # Could be keyed by controller_id
            out = []
            for key, val in data.items():
                if isinstance(val, dict):
                    val.setdefault("controller_id", key)
                    out.append(val)
                elif isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict):
                            item.setdefault("controller_id", key)
                            out.append(item)
            return out
    return []


# Only genuine round-trip position closes count as "trades". Everything else in
# close_type_counts is churn or non-trades: EARLY_STOP is pmm order re-quoting
# (thousands per session), POSITION_HOLD is a fill that built a still-open position,
# and INSUFFICIENT_BALANCE/FAILED/EXPIRED never executed.
_TRADE_CLOSE_TYPES = {
    "CloseType.TAKE_PROFIT",
    "CloseType.STOP_LOSS",
    "CloseType.TIME_LIMIT",
    "CloseType.TRAILING_STOP",
    "CloseType.COMPLETED",
}


def count_trade_closes(perf: dict) -> int:
    """Round-trip closes in one controller-performance payload's ``close_type_counts``.

    The single authority for "how many trades did this controller actually close",
    shared by the live snapshot aggregate and the cumulative history so both
    surfaces count the same events.
    """
    counts = perf.get("close_type_counts") or {}
    if not isinstance(counts, dict):
        return 0
    return sum(int(v or 0) for k, v in counts.items() if k in _TRADE_CLOSE_TYPES)


def _aggregate_by_bot(snapshots: list[dict]) -> dict[str, dict]:
    """Roll up per-controller snapshots into one aggregate per bot_name.

    Each controller entry keeps its ``positions_summary`` (the open positions the
    controller holds, with per-position PnL/volume/fees) and ``status`` so callers
    can render executor-like rows for bot-mode agents, whose executors live in the
    bot container and never surface in the ``agent_id``-keyed executor table.

    ``closed_trades`` carries the controller's real round-trip closes. Open
    positions are the only thing ``positions_summary`` describes, so without it a
    caller counting rows would read a bot's whole trading history as "however many
    positions happen to be open right now".

    ``close_type_counts`` is carried through verbatim, per controller and summed
    per bot. ``closed_trades`` deliberately counts only the round-trip close types
    (see :data:`_TRADE_CLOSE_TYPES`), which is right for a market maker whose
    EARLY_STOPs are order re-quoting and wrong for a directional controller whose
    EARLY_STOP is the risk stop that closed the position. Nothing in this payload
    distinguishes the two — ``controller_name`` comes back empty and an archived
    instance has no config left to ask — so the raw breakdown travels alongside
    the count and the reader decides. A session showing "0 trades" on six figures
    of volume is then explicable rather than merely wrong.
    """
    agg: dict[str, dict] = {}
    for snap in snapshots:
        bn = snap.get("bot_name", "")
        if not bn:
            continue
        perf = snap.get("performance", snap)
        if not isinstance(perf, dict):
            perf = {}
        if bn not in agg:
            agg[bn] = {
                "bot_name": bn,
                "realized_pnl_quote": 0.0,
                "unrealized_pnl_quote": 0.0,
                "global_pnl_quote": 0.0,
                "volume_traded": 0.0,
                "cum_fees_quote": 0.0,
                "closed_trades": 0,
                "close_type_counts": Counter(),
                "num_controllers": 0,
                "timestamp": "",
                "controllers": [],
            }
        realized = float(perf.get("realized_pnl_quote", 0) or 0)
        unrealized = float(perf.get("unrealized_pnl_quote", 0) or 0)
        volume = float(perf.get("volume_traded", 0) or 0)
        positions = [
            p for p in (perf.get("positions_summary") or []) if isinstance(p, dict)
        ]
        fees = sum(float(p.get("cum_fees_quote", 0) or 0) for p in positions)
        closes = count_trade_closes(perf)
        close_types = {
            str(k): int(v or 0)
            for k, v in (perf.get("close_type_counts") or {}).items()
            if isinstance(perf.get("close_type_counts"), dict) and int(v or 0) > 0
        }
        agg[bn]["close_type_counts"].update(close_types)
        agg[bn]["realized_pnl_quote"] += realized
        agg[bn]["unrealized_pnl_quote"] += unrealized
        agg[bn]["global_pnl_quote"] += realized + unrealized
        agg[bn]["volume_traded"] += volume
        agg[bn]["cum_fees_quote"] += fees
        agg[bn]["closed_trades"] += closes
        agg[bn]["num_controllers"] += 1
        # Track the freshest snapshot timestamp so suffix-tolerant resolution can
        # pick the most recent deploy of a re-launched bot.
        ts = str(snap.get("timestamp", "") or "")
        if ts > agg[bn]["timestamp"]:
            agg[bn]["timestamp"] = ts
        agg[bn]["controllers"].append(
            {
                # Which deploy this controller ran under. _merge_instance_aggregates
                # concatenates the controller lists of every instance under a base,
                # so without naming the instance here a redeployed bot's controllers
                # all read as one undifferentiated set.
                "bot_name": bn,
                "controller_id": snap.get("controller_id", ""),
                "controller_name": snap.get("controller_name", ""),
                "connector": snap.get("connector", snap.get("connector_name", "")),
                "trading_pair": snap.get("trading_pair", ""),
                "status": str(snap.get("status", "") or ""),
                "realized_pnl_quote": realized,
                "unrealized_pnl_quote": unrealized,
                "volume_traded": volume,
                "cum_fees_quote": fees,
                "closed_trades": closes,
                "close_type_counts": close_types,
                "positions_summary": positions,
            }
        )
    # Plain dict on the way out: the aggregate is serialized to the web wire and a
    # Counter is only an accumulation detail.
    for bot in agg.values():
        bot["close_type_counts"] = dict(bot["close_type_counts"])
    return agg


# ── Whole-server snapshot cache ──
# get_latest_controller_performance() is a WHOLE-SERVER call: it returns the
# latest snapshot of every bot and every controller, so the payload is identical
# no matter which caller asks. The agents rollup fans one call per strategy into
# a single asyncio.gather, which without coalescing fires N byte-identical
# whole-server requests at the same API server simultaneously. A short TTL plus
# in-flight coalescing collapses that burst into one round-trip and one
# aggregation shared by every caller, while staying far fresher than the 30s TTL
# the agents route already tolerates above this call.
_SNAPSHOT_TTL = 5.0
_snapshot_cache: dict[str, tuple[float, dict[str, dict]]] = {}
_snapshot_inflight: dict[str, tuple[Any, asyncio.Task]] = {}


def _server_key(client: Any) -> str:
    """Identify the API server a client talks to, or ``""`` if unidentifiable.

    Keyed on ``base_url`` — never ``id(client)``, whose reuse after GC would hand
    one server's snapshot to another. A client with no ``base_url`` (test doubles)
    cannot be told apart from any other, so it is not cached at all rather than
    sharing a blank key with unrelated servers.
    """
    return str(getattr(client, "base_url", "") or "")


def clear_snapshot_cache() -> None:
    """Drop every cached whole-server snapshot (tests, server reconfiguration)."""
    _snapshot_cache.clear()
    _snapshot_inflight.clear()


async def _fetch_and_aggregate(client: Any) -> dict[str, dict]:
    result = await client.bot_orchestration.get_latest_controller_performance()
    return _aggregate_by_bot(extract_snapshots(result))


async def fetch_all_bot_performance(client: Any) -> dict[str, dict]:
    """Return ``{bot_name: aggregate}`` from the latest controller-performance snapshot.

    Each aggregate has ``realized_pnl_quote``, ``unrealized_pnl_quote``,
    ``global_pnl_quote``, ``volume_traded``, ``num_controllers`` and a
    ``controllers`` breakdown. Raises if the API call fails — callers decide how
    to degrade.

    Cached per server for ``_SNAPSHOT_TTL`` seconds and coalesced while in flight,
    so concurrent callers on the same server share one round-trip and one
    aggregation. A fetch that raises is never cached: the exception propagates to
    every waiter and the next call retries. The returned aggregate is shared
    between callers and must be treated as read-only.
    """
    key = _server_key(client)
    if not key:
        return await _fetch_and_aggregate(client)

    entry = _snapshot_cache.get(key)
    if entry is not None and time.monotonic() - entry[0] <= _SNAPSHOT_TTL:
        return entry[1]

    # Reuse an in-flight fetch only from the loop that created it: a task is
    # bound to its loop and awaiting it from another one raises.
    loop = asyncio.get_running_loop()
    inflight = _snapshot_inflight.get(key)
    task = inflight[1] if inflight is not None and inflight[0] is loop else None
    if task is None:
        task = asyncio.ensure_future(_fetch_and_aggregate(client))
        _snapshot_inflight[key] = (loop, task)
        task.add_done_callback(lambda _t, k=key: _snapshot_inflight.pop(k, None))

    agg = await task
    _snapshot_cache[key] = (time.monotonic(), agg)
    return agg


def partition_instances(
    all_bot_perf: dict[str, dict],
    bases: list[str],
    extra_names: Iterable[str] = (),
) -> dict[str, list[str]]:
    """Assign every deployed instance to the LONGEST owned base that prefixes it.

    Prefix-matching one base at a time cannot tell a *tag* from a *deploy
    timestamp*, so a parent swallows its tagged siblings: ``brigado-ema_trend``
    prefix-matches ``brigado-ema_trend-btc-20260731-101500`` as readily as
    ``brigado-ema_trend-btc`` does. Deciding over ALL owned bases at once settles
    it — that instance lands on ``…-btc``, the longest base that prefixes it — so
    each instance's PnL is counted under exactly one base and a session operating
    several bots gets a truthful sum.

    ``extra_names`` widens the universe beyond the live snapshot — archived
    instances, which is where a stopped bot's realized PnL lives. Discovery is the
    only thing that was live-only: the controller-performance history endpoint
    serves a stopped instance as readily as a running one, so a base whose bots
    were all stopped used to resolve to nothing and report $0 rather than the loss
    it actually took.

    Returns one entry per base (possibly empty), each instance list ordered oldest
    first — by snapshot ``timestamp`` where there is one, else by name, which for
    a shared base is the same order since the ``-YYYYMMDD-HHMMSS`` deploy suffix
    sorts chronologically.
    """
    out: dict[str, list[str]] = {b: [] for b in bases if b}
    if not out:
        return out
    universe = {*all_bot_perf, *(n for n in extra_names if n)}
    if not universe:
        return out
    longest_first = sorted(out, key=len, reverse=True)
    for name in universe:
        for base in longest_first:
            if name == base or name.startswith(f"{base}-"):
                out[base].append(name)
                break
    for names in out.values():
        names.sort(key=lambda k: (str(all_bot_perf.get(k, {}).get("timestamp", "")), k))
    return out


def _merge_instance_aggregates(bots: list[dict]) -> dict:
    """Sum several live instance aggregates of one base into a single aggregate.

    A base that was redeployed while an earlier instance still ran, or that simply
    runs more than one instance, has its figures spread across them. Taking only
    the freshest — as this did before — silently dropped every other instance's
    PnL, so a strategy that redeploys under a stable base name under-reported by
    exactly the amount its previous instances earned.


    ``bots[0]`` names the result: the caller orders the list so the instance whose
    name should represent the base comes first. Only the figures are pooled.
    """
    if len(bots) == 1:
        return bots[0]
    merged = dict(bots[0])
    merged["controllers"] = list(bots[0].get("controllers", []))
    merged["close_type_counts"] = dict(bots[0].get("close_type_counts") or {})
    for b in bots[1:]:
        for ct, n in (b.get("close_type_counts") or {}).items():
            merged["close_type_counts"][ct] = merged["close_type_counts"].get(ct, 0) + n
        for key in (
            "realized_pnl_quote",
            "unrealized_pnl_quote",
            "global_pnl_quote",
            "volume_traded",
            "cum_fees_quote",
            "closed_trades",
            "num_controllers",
        ):
            merged[key] = (merged.get(key, 0) or 0) + (b.get(key, 0) or 0)
        merged["controllers"] += list(b.get("controllers", []))
        if str(b.get("timestamp", "")) > str(merged.get("timestamp", "")):
            merged["timestamp"] = b.get("timestamp", "")
    return merged


def resolve_bots(all_bot_perf: dict[str, dict], bases: list[str]) -> dict[str, dict]:
    """Live aggregate per base, partition-aware and summed across its instances.

    A bot deploys under an instance name with a timestamp suffix appended
    (``dn-CL-BRENTOIL-mm`` → ``dn-CL-BRENTOIL-mm-20260724-182221``), while the
    strategy config only knows the stable base name. Every *live* instance under a
    base contributes, so a base running several at once reports their sum rather
    than whichever happened to be freshest. The base is still *named* after its
    exact deploy where there is one, else its freshest instance — that rule now
    only picks a label, not which instance's PnL survives.

    Deliberately live-only: this is the source for unrealized PnL and the open
    position book, which belong to whoever is running right now. Realized PnL from
    stopped instances comes through :func:`fetch_base_histories`, whose universe
    includes archived names. Bases with no live instance are absent from the
    result.
    """
    out: dict[str, dict] = {}
    for base, insts in partition_instances(all_bot_perf, bases).items():
        live = [i for i in insts if i in all_bot_perf]
        if not live:
            continue
        # Name-bearer first: _merge_instance_aggregates keeps bots[0]'s bot_name.
        head = base if base in live else live[-1]
        ordered = [all_bot_perf[head]] + [all_bot_perf[i] for i in live if i != head]
        out[base] = _merge_instance_aggregates(ordered)
    return out


# Archived-bot discovery. The listing is a set of sqlite paths, one per stopped
# instance, and changes only when a bot is stopped — a short TTL is plenty and
# keeps the per-tick rollup from re-listing on every call.
_ARCHIVED_TTL = 60.0
_archived_cache: dict[str, tuple[float, dict[str, str]]] = {}


def _archived_name(db_path: str) -> str:
    """Instance name out of an archived database path."""
    name = str(db_path).rsplit("/", 1)[-1]
    for suffix in (".sqlite", ".db"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


async def fetch_archived_paths(client: Any) -> dict[str, str]:
    """Instance name -> archived database path for every stopped bot on this server.

    The instance name *is* the join key back to a ``bot_runs`` row: a deployment
    writes ``bot_name == instance_name`` (hummingbot-api
    ``routers/bot_orchestration.py``) and archives the instance directory under
    that same name, so the two surfaces address the same run by the same string.

    Best-effort: a backend without the archived-bots endpoint, or one that errors,
    yields ``{}`` and the caller falls back to live-only discovery — which
    under-reports stopped bots but is never wrong about running ones.

    Cheap by construction — the upstream listing is a directory walk that opens no
    database — so this is safe to call on a polling route. The short TTL only keeps
    a per-tick rollup from re-listing on every call.
    """
    key = _server_key(client)
    entry = _archived_cache.get(key) if key else None
    if entry is not None and time.monotonic() - entry[0] <= _ARCHIVED_TTL:
        return entry[1]
    try:
        databases = await client.archived_bots.list_databases()
    except Exception as e:
        logger.debug("fetch_archived_paths failed: %s", e)
        return {}
    paths: dict[str, str] = {}
    for db in databases or []:
        if isinstance(db, str):
            path = db
        elif isinstance(db, dict):
            if db.get("status") == "error":
                continue
            path = db.get("db_path") or db.get("path") or ""
        else:
            continue
        name = _archived_name(path)
        if name:
            paths.setdefault(name, path)
    if key:
        _archived_cache[key] = (time.monotonic(), paths)
    return paths


async def fetch_archived_instances(client: Any) -> list[str]:
    """Names of stopped/archived bot instances on this server."""
    return sorted(await fetch_archived_paths(client))


def clear_archived_cache() -> None:
    """Drop the cached archived-instance listing (tests, server reconfiguration)."""
    _archived_cache.clear()


def _iso_to_epoch(ts: Any) -> float | None:
    from datetime import datetime

    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts)).timestamp()
    except ValueError:
        return None


# Sampling resolutions the performance-history endpoint accepts, finest first.
# A fixed "5m" only ever covered ``limit`` × 5min ≈ 41h, and _cum_at reads
# anything older than the retained window as zero — so every session whose window
# closed before that boundary reported $0 while the single session straddling it
# absorbed all of their PnL. The strategy total stayed right, which is exactly why
# it went unnoticed. Coarsening the interval to fit the span keeps one row per
# bucket across the whole ownership timeline instead.
_INTERVAL_LADDER: tuple[tuple[str, int], ...] = (
    ("5m", 300),
    ("15m", 900),
    ("1h", 3600),
    ("4h", 14400),
    ("1d", 86400),
)


def choose_interval(span_seconds: float, limit: int = 500) -> str:
    """Finest sampling interval whose buckets cover ``span_seconds`` within ``limit``.

    Boundary precision degrades to the chosen interval, so a handover is attributed
    to within one bucket. That is a bounded error; the truncation it replaces was
    unbounded.
    """
    for name, secs in _INTERVAL_LADDER:
        if span_seconds <= secs * max(limit, 1):
            return name
    return _INTERVAL_LADDER[-1][0]


def extract_history_rows(result: Any) -> list[dict]:
    """Rows out of one controller-performance-history page, whatever it is wrapped in."""
    rows = result.get("data", []) if isinstance(result, dict) else result
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


# One instance's history is walked to exhaustion, so the cap is on rows
# accumulated, not on iterations — the walker's own empty-page and cursor-progress
# guards end a stalled walk. ``limit`` is the per-page budget; the endpoint counts
# it in ROWS, and rows are per controller, so a single page holds only
# ``limit / num_controllers`` timestamps.
MAX_HISTORY_ROWS = 5000
HISTORY_PAGE_SIZE = 500


# ── Per-instance history cache ──
# The cursor walk is the expensive end of the pipeline — up to 10 sequential
# 500-row pages per instance, fanned out over up to MAX_HISTORY_INSTANCES
# instances — and every consumer walks the same rows: one session-detail request
# triggers it twice back-to-back (performance rollup, then PnL series), and the
# tick engine repeats it every tick while the dashboard polls the same strategy.
# The rows are identical across those call sites because the fetch takes no
# since/until — window slicing happens downstream — so a short TTL plus in-flight
# coalescing (the _snapshot_cache idiom above) collapses the redundancy while
# staying fresher than the 30s route-level cache layered on top. Bounded as an
# LRU: keys accumulate one entry per (server, instance, interval) and stopped
# instances would otherwise pin their pages forever.
_HISTORY_TTL = 20.0
_HISTORY_CACHE_MAX = 256
_history_cache: OrderedDict[
    tuple, tuple[float, list[tuple[float, float, float, float, float]]]
] = OrderedDict()
_history_inflight: dict[tuple, tuple[Any, asyncio.Task]] = {}


def clear_history_cache() -> None:
    """Drop every cached instance history (tests, server reconfiguration)."""
    _history_cache.clear()
    _history_inflight.clear()


async def fetch_instance_history(
    client: Any,
    instance_name: str,
    interval: str = "5m",
    limit: int = HISTORY_PAGE_SIZE,
    max_rows: int = MAX_HISTORY_ROWS,
) -> list[tuple[float, float, float, float, float]]:
    """Return one bot instance's cumulative history as sorted rows.

    Each row is ``(ts_epoch, cum_realized_quote, cum_volume, cum_trades, cum_fees)``
    — the bot-instance total, obtained by carrying each controller's own
    cumulative forward and summing the carried values at every sampled instant.
    ``cum_trades`` counts real closes (``close_type_counts`` minus retry/abort
    noise). ``cum_fees`` is taken only from a genuinely cumulative
    ``cum_fees_quote``; the per-open-position fees ``_aggregate_by_bot`` derives are
    a point-in-time figure whose differences are meaningless, so they are not used
    here and the column stays 0 when the backend omits the cumulative field (see
    :func:`slice_history`'s callers, which fall back in that case).

    The forward-carry is what makes this correct, and summing the rows that share
    a timestamp — as this did before — is what made it wrong. The endpoint returns
    roughly one row per bucket *in total*, not one per bucket per controller, so
    above its native resolution a multi-controller bot's controllers appear in
    round-robin: a three-controller bot sampled at 15m yields buckets holding BTC
    alone, then ETH alone. Summing per timestamp then reads each bucket as "the
    bot's total", producing a series that lurches between one controller's
    cumulative and another's — and since :func:`slice_history` differences that
    series, the result was not merely imprecise but arbitrary. Carrying each
    controller's last known cumulative forward reconstructs the true bot total
    from whatever subset each bucket happens to carry.

    Walks the endpoint's cursor to exhaustion (or ``max_rows``) rather than
    keeping whatever fits in one page. ``limit`` is a *row* budget and rows are
    per controller, so one page of 500 covers ~41h of 5m samples for a
    single-controller bot but only ~14h for a three-controller one. Anything older
    than the first retained row reads as zero through :func:`_cum_at`, so a
    truncated walk hands the earliest session's whole lifetime cumulative to
    whichever window straddles the boundary — the misattribution this module
    exists to prevent. :func:`choose_interval` bounds the number of *buckets* in
    the span; only paginating bounds the number of controllers per bucket.

    The controller performance API retains history for archived/stopped instances,
    so closed sessions can be attributed too. Resilient: returns ``[]`` on API
    error, including one raised part-way through the walk — a partial timeline is
    the same silent misattribution as a truncated one, so the caller degrades to
    "no history" instead of to "wrong history".

    Cached per ``(server, instance, interval, limit, max_rows)`` for
    ``_HISTORY_TTL`` seconds and coalesced while in flight, so concurrent callers
    share one cursor walk. A walk that raises is never cached — every waiter gets
    the ``[]`` degrade and the next call retries — and an unidentifiable client
    (no ``base_url``) bypasses the cache entirely, like the snapshot cache above.
    The returned rows are shared between callers and must be treated as
    read-only.
    """
    server = _server_key(client)
    if not server:
        try:
            return await _walk_instance_history(
                client, instance_name, interval, limit, max_rows
            )
        except Exception as e:
            logger.debug("fetch_instance_history(%s) failed: %s", instance_name, e)
            return []

    key = (server, instance_name, interval, limit, max_rows)
    entry = _history_cache.get(key)
    if entry is not None and time.monotonic() - entry[0] <= _HISTORY_TTL:
        _history_cache.move_to_end(key)
        return entry[1]

    # Reuse an in-flight walk only from the loop that created it: a task is
    # bound to its loop and awaiting it from another one raises.
    loop = asyncio.get_running_loop()
    inflight = _history_inflight.get(key)
    task = inflight[1] if inflight is not None and inflight[0] is loop else None
    if task is None:
        task = asyncio.ensure_future(
            _walk_instance_history(client, instance_name, interval, limit, max_rows)
        )
        _history_inflight[key] = (loop, task)
        task.add_done_callback(lambda _t, k=key: _history_inflight.pop(k, None))

    try:
        rows = await task
    except Exception as e:
        logger.debug("fetch_instance_history(%s) failed: %s", instance_name, e)
        return []

    _history_cache[key] = (time.monotonic(), rows)
    _history_cache.move_to_end(key)
    while len(_history_cache) > _HISTORY_CACHE_MAX:
        _history_cache.popitem(last=False)
    return rows


async def _walk_instance_history(
    client: Any,
    instance_name: str,
    interval: str,
    limit: int,
    max_rows: int,
) -> list[tuple[float, float, float, float, float]]:
    """One uncached cursor walk + forward-carry merge. Raises on API error."""

    def _warn_truncated() -> None:
        # Older buckets were dropped, and everything before the oldest retained
        # row reads as zero. Say so rather than returning a silently truncated
        # timeline.
        logger.warning(
            "fetch_instance_history(%s): hit the %d-row cap at interval %s — "
            "history is truncated and per-session attribution may shift",
            instance_name,
            max_rows,
            interval,
        )

    rows: list[dict] = await collect_pages(
        partial(
            client.bot_orchestration.get_controller_performance_history,
            bot_name=instance_name,
            interval=interval,
        ),
        extract_history_rows,
        page_size=limit,
        max_items=max_rows,
        on_truncated=_warn_truncated,
    )

    # One cumulative series per controller. A repeated (controller, timestamp)
    # keeps the last value read, which is what the endpoint means by re-reporting
    # a bucket.
    by_controller: dict[str, dict[float, tuple[float, float, float, float]]] = {}
    # Rows carrying no controller_id cannot be told apart by name, so the nth
    # anonymous row at a timestamp is treated as the nth controller. Without this
    # they would all collapse onto one key and overwrite each other, turning a
    # multi-controller bucket into whichever row happened to be read last.
    anon_seen: dict[float, int] = {}
    for r in rows:
        epoch = _iso_to_epoch(r.get("timestamp"))
        if epoch is None:
            continue
        cid = str(r.get("controller_id", "") or "")
        if not cid:
            n = anon_seen.get(epoch, 0)
            anon_seen[epoch] = n + 1
            cid = f"#{n}"
        perf = r.get("performance") or {}
        by_controller.setdefault(cid, {})[epoch] = (
            float(perf.get("realized_pnl_quote", 0) or 0),
            float(perf.get("volume_traded", 0) or 0),
            float(count_trade_closes(perf)),
            float(perf.get("cum_fees_quote", 0) or 0),
        )
    if not by_controller:
        return []

    series = {cid: sorted(samples.items()) for cid, samples in by_controller.items()}
    # Advanced monotonically with the merged timeline, so the whole carry is one
    # linear pass over the rows rather than a scan per controller per instant.
    cursor = {cid: -1 for cid in series}
    carried = {cid: (0.0, 0.0, 0.0, 0.0) for cid in series}

    out: list[tuple[float, float, float, float, float]] = []
    for epoch in sorted({e for samples in series.values() for e, _ in samples}):
        for cid, samples in series.items():
            i = cursor[cid]
            while i + 1 < len(samples) and samples[i + 1][0] <= epoch:
                i += 1
            if i != cursor[cid]:
                cursor[cid] = i
                carried[cid] = samples[i][1]
        realized = volume = trades = fees = 0.0
        for r_c, v_c, t_c, f_c in carried.values():
            realized += r_c
            volume += v_c
            trades += t_c
            fees += f_c
        out.append((epoch, realized, volume, trades, fees))
    return out


def _cum_at(
    history: list[tuple[float, float, float, float, float]], t: float
) -> tuple[float, float, float, float]:
    """Cumulative (realized, volume, trades, fees) at time ``t`` for one instance.

    Zero before the instance's first snapshot; its final value at/after the last.
    """
    if not history or t < history[0][0]:
        return (0.0, 0.0, 0.0, 0.0)
    chosen = history[0]
    for row in history:
        if row[0] <= t:
            chosen = row
        else:
            break
    return (chosen[1], chosen[2], chosen[3], chosen[4])


def slice_history(
    histories: list[list[tuple[float, float, float, float, float]]],
    start: float,
    end: float,
) -> tuple[float, float, float, float]:
    """Sum (realized, volume, trades, fees) generated in ``[start, end)``.

    Each instance contributes ``cum_at(end) − cum_at(start)`` — naturally zero for
    an instance that lies wholly outside the window, and exact for partial overlap.
    Because session windows tile the timeline, summing every session's slice
    reproduces each instance's full cumulative with no double counting.
    """
    realized = volume = trades = fees = 0.0
    for h in histories:
        r_e, v_e, t_e, f_e = _cum_at(h, end)
        r_s, v_s, t_s, f_s = _cum_at(h, start)
        realized += r_e - r_s
        volume += v_e - v_s
        trades += t_e - t_s
        fees += f_e - f_s
    return realized, volume, trades, fees


def slice_history_series(
    histories: list[list[tuple[float, float, float, float, float]]],
    start: float,
    stamps: list[float],
) -> list[tuple[float, float, float, float, float]]:
    """:func:`slice_history` evaluated at every instant of ascending ``stamps``.

    Identical output to ``[slice_history(histories, start, t) for t in stamps]``
    — same per-instance differencing in the same order — but computed in one
    merge pass: the ``cum_at(start)`` baseline is constant across stamps so it
    is taken once per instance, and each instance's cursor only ever advances
    as the stamps increase (the same carry idiom
    :func:`fetch_instance_history` uses internally). That turns the naive
    O(stamps × rows) rescan into O(stamps + rows) per instance, which matters
    on the per-tick and per-request paths that rebuild whole-session curves
    from near-cap histories.

    Returns ``[(t, realized, volume, trades, fees), …]``, one row per stamp.
    """
    bases = [_cum_at(h, start) for h in histories]
    cursors = [-1] * len(histories)
    carried: list[tuple[float, float, float, float]] = [(0.0, 0.0, 0.0, 0.0)] * len(
        histories
    )

    out: list[tuple[float, float, float, float, float]] = []
    for t in stamps:
        realized = volume = trades = fees = 0.0
        for i, h in enumerate(histories):
            j = cursors[i]
            while j + 1 < len(h) and h[j + 1][0] <= t:
                j += 1
            if j != cursors[i]:
                cursors[i] = j
                row = h[j]
                carried[i] = (row[1], row[2], row[3], row[4])
            r_e, v_e, t_e, f_e = carried[i]
            r_s, v_s, t_s, f_s = bases[i]
            realized += r_e - r_s
            volume += v_e - v_s
            trades += t_e - t_s
            fees += f_e - f_s
        out.append((t, realized, volume, trades, fees))
    return out


# One shared fetch of every owned base's instance histories, at a resolution that
# actually spans the ownership timeline. Both attribution callers go through this
# so the dashboard's per-session slice and the live agent's own view of its PnL
# are computed from identical inputs.
MAX_HISTORY_INSTANCES = 24
MAX_CONCURRENT_HISTORY_FETCHES = 10


async def fetch_base_histories(
    client: Any,
    all_bot_perf: dict[str, dict],
    bases: list[str],
    earliest: float,
    now: float,
    extra_names: Iterable[str] = (),
) -> dict[str, list[list[tuple[float, float, float, float, float]]]]:
    """``{base: [history per deployed instance]}`` covering ``[earliest, now]``.

    Fans out one bounded cursor walk per instance, at most
    ``MAX_CONCURRENT_HISTORY_FETCHES`` at a time. A fetch that raises degrades to
    the empty list :func:`fetch_instance_history` already returns on API error, so
    one bad instance costs its own history rather than the whole rollup.

    ``extra_names`` are instances to consider beyond the live snapshot — archived
    ones, whose realized PnL is exactly what a stopped bot contributes and is
    otherwise invisible.
    """
    instances_by_base = partition_instances(all_bot_perf, bases, extra_names)
    all_instances = sorted({i for lst in instances_by_base.values() for i in lst})
    if len(all_instances) > MAX_HISTORY_INSTANCES:
        logger.warning(
            "bot history: %d instances, capping at %d newest "
            "(older sessions may under-report)",
            len(all_instances),
            MAX_HISTORY_INSTANCES,
        )
        all_instances = all_instances[-MAX_HISTORY_INSTANCES:]

    # A zero/absent takeover instant means "unknown", not "epoch 0" — spanning
    # back to 1970 would coarsen every bot to daily buckets. Cap the span at what
    # the finest ladder rung can hold and let the cap warning speak if it bites.
    span = now - earliest if earliest > 0 else _INTERVAL_LADDER[0][1] * 500

    # Budget the ladder against the rows the walk can actually hold, not one page
    # of them. Rows are per (bucket, controller), so the *bucket* budget is the row
    # cap divided by the busiest instance's controller count. Costing a page meant
    # coarsening past ~41h of 5m samples when the walk could hold ten times that,
    # and every rung above the endpoint's native resolution loses controllers (see
    # :func:`fetch_instance_history`) — so staying fine longer is strictly better.
    controllers = max(
        (
            int(all_bot_perf.get(i, {}).get("num_controllers", 0) or 0)
            for i in all_instances
        ),
        default=1,
    )
    interval = choose_interval(
        max(span, 0.0), limit=max(1, MAX_HISTORY_ROWS // max(1, controllers))
    )
    if interval != _INTERVAL_LADDER[0][0] and controllers > 1:
        logger.warning(
            "bot history: %d-controller instances sampled at %s (span %.1fh) — "
            "above the endpoint's native resolution controllers are returned "
            "round-robin, so per-session figures are approximate",
            controllers,
            interval,
            span / 3600.0,
        )

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_HISTORY_FETCHES)

    async def _bounded(instance_name: str):
        async with semaphore:
            return await fetch_instance_history(
                client, instance_name, interval=interval
            )

    results = await asyncio.gather(
        *(_bounded(inst) for inst in all_instances), return_exceptions=True
    )
    history = {
        inst: [] if isinstance(rows, BaseException) else rows
        for inst, rows in zip(all_instances, results)
    }
    return {
        base: [history[k] for k in insts if k in history]
        for base, insts in instances_by_base.items()
    }


def bot_executor_rows(aggregate: dict) -> list[dict[str, Any]]:
    """Build executor-like display rows from a resolved bot aggregate.

    One row per open position (from each controller's ``positions_summary``), in
    the same shape ``condor.agents.performance._executor_row`` emits, so the web
    executors tab and the agent's core-data view render bot-mode positions the
    same way as direct executors. Realized PnL from already-closed positions is
    not row-level here (the snapshot only summarizes open positions); it is still
    reflected in the aggregate totals the caller applies.
    """
    from datetime import datetime

    ts_epoch = 0.0
    ts_iso = str(aggregate.get("timestamp", "") or "")
    if ts_iso:
        try:
            ts_epoch = datetime.fromisoformat(ts_iso).timestamp()
        except ValueError:
            ts_epoch = 0.0

    rows: list[dict[str, Any]] = []
    for ctrl in aggregate.get("controllers", []):
        controller_id = ctrl.get("controller_id", "")
        status = str(ctrl.get("status", "") or "").upper()
        for pos in ctrl.get("positions_summary", []):
            pair = pos.get("trading_pair", "")
            entry = float(pos.get("breakeven_price", 0) or 0)
            amount = float(pos.get("amount", 0) or 0)
            unrealized = float(pos.get("unrealized_pnl_quote", 0) or 0)
            rows.append(
                {
                    "id": f"{controller_id}:{pair}" if pair else controller_id,
                    "type": "controller",
                    "connector": pos.get("connector_name", ctrl.get("connector", "")),
                    "pair": pair,
                    "side": normalize_executor_side(pos.get("side")),
                    "status": status,
                    "close_type": "",
                    # Row PnL is the live (unrealized) mark of the open position;
                    # realized carries in the aggregate totals, not per-row.
                    "pnl": unrealized,
                    "volume": float(pos.get("volume_traded_quote", 0) or 0),
                    "fees": float(pos.get("cum_fees_quote", 0) or 0),
                    "entry_price": entry,
                    "current_price": 0.0,
                    "amount": abs(amount) * entry,
                    "timestamp": ts_epoch,
                    "close_timestamp": 0.0,
                    "controller_id": controller_id,
                    "custom_info": {},
                    "config": {},
                }
            )
    return rows
