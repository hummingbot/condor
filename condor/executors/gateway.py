"""Typed async client for Hummingbot Gateway's unified /trading routes.

Loopback is trusted by gateway (feat/robinhood-chain security model), so
no API token is needed for local use; CONDOR_GATEWAY_TOKEN is sent as a
Bearer token when set for network-reachable gateways.

Every non-2xx response raises GatewayError with the route and response
body — no fallbacks, no silent degradation.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import aiohttp

DEFAULT_GATEWAY_URL = "http://localhost:15888"


class GatewayError(RuntimeError):
    def __init__(self, route: str, status: int, body: str):
        self.route = route
        self.status = status
        self.body = body
        super().__init__(f"gateway {route} -> HTTP {status}: {body[:500]}")


class GatewayClient:
    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None):
        self.base_url = (base_url or os.environ.get("CONDOR_GATEWAY_URL", DEFAULT_GATEWAY_URL)).rstrip("/")
        self.token = token or os.environ.get("CONDOR_GATEWAY_TOKEN", "")
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
            self._session = aiohttp.ClientSession(
                headers=headers, timeout=aiohttp.ClientTimeout(total=120)
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, route: str, **kwargs) -> Any:
        session = await self._get_session()
        async with session.request(method, f"{self.base_url}{route}", **kwargs) as resp:
            body = await resp.text()
            if resp.status // 100 != 2:
                raise GatewayError(route, resp.status, body)
            return await resp.json(content_type=None) if body else None

    async def _get(self, route: str, params: Optional[dict] = None) -> Any:
        # aiohttp rejects non-str params; drop Nones, stringify the rest
        clean = {k: str(v) for k, v in (params or {}).items() if v is not None}
        return await self._request("GET", route, params=clean)

    async def _post(self, route: str, payload: dict) -> Any:
        clean = {k: v for k, v in payload.items() if v is not None}
        return await self._request("POST", route, json=clean)

    # -- chain -------------------------------------------------------------

    async def get_balances(
        self, chain: str, network: str, address: str, tokens: Optional[list[str]] = None
    ) -> dict[str, float]:
        payload: dict[str, Any] = {"network": network, "address": address}
        if tokens:
            payload["tokens"] = tokens
        data = await self._post(f"/chains/{chain}/balances", payload)
        return data["balances"]

    async def poll_tx(self, chain: str, network: str, signature: str) -> dict:
        """txStatus: 1 = confirmed, 0 = pending, -1 = failed."""
        return await self._post(f"/chains/{chain}/poll", {"network": network, "signature": signature})

    async def get_wallets(self) -> list[dict]:
        return await self._get("/wallet/")

    # -- swap ---------------------------------------------------------------

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
        return await self._get(
            "/trading/swap/quote",
            {
                "chainNetwork": chain_network,
                "connector": connector,
                "baseToken": base_token,
                "quoteToken": quote_token,
                "amount": amount,
                "side": side,
                "slippagePct": slippage_pct,
            },
        )

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
        """Blocks until confirmed. Returns {signature, status, data:{amountIn, amountOut, fee, ...}}."""
        return await self._post(
            "/trading/swap/execute",
            {
                "chainNetwork": chain_network,
                "walletAddress": wallet_address,
                "connector": connector,
                "baseToken": base_token,
                "quoteToken": quote_token,
                "amount": amount,
                "side": side,
                "slippagePct": slippage_pct,
            },
        )

    # -- clmm ----------------------------------------------------------------

    async def clmm_pool_info(self, connector: str, chain_network: str, pool_address: str) -> dict:
        return await self._get(
            "/trading/clmm/pool-info",
            {"connector": connector, "chainNetwork": chain_network, "poolAddress": pool_address},
        )

    async def clmm_quote_position(
        self,
        connector: str,
        chain_network: str,
        pool_address: str,
        lower_price: float,
        upper_price: float,
        base_token_amount: Optional[float] = None,
        quote_token_amount: Optional[float] = None,
        slippage_pct: Optional[float] = None,
    ) -> dict:
        return await self._get(
            "/trading/clmm/quote-position",
            {
                "connector": connector,
                "chainNetwork": chain_network,
                "poolAddress": pool_address,
                "lowerPrice": lower_price,
                "upperPrice": upper_price,
                "baseTokenAmount": base_token_amount,
                "quoteTokenAmount": quote_token_amount,
                "slippagePct": slippage_pct,
            },
        )

    async def clmm_open(
        self,
        connector: str,
        chain_network: str,
        wallet_address: str,
        pool_address: str,
        lower_price: float,
        upper_price: float,
        base_token_amount: Optional[float] = None,
        quote_token_amount: Optional[float] = None,
        slippage_pct: Optional[float] = None,
        extra_params: Optional[dict] = None,
    ) -> dict:
        """Blocks until confirmed. Returns {signature, status,
        data:{fee, positionAddress, positionRent, baseTokenAmountAdded, quoteTokenAmountAdded}}."""
        payload = {
            "connector": connector,
            "chainNetwork": chain_network,
            "walletAddress": wallet_address,
            "poolAddress": pool_address,
            "lowerPrice": lower_price,
            "upperPrice": upper_price,
            "baseTokenAmount": base_token_amount,
            "quoteTokenAmount": quote_token_amount,
            "slippagePct": slippage_pct,
        }
        if extra_params:
            payload.update(extra_params)
        return await self._post("/trading/clmm/open", payload)

    async def clmm_close(
        self, connector: str, chain_network: str, wallet_address: str, position_address: str
    ) -> dict:
        """Blocks until confirmed. Returns {signature, status,
        data:{fee, positionRentRefunded, baseTokenAmountRemoved, quoteTokenAmountRemoved,
        baseFeeAmountCollected, quoteFeeAmountCollected}}."""
        return await self._post(
            "/trading/clmm/close",
            {
                "connector": connector,
                "chainNetwork": chain_network,
                "walletAddress": wallet_address,
                "positionAddress": position_address,
            },
        )

    async def clmm_position_info(
        self, connector: str, chain_network: str, position_address: str
    ) -> dict:
        return await self._get(
            "/trading/clmm/position-info",
            {
                "connector": connector,
                "chainNetwork": chain_network,
                "positionAddress": position_address,
            },
        )

    async def clmm_positions_owned(
        self, connector: str, chain_network: str, wallet_address: Optional[str] = None
    ) -> list[dict]:
        return await self._get(
            "/trading/clmm/positions-owned",
            {
                "connector": connector,
                "chainNetwork": chain_network,
                "walletAddress": wallet_address,
            },
        )

    async def clmm_collect_fees(
        self, connector: str, chain_network: str, wallet_address: str, position_address: str
    ) -> dict:
        return await self._post(
            "/trading/clmm/collect-fees",
            {
                "connector": connector,
                "chainNetwork": chain_network,
                "walletAddress": wallet_address,
                "positionAddress": position_address,
            },
        )
