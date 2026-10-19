"""The close-out report: what a stop closed, what it left, read from the venue.

A stop used to leave no inventory at all. The graceful path
(:meth:`condor.agents.engine.TickEngine._finish`) said nothing about what it had
been operating, and the emergency winddown's one report
(``"left N position(s) OPEN"``) was scoped to the live session's ``agent_id``, so
a previous session's residue was skipped exactly the way CORR-726's stop gate
skipped it. At the Agent Builder Cup an archived agent listed two Gate spot
``LIMIT_MAKER`` sells as non-terminal, cancelling them answered *"not found in
active orders"*, and it took an operator reading the venue subaccount by hand to
establish that **the local rows were stale and nothing was resting**
(hummingbot/condor#273).

That incident fixes the two rules this module is built on:

1. **The live venue read decides, not the local rows.** A report built from the
   executor records would have reproduced the very answer the operator had to
   disprove. The split is the one :mod:`condor.venue_drift` already names:
   ``fetch_tracked_positions`` and the executor records are what Condor
   *believes*; ``fetch_positions`` and ``fetch_active_orders`` are what is
   actually out there. A local row the live read contradicts is reported as
   **stale**, by name.
2. **"Could not check" is never "nothing there."** :data:`UNCONFIRMABLE` is a
   first-class outcome beside :data:`CLOSED` and :data:`LEFT`, because not
   knowing which of the two was true is what the race actually cost.

Scope is the **lineage** (``"{agent}.{strategy}_"``,
:func:`condor.agents.executor_adoption.session_lineage`) and not one
``agent_id``, so a previous session's executor appears in the report instead of
being invisible to it.

Emission is best-effort: :func:`emit_closeout` never raises and is bounded, so a
report that fails can neither fail nor delay the stop that asked for it — a stop
held open by its own paperwork is worse than no paperwork. The failure is
recorded *in* the report.

:func:`close_out_agent` is the same builder with the closing done first and no
engine at all, which is what makes it usable on an agent that is already
stopped — the state #273's operator was actually in.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from condor import venue_drift
from condor.fetchers.executors import (
    describe_executor_error,
    fetch_all_executors,
    get_executor_type,
)
from condor.fetchers.orders import fetch_active_orders
from condor.fetchers.positions import fetch_positions
from condor.fetchers.tracked_positions import fetch_tracked_positions
from condor.fsutil import atomic_write_text

from .executor_adoption import lineage_session_num, session_lineage
from .shutdown import (
    _ORDER_SCAN_LIMIT,
    CancelOutcome,
    _cancel_scope,
    _is_resting,
    _keep_position,
    _market_key,
    _order_id,
)

log = logging.getLogger(__name__)

#: The three things the report can say about one executor, order or position.
#: ``unconfirmable`` is deliberately not a flavour of ``left``: it means no read
#: settled the question, which is the state the race was stuck in.
CLOSED = "closed"
LEFT = "left"
UNCONFIRMABLE = "unconfirmable"
OUTCOMES = (CLOSED, LEFT, UNCONFIRMABLE)

#: Where an outcome was established. ``venue`` is a live authenticated read;
#: ``record`` is the API's own bookkeeping, which #273 proved can be stale; and
#: ``unconfirmed`` is nothing at all.
SOURCE_VENUE = "venue"
SOURCE_RECORD = "record"
SOURCE_NONE = "unconfirmed"

KIND_EXECUTOR = "executor"
KIND_ORDER = "order"
KIND_POSITION = "position"
KINDS = (KIND_EXECUTOR, KIND_ORDER, KIND_POSITION)

#: Executor statuses that still hold exposure. Everything else is terminal, and
#: a terminal executor is one this stop — or an earlier one — closed.
LIVE_EXECUTOR_STATUSES = ("RUNNING", "SHUTTING_DOWN")
#: Read as well, so the report can say *closed* and not only *left*. Cheap only
#: because the read is narrowed to this lineage's own tags server-side:
#: terminated rows are never pruned upstream (PERF-733).
_TERMINAL_EXECUTOR_STATUS = "TERMINATED"

#: Cap on one executor walk. A lineage's own rows, not a host's whole history.
_EXECUTOR_SCAN_MAX = 2000

#: Order statuses a *record* can carry that mean "still out there". The sibling
#: of ``shutdown._RESTING_STATUSES``, applied to the record rather than to a
#: live read: these are the rows #273 watched claim two open sells that were
#: not there. ``PENDING_CANCEL`` is included here, unlike in the sweep's set —
#: the sweep skips it to avoid re-issuing a cancel, but a report still has to
#: account for the order.
_RECORD_NON_TERMINAL = frozenset(
    {"OPEN", "SUBMITTED", "PENDING_CREATE", "PARTIALLY_FILLED", "PENDING_CANCEL"}
)

#: ``custom_info`` keys an executor record keeps order dicts under, read by
#: union: which one is populated depends on the executor type and on which
#: serializer produced the row.
#:
#: Deliberately **not** the keys whose own name says terminal
#: (``failed_orders``, ``canceled_orders``) or already filled (the orders that
#: opened a kept position): a row there makes no claim that something is still
#: out there, and reporting one as a claim the venue then contradicts would
#: manufacture stale rows rather than find them.
_RECORD_ORDER_KEYS = ("open_orders", "close_orders", "orders", "active_orders")

#: Ceiling on how long a close-out read may hold a stop open. The stop's own
#: work is finished by then, so this bounds paperwork and nothing else.
EMIT_TIMEOUT_SEC = 30.0

CLOSEOUT_MD = "closeout.md"
CLOSEOUT_JSON = "closeout.json"


# ── The record ──


@dataclass(frozen=True)
class CloseoutItem:
    """One executor, order or position, as the close-out found it."""

    kind: str
    ref: str  # executor id, order id, or "connector pair" for a position
    label: str  # how it reads to an operator
    outcome: str
    source: str
    detail: str = ""
    #: The ``controller_id`` the row carried. Kept because the whole point of
    #: scoping by lineage is that a row from a session other than the one
    #: stopping can appear here, and the reader has to be able to see which.
    session: str = ""

    @property
    def line(self) -> str:
        parts = [f"{self.label or self.ref or '?'} — {self.outcome} ({self.source})"]
        if self.session:
            parts.append(f"[{self.session}]")
        if self.detail:
            parts.append(f"— {self.detail}")
        return " ".join(parts)


@dataclass(frozen=True)
class CloseoutReport:
    """What one stop closed and what it left, over a whole lineage.

    ``error`` is the builder's own failure, carried rather than raised: a stop
    must finish whether or not its report could be built, and a report that is
    silently absent is the state this item exists to end.
    """

    agent_id: str = ""
    lineage: str = ""
    at: str = ""
    items: tuple[CloseoutItem, ...] = ()
    #: Local rows a live read contradicted, by name. #273's two phantom sells.
    stale_records: tuple[str, ...] = ()
    #: Reads that did not answer, so part of the report is ``unconfirmable``.
    unanswered: tuple[str, ...] = ()
    error: str = ""

    def of_kind(self, kind: str) -> tuple[CloseoutItem, ...]:
        return tuple(i for i in self.items if i.kind == kind)

    def of_outcome(self, outcome: str) -> tuple[CloseoutItem, ...]:
        return tuple(i for i in self.items if i.outcome == outcome)

    def counts(self) -> dict[str, dict[str, int]]:
        """``{kind: {outcome: n}}`` for every kind, zeros included."""
        return {
            kind: {
                outcome: sum(
                    1 for i in self.items if i.kind == kind and i.outcome == outcome
                )
                for outcome in OUTCOMES
            }
            for kind in KINDS
        }

    @property
    def clean(self) -> bool:
        """True only when nothing was left behind and nothing went unconfirmed.

        ``unanswered`` counts, and that is the whole of it: a read that did not
        answer returns an empty list, so a failed tracked-position read — or an
        order page that filled — leaves no row to carry an ``UNCONFIRMABLE``
        outcome. Judged on the items alone, a close-out that never checked the
        held positions reported ``clean`` and "nothing to close out", which is
        the one sentence this report exists to stop being a guess.
        """
        return (
            not self.error
            and not self.unanswered
            and not any(i.outcome in (LEFT, UNCONFIRMABLE) for i in self.items)
        )

    def summary(self) -> str:
        """One line, for the journal, a notification and the log."""
        if self.error and not self.items:
            return f"close-out report could NOT be built: {self.error}"
        counts = self.counts()
        parts = []
        for kind in KINDS:
            c = counts[kind]
            if not any(c.values()):
                continue
            bits = [f"{c[CLOSED]} closed"]
            if c[LEFT]:
                bits.append(f"{c[LEFT]} LEFT")
            if c[UNCONFIRMABLE]:
                bits.append(f"{c[UNCONFIRMABLE]} UNCONFIRMABLE")
            parts.append(f"{kind}s: " + ", ".join(bits))
        line = "; ".join(parts) or "nothing to close out"
        if self.stale_records:
            line += f"; {len(self.stale_records)} stale local row(s)"
        # Said out loud for the same reason it costs the report its ``clean``:
        # "nothing to close out" from a stop that could not read the positions
        # is the sentence an operator would act on.
        if self.unanswered:
            line += f"; {len(self.unanswered)} read(s) did not answer"
        if self.error:
            line += f"; report incomplete ({self.error})"
        return line

    def render(self) -> str:
        """The operator-facing markdown, written beside the journal."""
        lines = [
            f"# Close-out — {self.agent_id or self.lineage or '?'}",
            "",
            f"- when: {self.at}",
            f"- lineage: `{self.lineage}`",
            f"- summary: {self.summary()}",
            "",
        ]
        for kind in KINDS:
            lines.append(f"## {kind.capitalize()}s")
            lines += [f"- {i.line}" for i in self.of_kind(kind)] or ["- (none)"]
            lines.append("")
        if self.stale_records:
            lines.append("## Stale local rows")
            lines += [f"- {r}" for r in self.stale_records]
            lines.append("")
        if self.unanswered:
            lines.append("## Reads that did not answer")
            lines += [f"- {r}" for r in self.unanswered]
            lines.append("")
        if self.error:
            lines += ["## Report failure", f"- {self.error}", ""]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "lineage": self.lineage,
            "at": self.at,
            "summary": self.summary(),
            "clean": self.clean,
            "counts": self.counts(),
            "items": [
                {
                    "kind": i.kind,
                    "ref": i.ref,
                    "label": i.label,
                    "outcome": i.outcome,
                    "source": i.source,
                    "detail": i.detail,
                    "session": i.session,
                }
                for i in self.items
            ],
            "stale_records": list(self.stale_records),
            "unanswered": list(self.unanswered),
            "error": self.error,
        }


# ── Reading the rows ──


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ex_id(ex: Mapping[str, Any]) -> str:
    return str(ex.get("id") or ex.get("executor_id") or "").strip()


def _ex_controller(ex: Mapping[str, Any]) -> str:
    """The creator's tag, top level first — the precedence ``build_executor_row``
    established, since what is left inside ``config`` is the backend default."""
    cfg = ex.get("config")
    cfg = cfg if isinstance(cfg, Mapping) else {}
    return str(ex.get("controller_id") or cfg.get("controller_id") or "").strip()


def _ex_market(ex: Mapping[str, Any]) -> tuple[str, str]:
    cfg = ex.get("config")
    cfg = cfg if isinstance(cfg, Mapping) else {}
    connector = str(
        ex.get("connector_name")
        or cfg.get("connector_name")
        or ex.get("connector")
        or cfg.get("connector")
        or ""
    ).strip()
    pair = str(
        ex.get("trading_pair")
        or cfg.get("trading_pair")
        or ex.get("pair")
        or cfg.get("pair")
        or ""
    ).strip()
    return connector, pair


def lineage_rows(
    rows: Iterable[Mapping[str, Any]] | None, agent_slug: str, strategy_slug: str
) -> list[dict]:
    """Every row of this ``(agent, strategy)`` lineage, whatever session wrote it.

    The one rule that makes the report cover what the emergency shutdown's
    ``agent_id`` scoping cannot: a previous session's tag is still this
    strategy's tag. An experiment (``_e3``), another strategy and the backend's
    default ``"main"`` are all rejected by
    :func:`~condor.agents.executor_adoption.lineage_session_num`.
    """
    out: list[dict] = []
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        if lineage_session_num(_ex_controller(row), agent_slug, strategy_slug) is None:
            continue
        out.append(dict(row))
    return out


def lineage_controller_ids(agent_slug: str, strategy_slug: str) -> list[str]:
    """Every session tag this strategy has ever minted, from its session dirs.

    ``search_executors`` filters ``controller_ids`` by exact match and a lineage
    is a prefix, so this is what lets the read be narrowed server-side instead
    of walking a host's whole executor history and filtering it here. The
    sessions on disk are the authority on which numbers were minted — including
    the ones whose engine is long gone, which is the half the shutdown's
    ``agent_id`` scoping cannot see.

    Empty when nothing is on disk (an install whose agent folder is gone), and
    the caller then falls back to the prefix walk.
    """
    from condor.memory.paths import agent_home

    from .journal import iter_session_dirs
    from .strategy import LOOPS_DIRNAME

    lineage = session_lineage(agent_slug, strategy_slug)
    if not lineage:
        return []
    try:
        home = agent_home(agent_slug) / LOOPS_DIRNAME / strategy_slug
        return [f"{lineage}{num}" for num, _ in iter_session_dirs(home)]
    except Exception:  # noqa: BLE001 - a missing folder is not an error here
        log.debug("closeout: no session dirs for %s", lineage, exc_info=True)
        return []


def record_orders(executors: Iterable[Mapping[str, Any]] | None) -> list[dict]:
    """The orders the executor **records** claim, with the status they claim.

    This is deliberately the stale side of #273: it is what a report built from
    local rows would have believed, and naming it is what lets the live read
    contradict it.

    A claim is a **stated** non-terminal status, which is exactly the shape #273
    had ("listed two Gate spot ``LIMIT_MAKER`` sells as non-terminal"). A row
    that says ``FILLED``, or that says nothing at all, is not treated as one:
    inferring a claim from silence would invent stale rows for every order an
    executor ever placed, and the report would then "find" them all closed.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for ex in executors or ():
        if not isinstance(ex, Mapping):
            continue
        info = ex.get("custom_info")
        info = info if isinstance(info, Mapping) else {}
        connector, pair = _ex_market(ex)
        candidates: list[tuple[str, str]] = []
        for key in _RECORD_ORDER_KEYS:
            value = info.get(key)
            if not isinstance(value, (list, tuple)):
                continue
            for entry in value:
                if isinstance(entry, Mapping):
                    candidates.append(
                        (
                            _order_id(entry),
                            str(entry.get("status") or "").strip().upper(),
                        )
                    )
        for oid, status in candidates:
            if not oid or oid in seen:
                continue
            if status not in _RECORD_NON_TERMINAL:
                continue
            seen.add(oid)
            out.append(
                {
                    "order_id": oid,
                    "status": status,
                    "connector_name": connector,
                    "trading_pair": pair,
                    "controller_id": _ex_controller(ex),
                    "executor_id": _ex_id(ex),
                }
            )
    return out


