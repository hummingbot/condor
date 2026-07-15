"""Hyperliquid HIP-4 outcome markets — binary Yes/No prediction contracts.

HIP-4 outcome markets are spot-like (Yes/No tokens with a *merged* order book —
buying Yes at ``p`` equals selling No at ``1-p``) and settle in a range (binary:
Yes -> 1.0, No -> 0.0). The official SDK does not model them, so this client
talks to the raw API:

- **discovery**: ``outcomeMeta`` info endpoint (outcomes + Yes/No ``sideSpecs``)
- **price/book**: ``l2Book`` with the outcome's spot coin symbol ``#<encoding>``
- **orders**: built with the outcome asset id and signed with the SDK's
  low-level ``sign_l1_action`` (the SDK's ``name_to_asset`` can't resolve them)

Asset-id encoding (per HL docs):
    encoding  = 10 * outcome + side          # side 0 = Yes, 1 = No
    coin      = f"#{encoding}"                # spot coin symbol
    asset_id  = 100_000_000 + encoding        # integer used in the order action

Builder codes work the same as spot, so the 1 bps Foundation builder fee is
attached (opt-in, resolved once) via the shared HyperliquidClient.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from hyperliquid.utils.signing import (
    get_timestamp_ms,
    order_request_to_order_wire,
    order_wires_to_order_action,
    sign_l1_action,
)

from condor.executors.hyperliquid import HyperliquidClient, HyperliquidError, OrderAck

logger = logging.getLogger(__name__)

YES, NO = 0, 1


@dataclass
class Outcome:
    id: int
    name: str
    description: str
    sides: list[str]  # e.g. ["Yes", "No"]
    quote_token: str


class HyperliquidOutcomeClient:
    """Trade HIP-4 outcome markets with the same agent key as the perp connector."""

    def __init__(self, agent_private_key: str, account_address: str,
                 network: str = "mainnet", builder_supported: bool = True):
        self._hl = HyperliquidClient(agent_private_key, account_address, network, builder_supported)
        self.info = self._hl.info
        self.exchange = self._hl.exchange
        self.account_address = account_address
        self.network = network

    # -- asset-id encoding ---------------------------------------------------

    @staticmethod
    def encoding(outcome: int, side: int) -> int:
        return 10 * outcome + side

    @classmethod
    def coin(cls, outcome: int, side: int) -> str:
        """Spot coin symbol used for market data / order coin field."""
        return f"#{cls.encoding(outcome, side)}"

    @classmethod
    def token_name(cls, outcome: int, side: int) -> str:
        """Token name used in spot balances (distinct from the coin symbol)."""
        return f"+{cls.encoding(outcome, side)}"

    @classmethod
    def asset_id(cls, outcome: int, side: int) -> int:
        return 100_000_000 + cls.encoding(outcome, side)

    # -- discovery -----------------------------------------------------------

    async def outcomes(self) -> list[Outcome]:
        meta = await asyncio.to_thread(self.info.post, "/info", {"type": "outcomeMeta"})
        return [
            Outcome(
                id=int(o["outcome"]), name=o.get("name", ""), description=o.get("description", ""),
                sides=[s["name"] for s in o.get("sideSpecs", [])], quote_token=o.get("quoteToken", "USDC"),
            )
            for o in meta.get("outcomes", [])
        ]

    async def find_outcome(self, name_or_id) -> Outcome:
        """Resolve an outcome by numeric id or (case-insensitive) name."""
        outs = await self.outcomes()
        if isinstance(name_or_id, int) or str(name_or_id).isdigit():
            oid = int(name_or_id)
            for o in outs:
                if o.id == oid:
                    return o
        else:
            low = str(name_or_id).lower()
            for o in outs:
                if o.name.lower() == low:
                    return o
        raise HyperliquidError(f"outcome {name_or_id!r} not found")

    @staticmethod
    def side_index(outcome: Outcome, side_name: str) -> int:
        for i, s in enumerate(outcome.sides):
            if s.lower() == side_name.lower():
                return i
        raise HyperliquidError(f"side {side_name!r} not in {outcome.sides}")

    # -- market data ---------------------------------------------------------

    async def book(self, outcome: int, side: int) -> dict:
        return await asyncio.to_thread(
            self.info.post, "/info", {"type": "l2Book", "coin": self.coin(outcome, side)}
        )

    async def price(self, outcome: int, side: int, want: str = "mid") -> Decimal:
        """Best bid / ask / mid for an outcome side (USDC, [0, 1])."""
        book = await self.book(outcome, side)
        levels = (book or {}).get("levels") or [[], []]
        bids, asks = levels[0], levels[1]
        best_bid = Decimal(str(bids[0]["px"])) if bids else None
        best_ask = Decimal(str(asks[0]["px"])) if asks else None
        if want == "bid":
            if best_bid is None:
                raise HyperliquidError(f"no bid for outcome {outcome} side {side}")
            return best_bid
        if want == "ask":
            if best_ask is None:
                raise HyperliquidError(f"no ask for outcome {outcome} side {side}")
            return best_ask
        if best_bid is not None and best_ask is not None:
            return (best_bid + best_ask) / 2
        return best_bid or best_ask or Decimal("0")

    async def shares(self, outcome: int, side: int) -> Decimal:
        """Held shares of an outcome token (spot balances key on the token name
        ``+<encoding>``, NOT the ``#<encoding>`` coin symbol used for orders)."""
        return await self._spot_balance(self.token_name(outcome, side))

    async def usdc_balance(self) -> Decimal:
        return await self._spot_balance("USDC")

    async def _spot_balance(self, coin: str) -> Decimal:
        state = await asyncio.to_thread(self.info.spot_user_state, self.account_address)
        for b in state.get("balances", []):
            if b.get("coin") == coin:
                return Decimal(str(b.get("total", "0")))
        return Decimal("0")

    # -- orders --------------------------------------------------------------

    async def place_limit(
        self, outcome: int, side: int, is_buy: bool, size: Decimal, price: Decimal,
        tif: str = "Gtc", reduce_only: bool = False,
    ) -> OrderAck:
        """Signed limit order on the outcome market, built with the outcome asset
        id and posted via the SDK's low-level action signer."""
        asset = self.asset_id(outcome, side)
        order = {
            "coin": self.coin(outcome, side),
            "is_buy": is_buy,
            "sz": float(size),
            "limit_px": self._hl._round_px(price),
            "order_type": {"limit": {"tif": tif}},
            "reduce_only": reduce_only,
        }
        builder = await self._hl._builder()

        def _send():
            wire = order_request_to_order_wire(order, asset)
            action = order_wires_to_order_action([wire], builder, "na")
            ts = get_timestamp_ms()
            ex = self.exchange
            sig = sign_l1_action(
                ex.wallet, action, ex.vault_address, ts, ex.expires_after,
                self.network == "mainnet",
            )
            return ex._post_action(action, sig, ts)

        return _parse_ack(await asyncio.to_thread(_send))

    async def marketable_buy(
        self, outcome: int, side: int, size: Decimal, slippage_pct: Decimal = Decimal("2"),
    ) -> OrderAck:
        """Cross the spread: an IOC buy priced above the ask (capped at 1.0)."""
        ask = await self.price(outcome, side, "ask")
        px = min(ask * (1 + slippage_pct / 100), Decimal("0.999"))
        return await self.place_limit(outcome, side, True, size, px, tif="Ioc")

    async def marketable_sell(
        self, outcome: int, side: int, size: Decimal, slippage_pct: Decimal = Decimal("2"),
    ) -> OrderAck:
        """Cross the spread: an IOC sell priced below the bid (floored at 0.001)."""
        bid = await self.price(outcome, side, "bid")
        px = max(bid * (1 - slippage_pct / 100), Decimal("0.001"))
        return await self.place_limit(outcome, side, False, size, px, tif="Ioc")

    async def cancel(self, outcome: int, side: int, oid: int) -> None:
        # cancel takes the asset via name_to_asset in the SDK; build raw like orders
        from hyperliquid.utils.signing import sign_l1_action as _sign

        asset = self.asset_id(outcome, side)

        def _send():
            action = {"type": "cancel", "cancels": [{"a": asset, "o": oid}]}
            ts = get_timestamp_ms()
            ex = self.exchange
            sig = _sign(ex.wallet, action, ex.vault_address, ts, ex.expires_after,
                        self.network == "mainnet")
            return ex._post_action(action, sig, ts)

        await asyncio.to_thread(_send)

    async def close(self) -> None:
        return None


def _parse_ack(resp) -> OrderAck:
    if not isinstance(resp, dict) or resp.get("status") != "ok":
        raise HyperliquidError(f"outcome order rejected: {resp}")
    st = resp["response"]["data"]["statuses"][0]
    if "error" in st:
        return OrderAck(oid=None, status="error", detail=st["error"])
    if "resting" in st:
        return OrderAck(oid=int(st["resting"]["oid"]), status="resting")
    if "filled" in st:
        return OrderAck(oid=int(st["filled"]["oid"]), status="filled")
    raise HyperliquidError(f"unexpected outcome order status: {st}")
