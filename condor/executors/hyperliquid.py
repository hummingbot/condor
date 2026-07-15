"""Unified Hyperliquid connector — one venue for perp (and later spot).

Wraps the official ``hyperliquid-python-sdk`` (synchronous; calls are run in a
thread so they never block the executor loop). Unlike hummingbot's split
``hyperliquid`` / ``hyperliquid_perpetual`` connectors, this is a single client
keyed by one agent (API-wallet) key and one main account address.

Builder code: the same Foundation builder as hummingbot's connector
(0x10BA451e…0705, 1 bps = 10 tenths-bps). Opt-in and self-limiting — the
per-order fee is resolved once as ``min(on-chain approved max, 10)``: 1 bps only
if the user approved it in the dashboard, 0 otherwise. Attached to mainnet,
non-vault orders only.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

logger = logging.getLogger(__name__)

FOUNDATION_BUILDER_ADDRESS = "0x10BA451e6439Efc6a17dc20d21121Aa838100705"
FOUNDATION_BUILDER_FEE_TENTHS_BPS = 10  # 10 tenths-of-a-bp = 1 bp = 0.01%


class HyperliquidError(RuntimeError):
    pass


@dataclass
class Fill:
    oid: int
    avg_px: Decimal
    size: Decimal


@dataclass
class OrderAck:
    oid: Optional[int]
    status: str  # "resting" | "filled" | "error"
    detail: Optional[str] = None


@dataclass
class Position:
    coin: str
    side: str  # "LONG" | "SHORT"
    size: Decimal  # absolute base units
    entry_px: Decimal
    unrealized_pnl: Decimal
    liquidation_px: Optional[Decimal]
    margin_used: Decimal
    leverage: Optional[int]
    funding: Decimal


@dataclass
class AccountSummary:
    account_value: Decimal
    withdrawable: Decimal
    total_margin_used: Decimal


class HyperliquidClient:
    def __init__(
        self,
        agent_private_key: str,
        account_address: str,
        network: str = "mainnet",
        builder_supported: bool = True,
    ):
        if network not in ("mainnet", "testnet"):
            raise ValueError(f"network must be mainnet|testnet, got {network!r}")
        self.network = network
        self.account_address = account_address
        self.builder_supported = builder_supported
        base_url = constants.MAINNET_API_URL if network == "mainnet" else constants.TESTNET_API_URL
        wallet = Account.from_key(agent_private_key)
        self.info = Info(base_url, skip_ws=True)
        self.exchange = Exchange(wallet, base_url, account_address=account_address)
        self._builder_fee_tenths_bps: Optional[int] = None  # resolved lazily
        self._sz_decimals: dict[str, int] = {}

    # -- builder code --------------------------------------------------------

    def _should_inject_builder(self) -> bool:
        # Mainnet, non-vault only (we never trade a vault here); testnet rejects it.
        return self.builder_supported and self.network == "mainnet"

    async def ensure_builder_fee(self) -> int:
        """Resolve the per-order builder fee once: min(on-chain approved, 10).
        0 if not approved, or if the lookup fails (charge nothing that session)."""
        if self._builder_fee_tenths_bps is not None:
            return self._builder_fee_tenths_bps
        if not self._should_inject_builder():
            self._builder_fee_tenths_bps = 0
            return 0
        try:
            approved = int(await asyncio.to_thread(
                self.info.post, "/info",
                {"type": "maxBuilderFee", "user": self.account_address, "builder": FOUNDATION_BUILDER_ADDRESS},
            ))
        except Exception:
            logger.warning("could not query approved Hyperliquid builder fee; charging 0 bps", exc_info=True)
            self._builder_fee_tenths_bps = 0
            return 0
        self._builder_fee_tenths_bps = min(approved, FOUNDATION_BUILDER_FEE_TENTHS_BPS)
        return self._builder_fee_tenths_bps

    async def _builder(self) -> Optional[dict]:
        fee = await self.ensure_builder_fee()
        if not self._should_inject_builder() or fee <= 0:
            return None
        return {"b": FOUNDATION_BUILDER_ADDRESS.lower(), "f": fee}

    # -- metadata ------------------------------------------------------------

    async def sz_decimals(self, coin: str) -> int:
        if not self._sz_decimals:
            meta = await asyncio.to_thread(self.info.meta)
            self._sz_decimals = {a["name"]: int(a["szDecimals"]) for a in meta["universe"]}
        if coin not in self._sz_decimals:
            raise HyperliquidError(f"unknown coin {coin!r} (not in perp universe)")
        return self._sz_decimals[coin]

    async def _round_sz(self, coin: str, size: Decimal) -> float:
        dec = await self.sz_decimals(coin)
        q = Decimal(10) ** -dec
        return float(size.quantize(q))

    @staticmethod
    def _round_px(price: Decimal) -> float:
        """Hyperliquid price precision: <=5 significant figures, then <=6 decimals
        (mirrors hummingbot's HyperliquidPerpetualDerivative.quantize_order_price).
        Raw prices with more precision are rejected by the venue."""
        return round(float(f"{float(price):.5g}"), 6)

    # -- orders --------------------------------------------------------------

    async def set_leverage(self, coin: str, leverage: int, cross: bool = True) -> None:
        await asyncio.to_thread(self.exchange.update_leverage, leverage, coin, cross)

    async def market_open(self, coin: str, is_buy: bool, size: Decimal, slippage_pct: Decimal) -> Fill:
        sz = await self._round_sz(coin, size)
        builder = await self._builder()
        resp = await asyncio.to_thread(
            self.exchange.market_open, coin, is_buy, sz, None, float(slippage_pct) / 100, None, builder
        )
        return self._parse_fill(resp)

    async def place_limit(
        self, coin: str, is_buy: bool, size: Decimal, price: Decimal,
        tif: str = "Gtc", reduce_only: bool = False,
    ) -> OrderAck:
        sz = await self._round_sz(coin, size)
        builder = await self._builder()
        resp = await asyncio.to_thread(
            self.exchange.order, coin, is_buy, sz, self._round_px(price),
            {"limit": {"tif": tif}}, reduce_only, None, builder,
        )
        return self._parse_ack(resp)

    async def place_trigger(
        self, coin: str, is_buy: bool, size: Decimal, trigger_px: Decimal,
        kind: str, reduce_only: bool = True,
    ) -> OrderAck:
        """Reduce-only TP ('tp') or SL ('sl') market trigger — the daemon-down backstop."""
        sz = await self._round_sz(coin, size)
        px = self._round_px(trigger_px)
        order_type = {"trigger": {"triggerPx": px, "isMarket": True, "tpsl": kind}}
        resp = await asyncio.to_thread(
            self.exchange.order, coin, is_buy, sz, px, order_type, reduce_only,
        )
        return self._parse_ack(resp)

    async def market_close(self, coin: str, size: Optional[Decimal] = None) -> Fill:
        sz = await self._round_sz(coin, size) if size is not None else None
        resp = await asyncio.to_thread(self.exchange.market_close, coin, sz)
        return self._parse_fill(resp)

    async def cancel(self, coin: str, oid: int) -> None:
        await asyncio.to_thread(self.exchange.cancel, coin, oid)

    # -- reads ---------------------------------------------------------------

    async def order_status(self, oid: int) -> dict:
        return await asyncio.to_thread(self.info.query_order_by_oid, self.account_address, oid)

    async def position(self, coin: str) -> Optional[Position]:
        state = await asyncio.to_thread(self.info.user_state, self.account_address)
        for ap in state.get("assetPositions", []):
            p = ap.get("position", {})
            if p.get("coin") != coin:
                continue
            szi = Decimal(str(p.get("szi", "0")))
            if szi == 0:
                return None
            lev = p.get("leverage") or {}
            return Position(
                coin=coin,
                side="LONG" if szi > 0 else "SHORT",
                size=abs(szi),
                entry_px=Decimal(str(p.get("entryPx") or "0")),
                unrealized_pnl=Decimal(str(p.get("unrealizedPnl") or "0")),
                liquidation_px=Decimal(str(p["liquidationPx"])) if p.get("liquidationPx") else None,
                margin_used=Decimal(str(p.get("marginUsed") or "0")),
                leverage=int(lev["value"]) if lev.get("value") is not None else None,
                funding=Decimal(str((p.get("cumFunding") or {}).get("sinceOpen", "0"))),
            )
        return None

    async def fills(self, since_ms: Optional[int] = None) -> list[dict]:
        if since_ms is not None:
            return await asyncio.to_thread(self.info.user_fills_by_time, self.account_address, since_ms)
        return await asyncio.to_thread(self.info.user_fills, self.account_address)

    async def mid_price(self, coin: str) -> Decimal:
        mids = await asyncio.to_thread(self.info.all_mids)
        if coin not in mids:
            raise HyperliquidError(f"no mid price for {coin!r}")
        return Decimal(str(mids[coin]))

    async def account(self) -> AccountSummary:
        state = await asyncio.to_thread(self.info.user_state, self.account_address)
        ms = state.get("marginSummary", {})
        return AccountSummary(
            account_value=Decimal(str(ms.get("accountValue", "0"))),
            withdrawable=Decimal(str(state.get("withdrawable", "0"))),
            total_margin_used=Decimal(str(ms.get("totalMarginUsed", "0"))),
        )

    # -- response parsing ----------------------------------------------------

    @staticmethod
    def _statuses(resp: Any) -> list[dict]:
        if not isinstance(resp, dict) or resp.get("status") != "ok":
            raise HyperliquidError(f"hyperliquid order rejected: {resp}")
        return resp["response"]["data"]["statuses"]

    def _parse_fill(self, resp: Any) -> Fill:
        statuses = self._statuses(resp)
        for st in statuses:
            if "error" in st:
                raise HyperliquidError(f"order error: {st['error']}")
            if "filled" in st:
                f = st["filled"]
                return Fill(oid=int(f["oid"]), avg_px=Decimal(str(f["avgPx"])), size=Decimal(str(f["totalSz"])))
        raise HyperliquidError(f"expected a fill, got: {statuses}")

    def _parse_ack(self, resp: Any) -> OrderAck:
        statuses = self._statuses(resp)
        st = statuses[0]
        if "error" in st:
            return OrderAck(oid=None, status="error", detail=st["error"])
        if "resting" in st:
            return OrderAck(oid=int(st["resting"]["oid"]), status="resting")
        if "filled" in st:
            return OrderAck(oid=int(st["filled"]["oid"]), status="filled")
        raise HyperliquidError(f"unexpected order status: {st}")

    async def close(self) -> None:
        # Info opens a websocket unless skip_ws; we pass skip_ws=True, nothing to close.
        return None