# ── The three sections ──


def _executor_items(executors: Sequence[Mapping[str, Any]]) -> list[CloseoutItem]:
    """One item per lineage executor: terminal is closed, live is left.

    The source is ``record`` throughout, and says so: an executor is an API
    object and its status is the API's own statement. What it *holds* is a
    different question, and that one is checked against the venue below.
    """
    items: list[CloseoutItem] = []
    for ex in executors:
        connector, pair = _ex_market(ex)
        status = str(ex.get("status") or "").strip().upper()
        items.append(
            CloseoutItem(
                kind=KIND_EXECUTOR,
                ref=_ex_id(ex),
                label=f"{_ex_id(ex) or '?'} {connector} {pair}".strip(),
                outcome=LEFT if status in LIVE_EXECUTOR_STATUSES else CLOSED,
                source=SOURCE_RECORD,
                detail=f"status={status or 'unknown'}",
                session=_ex_controller(ex),
            )
        )
    return items


def _order_label(connector: Any, pair: Any, oid: str) -> str:
    return f"{connector or '?'} {pair or '?'} {oid}".strip()


def _order_items(
    records: Sequence[Mapping[str, Any]],
    venue_orders: list[dict] | None,
    markets: set[tuple[str, str]],
    *,
    read_error: str = "",
    cancel: CancelOutcome | None = None,
    out_of_reach: Sequence[str] = (),
) -> tuple[list[CloseoutItem], list[str]]:
    """The order section, and the stale local rows the live read exposed.

    ``venue_orders`` is the live read; ``None`` means it did not answer, and
    then **every** claim reads ``unconfirmable`` — never a clean book. When it
    did answer it is the authority: a record claiming an order the live read
    does not show is reported ``closed`` with its row named stale, which is the
    answer #273's operator had to establish by hand.
    """
    items: list[CloseoutItem] = []
    stale: list[str] = []
    resting: dict[str, Mapping[str, Any]] = {}

    if venue_orders is None:
        for rec in records:
            oid = _order_id(rec)
            items.append(
                CloseoutItem(
                    kind=KIND_ORDER,
                    ref=oid,
                    label=_order_label(
                        rec.get("connector_name"), rec.get("trading_pair"), oid
                    ),
                    outcome=UNCONFIRMABLE,
                    source=SOURCE_NONE,
                    detail=(
                        f"the record says {rec.get('status') or 'non-terminal'} and "
                        f"the live order read did not answer "
                        f"({read_error or 'no reason given'})"
                    ),
                    session=str(rec.get("controller_id") or ""),
                )
            )
        if not records:
            items.append(
                CloseoutItem(
                    kind=KIND_ORDER,
                    ref="",
                    label="open orders",
                    outcome=UNCONFIRMABLE,
                    source=SOURCE_NONE,
                    detail=(
                        f"the live order read did not answer "
                        f"({read_error or 'no reason given'}), so an empty book "
                        f"cannot be claimed"
                    ),
                )
            )
    else:
        resting = {
            _order_id(o): o
            for o in venue_orders
            if isinstance(o, Mapping)
            and _is_resting(o)
            and _order_id(o)
            and _market_key(o.get("connector_name"), o.get("trading_pair")) in markets
        }
        for oid, order in sorted(resting.items()):
            items.append(
                CloseoutItem(
                    kind=KIND_ORDER,
                    ref=oid,
                    label=_order_label(
                        order.get("connector_name"), order.get("trading_pair"), oid
                    ),
                    outcome=LEFT,
                    source=SOURCE_VENUE,
                    detail=f"resting on the venue ({order.get('status') or '?'})",
                )
            )
        for rec in records:
            oid = _order_id(rec)
            if oid in resting:
                continue  # already reported, from the side that can be trusted
            claimed = rec.get("status") or "non-terminal"
            label = _order_label(
                rec.get("connector_name"), rec.get("trading_pair"), oid
            )
            items.append(
                CloseoutItem(
                    kind=KIND_ORDER,
                    ref=oid,
                    label=label,
                    outcome=CLOSED,
                    source=SOURCE_VENUE,
                    detail=(
                        f"the record says {claimed}; the live read shows nothing "
                        f"resting — the local row is STALE"
                    ),
                    session=str(rec.get("controller_id") or ""),
                )
            )
            stale.append(f"{label}: record says {claimed}")

    # An order whose cancel was refused. ``CancelOutcome.failed`` carries it as
    # a formatted line rather than a row, so the overlap with what the live read
    # already answered for is found by looking for the id inside the line — no
    # assumption about that line's shape, which belongs to ``shutdown``.
    for failed in cancel.failed if cancel else ():
        text = str(failed)
        if any(oid in text for oid in resting):
            continue  # the venue answered for it above, as LEFT
        if venue_orders is None:
            # Refused, and nothing read it back: the one state the race was
            # stuck in, and the reason this outcome exists.
            items.append(
                CloseoutItem(
                    kind=KIND_ORDER,
                    ref="",
                    label=text,
                    outcome=UNCONFIRMABLE,
                    source=SOURCE_NONE,
                    detail="the cancel was refused and nothing read the order back",
                )
            )
        else:
            # The read covered this order's market and does not show it
            # resting, so it is gone — which is also the usual reason a cancel
            # is refused ("order already filled").
            items.append(
                CloseoutItem(
                    kind=KIND_ORDER,
                    ref="",
                    label=text,
                    outcome=CLOSED,
                    source=SOURCE_VENUE,
                    detail=(
                        "the cancel was refused, and the live read shows nothing "
                        "resting for it"
                    ),
                )
            )

    for market in out_of_reach:
        items.append(
            CloseoutItem(
                kind=KIND_ORDER,
                ref="",
                label=str(market),
                outcome=UNCONFIRMABLE,
                source=SOURCE_NONE,
                detail="no order-cancel path reaches this market, so nothing confirms it",
            )
        )
    return items, stale


