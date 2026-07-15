"""Hyperliquid spot connector — barrier-managed spot positions via the swap API.

HL spot pairs are ``<token>/USDC`` (the SDK resolves the pair name to asset
``10000 + spotIndex``). This client exposes the **same swap-interface** the
``position`` / ``swap`` executors call on the Solana/Jupiter connector
(``quote_swap`` / ``execute_swap`` / ``poll_tx`` / ``get_balances``), so those
executors run on Hyperliquid spot with no changes — routed by ``venue="hyperliquid"``.

Orders are marketable IOC limits through the SDK's ``exchange.order`` (spot is
supported natively; the 1 bps Foundation builder code applies like spot). Amounts
are UI units; ``price`` is USDC per token. Reuses ``HyperliquidClient`` for the
info/exchange objects, builder-fee resolution, price rounding, and fill parsing.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Optional

from condor.venues.hyperliquid.client import HyperliquidClient, HyperliquidError

logger = logging.getLogger(__name__)


class HyperliquidSpotClient:
    def __init__(self, agent_private_key: str, account_address: str,
                 network: str = "mainnet", builder_supported: bool = True):
        self._hl = HyperliquidClient(agent_private_key, account_address, network, builder_supported)
        self.info = self._hl.info
        self.exchange = self._hl.exchange
        self.account_address = account_address
        self.network = network
        self._pairs: Optional[dict[str, tuple[str, int]]] = None  # token -> (pair_name, szDecimals)

    async def _load_pairs(self) -> dict[str, tuple[str, int]]:
        if self._pairs is None:
            sm = await asyncio.to_thread(self.info.spot_meta)
            by_idx = {t["index"]: t for t in sm["tokens"]}
            usdc_idx = next(t["index"] for t in sm["tokens"] if t["name"] == "USDC")
            pairs: dict[str, tuple[str, int]] = {}
            for u in sm["universe"]:
                a, b = u["tokens"]
                base_idx = a if b == usdc_idx else (b if a == usdc_idx else None)
                if base_idx is None:
                    continue  # non-USDC-quoted pair
                bt = by_idx.get(base_idx)
                if bt:
                    pairs[bt["name"]] = (u["name"], int(bt["szDecimals"]))
            self._pairs = pairs
        return self._pairs

    async def _resolve(self, base_token: str, quote_token: str) -> tuple[str, bool, int]:
        """Return (pair_name, buying_the_token, szDecimals).

        ``buying_the_token`` is True when the *input* (base_token, on a SELL) is
        USDC — i.e. we BUY the token/USDC pair. HL spot is USDC-quoted, so exactly
        one of base/quote must be USDC.
        """
        b_usdc, q_usdc = base_token.upper() == "USDC", quote_token.upper() == "USDC"
        if b_usdc == q_usdc:
            raise HyperliquidError("HL spot is USDC-quoted; exactly one of base/quote must be USDC")
        token = quote_token if b_usdc else base_token
        pairs = await self._load_pairs()
        if token not in pairs:
            raise HyperliquidError(f"no Hyperliquid spot pair {token}/USDC")
        pair_name, sz_dec = pairs[token]
        return pair_name, b_usdc, sz_dec

    async def _book(self, pair_name: str) -> tuple[Optional[Decimal], Optional[Decimal]]:
        book = await asyncio.to_thread(self.info.post, "/info", {"type": "l2Book", "coin": pair_name})
        levels = (book or {}).get("levels") or [[], []]
        bid = Decimal(str(levels[0][0]["px"])) if levels[0] else None
        ask = Decimal(str(levels[1][0]["px"])) if levels[1] else None
        return bid, ask

    # -- swap interface ------------------------------------------------------

    async def quote_swap(self, chain_network, base_token, quote_token, amount, side,
                         connector=None, slippage_pct=None) -> dict:
        pair_name, buying, _ = await self._resolve(base_token, quote_token)
        bid, ask = await self._book(pair_name)
        px = (ask if buying else bid) or bid or ask  # USDC per token
        if px is None:
            raise HyperliquidError(f"no book for {pair_name}")
        return {"price": float(px)}

    async def execute_swap(self, chain_network, wallet_address, base_token, quote_token, amount, side,
                           connector=None, slippage_pct=None) -> dict:
        pair_name, buying, sz_dec = await self._resolve(base_token, quote_token)
        bid, ask = await self._book(pair_name)
        slip = Decimal(str(slippage_pct if slippage_pct is not None else 1.0)) / 100
        amount = Decimal(str(amount))

        if buying:
            # spend `amount` USDC to buy the token — size in token units at the ask
            if not ask:
                raise HyperliquidError(f"no ask for {pair_name}")
            fill = await self._marketable(pair_name, True, amount / ask, sz_dec, ask * (1 + slip))
            amount_out = fill.size                    # token received
            amount_in = fill.size * fill.avg_px       # USDC spent
        else:
            # sell `amount` token for USDC
            if not bid:
                raise HyperliquidError(f"no bid for {pair_name}")
            fill = await self._marketable(pair_name, False, amount, sz_dec, bid * (1 - slip))
            amount_out = fill.size * fill.avg_px       # USDC received
            amount_in = fill.size                      # token sold

        return {
            "signature": str(fill.oid),
            "status": 1,
            "data": {"amountIn": float(amount_in), "amountOut": float(amount_out), "fee": 0.0},
        }

    async def _marketable(self, pair_name: str, is_buy: bool, size: Decimal, sz_dec: int, limit_px: Decimal):
        sz = float(size.quantize(Decimal(10) ** -sz_dec))
        if sz <= 0:
            raise HyperliquidError(f"order size rounds to 0 at {sz_dec} decimals")
        px = self._hl._round_px(limit_px)
        builder = await self._hl._builder()
        resp = await asyncio.to_thread(
            self.exchange.order, pair_name, is_buy, sz, px, {"limit": {"tif": "Ioc"}}, False, None, builder
        )
        return self._hl._parse_fill(resp)

    async def poll_tx(self, chain: str, network: str, signature: str) -> dict:
        return {"txStatus": 1}  # HL IOC orders settle synchronously

    async def get_balances(self, chain: str, network: str, address: str,
                           tokens: Optional[list[str]] = None) -> dict[str, float]:
        state = await asyncio.to_thread(self.info.spot_user_state, self.account_address)
        bals = {b["coin"]: float(b.get("total", 0)) for b in state.get("balances", [])}
        return {t: bals.get(t, 0.0) for t in tokens} if tokens else bals

    async def save_token(self, chain_network: str, address: str) -> dict:
        return {"address": address, "registered": True}  # HL pairs resolve by name

    async def close(self) -> None:
        return None
