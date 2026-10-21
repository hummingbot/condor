"""Fetch and manage executors via Hummingbot API."""

import asyncio
import logging
import time
from collections import OrderedDict
from functools import partial
from typing import Any, Dict, List, Optional

import aiohttp

from condor.asyncutil import SingleFlight
from condor.fetchers._pagination import collect_pages
from condor.fetchers._server import _server_key

logger = logging.getLogger(__name__)

# Cap on how many executors a single walk accumulates (not on iterations:
# the walker's own empty-page and cursor-progress guards end a stalled walk).
# A cap on *history*, because nothing upstream prunes terminated executor
# records: a host that has run many generations keeps every terminated row, so
# a walk that asks for no status spends the whole budget on them (PERF-733).
MAX_EXECUTORS_FETCH = 5000
EXECUTORS_PAGE_SIZE = 500

# Budget for one SDS poll tick. The EXECUTORS key is polled every 2s for every
# configured server, and SDS spends exactly one rate-limiter token per key per
# tick, so the poll must cost exactly one request: keep this at one page.
EXECUTORS_POLL_MAX = EXECUTORS_PAGE_SIZE


# ============================================
# EXTRACTION / PARSING HELPERS
# ============================================


def extract_executors_list(result: Any) -> list[dict]:
    """Extract executor list from various API response shapes."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("executors", "data", "results", "items"):
            if key in result and isinstance(result[key], list):
                return result[key]
    return []


def get_executor_type(executor: Dict[str, Any]) -> str:
    """Determine executor type from its data.

    Returns the executor type label (e.g. 'grid', 'position', 'order', 'dca', 'lp').

    Never raises: this runs while building every display row, so an executor
    whose ``config`` is explicitly ``null`` (a shape the backend does emit) must
    degrade to ``unknown`` for that one row instead of taking down the whole
    listing.
    """
    # ``config`` is often present *and* null, so a ``dict.get`` default is not
    # enough -- it only covers the absent key. Same isinstance guard as
    # :func:`build_executor_row`, falling back to the executor itself so a row
    # that carries its fields at the top level still resolves as before.
    config = executor.get("config")
    if not isinstance(config, dict):
        config = executor
    for source in (config, executor):
        ex_type = source.get("type", "") or source.get("executor_type", "")
        if isinstance(ex_type, str) and ex_type:
            label = (
                ex_type.lower()
                .replace("_executor", "")
                .replace("executor", "")
                .strip("_")
            )
            if label:
                return label
    if "start_price" in config and "end_price" in config:
        return "grid"
    if "stop_loss" in config or "trailing_stop" in config:
        return "position"
    return "unknown"


def normalize_executor_side(raw: Any) -> str:
    """Canonical ``BUY``/``SELL`` from any side encoding the API emits.

    The same logical side arrives in at least three shapes depending on which
    endpoint produced it: the enum's integer value (``1``/``2``), a bare word
    (``BUY``/``SELL``/``LONG``/``SHORT``), or a stringified enum
    (``TradeType.SELL``, ``PositionSide.LONG``). Every consumer funnels through
    here so no renderer has to guess -- the executor table used to carry a
    ``side === "1"`` fallback precisely because unnormalized values leaked to it.

    An unrecognized side is passed through uppercased rather than coerced, so a
    value we have never seen stays visible instead of silently rendering as a buy.
    """
    s = str(raw or "").strip().upper()
    # ``TradeType.SELL`` -> ``SELL``. Guarded on an alphabetic tail so a numeric
    # side is never split on its own decimal point (``1.0`` must not become ``0``).
    prefix, _, tail = s.rpartition(".")
    if prefix and tail.isalpha():
        s = tail
    if s in ("1", "BUY", "LONG"):
        return "BUY"
    if s in ("2", "SELL", "SHORT"):
        return "SELL"
    return s


def get_executor_pnl(executor: Dict[str, Any]) -> float:
    """Extract PnL from an executor response."""
    for key in (
        "net_pnl_quote",
        "pnl_quote",
        "unrealized_pnl_quote",
        "realized_pnl_quote",
        "net_pnl",
        "pnl",
        "close_pnl",
    ):
        val = executor.get(key)
        if val is not None and val != 0:
            return float(val)
    return 0.0


#: The only fields that mean *traded volume* — the quote value of the fills an
#: executor has actually done. The list is an allow-list on purpose: a position
#: field looks just as numeric, and an executor carries several that are signed.
#: Grid, for one, reports ``position_size_base`` as ``executed − close_executed``
#: and ``position_size_quote`` priced off it, so both go negative once the close
#: order fills past the opens. Reading one of those as volume is how a negative
#: volume reached an agent's journal ([[CORR-731]]).
TRADED_VOLUME_KEYS = ("filled_amount_quote", "volume_traded", "total_volume")


def get_executor_volume(executor: Dict[str, Any]) -> float:
    """Extract filled/traded volume from an executor response.

    Reads only :data:`TRADED_VOLUME_KEYS`, and as a magnitude: volume is a sum
    of fills, so the sign of whatever arrived in the field is never information
    about it. A backend that hands back a signed figure yields that trade's
    size, not a negative volume that the surfaces above would then total
    ([[CORR-731]]).
    """
    for key in TRADED_VOLUME_KEYS:
        val = executor.get(key)
        if val is not None and val != 0:
            try:
                return abs(float(val))
            except (TypeError, ValueError):
                logger.debug(
                    "executor %s: %s is not a number: %r",
                    executor.get("id") or executor.get("executor_id") or "?",
                    key,
                    val,
                )
                return 0.0
    return 0.0


def get_executor_fees(executor: Dict[str, Any]) -> float:
    """Extract cumulative fees from an executor response."""
    for key in ("cum_fees_quote", "fees_quote", "total_fees"):
        val = executor.get(key)
        if val is not None and val != 0:
            return float(val)
    return 0.0


def _quote_size(
    ex: Dict[str, Any],
    cfg: Dict[str, Any],
    custom_info: Dict[str, Any],
    entry_price: float,
    current_price: float,
) -> float:
    return _quote_size_checked(ex, cfg, custom_info, entry_price, current_price)[0]


def _quote_size_checked(
    ex: Dict[str, Any],
    cfg: Dict[str, Any],
    custom_info: Dict[str, Any],
    entry_price: float,
    current_price: float,
) -> tuple[float, bool]:
    """An executor's size in QUOTE currency -- the row's ``amount``.

    Each executor type sizes itself in a different field and a different unit:

    - grid: ``total_amount_quote``, already quote.
    - dca: the ``amounts_quote`` ladder, already quote.
    - position / order: ``amount`` in BASE currency, priced at the entry (or the
      order's limit ``price``, or the live price for a market order) -- the same
      base-times-price rule the risk gate prices a create with
      (``condor.agents.risk._planned_amount_quote``), so the book it reads next
      tick agrees with what it approved.
    - lp: ``custom_info.total_value_quote``, the position's current value.

    A base ``amount`` with no price to value it at falls back to the quote the
    executor reports as filled, and otherwise to 0: it is never returned as-is.
    Summed as exposure, a base figure made 2.9 LTC read as $2.90, so a $0.70
    loss on a ~$200 position read as a 24% drawdown and tripped the kill switch.
    That 0 means "not priced here", not "no exposure": a caller that has a
    client prices such a row at the live price with
    :func:`price_pending_rows`, below.

    ``known`` separates the two meanings that 0 used to carry, because only one
    of them is resolvable downstream. It is ``False`` only when *none* of the
    fields above was present at all -- an lp with no ``total_value_quote``, a
    grid with no ``total_amount_quote``, a dca with an empty ladder. Such a row
    has no size anyone can compute, so a caller summing exposure must report it
    unknown rather than fold it in at $0 (CORR-740); ``price_pending_rows``
    cannot rescue it, since the only live source here is a *pair's* price and
    none of these types is denominated in base.

    A field present and zero is known-zero: an lp position withdrawn but not
    closed reports ``total_value_quote: 0``, and that is a fact about the
    position, the same distinction the display layer already draws between
    ``$0.00`` and an em-dash (``frontend/src/components/dex/format.ts``).

    The ``(value, known)`` sibling with the plain form delegating to it is the
    repo's own shape for an unreadable metric, after
    :meth:`condor.agents.journal.JournalManager.get_drawdown_pct_checked`.
    """
    ladder = cfg.get("amounts_quote")
    # Presence, not truth: every branch below reads one of these, so if all of
    # them are absent the function had nothing to size the row from.
    known = (
        cfg.get("total_amount_quote") is not None
        or (isinstance(ladder, list) and bool(ladder))
        or cfg.get("amount") is not None
        or custom_info.get("total_value_quote") is not None
        or ex.get("filled_amount_quote") is not None
    )
    total = float(cfg.get("total_amount_quote") or 0)
    if total > 0:
        return total, known
    if isinstance(ladder, list) and ladder:
        try:
            total = sum(float(v or 0) for v in ladder)
        except (TypeError, ValueError):
            total = 0.0
        if total > 0:
            return total, known
    base = float(cfg.get("amount") or 0)
    if base > 0:
        price = entry_price or float(cfg.get("price") or 0) or current_price
        if price > 0:
            return base * price, known
        return float(ex.get("filled_amount_quote") or 0), known
    return float(custom_info.get("total_value_quote") or 0), known


def _pending_base(row: Dict[str, Any]) -> float:
    """The BASE amount :func:`price_pending_rows` would value ``row`` from."""
    try:
        return float((row.get("config") or {}).get("amount") or 0)
    except (TypeError, ValueError):
        return 0.0


def _size_unknown(row: Dict[str, Any]) -> bool:
    """Whether :func:`build_executor_row` found no sizing field for ``row``.

    Read off the row rather than re-derived from its ``config``: the field list
    belongs to :func:`_quote_size_checked`, which is also the only place that
    can see ``filled_amount_quote``, and the last time this transform was
    written twice the two copies drifted (see :func:`build_executor_row`).

    Both builders of this shape set it: this module's
    :func:`build_executor_row` from a raw executor's sizing fields, and
    ``bot_performance.bot_executor_rows`` from the two fields its controller
    row's size is derived from (CORR-742). On a row from anywhere else absence
    still means "not this helper's business", the behaviour it had before.
    """
    return not row.get("amount_known", True)


def unpriced_row_ids(rows: List[Dict[str, Any]]) -> List[str]:
    """Ids :func:`price_pending_rows` would leave of unknown size.

    The verdict it reaches with every price lookup failing, so a caller whose
    ``except`` arm has no return value from it names the same set its success
    arm would. The two used to disagree -- the failure arm reported every
    unvalued row and the success arm only the base-denominated ones (CORR-740).
    """
    return [
        row.get("id") or "?"
        for row in rows
        if not float(row.get("amount") or 0) > 0
        and (_pending_base(row) > 0 or _size_unknown(row))
    ]


async def price_pending_rows(client: Any, rows: List[Dict[str, Any]]) -> List[str]:
    """Value the rows :func:`build_executor_row` could not, at the live price.

    Two shapes arrive here unvalued, and only one of them can be priced. A
    market order still pending at the next tick reports no entry, no limit price
    and no fill, so ``build_executor_row`` -- pure, no client -- sizes it at 0;
    the risk gate priced that same create at the live price before approving it
    (``condor.agents.risk._planned_amount_quote``), and dropping it from the
    book one tick later let a second create through a position cap the two
    together exceed. So it is priced here the way the gate priced it.

    The other is an executor whose own sizing field is absent: an lp with no
    ``total_value_quote``, a grid with no ``total_amount_quote``, a dca with an
    empty ladder. None of those is denominated in base and the only live source
    here is a pair's price, so pricing cannot be the answer -- reporting can.
    Such a row is appended to the returned ids unpriced instead of entering the
    book at $0 and being counted as valued (CORR-740).

    A row whose sizing field was present and zero is neither: it is worth zero
    and says so, and refusing creates over it would refuse them for every
    withdrawn-but-open lp position.

    Lives beside :func:`_quote_size`, whose 0 it exists to resolve, because two
    callers need it: the agents' executors provider over its own fetched rows,
    and the tick's executor adoption over the orphans it inherits (CORR-735) --
    rows that never pass through the provider at all, since the provider fetches
    under this session's own controller tag.

    Rows are updated in place. Returns the ids whose exposure is still unknown,
    not zero, and the caller must say so.
    """
    from condor.fetchers.market_data import fetch_current_price

    prices: Dict[tuple[str, str], float] = {}
    unpriced: List[str] = []
    for row in rows:
        if row.get("amount", 0) > 0:
            continue
        base = _pending_base(row)
        if base <= 0:
            if _size_unknown(row):
                unpriced.append(row.get("id") or "?")
            continue
        market = (row.get("connector") or "", row.get("pair") or "")
        if market not in prices:
            try:
                prices[market] = float(await fetch_current_price(client, *market) or 0)
            except (TypeError, ValueError):
                prices[market] = 0.0
        if prices[market] > 0:
            row["amount"] = base * prices[market]
        else:
            unpriced.append(row.get("id") or "?")
    return unpriced


def build_executor_row(ex: Dict[str, Any]) -> Dict[str, Any]:
    """Canonical display row for one raw executor.

    Single source of truth for the raw-executor -> display-row transform, the
    same role :func:`condor.fetchers.bots.build_bots_page` plays for bots. Three
    layers render executors -- the REST route (via
    :meth:`condor.web.models.ExecutorInfo.from_raw`), the WS broadcast, and the
    agents performance rollup -- and the transform used to be written twice, once
    per package. The two copies had already drifted on ``current_price`` with
    neither a superset of the other, so the *same* executor showed a live price in
    the executors tab and ``0`` in the agent's session view. Both chains live here
    now, unioned, and nowhere else.

    Pure: no client, no caching, no UI. The keys are the agent-row shape that
    :func:`condor.fetchers.bot_performance.bot_executor_rows` also emits and the
    agents API serves; ``ExecutorInfo.from_raw`` renames them for the REST/WS
    wire. ``amount_known`` is set here from a raw executor's sizing fields and
    there from the two a controller position's size is derived from; on a row
    from any other builder its absence means the same as ``True``
    (:func:`_size_unknown`). Casing of ``status`` and ``close_type`` is the callers' business -- they
    are returned as the API spelled them, only stringified.
    """
    config = ex.get("config")
    cfg = config if isinstance(config, dict) else ex
    custom_info = ex.get("custom_info")
    if not isinstance(custom_info, dict):
        custom_info = {}

    # entry_price is a display-only field; PnL comes straight from the executor's
    # reported fields via get_executor_pnl() and never depends on it.
    # Only position executors carry a real entry_price (config > top-level > custom_info).
    # Grid/DCA executors expose break_even_price instead; use it as the display "entry".
    _cfg_entry = float(cfg.get("entry_price") or 0)
    _top_entry = float(ex.get("entry_price") or 0)
    _ci_entry = float(custom_info.get("current_position_average_price") or 0)
    _be_price = float(custom_info.get("break_even_price") or 0)
    entry_price = _cfg_entry or _top_entry or _ci_entry or _be_price or 0.0

    # current_price: the union of the two chains that used to live apart. A
    # position executor marks itself in custom_info.current_price, a closed one
    # only has custom_info.close_price, and an order executor keeps its fill price
    # in held_position_orders -- each copy knew about some of those, so whichever
    # copy built the row decided whether a price appeared at all.
    _held_price = 0.0
    held_orders = custom_info.get("held_position_orders")
    if isinstance(held_orders, list) and held_orders:
        try:
            _held_price = float(held_orders[-1].get("price") or 0)
        except (TypeError, ValueError, AttributeError):
            pass
    current_price = 0.0
    for _candidate in (
        float(ex.get("current_price") or 0),
        float(custom_info.get("current_price") or 0),
        float(custom_info.get("close_price") or 0),
        _held_price,
    ):
        if _candidate > 0:
            current_price = _candidate
            break

    # Computed once, here, where the fields are read: ``amount_known`` is the
    # difference between "worth nothing" and "nothing could size this", and a
    # consumer that re-derived it from the row would be the second copy of
    # ``_quote_size``'s field list (CORR-740).
    amount, amount_known = _quote_size_checked(
        ex, cfg, custom_info, entry_price, current_price
    )

    return {
        "id": str(ex.get("id") or ex.get("executor_id") or ""),
        "type": cfg.get("type") or ex.get("type") or "",
        # ``config`` describes the executor authoritatively, so its own
        # ``connector`` outranks the top-level WS-format shorthand. The web copy
        # skipped ``config.connector`` entirely and rendered a blank connector
        # for the rows that only carry it.
        "connector": cfg.get("connector_name")
        or ex.get("connector_name")
        or cfg.get("connector")
        or ex.get("connector")
        or "",
        "pair": cfg.get("trading_pair") or ex.get("trading_pair") or "",
        # custom_info first: that is where a position executor carries its side.
        "side": normalize_executor_side(
            custom_info.get("side") or cfg.get("side") or ex.get("side")
        ),
        "status": str(ex.get("status") or ""),
        "close_type": str(ex.get("close_type") or ""),
        "pnl": get_executor_pnl(ex),
        "volume": get_executor_volume(ex),
        "fees": get_executor_fees(ex),
        "entry_price": entry_price,
        "current_price": current_price,
        "amount": amount,
        "amount_known": amount_known,
        "timestamp": float(cfg.get("timestamp") or ex.get("timestamp") or 0),
        "close_timestamp": float(ex.get("close_timestamp") or 0),
        # Top-level first, and that is not a style choice. The creator's
        # ``controller_id`` -- the tag the risk gate attributed the position to --
        # is stored on the executor *record*; the MCP create path pops it out of
        # the config precisely so it "never travels inside the config"
        # (``executor_create.create_executor``). What is left in ``config`` is the
        # backend schema's own default, "main". Reading the config first made an
        # agent's own executor come back owned by "main", which dropped it from
        # the session ledger and made the run warn that it had deployed something
        # it did not own (CORR: unledgered deployment).
        "controller_id": str(ex.get("controller_id") or cfg.get("controller_id") or ""),
        "custom_info": custom_info,
        "config": config if isinstance(config, dict) else {},
    }


def executor_quote(pair: str) -> str:
    """Quote asset an executor's PnL and volume are denominated in.

    A pair with no quote segment (or none at all) falls back to ``USDT``, which
    is what the API reports for the overwhelming majority of markets and what the
    KPI strip assumed before the totals moved server-side.
    """
    quote = (pair or "").split("-")[1:2]
    return (quote[0] or "USDT").upper() if quote else "USDT"


def summarize_executors_by_quote(
    executors: List[Dict[str, Any]], since: float
) -> Dict[str, Dict[str, float]]:
    """Period totals for executors started at or after ``since``, per quote asset.

    Deliberately currency-blind: PnL and volume are quote-denominated, and two
    executors on ``BTC-USDT`` and ``ETH-BTC`` cannot be added without a rate. The
    caller owns that conversion, so this stays pure and the rate lookup happens
    once per quote instead of once per executor.

    ``since`` is compared against the executor's *start* timestamp, the same
    filter the KPI strip applied client-side before this moved server-side: an
    executor opened before the window is outside it even if it is still running.

    Returns:
        ``{quote: {"pnl": float, "volume": float, "count": float}}``
    """
    totals: Dict[str, Dict[str, float]] = {}
    for ex in executors:
        row = build_executor_row(ex)
        if row["timestamp"] < since:
            continue
        bucket = totals.setdefault(
            executor_quote(row["pair"]), {"pnl": 0.0, "volume": 0.0, "count": 0.0}
        )
        bucket["pnl"] += row["pnl"]
        bucket["volume"] += row["volume"]
        bucket["count"] += 1
    return totals


# ============================================
# API FETCHERS
# ============================================

# ── Shared walk cache ──
# One agent tick asked the venue for its RUNNING executors twice, microseconds
# apart and byte-identically: the adoption pass (``agents/engine.py``) walks
# them immediately before ``run_core_providers``, and the drift provider walks
# the same set as one of its four gathered reads. Both are right to ask every
# tick -- a sibling session can end at any point, and the venue answers for the
# whole account -- so the fix is to ask once, not less often (PERF-736).
#
# A short TTL plus in-flight coalescing, the ``bot_performance._snapshot_cache``
# idiom: the TTL covers the two sequential calls, the coalescing covers the
# drift provider's ``asyncio.gather`` racing a walk adoption already started.
# The entry is stamped when the rows are *inserted*, never before the walk
# starts, so a walk slower than the TTL cannot write an already-stale entry
# (CORR-584). A walk that raises is never cached: the exception reaches every
# waiter and the next call retries.
#
# The TTL is an order of magnitude under the fastest shipped loop cadence
# (``frequency_sec`` 60), so no tick is ever served the previous tick's book.
# It also collapses the boot scan's one walk per settled strategy
# (``runtime/loops.py``) for strategies sharing a server, which is sound: the
# rows are the same question and each strategy's own ``live`` set is applied to
# them afterwards.
#
# Bounded as an LRU because the key carries the filter set, and the executors
# route turns user-supplied filters into keys -- an unbounded dict would let a
# scripted scroll pin arbitrarily many 500-row pages for the TTL.
#
# Every list handed back is shared between callers and must be treated as
# **read-only**. Today's consumers all copy what they keep
# (``orphaned_running_rows`` appends ``dict(row)``, ``build_executor_row``
# returns a fresh dict, ``venue_drift.tracked_from_active`` only reads), but
# :func:`price_pending_rows`, in this module, mutates rows in place on a
# neighbouring path, and a caller that did that to a cached list would corrupt
# the other consumer's view of the same rows.
_WALK_TTL = 2.0
_WALK_CACHE_MAX = 64
_walk_cache: OrderedDict[tuple, tuple[float, list[dict]]] = OrderedDict()
_walk_inflight = SingleFlight()


def _walk_key(client: Any, max_items: int, filters: Dict[str, Any]) -> Optional[tuple]:
    """Cache key for one walk, or ``None`` when this walk must not be cached.

    The filter set and ``max_items`` are in the key, not just the server: the
    executors route's unfiltered page walk and the agents' ``status="RUNNING"``
    walk ask the same server different questions, and serving either one the
    other's rows would be silent corruption rather than staleness. Filter values
    go in as ``repr`` because a route filter can be a list (``executor_types``,
    ``trading_pairs``), which is not hashable.

    A client with no ``base_url`` bypasses the cache entirely, like every other
    cache in this package: it cannot be told apart from any other client.
    """
    server = _server_key(client)
    if not server:
        return None
    return (
        server,
        max_items,
        tuple(sorted((name, repr(value)) for name, value in filters.items())),
    )


def clear_executor_cache() -> None:
    """Drop every cached executor walk (tests, server reconfiguration)."""
    _walk_cache.clear()
    _walk_inflight.clear()


async def _walk_executors(
    client, max_items: int, filters: Dict[str, Any]
) -> list[dict]:
    """The uncached cursor walk behind :func:`fetch_all_executors`."""
    return await collect_pages(
        partial(client.executors.search_executors, **filters),
        extract_executors_list,
        page_size=EXECUTORS_PAGE_SIZE,
        max_items=max_items,
    )


async def fetch_executors(client, **_kw) -> list[dict]:
    """Poll variant: the most recent page of executors, in one request.

    This is what SDS registers for ``ServerDataType.EXECUTORS`` and polls every
    2s per server, so it is deliberately bounded to ``EXECUTORS_POLL_MAX``
    instead of walking the whole history: one tick, one request, matching the
    single rate-limiter token SDS spends on it. Callers that need more than the
    newest page (history exports, filtered searches) call ``fetch_all_executors``
    directly with their own ``max_items``.

    Deliberately *not* cached, unlike ``fetch_all_executors``: this is the only
    caller whose cadence is the cache's own TTL, so an entry would be live for
    about as long as the gap between two polls and roughly every second tick
    would be served the previous tick's page -- halving the freshness of the
    live dashboard to collapse a duplicate that does not exist, since nothing
    else asks this question at this instant. SDS's own per-key cache is the
    sharing layer for this path.
    """
    return await _walk_executors(client, EXECUTORS_POLL_MAX, {})


async def fetch_all_executors(
    client, max_items: int = MAX_EXECUTORS_FETCH, **filters
) -> list[dict]:
    """Full-history variant: walk the cursor across pages, on demand.

    Walks the cursor until exhausted or ``max_items`` is reached. Not for the
    hot poll — each call can issue up to ``max_items / EXECUTORS_PAGE_SIZE``
    sequential requests that the SDS rate limiter cannot see.

    Deliberately unfiltered by default: the history and orphan paths *want*
    terminated rows (the period summary totals closed PnL, and a terminated
    executor is what strands an on-chain position). ``**filters`` are bound into
    the partial, so they reach ``search_executors`` on *every* page of the walk
    and not just the first — which is why a caller that wants only live
    executors passes ``status=`` rather than walking the whole history and
    dropping the terminal rows afterwards. Terminated records are never pruned
    upstream, so the unfiltered walk pays for history a live-only caller
    discards, and a host with more than ``max_items`` of it would have its live
    rows truncated away before they were ever returned (PERF-733). The API
    filters on one status at a time, so "live" is one walk per status —
    ``RUNNING``, plus ``SHUTTING_DOWN`` where an executor that is still
    flattening counts as live (``providers/drift.py``).

    Cached per ``(server, max_items, filter set)`` for ``_WALK_TTL`` seconds and
    coalesced while in flight, so the two consumers that walk the same RUNNING
    set in one agent tick share one walk instead of asking twice (PERF-736). An
    unidentifiable client (no ``base_url``) bypasses the cache. **The returned
    list is shared between callers and must be treated as read-only** -- see the
    cache's own comment above.
    """
    key = _walk_key(client, max_items, filters)
    if key is None:
        return await _walk_executors(client, max_items, filters)

    entry = _walk_cache.get(key)
    if entry is not None and time.monotonic() - entry[0] <= _WALK_TTL:
        _walk_cache.move_to_end(key)
        return entry[1]

    rows = await _walk_inflight.run(
        key, lambda: _walk_executors(client, max_items, filters)
    )
    _walk_cache[key] = (time.monotonic(), rows)
    _walk_cache.move_to_end(key)
    while len(_walk_cache) > _WALK_CACHE_MAX:
        _walk_cache.popitem(last=False)
    return rows


def describe_executor_error(exc: BaseException) -> tuple[Optional[int], str]:
    """Split a mutation failure into ``(upstream status, user-safe message)``.

    Never show a user ``str(exc)``: ``aiohttp.ClientResponseError`` stringifies
    with the backend's own URL in it, so a trader on a shared server would read
    the internal host and port off a failed deploy. The pieces that are safe to
    surface live in the attributes — ``status`` is the code the API answered
    with and ``message`` is the API's own ``detail`` — and a transport failure,
    which has neither, collapses to a generic line.

    A ``None`` status means "no HTTP answer": the caller maps that to 502.
    """
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        message = getattr(exc, "message", None)
        if isinstance(message, str) and message.strip():
            return status, message.strip()
        return status, f"the trading API returned HTTP {status}"
    if isinstance(exc, (aiohttp.ClientError, asyncio.TimeoutError, OSError)):
        return None, "the trading API is unreachable"
    return None, type(exc).__name__


async def create_executor(
    client, config: Dict[str, Any], account_name: str = "master_account"
) -> Dict[str, Any]:
    """Create a new executor. Raises on failure.

    Deliberately does not translate a failure into a ``{"status": "error"}``
    dict: that envelope is shaped like a successful response, so callers ended
    up guessing at success by substring ("created" appears in *"could not be
    created"*) and pasting the raw exception into user-facing text. Failure is
    an exception here, as in the other fetchers; callers classify it once at
    their own boundary with :func:`describe_executor_error`.
    """
    try:
        return await client.executors.create_executor(
            executor_config=config, account_name=account_name
        )
    except Exception as e:
        logger.error("Error creating executor: %s", e, exc_info=True)
        raise


async def stop_executor(
    client, executor_id: str, keep_position: bool = False
) -> Dict[str, Any]:
    """Stop a running executor. Raises on failure.

    See :func:`create_executor` for why a failure is not returned as a dict.
    The old HTTP-code-in-the-message classification is gone with it: it matched
    ``"400"`` against any executor id that happened to contain those digits.
    """
    try:
        return await client.executors.stop_executor(
            executor_id=executor_id, keep_position=keep_position
        )
    except Exception as e:
        logger.error("Error stopping executor: %s", e, exc_info=True)
        raise


async def get_executor_detail(client, executor_id: str) -> Optional[Dict[str, Any]]:
    """Get details for a specific executor."""
    try:
        return await client.executors.get_executor(executor_id=executor_id)
    except Exception as e:
        logger.error("Error getting executor detail: %s", e, exc_info=True)
        return None