def _position_items(
    report: venue_drift.DriftReport,
    lineage: str,
    markets: set[tuple[str, str]],
    spot_holds: Sequence[Mapping[str, Any]],
) -> tuple[list[CloseoutItem], list[str]]:
    """The position section, read off the drift check rather than re-derived.

    :mod:`condor.venue_drift` already owns *tracked versus venue* and warns
    against a second copy of it, so its verdicts are mapped here instead of
    recomputed: ``unanswered`` is ``unconfirmable`` (an unreachable venue is not
    a flat venue), ``ghost`` is ``closed`` with the tracked row named stale, and
    anything the venue still holds is ``left``.

    That check is perps-only — ``/trading/positions`` answers for perp
    connectors alone — so a spot hold cannot be confirmed either way and is
    reported from the record, saying so. Not the same as collapsing it into
    ``unconfirmable``: the record does make a statement, it just is not the
    venue's.
    """
    items: list[CloseoutItem] = []
    stale: list[str] = []
    for row in report.rows:
        mine = any(cid.startswith(lineage) for cid in row.controller_ids) or (
            _market_key(row.connector, row.pair) in markets
        )
        if not mine:
            continue
        label = f"{row.connector} {row.pair}".strip()
        session = next((c for c in row.controller_ids if c.startswith(lineage)), "")
        if row.verdict == "unanswered":
            items.append(
                CloseoutItem(
                    kind=KIND_POSITION,
                    ref=label,
                    label=label,
                    outcome=UNCONFIRMABLE,
                    source=SOURCE_NONE,
                    detail=(
                        f"tracked {row.tracked_base:+.8g} base and the venue did not "
                        f"answer ({report.reason or 'no reason given'})"
                    ),
                    session=session,
                )
            )
        elif row.has_venue:
            items.append(
                CloseoutItem(
                    kind=KIND_POSITION,
                    ref=label,
                    label=label,
                    outcome=LEFT,
                    source=SOURCE_VENUE,
                    detail=f"the venue holds {row.venue_base:+.8g} base ({row.verdict})",
                    session=session,
                )
            )
        elif row.has_tracked:
            items.append(
                CloseoutItem(
                    kind=KIND_POSITION,
                    ref=label,
                    label=label,
                    outcome=CLOSED,
                    source=SOURCE_VENUE,
                    detail=(
                        f"tracked {row.tracked_base:+.8g} base, the venue holds none "
                        f"— the local row is STALE"
                    ),
                    session=session,
                )
            )
            stale.append(f"{label}: tracked {row.tracked_base:+.8g} base, venue flat")

    for hold in spot_holds:
        connector = str(hold.get("connector_name") or hold.get("connector") or "")
        pair = str(hold.get("trading_pair") or hold.get("pair") or "")
        label = f"{connector} {pair}".strip()
        items.append(
            CloseoutItem(
                kind=KIND_POSITION,
                ref=label,
                label=label,
                outcome=LEFT,
                source=SOURCE_RECORD,
                detail=(
                    "spot: the venue position read covers perps only, so this is the "
                    "API's record and not a venue confirmation"
                ),
                session=str(hold.get("controller_id") or ""),
            )
        )
    return items, stale


