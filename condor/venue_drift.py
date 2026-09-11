"""The check: Condor's book against the venue's, per ``(account, connector, pair)``.

Two sides of the same question have always been fetched and never compared.
``condor.fetchers.tracked_positions`` reads what the executors believe they
hold (``PositionHold``, in-process bookkeeping rehydrated from the API's
``position_holds`` table); ``condor.fetchers.positions`` reads what the
exchange actually holds (an authenticated REST call, not a cache). This module
is where they meet.

Vocabulary — deliberately *not* "reconcile", which ``reconcile.ts`` already owns
for two **internal** accounting methods compared against each other, both of them
correct. Here one side is simply right and the other is simply wrong:

- **the check** — the act (:func:`check`);
- **drift** — the quantity (base units, and quote when a price is available;
  the ``drift`` in ``condor.agents.canvas`` is a PnL band scoped to the canvas,
  so the collision is a word and not a concept);
- five **verdicts** — ``agreed``, ``mismatch``, ``ghost``, ``orphan``,
  ``unanswered``.

Three rules this module encodes, each the answer to a way of getting it wrong:

1. **The tracked side is summed across controllers before comparing.** A
   ``PositionHold`` is keyed by ``controller_id`` and the venue is not, so a
   pair held by two controllers would otherwise read as drift on both rows. The
   controllers survive on the row as ``controller_ids``, which is what makes the
   "yours" annotation possible.
2. **Signed net base per key, never per side.** In HEDGE mode a venue can hold a
   long and a short on one pair and in ONEWAY it nets them; comparing
   side-by-side would report drift that is purely a difference of position mode.
   ``sides`` is carried for the reader and is not part of the key. A
   ``position_side`` of ``FLAT`` contributes nothing.
3. **``venue is None`` means unanswered, and unanswered is not agreement.**
   ``trusted`` is ``False``, every row reads ``unanswered``, and ``reason``
   carries why. ``delta_quote`` is ``None`` rather than ``0.0`` wherever neither
   side offered a price — no statement is not zero.

Scope: **perps only**, because upstream's ``/trading/positions`` only queries
connectors whose name contains ``_perpetual``. Spot inventory is a genuinely
different comparison (a wallet balance is shared by everything on the account
and is not a position) and LP/CLMM truth is on-chain; both are out of scope and
named as such rather than faked here.

Pure: no I/O, no formatting decisions taken elsewhere. The provider
(``condor.agents.providers.drift``) feeds it, the risk engine reads its verdict,
and a future dashboard route calls this rather than writing a second comparison
in TypeScript.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Collection, Iterable, Mapping, Sequence

#: The five things the check can say about one ``(account, connector, pair)``.
VERDICTS = ("agreed", "mismatch", "ghost", "orphan", "unanswered")

# Dust, not drift. A partial fill, a fee taken in kind, or a position that closed
# between the two reads all land here, and an install that paged on them would be
# turned off within a week.
REL_TOLERANCE = 0.005  # 0.5% of the larger side
ABS_TOLERANCE_QUOTE = 1.0  # or under a dollar of notional, whichever is kinder

# Below this a base amount is flat. Not a tolerance — a guard against float noise
# in a summed net that ought to be exactly zero.
_FLAT_EPS = 1e-12

_SHORT_WORDS = frozenset({"short", "sell"})
_LONG_WORDS = frozenset({"long", "buy"})


@dataclass(frozen=True)
class DriftRow:
    """One ``(account, connector, pair)`` as both sides see it."""

    account: str
    connector: str
    pair: str
    tracked_base: float  # signed, summed across controllers
    venue_base: float  # signed
    delta_base: float  # tracked - venue
    delta_quote: float | None  # None when neither side priced it — never 0.0
    verdict: str
    controller_ids: tuple[str, ...] = ()  # the tracked side's, for the annotation
    sides: tuple[str, ...] = ()  # what each side called it, for display only

    @property
    def has_tracked(self) -> bool:
        return abs(self.tracked_base) > _FLAT_EPS

    @property
    def has_venue(self) -> bool:
        return abs(self.venue_base) > _FLAT_EPS


@dataclass(frozen=True)
class DriftReport:
    """The whole check, for every account either side named."""

    rows: tuple[DriftRow, ...] = ()
    trusted: bool = True  # False when the venue did not answer
    reason: str = ""  # why not, when not
    accounts: tuple[str, ...] = ()

    @property
    def drifting_count(self) -> int:
        return sum(1 for r in self.rows if r.verdict != "agreed")


# ── Reading the two shapes ──


def _as_float(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if out == out and abs(out) != float("inf") else 0.0  # NaN/inf → 0


def _side_word(row: Mapping[str, Any]) -> str:
    return str(row.get("position_side") or row.get("side") or "").strip().lower()


def _signed_base(row: Mapping[str, Any], *keys: str) -> float:
    """The row's net base, signed by what the row calls its side.

    Both shapes are accepted: a side of ``SHORT``/``SELL`` makes the amount
    negative and ``LONG``/``BUY`` makes it positive, whether or not the source
    already signed it, so a signed and an unsigned feed net out the same.
    ``FLAT`` contributes nothing. A row with no side at all makes no directional
    statement, so its amount is taken with the sign it already carries.
    """
    amount = 0.0
    for key in keys:
        if key in row and row.get(key) is not None:
            amount = _as_float(row.get(key))
            break

    side = _side_word(row)
    if side == "flat":
        return 0.0
    if side in _SHORT_WORDS:
        return -abs(amount)
    if side in _LONG_WORDS:
        return abs(amount)
    return amount


def _price_of(row: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value in (None, "", 0, 0.0):
            continue
        price = _as_float(value)
        if price > 0:
            return price
    return None


def _key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    account = str(row.get("account_name") or row.get("account") or "").strip()
    connector = str(row.get("connector_name") or row.get("connector") or "").strip()
    pair = str(row.get("trading_pair") or row.get("pair") or "").strip()
    return account, connector, pair


@dataclass
class _Side:
    """One side of one key, accumulated across however many rows fed it."""

    base: float = 0.0
    price: float | None = None
    controller_ids: list[str] = field(default_factory=list)
    sides: list[str] = field(default_factory=list)
    present: bool = False


def _fold(
    rows: Iterable[Mapping[str, Any]],
    *,
    amount_keys: Sequence[str],
    price_keys: Sequence[str],
    take_controllers: bool,
) -> dict[tuple[str, str, str], _Side]:
    """Sum one side's rows into one entry per ``(account, connector, pair)``."""
    folded: dict[tuple[str, str, str], _Side] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        entry = folded.setdefault(_key(row), _Side())
        entry.present = True
        entry.base += _signed_base(row, *amount_keys)
        if entry.price is None:
            entry.price = _price_of(row, *price_keys)
        side = _side_word(row)
        if side and side not in entry.sides:
            entry.sides.append(side)
        if take_controllers:
            cid = str(row.get("controller_id") or "").strip()
            if cid and cid not in entry.controller_ids:
                entry.controller_ids.append(cid)
    return folded


