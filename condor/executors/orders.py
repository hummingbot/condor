"""The uniform ``orders[]`` collection — the sole durable financial authority
per executor (§6.2b).

Every executor state carries ONE ``orders`` list holding only
**venue-acknowledged** orders, keyed by their unique ``venue_order_id``. A
rejected submission is an executor transition/error, not a fake order record;
a landed retry is a new entry under its own id. Deliberately derived, never
stored: ``average_fill_price`` (= filled_quote / filled_base) and
partial-fill status (= OPEN with filled > 0).

Update semantics are **idempotent absolute-cumulative**: polls overwrite
with the venue's absolute cumulative state, never add the latest observed
delta. A venue that exposes only fill deltas persists a compact
``cursor``/seen-fill-id value inside the owning entry so a restart never
double-counts a replayed delta.

Folds (signed inventory, ownership, reservation, performance) all run over
this one collection with no per-kind branching.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class OrderRole(str, Enum):
    TRADE = "trade"  # standalone single-leg order (order-kind executor)
    ENTRY = "entry"  # position leg opening exposure
    EXIT = "exit"  # position leg reducing/closing exposure
    PROTECTION = "protection"  # native TP/SL trigger


class OrderStatus(str, Enum):
    OPEN = "OPEN"
    CANCEL_PENDING = "CANCEL_PENDING"  # load-bearing for stop/lease lifecycle
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    UNKNOWN = "UNKNOWN"  # landed order whose state is temporarily unresolvable

    @property
    def is_live(self) -> bool:
        return self in (OrderStatus.OPEN, OrderStatus.CANCEL_PENDING)


class LandedOrder(BaseModel):
    """One venue-acknowledged order, cumulative-by-construction."""

    venue_order_id: str
    client_order_id: Optional[str] = None
    role: OrderRole
    side: str  # "buy" | "sell"
    requested_qty: Decimal = Decimal("0")
    requested_unit: str = "base"  # "base" | "quote" (Jupiter buys are quote-in)
    cumulative_filled_base_qty: Decimal = Decimal("0")
    cumulative_filled_quote_qty: Decimal = Decimal("0")
    fees_by_asset: dict[str, Decimal] = Field(default_factory=dict)
    status: OrderStatus = OrderStatus.OPEN
    # Delta-only venues: compact cursor / last-seen-fill-id for restart-safe
    # dedup. None for venues reporting absolute cumulative state.
    cursor: Optional[str] = None

    def average_fill_price(self) -> Optional[Decimal]:
        """DERIVED, never stored."""
        if self.cumulative_filled_base_qty > 0:
            return self.cumulative_filled_quote_qty / self.cumulative_filled_base_qty
        return None

    def apply_absolute(
        self,
        *,
        filled_base: Decimal | None = None,
        filled_quote: Decimal | None = None,
        fees_by_asset: dict[str, Decimal] | None = None,
        status: OrderStatus | str | None = None,
        cursor: str | None = None,
    ) -> None:
        """Overwrite with the venue's ABSOLUTE cumulative state (idempotent —
        applying the same poll twice changes nothing)."""
        if filled_base is not None:
            self.cumulative_filled_base_qty = Decimal(filled_base)
        if filled_quote is not None:
            self.cumulative_filled_quote_qty = Decimal(filled_quote)
        if fees_by_asset is not None:
            self.fees_by_asset = {k: Decimal(v) for k, v in fees_by_asset.items()}
        if status is not None:
            self.status = OrderStatus(status)
        if cursor is not None:
            self.cursor = cursor


# ---------------------------------------------------------------------------
# Collection helpers (fold over one executor's — or one scope's — orders[])
# ---------------------------------------------------------------------------


def upsert_landed(orders: list[LandedOrder], entry: LandedOrder) -> LandedOrder:
    """Insert a newly landed order, or return the existing entry for its id
    (idempotent landing — an MCP retry that re-observes the same venue id
    must not duplicate it)."""
    for o in orders:
        if o.venue_order_id == entry.venue_order_id:
            return o
    orders.append(entry)
    return entry


def find_order(orders: list[LandedOrder], venue_order_id: str) -> Optional[LandedOrder]:
    for o in orders:
        if o.venue_order_id == venue_order_id:
            return o
    return None


def live_orders(orders: list[LandedOrder]) -> list[LandedOrder]:
    """Orders still working on the venue (OPEN / CANCEL_PENDING) — the set
    stop must cancel and the lease must outlive."""
    return [o for o in orders if o.status.is_live]


def owned_net_base(
    orders: list[LandedOrder],
    *,
    product: str,
    base_asset: str = "",
) -> Decimal:
    """Remaining signed inventory from the scope's landed fills (§6.2).

    Product-specific because fees can be charged in the base asset:

    - ``derivatives`` (netted): Σ gross buy fills − Σ gross sell fills
      (fees never change a derivative position's size);
    - ``spot`` / ``pred`` holdings: gross buys − gross sells − Σ base-asset
      fees (quote-/third-asset fees affect performance only).

    Close orders size to ``−owned_net_base`` — never raw gross entry fills,
    which over-close after a partial exit, a cancel/fill race, base-asset
    fees, or offsetting two-sided fills.
    """
    net = Decimal("0")
    base_fees = Decimal("0")
    for o in orders:
        if o.role == OrderRole.PROTECTION and o.cumulative_filled_base_qty == 0:
            continue  # unfilled trigger contributes nothing
        signed = o.cumulative_filled_base_qty
        net += signed if o.side == "buy" else -signed
        if product in ("spot", "pred") and base_asset:
            fee = o.fees_by_asset.get(base_asset)
            if fee:
                base_fees += Decimal(fee)
    if product in ("spot", "pred"):
        net -= base_fees
    return net


def unfilled_remainder(order: LandedOrder) -> Decimal:
    """The live unfilled remainder of a landed order, in its requested unit
    (risk bucket 3 sizes from this for risk-INCREASING orders)."""
    if not order.status.is_live:
        return Decimal("0")
    if order.requested_unit == "base":
        rem = order.requested_qty - order.cumulative_filled_base_qty
    else:
        rem = order.requested_qty - order.cumulative_filled_quote_qty
    return rem if rem > 0 else Decimal("0")


def orders_digest(orders: list[LandedOrder]) -> str:
    """Recovery digest covering EVERY recovery-relevant field (§6.2b): ids,
    cumulative quantities, fees, status, client id, cursor. An
    identifier-only transition (a newly landed venue id) or a new partial
    fill always changes the digest, so persist() never dedups it away."""
    payload = [
        {
            "v": o.venue_order_id,
            "c": o.client_order_id,
            "r": o.role.value,
            "s": o.side,
            "rq": str(o.requested_qty),
            "ru": o.requested_unit,
            "fb": str(o.cumulative_filled_base_qty),
            "fq": str(o.cumulative_filled_quote_qty),
            "fees": {k: str(v) for k, v in sorted(o.fees_by_asset.items())},
            "st": o.status.value,
            "cur": o.cursor,
        }
        for o in orders
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