# ── The builder ──


def _scope_rows(executors: Iterable[Mapping[str, Any]]) -> list[dict]:
    """The lineage's executors in the shape ``shutdown``'s helpers read.

    Those helpers (``_cancel_scope``, ``_keep_position``) were written for the
    display rows the winddown's executor provider hands them, which spell the
    market ``connector``/``pair``; a raw ``search_executors`` row spells it
    ``connector_name``/``trading_pair`` and may bury it in ``config``. Reusing
    the rules rather than restating them means translating the shape here —
    a market is out of reach only when *every* executor the lineage ran there
    is gateway/CLMM, and the report must not claim a cancel path the sweep
    itself does not have.

    ``custom_info`` travels with the row because the sweep's ownership test
    reads it (``shutdown.claimed_order_ids``, SEC-739): translated without it,
    the close-out action would hand the sweep rows that claim no order at all
    and every resting order of the lineage would read as another principal's.
    """
    rows = []
    for ex in executors:
        connector, pair = _ex_market(ex)
        info = ex.get("custom_info")
        rows.append(
            {
                "connector": connector,
                "pair": pair,
                "custom_info": info if isinstance(info, Mapping) else {},
                # Resolved here rather than left to ``_cancel_scope``'s own
                # lookup: the type can sit on the record, in the config, or be
                # inferable from neither, and ``get_executor_type`` is the one
                # place that knows all three.
                "type": get_executor_type(dict(ex)),
                "id": _ex_id(ex),
            }
        )
    return rows


