"""Emergency shutdown -- declarative winddown policy for a strategy.

When a strategy hits a kill-switch (a hard risk breach or a manual emergency
stop) its open **positions and executors** must be wound down, not left stranded.
The policy is declared per strategy in a ``shutdown.md`` file that reuses the exact
YAML-frontmatter + markdown-body format of ``loop.md`` and the same
strategy-over-agent-over-default inheritance chain:

    agents/{slug}/loops/{sslug}/shutdown.md   # this strategy
    agents/{slug}/shutdown.md                       # this agent (all its strategies)
    agents/_defaults/shutdown.md                    # shipped default

The front-matter is a machine-executable policy the deterministic winddown reads;
the body is free-form instructions handed to the bounded LLM cleanup pass.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from condor.fetchers.tracked_positions import fetch_tracked_positions
from condor.runtime.timeouts import resolve_tick_timeout

from .strategy import LOOPS_DIRNAME, Strategy

log = logging.getLogger(__name__)

# on_kill_switch policy values. Default matches the user's framing of a kill
# switch: drop the dangerous leveraged risk (perp) without force-selling spot.
POLICY_FLATTEN_ALL = "flatten_all"
POLICY_KEEP_SPOT_CLOSE_PERP = "keep_spot_close_perp"
POLICY_KEEP_ALL = "keep_all"
VALID_POLICIES = (POLICY_FLATTEN_ALL, POLICY_KEEP_SPOT_CLOSE_PERP, POLICY_KEEP_ALL)
DEFAULT_POLICY = POLICY_KEEP_SPOT_CLOSE_PERP


class ShutdownPolicy:
    """Machine-executable winddown policy parsed from ``shutdown.md`` front-matter."""

    def __init__(
        self,
        on_kill_switch: str = DEFAULT_POLICY,
        cancel_open_orders: bool = True,
    ):
        self.on_kill_switch = on_kill_switch
        self.cancel_open_orders = cancel_open_orders

    @classmethod
    def from_dict(cls, d: dict) -> "ShutdownPolicy":
        policy = str((d or {}).get("on_kill_switch", DEFAULT_POLICY)).strip()
        if policy not in VALID_POLICIES:
            log.warning(
                "Unknown shutdown policy %r; falling back to %s", policy, DEFAULT_POLICY
            )
            policy = DEFAULT_POLICY
        return cls(
            on_kill_switch=policy,
            cancel_open_orders=bool((d or {}).get("cancel_open_orders", True)),
        )

    def to_dict(self) -> dict:
        return {
            "on_kill_switch": self.on_kill_switch,
            "cancel_open_orders": self.cancel_open_orders,
        }

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"ShutdownPolicy(on_kill_switch={self.on_kill_switch!r}, "
            f"cancel_open_orders={self.cancel_open_orders})"
        )


def load_shutdown_policy(strategy: Strategy) -> tuple[ShutdownPolicy, str]:
    """Resolve the shutdown policy + LLM body for ``strategy``.

    Walks strategy → agent → shipped default, returning the first ``shutdown.md``
    found, and consults **both roots at each level** (local before stock), so an
    install that wrote its own policy shadows the shipped one for that level and
    nothing else. It used to reach the defaults by walking *up* from
    ``strategy.dir``, which with two roots would land in whichever layer the
    strategy happened to resolve from. If nothing is on disk, returns the
    built-in default policy with an empty body.
    """
    from condor.memory.paths import read_layered_file

    # No skip_empty_body: a frontmatter-only shutdown.md *is* a policy.
    found = read_layered_file(
        "shutdown.md",
        strategy.agent_slug,
        within=(LOOPS_DIRNAME, strategy.slug),
    )
    if found is None:
        return ShutdownPolicy(), ""
    meta, body = found
    return ShutdownPolicy.from_dict(meta), body


# ---------------------------------------------------------------------------
# Deterministic winddown -- the safety-critical floor
# ---------------------------------------------------------------------------
#
# This runs first and without any LLM, so a kill switch is guaranteed to act even
# when the model or market is misbehaving (the exact conditions that trigger it).
# The reliable close primitive is ``stop_executor(keep_position=False)``, which
# closes the position the executor holds. We deliberately do NOT call
# ``clear_position_held`` on live positions: it only clears *tracking* (for
# already externally-closed positions) and would hide a still-open one. Genuine
# orphan positions (open, no executor) are left to the LLM cleanup pass and, if
# they persist, the verify step raises a loud alert.


def _is_perp(connector: str) -> bool:
    """Whether a connector is a perpetual/futures market.

    Hummingbot encodes market type in the connector name (``binance`` vs
    ``binance_perpetual``). Unknown/ambiguous connectors are treated as perp so a
    kill switch errs toward *closing* leveraged risk rather than leaving it open.
    """
    c = (connector or "").lower()
    if not c:
        return True
    return "perpetual" in c or c.endswith("_perp")


def _keep_position(executor: dict, policy: ShutdownPolicy) -> bool:
    """Whether stopping ``executor`` should keep its position, per the policy."""
    if policy.on_kill_switch == POLICY_FLATTEN_ALL:
        return False
    if policy.on_kill_switch == POLICY_KEEP_ALL:
        return True
    # keep_spot_close_perp: keep spot, close perp
    return not _is_perp(executor.get("connector", ""))


def _position_connector(position: dict) -> str:
    return position.get("connector_name") or position.get("connector") or ""


def _position_pair(position: dict) -> str:
    return position.get("trading_pair") or position.get("pair") or ""


def _describe_position(position: dict) -> str:
    return f"{_position_connector(position)} {_position_pair(position)}".strip()


def _should_remain_open(position: dict, policy: ShutdownPolicy) -> bool:
    """Whether ``position`` is *expected* to still be open after a clean winddown.

    Used by the verify step: any position the policy said to close that is still
    open is a stranded position and triggers a loud alert.
    """
    if policy.on_kill_switch == POLICY_KEEP_ALL:
        return True
    if policy.on_kill_switch == POLICY_FLATTEN_ALL:
        return False
    # keep_spot_close_perp: spot should remain, perp should be gone
    return not _is_perp(_position_connector(position))


async def _get_running_executors(engine: Any, client: Any) -> list[dict]:
    """This session's running executors -- fresh if possible, else last snapshot.

    Re-runs only the executors core provider so the winddown acts on current
    truth without paying for the positions and account-wide drift reads it
    would discard. Scoped like the tick: the ledger's bases, so a session that
    deployed more than its configured bot winds all of them down. On any
    failure it falls back to ``engine._last_skill_data`` (already scoped to this
    session's ``agent_id`` by the last tick).

    Plus whatever the tick adopted from a dead session of this same strategy
    (CORR-726). The provider cannot see them -- it reads this session's own
    ``controller_id`` tag -- so a guaranteed winddown scoped to it alone left
    every inherited position open, on the one path whose whole promise is that
    nothing is left stranded. They are already this session's to close: the
    prompt says so and the stop gate allows their ids.
    """
    ledger = getattr(engine, "ledger", None)
    rows: list[dict] | None = None
    try:
        results = await engine.provider_registry.run_core_providers(
            client,
            engine.config,
            agent_id=engine.agent_id,
            bot_names=ledger.bases() if ledger else None,
            owned=ledger.owned() if ledger else None,
            names=("executors",),
        )
        ex_result = results.get("executors")
        if ex_result is not None and "executors" in getattr(ex_result, "data", {}):
            rows = list(ex_result.data["executors"])
    except Exception:
        log.exception("shutdown: executor refresh failed; using last snapshot")
    if rows is None:
        rows = list((engine._last_skill_data or {}).get("executors", []))
    # ``getattr``: the winddown is also driven by stand-in engines that carry
    # only what they bind, the convention ``_finish`` documents. Adopted rows
    # carry a foreign ``controller_id`` by definition, so they cannot duplicate
    # a row the provider just returned.
    adopted = getattr(engine, "_adopted_executors", None) or []
    return rows + [row for row in adopted if isinstance(row, dict)]


async def _fetch_positions(client: Any, agent_id: str) -> list[dict]:
    """Positions summary scoped to this session (``controller_id``).

    Non-strict on purpose: ``run_shutdown`` never raises for an individual API
    failure and ``_verify_and_retry`` calls this unguarded, so a failed request
    is logged by the fetcher and reads as no positions.
    """
    return await fetch_tracked_positions(
        client, controller_id=agent_id or None, strict=False
    )


async def _deterministic_baseline(
    engine: Any, client: Any, policy: ShutdownPolicy, running: list[dict] | None = None
) -> tuple[int, list[str]]:
    """Stop this session's executors with ``keep_position`` per policy.

    Each stop is isolated so one failure never aborts the rest. Returns
    ``(stopped_count, failures)``.

    ``running`` lets the caller hand in the rows it already read, so the
    winddown pays for one executor refresh and the order sweep that follows
    scopes itself to the same rows this stop acted on.
    """
    from condor.fetchers.executors import describe_executor_error, stop_executor

    if running is None:
        running = await _get_running_executors(engine, client)
    targets = [ex for ex in running if ex.get("id") or ex.get("executor_id")]

    async def _stop_one(ex: dict) -> str | None:
        ex_id = ex.get("id") or ex.get("executor_id")
        try:
            await stop_executor(client, ex_id, keep_position=_keep_position(ex, policy))
        except Exception as e:
            # A raise is the only failure signal: these failures are read back
            # by the operator and by the LLM cleanup pass, so keep the raw
            # exception (which embeds the backend URL) out of them.
            _, message = describe_executor_error(e)
            return f"stop {ex_id}: {message}"
        return None

    # Every stop signal goes out at once (PERF-668): on a hanging API a serial
    # loop would not even send the last executor's stop until the previous ones
    # had each hit the client timeout. gather keeps results in ``running`` order.
    results = await asyncio.gather(*(_stop_one(ex) for ex in targets))
    failures = [r for r in results if r]
    stopped = sum(1 for r in results if r is None)
    return stopped, failures


async def _verify_and_retry(
    engine: Any, client: Any, policy: ShutdownPolicy
) -> list[dict]:
    """Re-query positions; retry the deterministic close once; return residuals.

    Never trusts the LLM: computes which positions *should* be gone under the
    policy, and if any remain, stops any still-running executor that should be
    closed and re-checks. Returns the list of positions still stranded.
    """
    positions = await _fetch_positions(client, engine.agent_id)
    stranded = [p for p in positions if not _should_remain_open(p, policy)]
    if not stranded:
        return []

    from condor.fetchers.executors import stop_executor

    running = await _get_running_executors(engine, client)
    targets = [
        ex
        for ex in running
        if not _keep_position(ex, policy) and (ex.get("id") or ex.get("executor_id"))
    ]

    async def _retry_one(ex: dict) -> None:
        ex_id = ex.get("id") or ex.get("executor_id")
        try:
            await stop_executor(client, ex_id, keep_position=False)
        except Exception:
            log.exception("shutdown: retry stop failed for %s", ex_id)

    await asyncio.gather(*(_retry_one(ex) for ex in targets))

    positions = await _fetch_positions(client, engine.agent_id)
    return [p for p in positions if not _should_remain_open(p, policy)]


# ---------------------------------------------------------------------------
# cancel_open_orders -- executed, not described
# ---------------------------------------------------------------------------
#
# The policy flag used to reach only the LLM context, as the sentence
# ``cancel_open_orders=True``. Nothing in the code had ever cancelled an order,
# so a stop that reported success could leave resting orders on the venue and
# the only thing standing between the operator and that was whether a model
# happened to act on a sentence (CORR-727, hummingbot/condor#273). It is
# executed here instead, deterministically and before the cleanup pass, and the
# sentence now reports what the execution achieved.
#
# Two filters, and they answer different questions (SEC-739). The *read* is
# narrowed to this session's own markets -- the ``(connector, trading_pair)`` of
# the executors the winddown just acted on -- because that is as narrow as the
# API allows an active-order read to be: the row it returns carries its account,
# connector and pair and no controller or executor id. That is a scope, not an
# ownership test, and on a server with a second principal on the same pair those
# markets hold a neighbour's resting orders too. The *ownership test* is
# ``claimed_order_ids`` below: the order ids this session's own executor records
# mention. Only an order that is both in scope and claimed is cancelled; a
# resting order in scope that nothing claims is left alone and recorded on the
# outcome as unattributed, because stopping one session is not a licence to
# liquidate another's book (the order half of the envelope rule risk.py states
# and SEC-559 built for executors).

#: Executor types whose exposure is a gateway/CLMM position rather than an
#: order-book order: there is no order to cancel and ``/trading`` does not reach
#: the venue at all. A market held only by these records the policy as *not
#: carried out* rather than as swept clean.
_NO_CANCEL_PATH_TYPES = frozenset({"lp"})

#: Order statuses still resting on the venue. ``PENDING_CANCEL`` is deliberately
#: absent: a cancel is already in flight for it, and re-issuing one would read as
#: this sweep's own success.
_RESTING_STATUSES = frozenset({"OPEN", "SUBMITTED", "PARTIALLY_FILLED"})

#: One page is the whole sweep -- these are in-flight orders of a handful of
#: markets, not order history.
_ORDER_SCAN_LIMIT = 500

#: How many unattributed order ids ``CancelOutcome.describe()`` names before it
#: counts the rest. The whole list is kept on the outcome; this line reaches a
#: Telegram notification, and on a busy market a second principal's resting
#: orders can be most of the page read above. The same ceiling ``run_shutdown``
#: puts on the winddown failures it quotes.
_DISCLOSURE_SAMPLE = 5


@dataclass
class CancelOutcome:
    """What the ``cancel_open_orders`` policy actually did, not what it asked for.

    ``requested`` is the configured flag; everything else is observed. The
    distinction is the whole point of CORR-727: a policy that could not be
    carried out has to say so instead of reading as done.
    """

    requested: bool = False
    #: Orders this sweep issued a cancel for, as ``connector pair order_id``.
    cancelled: list[str] = field(default_factory=list)
    #: Orders whose cancel was refused -- left resting on the venue.
    failed: list[str] = field(default_factory=list)
    #: Resting orders found in this session's markets that no executor record of
    #: this session claims, as ``connector pair order_id`` -- left alone because
    #: ownership could not be established (SEC-739). Deliberately neither
    #: ``unsupported`` (the market has a cancel path and was swept) nor
    #: ``failed`` (no cancel was attempted, so none was refused), and
    #: deliberately not a bar to :attr:`carried_out`: the policy's scope is this
    #: session's own orders, and those were all swept. It is a disclosure of what
    #: was deliberately not touched.
    unattributed: list[str] = field(default_factory=list)
    #: Markets the policy cannot be carried out on, as ``connector pair``.
    unsupported: list[str] = field(default_factory=list)
    #: Set when the active-order read itself failed, so nothing is known about
    #: what is resting. Not the same as "nothing was resting".
    unknown: str = ""

    @property
    def carried_out(self) -> bool:
        """True only when the whole policy was executed over the whole scope."""
        if not self.requested:
            return False
        return not (self.failed or self.unsupported or self.unknown)

    def describe(self) -> str:
        """One line, in the executed past tense, for context/journal/notify."""
        if not self.requested:
            return "cancel_open_orders=false: no order cancel was attempted."
        parts = [f"cancelled {len(self.cancelled)} resting order(s)"]
        if self.failed:
            parts.append(f"{len(self.failed)} LEFT RESTING (cancel failed)")
        if self.unattributed:
            shown = ", ".join(self.unattributed[:_DISCLOSURE_SAMPLE])
            rest = len(self.unattributed) - _DISCLOSURE_SAMPLE
            parts.append(
                f"{len(self.unattributed)} resting order(s) LEFT ALONE, no "
                f"executor of this session claims them: {shown}"
                + (f", +{rest} more" if rest > 0 else "")
            )
        if self.unsupported:
            parts.append(
                f"NOT carried out on {len(self.unsupported)} market(s) with no "
                f"cancel path: {', '.join(self.unsupported)}"
            )
        if self.unknown:
            parts.append(f"open orders UNKNOWN ({self.unknown})")
        return "cancel_open_orders=true, executed: " + "; ".join(parts) + "."


def _market_key(connector: Any, pair: Any) -> tuple[str, str]:
    """Case-folded ``(connector, pair)`` -- the API spells both inconsistently."""
    return (str(connector or "").strip().lower(), str(pair or "").strip().upper())


def _cancel_scope(executors: list[dict]) -> tuple[set[tuple[str, str]], list[str]]:
    """The markets to sweep, and the ones the policy cannot be carried out on.

    A market is unsupported only when *every* executor this session ran there is
    gateway/CLMM: a connector that also carries an order-book executor has a
    cancel path, and its resting orders are in scope.
    """
    from condor.fetchers.executors import get_executor_type

    cancellable: set[tuple[str, str]] = set()
    no_path: dict[tuple[str, str], str] = {}
    for ex in executors:
        connector = ex.get("connector") or ex.get("connector_name") or ""
        pair = ex.get("pair") or ex.get("trading_pair") or ""
        if not connector or not pair:
            continue
        key = _market_key(connector, pair)
        if get_executor_type(ex) in _NO_CANCEL_PATH_TYPES:
            no_path.setdefault(key, f"{connector} {pair}")
        else:
            cancellable.add(key)
    unsupported = sorted(
        label for key, label in no_path.items() if key not in cancellable
    )
    return cancellable, unsupported


def _is_resting(order: dict) -> bool:
    return str(order.get("status") or "").strip().upper() in _RESTING_STATUSES


def _order_id(order: Mapping[str, Any]) -> str:
    return str(order.get("order_id") or order.get("client_order_id") or "").strip()


def _order_label(order: Mapping[str, Any]) -> str:
    """``connector pair order_id`` -- how every outcome list names an order."""
    return (
        f"{order.get('connector_name') or '?'} "
        f"{order.get('trading_pair') or '?'} {_order_id(order)}"
    )


#: Every ``custom_info`` key an executor record keeps order dicts under, read by
#: union: which ones are populated depends on the executor type and on which
#: serializer produced the row.
#:
#: Deliberately wider than ``closeout._RECORD_ORDER_KEYS``, which drops the keys
#: whose own name says terminal. That is right for a *claim* -- a terminal row
#: makes no claim that something is still out there -- and wrong for
#: *ownership*: an order the record calls failed or cancelled while the venue
#: still shows it resting is both this session's and exactly #273's drift, and
#: dropping it here would leave the bug CORR-727 fixed uncancelled again.
_CLAIM_ORDER_KEYS = (
    "orders",
    "open_orders",
    "close_orders",
    "active_orders",
    "held_position_orders",
    "failed_orders",
    "canceled_orders",
)


def claimed_order_ids(executors: Iterable[Mapping[str, Any]] | None) -> set[str]:
    """The order ids this session's executor records mention, at any status.

    The ownership test an active-order row cannot answer: that row carries its
    account, connector and pair and no controller or executor id, but the
    executor rows on the other side carry the ids of the orders they placed.
    ``closeout.record_orders`` (FEAT-134) walks the same lists for a different
    question -- which orders the records *claim are still resting* -- so it is
    not reused here; ownership is indifferent to the status a row states, and
    filtering by it would un-own exactly the stale rows #273 was about.

    Every status and every key, so an order this session placed is this
    session's however its record ended up spelling it. An id missing from here
    is not cancelled: see :attr:`CancelOutcome.unattributed`.
    """
    claimed: set[str] = set()
    for ex in executors or ():
        if not isinstance(ex, Mapping):
            continue
        info = ex.get("custom_info")
        if not isinstance(info, Mapping):
            continue
        for key in _CLAIM_ORDER_KEYS:
            value = info.get(key)
            if not isinstance(value, (list, tuple)):
                continue
            for entry in value:
                if isinstance(entry, Mapping) and (oid := _order_id(entry)):
                    claimed.add(oid)
    return claimed


async def cancel_resting_orders(
    engine: Any, client: Any, policy: ShutdownPolicy, executors: list[dict]
) -> CancelOutcome:
    """Cancel this session's resting orders when the policy asks for it.

    Deterministic -- no LLM turn is involved -- and it never raises: a stop has
    to finish even when every cancel is refused, so a refusal is recorded as an
    order left behind and the caller reports it. ``executors`` are this session's
    rows, and they are the whole scope (see the module comment above).

    A controller-mode session is reported as unsupported rather than swept: its
    orders live inside the bot's own Hummingbot instance, which the API's
    ``/trading`` routes neither list nor cancel -- stopping the bot with
    ``skip_order_cancellation=False`` is that path, and it is not this stop.
    """
    outcome = CancelOutcome(requested=bool(policy.cancel_open_orders))
    if not outcome.requested:
        return outcome

    cancellable, outcome.unsupported = _cancel_scope(executors)

    bases: list[str] = []
    ledger = getattr(engine, "ledger", None)
    if ledger is not None:
        try:
            bases = list(ledger.bases())
        except Exception:
            bases = []
    if bases:
        # Controller mode: the whole session trades inside its bots' own
        # Hummingbot instances, so none of its orders are in ``/trading`` and
        # the executor rows above describe the bots' controllers, not anything
        # the API holds. Sweeping that scope would reach only orders that are
        # somebody else's. Record it and stop.
        outcome.unsupported = sorted(
            set(outcome.unsupported) | {f"bot {b} (own instance)" for b in bases}
        )
        return outcome

    if not cancellable:
        return outcome

    trading = getattr(client, "trading", None)
    if trading is None or not hasattr(trading, "cancel_order"):
        outcome.unknown = "this API client exposes no order-cancel route"
        return outcome

    try:
        from condor.fetchers.orders import fetch_active_orders

        # Narrowed server-side to this session's markets. Not just for cost: the
        # backend returns the first ``limit`` of *every* account's in-flight
        # orders, so an unfiltered read on a busy server can page this session's
        # orders out and read as an empty book.
        orders = await fetch_active_orders(
            client,
            limit=str(_ORDER_SCAN_LIMIT),
            strict=True,
            connector_names=sorted({c for c, _ in cancellable}),
            trading_pairs=sorted({p for _, p in cancellable}),
        )
    except Exception as e:
        # A failed read is not an empty book: say the open orders are unknown
        # rather than reporting a clean sweep. The message is read back by the
        # operator and by the cleanup pass, so keep the raw exception (which
        # embeds the backend URL) out of it.
        log.exception("shutdown: could not read active orders")
        outcome.unknown = f"active-order read failed ({type(e).__name__})"
        return outcome

    resting = [
        o
        for o in orders
        if isinstance(o, dict)
        and _is_resting(o)
        and _order_id(o)
        and _market_key(o.get("connector_name"), o.get("trading_pair")) in cancellable
    ]
    # The market filter above is a scope; this is the ownership test (SEC-739).
    # A resting order in one of this session's markets that none of its executor
    # records mentions belongs to another principal -- a second session, a
    # hand-run bot, a human's manual order -- and is left alone and disclosed.
    claimed = claimed_order_ids(executors)
    targets = [o for o in resting if _order_id(o) in claimed]
    outcome.unattributed = sorted(
        _order_label(o) for o in resting if _order_id(o) not in claimed
    )

    from condor.fetchers.executors import describe_executor_error

    async def _cancel_one(order: dict) -> tuple[str, str | None]:
        label = _order_label(order)
        try:
            await trading.cancel_order(
                account_name=str(order.get("account_name") or "master_account"),
                connector_name=str(order.get("connector_name") or ""),
                client_order_id=_order_id(order),
            )
        except Exception as e:
            _, message = describe_executor_error(e)
            return label, message
        return label, None

    if len(orders) >= _ORDER_SCAN_LIMIT:
        # The page was full, so there may be resting orders past it that this
        # sweep never saw. Say so rather than letting the cancels below read as
        # the whole book.
        outcome.unknown = (
            f"the active-order read filled its {_ORDER_SCAN_LIMIT}-row page, so "
            "orders past it were not swept"
        )

    # Every cancel goes out at once, for the same reason the stops do (PERF-668):
    # on a hanging API a serial loop would not even send the last order's cancel
    # until the earlier ones had each hit the client timeout.
    results = await asyncio.gather(*(_cancel_one(o) for o in targets))
    for label, error in results:
        if error is None:
            outcome.cancelled.append(label)
        else:
            outcome.failed.append(f"{label}: {error}")
    return outcome


def _build_llm_context(
    policy: ShutdownPolicy,
    running: list[dict],
    positions: list[dict],
    failures: list[str],
    cancel: CancelOutcome | None = None,
) -> str:
    """Post-baseline state handed to the LLM cleanup pass.

    The order line is derived from ``cancel`` -- what the sweep executed -- and
    not from ``policy.cancel_open_orders``, so the context cannot claim an
    action the code did not take (CORR-727).
    """
    lines = [
        "An emergency shutdown was triggered. The deterministic winddown has ALREADY run.",
        f"Policy: on_kill_switch={policy.on_kill_switch}.",
        (cancel or CancelOutcome()).describe(),
        "",
        f"Executors still running after the baseline stop ({len(running)}):",
    ]
    lines += [
        f"  - {ex.get('id') or ex.get('executor_id') or '?'} "
        f"{ex.get('connector', '?')} {ex.get('pair', '')}".rstrip()
        for ex in running
    ] or ["  (none)"]
    lines += ["", f"Open positions after the baseline stop ({len(positions)}):"]
    lines += [
        f"  - {_describe_position(p)} pnl="
        f"{p.get('unrealized_pnl_quote', p.get('unrealized_pnl', '?'))}"
        for p in positions
    ] or ["  (none)"]
    if failures:
        lines += ["", f"Deterministic winddown errors ({len(failures)}):"]
        lines += [f"  - {f}" for f in failures]
    return "\n".join(lines)


async def _run_llm_cleanup(
    engine: Any,
    client: Any,
    policy: ShutdownPolicy,
    body: str,
    failures: list[str],
    cancel: CancelOutcome | None = None,
) -> None:
    """Best-effort LLM nuance pass on top of the guaranteed deterministic floor.

    Bounded by the same budget the tick agent session runs under (the shared
    timeout policy, overridable per run with ``tick_timeout_sec``) and fully
    fail-open: the safety-critical winddown already happened, so any hang or
    error here is logged and swallowed — it can never strand a position the way
    an LLM-only shutdown could.
    """
    agent = getattr(engine, "agent", None)
    if not body or agent is None:
        return
    try:
        from condor.runtime import context as runtime_context

        from .engine import _NullTracker, build_gated_client

        # Independent reads, and each swallows its own failure.
        running, positions = await asyncio.gather(
            _get_running_executors(engine, client),
            _fetch_positions(client, engine.agent_id),
        )
        context = _build_llm_context(policy, running, positions, failures, cancel)
        cleanup_timeout = resolve_tick_timeout(
            strategy=engine.config.get("tick_timeout_sec")
        )
        prompt = runtime_context.build_agent_context(
            agent, engine.user_id, body, context
        )
        # The tick's own client and gate, in shutdown mode (SEC-631): the same
        # narrow toolset, and only the brakes are approved — each still bound to
        # this session's executors and bot namespace.
        risk_state = engine.risk.get_state(engine.journal or _NullTracker())
        llm = build_gated_client(engine, risk_state, client, "shutdown")
        async with asyncio.timeout(cleanup_timeout):
            await llm.start()
            try:
                await llm.prompt(prompt)
            finally:
                await llm.stop()
    except asyncio.TimeoutError:
        log.warning(
            "TickEngine %s: shutdown LLM cleanup timed out (floor already secured)",
            engine.agent_id,
        )
    except Exception:
        log.exception(
            "TickEngine %s: shutdown LLM cleanup failed (floor already secured)",
            engine.agent_id,
        )
    _journal_cleanup_refusals(engine)


def _journal_cleanup_refusals(engine: Any) -> None:
    """Write what the gate refused during the cleanup pass into the journal.

    The journal is still open here (``TickEngine._run_shutdown`` closes it only
    in its ``finally``), and nothing drains the refusal log after this: the run
    ends with the winddown. Never raises — the verify step still has to run.
    """
    try:
        engine._last_refusals = engine._refusals.drain()
        if engine.journal:
            engine._journal_refusals(engine.journal.tick_count + 1)
    except Exception:
        log.exception(
            "TickEngine %s: could not journal the cleanup's refusals", engine.agent_id
        )


async def run_shutdown(engine: Any, reason: str) -> None:
    """Wind down this session's executors/positions per its ``shutdown.md`` policy.

    Sequence (the LLM judgment pass is inserted between baseline and verify):

    1. Load the resolved policy + body; journal ``shutdown_start``.
    2. Deterministic baseline: stop this session's executors with ``keep_position``
       per policy (the guaranteed floor).
    3. Execute ``cancel_open_orders`` over this session's markets, and record what
       it achieved -- including where it could not be carried out (CORR-727).
    4. Verify: re-query positions, retry the close once, and loudly alert the user
       if anything the policy said to close is still open.
    5. Journal ``shutdown_done``.

    The caller (:meth:`TickEngine._run_shutdown`) owns the idempotency guard and
    the self-stop; this function performs the winddown itself and never raises for
    an individual API failure -- failures are collected and surfaced.
    """
    policy, body = load_shutdown_policy(engine.strategy)
    agent_id = engine.agent_id
    log.warning(
        "TickEngine %s: SHUTDOWN starting -- %s (policy=%s)",
        agent_id,
        reason,
        policy.on_kill_switch,
    )
    if engine.journal:
        engine.journal.append_action(
            engine.journal.tick_count + 1,
            "shutdown_start",
            f"{reason} (policy={policy.on_kill_switch})",
        )

    client = await engine._get_client()
    if client is None:
        msg = (
            f"🚨 Agent {agent_id}: emergency shutdown could NOT reach the API — "
            f"positions may be OPEN, check manually! ({reason})"
        )
        log.error(msg)
        await engine._notify(msg)
        if engine.journal:
            with engine.journal.batch():
                engine.journal.append_action(
                    engine.journal.tick_count + 1, "shutdown_failed", "no API client"
                )
                engine.journal.record_tick("shutdown failed (no client): " + reason)
        return

    # One executor refresh for the whole winddown: the stop acts on these rows
    # and the order sweep scopes itself to the same markets.
    running = await _get_running_executors(engine, client)
    stopped, failures = await _deterministic_baseline(engine, client, policy, running)

    # The policy's own order cancel, executed before anything is told about it.
    # Recorded on the engine so ``TickEngine._finish`` does not sweep a second
    # time on its way out.
    cancel = await cancel_resting_orders(engine, client, policy, running)
    engine._cancel_outcome = cancel

    # LLM nuance pass on top of the guaranteed floor (best-effort, bounded).
    await _run_llm_cleanup(engine, client, policy, body, failures, cancel)

    stranded = await _verify_and_retry(engine, client, policy)

    if stranded:
        details = ", ".join(_describe_position(p) for p in stranded) or "unknown"
        msg = (
            f"🚨 Agent {agent_id}: emergency shutdown left {len(stranded)} position(s) "
            f"OPEN that the '{policy.on_kill_switch}' policy said to close: {details}. "
            f"Close them manually!"
        )
        log.error(msg)
    else:
        msg = (
            f"✅ Agent {agent_id}: emergency shutdown complete — wound down per "
            f"'{policy.on_kill_switch}' (stopped {stopped} executor(s)). ({reason})"
        )
    if failures:
        msg += f"\n⚠️ {len(failures)} winddown error(s): " + "; ".join(failures[:5])
    if cancel.failed or cancel.unattributed or cancel.unsupported or cancel.unknown:
        msg += f"\n⚠️ {cancel.describe()}"
    await engine._notify(msg)

    if engine.journal:
        verified = "flat" if not stranded else f"{len(stranded)} stranded"
        # Both journal.md updates of this winddown go into one batch, so the file
        # is rewritten once instead of twice (PERF-136 idiom, PERF-173).
        with engine.journal.batch():
            if cancel.requested:
                engine.journal.append_action(
                    engine.journal.tick_count + 1,
                    "cancel_open_orders",
                    cancel.describe(),
                )
            engine.journal.append_action(
                engine.journal.tick_count + 1,
                "shutdown_done",
                f"stopped={stopped}, failures={len(failures)}, verify={verified}",
            )
            engine.journal.record_tick("shutdown: " + reason)