# ── The check ──


def _verdict(
    tracked: _Side | None, venue: _Side | None, delta: float, quote: float | None
) -> str:
    tracked_base = tracked.base if tracked else 0.0
    venue_base = venue.base if venue else 0.0

    within_rel = abs(delta) <= REL_TOLERANCE * max(abs(tracked_base), abs(venue_base))
    within_abs = quote is not None and abs(quote) <= ABS_TOLERANCE_QUOTE
    if abs(delta) <= _FLAT_EPS or within_rel or within_abs:
        return "agreed"

    if abs(venue_base) <= _FLAT_EPS:
        return "ghost"  # the executors track it; the venue does not report it
    if abs(tracked_base) <= _FLAT_EPS:
        return "orphan"  # the venue holds it; no executor accounts for it
    return "mismatch"


def check(
    tracked: list[dict] | None,
    venue: list[dict] | None,
    *,
    reason: str = "",
) -> DriftReport:
    """Compare the tracked book against the venue's.

    ``tracked`` are ``PositionHold`` rows (``net_amount_base``,
    ``buy_breakeven_price``, ``controller_id``); ``venue`` are exchange rows
    (``amount``, ``entry_price``, ``side``). ``venue is None`` means the venue
    did not answer — the report is untrusted, every row reads ``unanswered``,
    and ``reason`` says why. That is the one distinction that matters most: an
    unreachable venue must never be scored as agreement.
    """
    tracked_rows = _fold(
        tracked or [],
        amount_keys=("net_amount_base", "amount"),
        price_keys=("buy_breakeven_price", "entry_price", "current_price"),
        take_controllers=True,
    )

    if venue is None:
        rows = tuple(
            DriftRow(
                account=key[0],
                connector=key[1],
                pair=key[2],
                tracked_base=side.base,
                # No statement, not a zero: the venue said nothing, so the delta
                # is unknown and the verdict — not a number — carries it.
                venue_base=0.0,
                delta_base=0.0,
                delta_quote=None,
                verdict="unanswered",
                controller_ids=tuple(side.controller_ids),
                sides=tuple(side.sides),
            )
            for key, side in sorted(tracked_rows.items())
        )
        return DriftReport(
            rows=rows,
            trusted=False,
            reason=reason or "venue did not answer",
            accounts=tuple(sorted({k[0] for k in tracked_rows})),
        )

    venue_rows = _fold(
        venue,
        amount_keys=("amount", "net_amount_base"),
        price_keys=("entry_price", "buy_breakeven_price", "current_price"),
        take_controllers=False,
    )

    rows: list[DriftRow] = []
    for key in sorted(set(tracked_rows) | set(venue_rows)):
        t = tracked_rows.get(key)
        v = venue_rows.get(key)
        tracked_base = t.base if t else 0.0
        venue_base = v.base if v else 0.0
        delta = tracked_base - venue_base

        # The venue's entry price first: it is the authority on both sides of
        # this comparison. The tracked breakeven is the fallback, and when
        # neither priced it the quote delta stays None rather than becoming 0.0.
        price = (v.price if v else None) or (t.price if t else None)
        delta_quote = delta * price if price else None

        rows.append(
            DriftRow(
                account=key[0],
                connector=key[1],
                pair=key[2],
                tracked_base=tracked_base,
                venue_base=venue_base,
                delta_base=delta,
                delta_quote=delta_quote,
                verdict=_verdict(t, v, delta, delta_quote),
                controller_ids=tuple(t.controller_ids) if t else (),
                sides=tuple(
                    dict.fromkeys(
                        (list(t.sides) if t else []) + (list(v.sides) if v else [])
                    )
                ),
            )
        )

    return DriftReport(
        rows=tuple(rows),
        trusted=True,
        reason="",
        accounts=tuple(sorted({r.account for r in rows})),
    )


