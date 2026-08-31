"""Valuation-only dedup of Hyperliquid unified-account collateral.

In unified / portfolio-margin mode the SAME USDC collateral is reported by BOTH
the spot (``hyperliquid``) and perp (``hyperliquid_perpetual``) clearinghouse
states, so any view that VALUES the portfolio counts it twice. Hyperliquid
exposes no account-mode flag, so the unified case is detected by the near-equal
shared stable balance; Hyperliquid's own guidance is to use the spot
clearinghouse state as the unified account balance.

This is deliberately PURE and presentation-only: it returns an adjusted deep
copy and never mutates its input. The raw state must stay intact because
trading views (handlers/cex/trade.py reads the same SDS PORTFOLIO cache) show
the perp collateral as what is available to trade -- for trading it is real,
it is only the portfolio TOTAL that must not count it twice.
"""

import copy
from typing import Any

# Hyperliquid stable/quote symbols (perp margin reports "USD", spot holds "USDC").
_HL_STABLES = {"USD", "USDC"}
_SPOT_CONNECTOR = "hyperliquid"
_PERP_CONNECTOR = "hyperliquid_perpetual"

UNIFIED_NOTE = (
    "Unified account — USDC balance is reflected in the hyperliquid (spot) balance."
)


def _token(item: dict) -> str:
    return item.get("token", item.get("asset", ""))


def _value(item: dict) -> float:
    return float(item.get("value", item.get("usd_value", 0)) or 0)


def _stable_total(balances: list) -> float:
    return sum(
        _value(item)
        for item in balances
        if isinstance(item, dict) and _token(item) in _HL_STABLES
    )


def dedupe_hyperliquid_unified(state: Any) -> tuple[Any, set[str]]:
    """Return (adjusted deep copy of ``state``, names of accounts adjusted).

    ``state`` is the raw portfolio shape ``{account: {connector: [balance
    dicts]}}``; anything else passes through unchanged. Per account, when both
    Hyperliquid legs hold stable value and the perp stable total matches the
    spot's within ~1% (one shared pool), the OVERLAP -- min of the two -- is
    removed from the perp side, scaling units along with value so a small
    genuine difference survives rather than being written off with the
    duplicate. No-op in standard mode, where the two balances differ.

    Caveat: with an open Hyperliquid perp position, perp equity (margin plus
    unrealized PnL) can drift more than 1% from the spot collateral, and the
    dedup stands down until the position closes. The equality gate is what
    keeps this safe for genuinely separate accounts; the drift is accepted
    rather than dedup applied on a guess.
    """
    if not isinstance(state, dict):
        return state, set()

    out = copy.deepcopy(state)
    adjusted: set[str] = set()

    for account, connectors in out.items():
        if not isinstance(connectors, dict):
            continue
        spot = connectors.get(_SPOT_CONNECTOR)
        perp = connectors.get(_PERP_CONNECTOR)
        if not isinstance(spot, list) or not isinstance(perp, list):
            continue

        spot_stable = _stable_total(spot)
        perp_stable = _stable_total(perp)
        if spot_stable <= 0 or perp_stable <= 0:
            continue
        # Unified when the perp stable collateral matches the spot stable
        # within ~1% (one shared pool).
        if abs(perp_stable - spot_stable) > max(0.01, 0.01 * spot_stable):
            continue

        remaining = min(spot_stable, perp_stable)
        stable_items = sorted(
            (
                item
                for item in perp
                if isinstance(item, dict) and _token(item) in _HL_STABLES
            ),
            key=_value,
            reverse=True,
        )
        for item in stable_items:
            if remaining <= 0:
                break
            value = _value(item)
            if value <= 0:
                continue
            take = min(value, remaining)
            remaining -= take
            scale = (value - take) / value
            for key in ("value", "usd_value"):
                if key in item and item[key] is not None:
                    item[key] = float(item[key]) * scale
            for key in (
                "units",
                "total_balance",
                "available_units",
                "available_balance",
            ):
                if key in item and item[key] is not None:
                    item[key] = float(item[key]) * scale
        adjusted.add(account)

    return out, adjusted