async def _read_lineage_executors(
    client: Any,
    agent_slug: str,
    strategy_slug: str,
    controller_ids: Sequence[str] | None,
) -> tuple[list[dict], str]:
    """This lineage's executors, and the reason the read is incomplete, if any.

    With ``controller_ids`` the three status reads are narrowed server-side, so
    the terminated rows — which is what lets the report say *closed* and not only
    *left* — cost one scoped page instead of a walk over a host's whole history
    (PERF-733). Without them only the live statuses are read and the lineage
    filter is applied here; a strategy with no sessions on disk is the only way
    to land there, and it has no terminated history worth paying for.

    Each read is independent and allowed to fail: a failure costs its own rows
    and is named, never silently read as "nothing there".
    """
    if controller_ids:
        statuses = (*LIVE_EXECUTOR_STATUSES, _TERMINAL_EXECUTOR_STATUS)
        filters: dict[str, Any] = {"controller_ids": list(controller_ids)}
    else:
        statuses = LIVE_EXECUTOR_STATUSES
        filters = {}
    reads = await asyncio.gather(
        *(
            fetch_all_executors(
                client, status=status, max_items=_EXECUTOR_SCAN_MAX, **filters
            )
            for status in statuses
        ),
        return_exceptions=True,
    )
    rows: list[dict] = []
    error = ""
    for status, read in zip(statuses, reads):
        if isinstance(read, BaseException):
            _, message = describe_executor_error(read)
            log.warning("closeout: %s executor read failed", status, exc_info=read)
            error = f"{status} read failed: {message}"[:160]
            continue
        rows += list(read or [])
    # Filtered either way: ``controller_ids`` is an exact-match list and a
    # server that ignores it would otherwise widen the report silently.
    return lineage_rows(rows, agent_slug, strategy_slug), error