# ── Reading a report ──


def _mine(row: DriftRow, controller_ids: Collection[str] | None) -> bool:
    if not controller_ids:
        return False
    wanted = set(controller_ids)
    return any(cid in wanted for cid in row.controller_ids)


def drifting(
    report: DriftReport, controller_ids: Collection[str] | None = None
) -> tuple[DriftRow, ...]:
    """Rows whose verdict is not ``agreed``, optionally narrowed to ones a caller owns.

    ``None`` — the default — means "do not narrow": every drifting row, because
    the venue answers for the whole account and the account's drift is the fact.
    Passing a collection narrows to it, and an **empty** collection therefore
    narrows to nothing: a caller that owns no controller on this server is party
    to none of the drift, and must not be handed the account's as its own.
    """
    rows = tuple(r for r in report.rows if r.verdict != "agreed")
    if controller_ids is None:
        return rows
    return tuple(r for r in rows if _mine(r, controller_ids))


def worst_quote(
    report: DriftReport, controller_ids: Collection[str] | None = None
) -> float | None:
    """The largest ``|delta_quote|`` among drifting rows, or None when nothing was priced.

    ``controller_ids`` narrows exactly as :func:`drifting` does, so the gate
    reads the worst drift *this agent is party to* and not the account's.
    """
    priced = [
        abs(r.delta_quote)
        for r in drifting(report, controller_ids)
        if r.delta_quote is not None
    ]
    return max(priced) if priced else None


# ── Rendering ──


def _fmt_base(value: float) -> str:
    """A base amount, signed, with enough precision for a dust-sized position."""
    if value == 0:
        return "0"
    if abs(value) >= 1000:
        return f"{value:+,.0f}"
    if abs(value) >= 1:
        return f"{value:+,.4f}".rstrip("0").rstrip(".")
    return f"{value:+.8f}".rstrip("0").rstrip(".")


def _pct(row: DriftRow) -> str:
    base = max(abs(row.tracked_base), abs(row.venue_base))
    if base <= _FLAT_EPS:
        return ""
    return f" ({abs(row.delta_base) / base * 100:.1f}%)"


def _row_line(row: DriftRow, mine: bool) -> str:
    tracked = "none" if not row.has_tracked else _fmt_base(row.tracked_base)
    if row.verdict == "unanswered":
        venue = "unanswered"
        delta = "Δ unknown"
    else:
        venue = "none" if not row.has_venue else _fmt_base(row.venue_base)
        delta = f"Δ {_fmt_base(row.delta_base)}{_pct(row)}"
        if row.delta_quote is not None:
            delta += f" ≈ {row.delta_quote:+,.2f} quote"
    line = (
        f"  {row.verdict.upper():<10} {row.account} {row.connector} {row.pair}"
        f"  tracked {tracked}  venue {venue}  {delta}"
    )
    return line + "  ← yours" if mine else line


def summarize(
    report: DriftReport, controller_ids: Collection[str] | None = None
) -> str:
    """The ``[CORE DATA - drift]`` block.

    Formatting lives here so the provider is I/O and nothing else, and so a
    future dashboard surface renders the same verdicts from the same words.
    """
    if not report.trusted:
        head = (
            f"Book vs venue — THE VENUE DID NOT ANSWER: {report.reason}. "
            f"{len(report.rows)} tracked row(s) are unverified; treat the book "
            "as untrustworthy until it answers."
        )
        lines = [head]
        lines += [_row_line(r, _mine(r, controller_ids)) for r in report.rows]
        return "\n".join(lines)

    if not report.rows:
        return "Book vs venue — nothing tracked and nothing open on the venue: agreed."

    drift_rows = drifting(report)
    agreed = len(report.rows) - len(drift_rows)
    head = (
        f"Book vs venue — {len(report.accounts)} account(s), {len(report.rows)} row(s), "
        f"{len(drift_rows)} drifting:"
    )
    if not drift_rows:
        return f"{head}\n  all {agreed} agreed."

    lines = [head]
    mine_count = 0
    for row in drift_rows:
        mine = _mine(row, controller_ids)
        mine_count += 1 if mine else 0
        lines.append(_row_line(row, mine))

    tail = f"  {agreed} agreed."
    if controller_ids:
        tail += f" {mine_count} of {len(drift_rows)} involves your controllers."
    lines.append(tail)
    return "\n".join(lines)
