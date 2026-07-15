"""Polymarket CLOB connector — prediction-market outcome tokens (CLOB **V2**).

Wraps Polymarket's official ``py-clob-client-v2`` (the current CLOB V2 client;
the v1 client signs an order version the API now rejects with "invalid order
version"). A Polymarket market has two ERC-1155 outcome tokens (YES / NO) priced
in USDC on [0, 1]; you BUY/SELL shares of a ``token_id`` on a central limit
order book. This connector mirrors ``HyperliquidClient``: quotes + order
placement/cancel + balances, keyed by one Polygon signing key.

Auth levels: public reads (price / book / midpoint) need no creds; order
placement needs L2 API creds, derived from the signing key on first use
(``create_or_derive_api_key``) and cached.

signature_type selects the wallet model:
  0 = EOA (browser wallet signs, holds its own USDC)
  1 = Magic/email proxy   2 = Polymarket Gnosis-safe proxy (browser-wallet accts)
For 1/2, ``funder`` is the proxy address that actually holds the USDC.

Every order sets ``tick_size`` + ``neg_risk`` (fetched per token): neg-risk
(multi-outcome) markets use a different exchange contract and are rejected
without the flag.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import (
    ApiCreds,
    AssetType,
    BalanceAllowanceParams,
    MarketOrderArgsV2,
    OrderArgsV2,
    OrderPayload,
    OrderType,
    PartialCreateOrderOptions,
)
from py_clob_client_v2.order_builder.constants import BUY, SELL

logger = logging.getLogger(__name__)

DEFAULT_HOST = "https://clob.polymarket.com"
POLYGON = 137
USDC_DECIMALS = Decimal(10) ** 6


class PolymarketError(RuntimeError):
    pass


@dataclass
class OrderAck:
    order_id: Optional[str]
    status: str  # "live" | "matched" | "delayed" | "error"
    success: bool
    detail: Optional[str] = None


class PolymarketClient:
    def __init__(
        self,
        private_key: str,
        funder: Optional[str] = None,
        signature_type: int = 0,
        creds: Optional[dict] = None,
        host: str = DEFAULT_HOST,
        chain_id: int = POLYGON,
    ):
        if signature_type not in (0, 1, 2):
            raise ValueError(f"signature_type must be 0|1|2, got {signature_type}")
        self.signature_type = signature_type
        api_creds = (
            ApiCreds(creds["api_key"], creds["api_secret"], creds["api_passphrase"])
            if creds else None
        )
        self.client = ClobClient(
            host, chain_id=chain_id, key=private_key,
            creds=api_creds, signature_type=signature_type, funder=funder,
        )
        self._authed = api_creds is not None

    @property
    def address(self) -> str:
        return self.client.get_address()

    async def ensure_auth(self) -> None:
        """Derive + attach L2 API creds from the signing key (once)."""
        if self._authed:
            return
        creds = await asyncio.to_thread(self.client.create_or_derive_api_key)
        self.client.set_api_creds(creds)
        self._authed = True

    # -- market data (public) ------------------------------------------------

    async def price(self, token_id: str, side: str = "BUY") -> Decimal:
        """Best price for a side (BUY = best ask you'd pay, SELL = best bid)."""
        resp = await asyncio.to_thread(self.client.get_price, token_id, _side(side))
        return Decimal(str(resp["price"]))

    async def midpoint(self, token_id: str) -> Decimal:
        resp = await asyncio.to_thread(self.client.get_midpoint, token_id)
        return Decimal(str(resp["mid"]))

    async def order_book(self, token_id: str):
        return await asyncio.to_thread(self.client.get_order_book, token_id)

    # -- orders --------------------------------------------------------------

    async def place_limit(
        self, token_id: str, side: str, price: Decimal, size: Decimal,
        order_type: str = "GTC",
    ) -> OrderAck:
        """Signed limit order: BUY/SELL ``size`` shares at ``price`` (USDC, [0,1])."""
        await self.ensure_auth()
        args = OrderArgsV2(token_id=token_id, price=float(price), size=float(size), side=_side(side))
        opts = await self._order_options(token_id)
        resp = await asyncio.to_thread(
            self.client.create_and_post_order, args, opts, _order_type(order_type)
        )
        return self._parse_ack(resp)

    async def place_market(self, token_id: str, side: str, amount: Decimal) -> OrderAck:
        """Marketable order. BUY: ``amount`` is USDC to spend; SELL: shares to sell."""
        await self.ensure_auth()
        args = MarketOrderArgsV2(token_id=token_id, amount=float(amount), side=_side(side))
        opts = await self._order_options(token_id)
        resp = await asyncio.to_thread(
            self.client.create_and_post_market_order, args, opts, OrderType.FOK
        )
        return self._parse_ack(resp)

    async def _order_options(self, token_id: str) -> PartialCreateOrderOptions:
        """tick_size + neg_risk per token. neg-risk markets use a different
        exchange contract; both are required for the CLOB to accept the order."""
        tick_size = await asyncio.to_thread(self.client.get_tick_size, token_id)
        neg_risk = await asyncio.to_thread(self.client.get_neg_risk, token_id)
        return PartialCreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)

    async def cancel(self, order_id: str) -> None:
        await self.ensure_auth()
        await asyncio.to_thread(self.client.cancel_order, OrderPayload(orderID=order_id))

    async def cancel_all(self) -> None:
        await self.ensure_auth()
        await asyncio.to_thread(self.client.cancel_all)

    async def orders(self, market: Optional[str] = None, asset_id: Optional[str] = None) -> list:
        await self.ensure_auth()
        params = None
        if market or asset_id:
            from py_clob_client_v2.clob_types import OpenOrderParams
            params = OpenOrderParams(market=market, asset_id=asset_id)
        return await asyncio.to_thread(self.client.get_open_orders, params)

    async def get_order(self, order_id: str) -> dict:
        await self.ensure_auth()
        return await asyncio.to_thread(self.client.get_order, order_id)

    # -- balances ------------------------------------------------------------

    async def usdc_balance(self) -> Decimal:
        """Available USDC collateral (UI units)."""
        await self.ensure_auth()
        params = BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL, signature_type=self.signature_type
        )
        resp = await asyncio.to_thread(self.client.get_balance_allowance, params)
        return Decimal(str(resp["balance"])) / USDC_DECIMALS

    async def shares_balance(self, token_id: str) -> Decimal:
        """Held shares of an outcome token (UI units)."""
        await self.ensure_auth()
        params = BalanceAllowanceParams(
            asset_type=AssetType.CONDITIONAL, token_id=token_id,
            signature_type=self.signature_type,
        )
        resp = await asyncio.to_thread(self.client.get_balance_allowance, params)
        return Decimal(str(resp["balance"])) / USDC_DECIMALS

    # -- markets -------------------------------------------------------------

    async def market(self, condition_id: str) -> dict:
        return await asyncio.to_thread(self.client.get_market, condition_id)

    # -- parsing -------------------------------------------------------------

    @staticmethod
    def _parse_ack(resp) -> OrderAck:
        if not isinstance(resp, dict):
            raise PolymarketError(f"unexpected order response: {resp!r}")
        success = bool(resp.get("success"))
        order_id = resp.get("orderID") or resp.get("orderId")
        if not success and not order_id:
            return OrderAck(order_id=None, status="error", success=False,
                            detail=resp.get("errorMsg") or resp.get("error") or str(resp))
        return OrderAck(order_id=order_id, status=resp.get("status", "unknown"), success=success)

    async def close(self) -> None:
        return None


def _side(side: str) -> str:
    s = side.upper()
    if s == "BUY":
        return BUY
    if s == "SELL":
        return SELL
    raise ValueError(f"side must be BUY|SELL, got {side!r}")


def _order_type(order_type: str):
    ot = order_type.upper()
    if not hasattr(OrderType, ot):
        raise ValueError(f"order_type must be one of GTC|GTD|FOK|FAK, got {order_type!r}")
    return getattr(OrderType, ot)
