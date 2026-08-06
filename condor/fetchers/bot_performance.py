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
from functools import partial
from typing import Any

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
                "positions_summary": positions,
            }
        )
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
    all_bot_perf: dict[str, dict], bases: list[str]
) -> dict[str, list[str]]:
    """Assign every deployed instance to the LONGEST owned base that prefixes it.

    Prefix-matching one base at a time cannot tell a *tag* from a *deploy
    timestamp*, so a parent swallows its tagged siblings: ``brigado-ema_trend``
    prefix-matches ``brigado-ema_trend-btc-20260731-101500`` as readily as
    ``brigado-ema_trend-btc`` does. Deciding over ALL owned bases at once settles
    it — that instance lands on ``…-btc``, the longest base that prefixes it — so
    each instance's PnL is counted under exactly one base and a session operating
    several bots gets a truthful sum.

    Returns one entry per base (possibly empty), each instance list ordered oldest
    first.
    """
    out: dict[str, list[str]] = {b: [] for b in bases if b}
    if not all_bot_perf or not out:
        return out
    longest_first = sorted(out, key=len, reverse=True)
    for name in all_bot_perf:
        for base in longest_first:
            if name == base or name.startswith(f"{base}-"):
                out[base].append(name)
                break
    for names in out.values():
        names.sort(key=lambda k: (str(all_bot_perf[k].get("timestamp", "")), k))
    return out


def resolve_bots(all_bot_perf: dict[str, dict], bases: list[str]) -> dict[str, dict]:
    """Live aggregate per base, partition-aware.

    A bot deploys under an instance name with a timestamp suffix appended
    (``dn-CL-BRENTOIL-mm`` → ``dn-CL-BRENTOIL-mm-20260724-182221``), while the
    strategy config only knows the stable base name. Each base resolves to the
    exact name if it was deployed under it, else to its freshest instance (ISO
    ``timestamp`` strings sort chronologically, so a re-launched bot resolves to
    its most recent deploy). Applying that rule within each base's own partition
    means an owned parent never resolves to a sibling's instance and two owned
    bases never hand back the same aggregate twice. Bases with no deployed
    instance are absent from the result.
    """
    out: dict[str, dict] = {}
    for base, insts in partition_instances(all_bot_perf, bases).items():
        if not insts:
            continue
        out[base] = all_bot_perf[base if base in insts else insts[-1]]
    return out


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


async def fetch_instance_history(
    client: Any,
    instance_name: str,
    interval: str = "5m",
    limit: int = HISTORY_PAGE_SIZE,
    max_rows: int = MAX_HISTORY_ROWS,
) -> list[tuple[float, float, float, float, float]]:
    """Return one bot instance's cumulative history as sorted rows.

    Each row is ``(ts_epoch, cum_realized_quote, cum_volume, cum_trades, cum_fees)``
    with the per-controller snapshots at each timestamp summed to a bot-instance
    total, so a single- and multi-controller bot are handled identically.
    ``cum_trades`` counts real closes (``close_type_counts`` minus retry/abort
    noise). ``cum_fees`` is taken only from a genuinely cumulative
    ``cum_fees_quote``; the per-open-position fees ``_aggregate_by_bot`` derives are
    a point-in-time figure whose differences are meaningless, so they are not used
    here and the column stays 0 when the backend omits the cumulative field (see
    :func:`slice_history`'s callers, which fall back in that case).

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
    """

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

    try:
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
    except Exception as e:
        logger.debug("fetch_instance_history(%s) failed: %s", instance_name, e)
        return []

    by_ts: dict[str, list[float]] = {}
    for r in rows:
        ts = r.get("timestamp")
        perf = r.get("performance") or {}
        realized = float(perf.get("realized_pnl_quote", 0) or 0)
        volume = float(perf.get("volume_traded", 0) or 0)
        trades = count_trade_closes(perf)
        fees = float(perf.get("cum_fees_quote", 0) or 0)
        acc = by_ts.setdefault(str(ts), [0.0, 0.0, 0.0, 0.0])
        acc[0] += realized
        acc[1] += volume
        acc[2] += trades
        acc[3] += fees

    out: list[tuple[float, float, float, float, float]] = []
    for ts, (realized, volume, trades, fees) in by_ts.items():
        epoch = _iso_to_epoch(ts)
        if epoch is not None:
            out.append((epoch, realized, volume, trades, fees))
    out.sort()
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
) -> dict[str, list[list[tuple[float, float, float, float, float]]]]:
    """``{base: [history per deployed instance]}`` covering ``[earliest, now]``.

    Fans out one bounded cursor walk per instance, at most
    ``MAX_CONCURRENT_HISTORY_FETCHES`` at a time. A fetch that raises degrades to
    the empty list :func:`fetch_instance_history` already returns on API error, so
    one bad instance costs its own history rather than the whole rollup.
    """
    instances_by_base = partition_instances(all_bot_perf, bases)
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
    interval = choose_interval(max(span, 0.0))

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