async def build_closeout(
    client: Any,
    agent_slug: str,
    strategy_slug: str,
    *,
    agent_id: str = "",
    cancel: CancelOutcome | None = None,
    controller_ids: Sequence[str] | None = None,
) -> CloseoutReport:
    """Read the venue and produce the close-out record for one lineage.

    Takes a client and two slugs and nothing else — no engine, no live session —
    which is what lets a stop and the standalone action share one builder.

    Every read is independent and every one of them may fail: a read that did
    not answer makes its own section ``unconfirmable`` and leaves the rest
    intact. This never raises for an API failure; :func:`emit_closeout` is what
    also catches what cannot be foreseen.
    """
    lineage = session_lineage(agent_slug, strategy_slug)
    at = _now()
    if not lineage:
        return CloseoutReport(
            agent_id=agent_id,
            at=at,
            error="no lineage: the agent or strategy slug is missing",
        )
    if controller_ids is None:
        controller_ids = lineage_controller_ids(agent_slug, strategy_slug)

    mine, executor_error = await _read_lineage_executors(
        client, agent_slug, strategy_slug, controller_ids
    )
    unanswered: list[str] = []
    if executor_error:
        unanswered.append(f"executor read: {executor_error}")

    markets = {_market_key(*_ex_market(ex)) for ex in mine if all(_ex_market(ex))}
    items = _executor_items(mine)
    stale: list[str] = []

    # ── Orders: the live read, against what the records claim ──
    records = record_orders(mine)
    cancellable, no_path = _cancel_scope(_scope_rows(mine))
    venue_orders: list[dict] | None = None
    order_error = ""
    if cancellable:
        try:
            # Narrowed server-side for the same reason the sweep narrows it: the
            # backend returns the first page of *every* account's in-flight
            # orders, so an unfiltered read on a busy server can page this
            # lineage's orders out and read as an empty book.
            venue_orders = await fetch_active_orders(
                client,
                limit=str(_ORDER_SCAN_LIMIT),
                strict=True,
                connector_names=sorted({c for c, _ in cancellable}),
                trading_pairs=sorted({p for _, p in cancellable}),
            )
        except Exception as e:
            log.warning("closeout: the live order read failed", exc_info=True)
            order_error = f"active-order read failed ({type(e).__name__})"
            unanswered.append(f"order read: {order_error}")
        else:
            if len(venue_orders) >= _ORDER_SCAN_LIMIT:
                unanswered.append(
                    f"order read: the {_ORDER_SCAN_LIMIT}-row page filled, so orders "
                    f"past it were not seen"
                )
    elif records:
        # Claims with no market the live read can cover: unconfirmable, not clean.
        order_error = "no market of this lineage has an order-cancel path"
        unanswered.append(f"order read: {order_error}")
    else:
        # No order-book market and no claim: the book is empty by construction,
        # which is a confirmed emptiness and not an unread one.
        venue_orders = []
    if cancel is not None and cancel.unknown:
        unanswered.append(f"order sweep: {cancel.unknown}")

    order_items, order_stale = _order_items(
        records,
        venue_orders,
        cancellable,
        read_error=order_error,
        cancel=cancel,
        out_of_reach=sorted(set(no_path) | set(cancel.unsupported if cancel else ())),
    )
    items += order_items
    stale += order_stale

    # ── Positions: the drift check, mapped ──
    # After the order read rather than alongside it. The two are independent and
    # could overlap, but holding a pending gather across the order read's own
    # try/except is a footgun for the next editor (an early return there would
    # leak the task), and the whole emission is capped by ``EMIT_TIMEOUT_SEC``
    # anyway — the serial cost is one round trip on a path that runs once per
    # stop, after the stop's own work is already done.
    tracked, venue = await asyncio.gather(
        fetch_tracked_positions(client, strict=True),
        fetch_positions(client, strict=True),
        return_exceptions=True,
    )
    if isinstance(tracked, BaseException):
        _, message = describe_executor_error(tracked)
        log.warning("closeout: the tracked-position read failed", exc_info=tracked)
        unanswered.append(f"tracked-position read: {message[:160]}")
        tracked_rows: list[dict] = []
    else:
        tracked_rows = lineage_rows(tracked, agent_slug, strategy_slug)

    # A live executor's fills are on the venue and in no hold (CORR-708), so
    # without these every running directional executor would read as a position
    # the venue holds and nothing accounts for.
    active_rows, unmeasured = venue_drift.tracked_from_active(mine)
    spot_holds = [
        row
        for row in tracked_rows
        if "_perpetual"
        not in str(row.get("connector_name") or row.get("connector") or "")
    ]
    if isinstance(venue, BaseException):
        _, message = describe_executor_error(venue)
        log.warning("closeout: the venue-position read failed", exc_info=venue)
        unanswered.append(f"venue-position read: {message[:160]}")
        drift = venue_drift.check(
            tracked_rows + active_rows,
            None,
            reason=message[:120],
            unmeasured=unmeasured,
        )
    else:
        drift = venue_drift.check(
            tracked_rows + active_rows, list(venue or []), unmeasured=unmeasured
        )
    position_items, position_stale = _position_items(
        drift, lineage, markets, spot_holds
    )
    items += position_items
    stale += position_stale

    for name in unmeasured:
        items.append(
            CloseoutItem(
                kind=KIND_POSITION,
                ref=name,
                label=name,
                outcome=UNCONFIRMABLE,
                source=SOURCE_NONE,
                detail=(
                    "this executor's open inventory could not be read, so a venue "
                    "row it might explain is not accounted for"
                ),
            )
        )

    return CloseoutReport(
        agent_id=agent_id,
        lineage=lineage,
        at=at,
        items=tuple(items),
        stale_records=tuple(stale),
        unanswered=tuple(unanswered),
        error=f"executor read incomplete: {executor_error}" if executor_error else "",
    )


