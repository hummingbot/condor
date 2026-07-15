"""Jupiter swap protocol client + a Gateway-compatible connector adapter.

``JupiterSwap`` builds unsigned swap transactions (quote → /swap); ``SolanaConnector``
signs/sends/polls them. ``JupiterConnector`` glues the two behind the *exact*
method surface the executor connector interface requires — the one the
``SpotAdapter`` calls (quote_swap / execute_swap / poll_tx / get_balances /
save_token), so order_spot / position_spot run natively on Solana.

Endpoint is Jupiter's public swap API; override with CONDOR_JUPITER_URL. Set
CONDOR_JUPITER_API_KEY to use the keyed Pro endpoint (api.jup.ag) with the
``x-api-key`` header — needed to survive the free tier's ~1 req/s limit under
multi-position polling.
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Any, Optional

import httpx

from condor.venues.solana.connector import WSOL_MINT, SolanaConnector

logger = logging.getLogger(__name__)

DEFAULT_JUPITER_URL = "https://lite-api.jup.ag/swap/v1"  # keyless (free, ~1 req/s)
KEYED_JUPITER_URL = "https://api.jup.ag/swap/v1"  # with an API key (x-api-key header)

# Canonical mints for the symbols Condor quotes against (on-chain identities).
_SYMBOL_MINTS = {
    "SOL": WSOL_MINT,
    "WSOL": WSOL_MINT,
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}


class JupiterError(RuntimeError):
    def __init__(self, route: str, status: int, body: str):
        self.route = route
        self.status = status
        self.body = body
        super().__init__(f"jupiter {route} -> HTTP {status}: {body[:400]}")


class JupiterSwap:
    def __init__(self, base_url: Optional[str] = None, client: Optional[httpx.AsyncClient] = None):
        # A key unlocks the Pro endpoint + higher rate limits; default the base
        # URL to the keyed host when one is set (unless explicitly overridden).
        # Env wins; else the imported (encrypted) venues store.
        api_key = os.environ.get("CONDOR_JUPITER_API_KEY")
        if not api_key:
            from condor.executors.wallets import venue_config

            api_key = venue_config("jupiter").get("api_key")
        api_key = api_key or None
        default_url = KEYED_JUPITER_URL if api_key else DEFAULT_JUPITER_URL
        self.base_url = (base_url or os.environ.get("CONDOR_JUPITER_URL", default_url)).rstrip("/")
        headers = {"x-api-key": api_key} if api_key else {}
        self._client = client or httpx.AsyncClient(timeout=30.0, headers=headers)

    async def close(self) -> None:
        await self._client.aclose()

    async def quote(
        self,
        input_mint: str,
        output_mint: str,
        amount_atomic: int,
        slippage_bps: int,
        swap_mode: str = "ExactIn",
    ) -> dict:
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount_atomic),
            "slippageBps": str(slippage_bps),
            "swapMode": swap_mode,
        }
        r = await self._client.get(f"{self.base_url}/quote", params=params)
        if r.status_code // 100 != 2:
            raise JupiterError("/quote", r.status_code, r.text)
        return r.json()

    async def build_swap_tx(
        self,
        quote_resp: dict,
        user_pubkey: str,
        priority_lamports: Optional[int] = None,
    ) -> str:
        """POST /swap → a ready-to-sign base64 v0 transaction (routing, ALTs,
        compute budget, wSOL wrap/unwrap, ATA creation all built in)."""
        payload: dict[str, Any] = {
            "quoteResponse": quote_resp,
            "userPublicKey": user_pubkey,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": priority_lamports if priority_lamports is not None else "auto",
        }
        r = await self._client.post(f"{self.base_url}/swap", json=payload)
        if r.status_code // 100 != 2:
            raise JupiterError("/swap", r.status_code, r.text)
        return r.json()["swapTransaction"]


class JupiterConnector:
    """Executor-connector-shaped adapter backed by SolanaConnector + JupiterSwap.

    Only the methods the swap/position executors use are implemented. Amounts
    are UI units; ``price`` is quote-token per base-token, matching Gateway's
    /trading/swap semantics (side SELL sells the base, BUY buys the base).
    """

    def __init__(self, connector: SolanaConnector, jupiter: Optional[JupiterSwap] = None):
        self.solana = connector
        self.jupiter = jupiter or JupiterSwap()

    async def close(self) -> None:
        await self.jupiter.close()
        await self.solana.close()

    # -- token resolution ----------------------------------------------------

    def _mint(self, token: str) -> str:
        return _SYMBOL_MINTS.get(token.upper(), token)

    async def _decimals(self, mint: str) -> int:
        return await self.solana.token_decimals(mint)

    # -- quote ---------------------------------------------------------------

    async def _quote_for(
        self, base_token: str, quote_token: str, amount: float, side: str, slippage_pct: Optional[float]
    ) -> dict:
        """Return a normalized quote dict with the raw Jupiter response attached.

        SELL: ExactIn, input=base, output=quote. BUY: ExactOut, output=base,
        input=quote. ``price`` is always quote-per-base UI.
        """
        slippage_bps = int(round((slippage_pct if slippage_pct is not None else 1.0) * 100))
        base_mint, quote_mint = self._mint(base_token), self._mint(quote_token)
        base_dec = await self._decimals(base_mint)
        quote_dec = await self._decimals(quote_mint)

        if side.upper() == "SELL":
            amount_atomic = int(Decimal(str(amount)) * (Decimal(10) ** base_dec))
            q = await self.jupiter.quote(base_mint, quote_mint, amount_atomic, slippage_bps, "ExactIn")
            in_ui = Decimal(str(amount))
            out_ui = Decimal(q["outAmount"]) / (Decimal(10) ** quote_dec)
        else:  # BUY base with quote, exact base out
            amount_atomic = int(Decimal(str(amount)) * (Decimal(10) ** base_dec))
            q = await self.jupiter.quote(quote_mint, base_mint, amount_atomic, slippage_bps, "ExactOut")
            out_ui = Decimal(str(amount))
            in_ui = Decimal(q["inAmount"]) / (Decimal(10) ** quote_dec)

        price = (out_ui / in_ui) if side.upper() == "SELL" else (in_ui / out_ui)
        return {
            "price": float(price),
            "amountIn": float(in_ui),
            "amountOut": float(out_ui),
            "_quote": q,
            "_base_mint": base_mint,
            "_quote_mint": quote_mint,
            "_base_dec": base_dec,
            "_quote_dec": quote_dec,
            "_side": side.upper(),
        }

    async def quote_swap(
        self,
        chain_network: str,
        base_token: str,
        quote_token: str,
        amount: float,
        side: str,
        connector: Optional[str] = None,
        slippage_pct: Optional[float] = None,
    ) -> dict:
        return await self._quote_for(base_token, quote_token, amount, side, slippage_pct)

    # -- execute -------------------------------------------------------------

    async def execute_swap(
        self,
        chain_network: str,
        wallet_address: str,
        base_token: str,
        quote_token: str,
        amount: float,
        side: str,
        connector: Optional[str] = None,
        slippage_pct: Optional[float] = None,
    ) -> dict:
        q = await self._quote_for(base_token, quote_token, amount, side, slippage_pct)
        tx_b64 = await self.jupiter.build_swap_tx(q["_quote"], wallet_address)
        signature = await self.solana.sign_and_send(tx_b64)
        result = await self.solana.poll(signature)

        # The token RECEIVED must be the actual on-chain amount so a later close
        # never over-sells. SPL deltas are exact; for native SOL out, add the fee
        # back to isolate the swap's gross receipt.
        out_mint = q["_quote_mint"] if q["_side"] == "SELL" else q["_base_mint"]
        in_mint = q["_base_mint"] if q["_side"] == "SELL" else q["_quote_mint"]
        amount_out = self._received(result, out_mint, Decimal(str(q["amountOut"])))
        amount_in = self._spent(result, in_mint, Decimal(str(q["amountIn"])))
        return {
            "signature": signature,
            "status": 1 if result.confirmed else -1,
            "data": {
                "amountIn": float(amount_in),
                "amountOut": float(amount_out),
                "fee": float(Decimal(result.fee_lamports) / (Decimal(10) ** 9)),
            },
        }

    def _received(self, result, mint: str, quoted: Decimal) -> Decimal:
        if mint == WSOL_MINT:
            gross = Decimal(result.sol_delta_lamports + result.fee_lamports) / (Decimal(10) ** 9)
            return gross if gross > 0 else quoted
        delta = result.token_deltas.get(mint)
        return delta if (delta is not None and delta > 0) else quoted

    def _spent(self, result, mint: str, quoted: Decimal) -> Decimal:
        if mint == WSOL_MINT:
            return quoted  # ExactIn spent exactly the intended SOL
        delta = result.token_deltas.get(mint)
        return abs(delta) if (delta is not None and delta != 0) else quoted

    # -- misc gateway surface ------------------------------------------------

    async def poll_tx(self, chain: str, network: str, signature: str) -> dict:
        return {"txStatus": await self.solana.status(signature)}

    async def get_balances(
        self, chain: str, network: str, address: str, tokens: Optional[list[str]] = None
    ) -> dict[str, float]:
        out: dict[str, float] = {}
        for tok in (tokens or ["SOL"]):
            out[tok] = float(await self.solana.balance(None if tok.upper() == "SOL" else self._mint(tok)))
        return out

    async def save_token(self, chain_network: str, address: str) -> dict:
        # Jupiter routes by mint address directly — no token registry to update.
        return {"address": address, "registered": True}