# ── Persistence ──


def write_closeout(session_dir: Path | None, report: CloseoutReport) -> Path | None:
    """Write the record beside the journal. Returns the markdown path, or None.

    Atomic, so a close-out read while it is being written never sees half a file.
    """
    if session_dir is None:
        return None
    md = Path(session_dir) / CLOSEOUT_MD
    atomic_write_text(md, report.render())
    atomic_write_text(
        Path(session_dir) / CLOSEOUT_JSON, json.dumps(report.to_dict(), indent=2) + "\n"
    )
    return md


def read_closeout(session_dir: Path | None) -> dict[str, Any] | None:
    """The stored close-out of a session, or ``None`` when it has none.

    A session with no close-out is a normal state — it predates this report, or
    it is still running — so the caller gets ``None`` rather than an error it
    would have to tell apart from a missing session.
    """
    if session_dir is None:
        return None
    path = Path(session_dir) / CLOSEOUT_JSON
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        log.warning("closeout: %s is not readable as JSON", path)
        return None
    return data if isinstance(data, dict) else None


# ── Emission from a stop ──


def _failed_report(engine: Any, strategy: Any, error: str) -> CloseoutReport:
    return CloseoutReport(
        agent_id=str(getattr(engine, "agent_id", "") or ""),
        lineage=session_lineage(
            str(getattr(strategy, "agent_slug", "") or ""),
            str(getattr(strategy, "slug", "") or ""),
        ),
        at=_now(),
        error=error,
    )


async def emit_closeout(engine: Any) -> CloseoutReport | None:
    """Build and record the close-out for a stop. Never raises, always bounded.

    Called from :meth:`condor.agents.engine.TickEngine._finish`, which is the one
    teardown *every* exit path goes through — a graceful stop, an emergency
    winddown, an error, and the stop half of a restart — so one call site covers
    all of them.

    Two guarantees, and they are the same one from two directions: the stop's own
    work is already finished when this runs, and this cannot make the stop fail.
    So every failure is caught and recorded *in* the report, and the whole thing
    is capped at :data:`EMIT_TIMEOUT_SEC` — a report that hangs must not hold a
    stop open.

    ``getattr`` throughout: ``_finish`` is driven in tests by ``SimpleNamespace``
    stand-ins that carry only what they bind, and a teardown must finish either
    way.
    """
    from .config import is_experiment_mode

    config = getattr(engine, "config", None) or {}
    if is_experiment_mode(str(config.get("execution_mode") or "")):
        return None  # an experiment trades nothing and keeps no session dir
    strategy = getattr(engine, "strategy", None)
    if strategy is None:
        return None
    agent_id = str(getattr(engine, "agent_id", "") or "")
    journal = getattr(engine, "journal", None)

    try:
        async with asyncio.timeout(EMIT_TIMEOUT_SEC):
            client = await engine._get_client()
            if client is None:
                report = _failed_report(
                    engine,
                    strategy,
                    "could not reach the API, so nothing could be confirmed",
                )
            else:
                report = await build_closeout(
                    client,
                    strategy.agent_slug,
                    strategy.slug,
                    agent_id=agent_id,
                    cancel=getattr(engine, "_cancel_outcome", None),
                )
    except asyncio.TimeoutError:
        report = _failed_report(
            engine,
            strategy,
            f"the close-out read did not finish within {EMIT_TIMEOUT_SEC:g}s",
        )
    except Exception as e:  # noqa: BLE001 - recorded, never propagated into the stop
        log.exception("TickEngine %s: could not build the close-out report", agent_id)
        report = _failed_report(
            engine,
            strategy,
            f"the close-out builder raised {type(e).__name__}: {e}"[:300],
        )

    engine._closeout = report
    try:
        write_closeout(getattr(engine, "session_dir", None), report)
    except Exception:
        log.exception("TickEngine %s: could not write the close-out record", agent_id)
    try:
        if journal is not None:
            journal.append_action(
                getattr(journal, "tick_count", 0) + 1, "closeout", report.summary()
            )
    except Exception:
        log.exception("TickEngine %s: could not journal the close-out", agent_id)
    if not report.clean:
        log.warning("TickEngine %s close-out: %s", agent_id, report.summary())
    return report


# ── The standalone action ──


async def close_out_agent(
    client: Any,
    agent_slug: str,
    strategy_slug: str,
    *,
    policy: Any = None,
    session_dir: Path | None = None,
) -> CloseoutReport:
    """Close everything this lineage still holds, then report it. No engine.

    The post-mortem door: it takes the two slugs rather than a live session, so
    it works on an agent that is already stopped — which is the state #273's
    operator was in when they had to go to the venue by hand, and the state a
    crashed process leaves behind, having never reached
    :meth:`TickEngine._finish` at all.

    The order of work is the winddown's: stop the lineage's live executors with
    ``keep_position`` per policy, sweep the resting orders, *then* read the venue
    — so the report describes the state the closing left and not the one it
    found.
    """
    from condor.fetchers.executors import stop_executor

    from .shutdown import ShutdownPolicy, cancel_resting_orders

    policy = policy or ShutdownPolicy()
    lineage = session_lineage(agent_slug, strategy_slug)
    if not lineage:
        return CloseoutReport(
            at=_now(), error="no lineage: the agent or strategy slug is missing"
        )

    controller_ids = lineage_controller_ids(agent_slug, strategy_slug)
    live_rows, _error = await _read_lineage_executors(
        client, agent_slug, strategy_slug, controller_ids
    )
    # Normalized once, and that is not cosmetic: ``_keep_position`` reads
    # ``connector`` (the display-row spelling the winddown's provider hands it),
    # and a raw API row spells it ``connector_name`` — so an unnormalized spot
    # row would read as a blank connector, which ``_is_perp`` treats as perp and
    # would force-close.
    targets = [
        row
        for row in _scope_rows(
            ex
            for ex in live_rows
            if str(ex.get("status") or "").strip().upper() in LIVE_EXECUTOR_STATUSES
        )
        if row["id"]
    ]

    async def _stop_one(row: dict) -> None:
        try:
            await stop_executor(
                client, row["id"], keep_position=_keep_position(row, policy)
            )
        except Exception:
            # Not collected: the venue read below has the last word on whether
            # the executor is actually gone, and that is what gets reported.
            log.warning("close_out_agent: stop %s failed", row["id"], exc_info=True)

    # Every stop at once, for the reason the winddown's are (PERF-668): on a
    # hanging API a serial loop would not even send the last stop until every
    # earlier one had hit the client timeout.
    await asyncio.gather(*(_stop_one(row) for row in targets))

    # ``None`` as the engine: ``cancel_resting_orders`` reads only its ledger,
    # and a stopped agent has none — which is also what keeps the sweep over the
    # executors' markets instead of refusing the whole scope as controller-mode.
    #
    # Scoped to the WHOLE lineage and not to ``targets``: an executor that has
    # already terminated can be exactly the one that left an order resting, and
    # that is the state this action is for. Scoped to the live rows, a stopped
    # agent swept nothing at all — ``targets`` is empty, so ``_cancel_scope``
    # has no market — and the report named the leftover order a few lines later
    # without anything having tried to cancel it. The same distinction
    # ``TickEngine._cancel_resting_orders`` draws with ``all_executors``. Stops
    # stay on the live rows above: a terminated executor has nothing to stop.
    cancel = await cancel_resting_orders(None, client, policy, _scope_rows(live_rows))

    report = await build_closeout(
        client,
        agent_slug,
        strategy_slug,
        agent_id=lineage,
        cancel=cancel,
        controller_ids=controller_ids,
    )
    if session_dir is not None:
        try:
            write_closeout(session_dir, report)
        except Exception:
            log.exception("close_out_agent: could not write the close-out record")
    return report
