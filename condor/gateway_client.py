"""Gateway, reached through the server's hummingbot-api.

Condor needs routes hummingbot-api has no typed method for — ``/chains/solana/status``,
``/wallet/swig/*``, the launch builds, the vault routes — so it sends them
through hbapi's passthrough (``/gateway/proxy/{path}``) rather than opening its
own connection.

**That is a correctness choice, not a plumbing one.** A direct connection means
a second address for the same Gateway, configured somewhere else, with nothing
keeping the two in step: Condor would build a transaction against one node while
every executor traded through another, and the first symptom would be a delegate
key that "does not exist". It is not a hypothetical — an hbapi in a container
reaches its Gateway at `host.docker.internal`, which does not even resolve from
Condor's host, so the two settings *cannot* be kept identical by copying one
into the other. Through hbapi there is one address, the server's own, and the
question cannot be asked twice.

One rule survives from the prototype (its handover, "Rules that cost time when
broken"): **the RPC is Gateway's.** Anything Condor reads from the chain, it
reads through the node Gateway reports in ``/chains/solana/status`` — the same
node every transaction Gateway builds is simulated and sent on. The URL itself
may carry a key, so callers hand the browser :func:`host_of` and nothing more.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import urlsplit

import aiohttp

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 8.0

#: Where hummingbot-api forwards what it has no typed method for. Paths below
#: are Gateway's own, so they read exactly as Gateway's docs write them.
PROXY_PREFIX = "/gateway/proxy"


class GatewayClient:
    """One server's Gateway, addressed through that server's hummingbot-api."""

    def __init__(self, client: Any):
        self._client = client

    @classmethod
    async def for_server(cls, cm, server_name: str) -> "GatewayClient":
        """The client for a configured server. Raises whatever the config
        manager raises for a server it does not have."""
        return cls(await cm.get_client(server_name))

    # `_get` and `_post` rather than a named client method: the pinned
    # hummingbot-api-client has no passthrough method yet (it gains one in the
    # same PR as the route). They raise `aiohttp.ClientResponseError` carrying
    # the upstream detail, which is the error contract every caller here
    # already handles.
    async def get(self, path: str, params: Optional[dict] = None) -> Any:
        return await self._client.gateway._get(f"{PROXY_PREFIX}{path}", params=params)

    async def post(self, path: str, json: Any) -> Any:
        return await self._client.gateway._post(f"{PROXY_PREFIX}{path}", json=json)

    async def solana_status(self, network: str = "mainnet-beta") -> dict:
        return await self.get("/chains/solana/status", {"network": network})

    async def solana_rpc_url(self, network: str = "mainnet-beta") -> str:
        """The RPC Gateway uses for ``network``. Raises if Gateway cannot say."""
        status = await self.solana_status(network)
        url = (status.get("rpcUrl") or "").strip() if isinstance(status, dict) else ""
        if not url:
            provider = status.get("rpcProvider") if isinstance(status, dict) else None
            raise RuntimeError(
                f"Gateway reports no RPC for solana/{network} "
                f"(rpcProvider {provider or 'unset'}) — fix its chain config"
            )
        return url


# ── The RPC probe ─────────────────────────────────────────────────────────────


def host_of(url: str) -> str:
    """``host[:port]`` of a URL — what may be shown to a browser. Never the path
    or query, which is where a provider's key lives."""
    parts = urlsplit(url)
    return parts.netloc.rsplit("@", 1)[-1] if parts.netloc else url


async def rpc_call(
    url: str,
    method: str,
    params: Optional[list] = None,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict:
    """One JSON-RPC call. Returns the whole envelope (``result`` or ``error``)."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=timeout_s)
    ) as session:
        async with session.post(url, json=payload) as resp:
            return await resp.json(content_type=None)


def classify_rpc(version: Any, surfnet_info: Any) -> dict:
    """Surfpool or a real node, from two RPC answers.

    Surfpool's ``getVersion`` result carries ``surfnet-version`` beside
    ``solana-core``; a real node's carries ``solana-core`` alone, and some
    providers (QuickNode) refuse ``getVersion`` outright. ``surfnet_getSurfnetInfo``
    corroborates: surfpool answers it, a node says method not found. Verified
    against surfpool 1.5.0 on :8899 and a QuickNode mainnet endpoint, 2026-09-17.
    """
    v = version.get("result") if isinstance(version, dict) else None
    v = v if isinstance(v, dict) else {}
    surfnet_version = v.get("surfnet-version")
    surfnet_answered = isinstance(surfnet_info, dict) and "result" in surfnet_info
    kind = "surfpool" if (surfnet_version or surfnet_answered) else "node"
    return {
        "kind": kind,
        "surfnet_version": surfnet_version,
        "solana_core": v.get("solana-core"),
    }


async def probe_rpc(url: str) -> dict:
    """What kind of chain is behind ``url``, plus its slot. Raises if it does
    not answer at all; a refused method is an answer."""
    version = await rpc_call(url, "getVersion")
    surfnet = await rpc_call(url, "surfnet_getSurfnetInfo")
    slot_env = await rpc_call(url, "getSlot")
    slot = slot_env.get("result") if isinstance(slot_env, dict) else None
    info = classify_rpc(version, surfnet)
    info["rpc_host"] = host_of(url)
    # The whole URL, for the browser's wallet layer: a Solana cluster is
    # configured with one, and ConnectorKit's own reads go straight to it.
    info["rpc_url"] = url
    info["slot"] = slot if isinstance(slot, int) else None
    return info


# ── The vault program, through Gateway ────────────────────────────────────────


class VaultGateway:
    """The vault routes, as Condor calls them.

    A thin, named surface over :class:`GatewayClient` so that the route layer
    reads as vault operations rather than as URL strings, and so there is one
    place to look when Gateway's contract moves. Every ``build_*`` returns an
    unsigned transaction for the runner's browser; everything else executes
    under Gateway's platform key, and each of those is an instruction the
    *program* restricts to that signer (or to anyone at all).
    """

    BASE = "/chains/solana/vaults"

    def __init__(
        self,
        client: "GatewayClient",
        network: str = "mainnet-beta",
        server: str = "",
    ):
        self.client = client
        self.network = network
        #: The Condor server whose Gateway this is — the only address there is,
        #: and so what an error names.
        self.server = server

    @classmethod
    async def for_server(
        cls, cm, server_name: str, network: str = "mainnet-beta"
    ) -> "VaultGateway":
        return cls(
            await GatewayClient.for_server(cm, server_name), network, server_name
        )

    # reads
    async def list_vaults(self) -> list:
        return await self.client.get(self.BASE, {"network": self.network})

    async def protocol(self) -> dict:
        return await self.client.get(f"{self.BASE}/protocol", {"network": self.network})

    async def vault(self, swig_account: str) -> dict:
        return await self.client.get(
            f"{self.BASE}/{swig_account}", {"network": self.network}
        )

    # builds
    async def build(self, name: str, body: dict) -> dict:
        return await self.client.post(
            f"{self.BASE}/{name}", {"network": self.network, **body}
        )

    # executes
    async def execute(self, name: str, body: dict) -> dict:
        return await self.client.post(
            f"{self.BASE}/{name}", {"network": self.network, **body}
        )

    # the chain, for what the program does not record
    async def balances(self, address: str, tokens: Optional[list] = None) -> dict:
        return await self.client.post(
            "/chains/solana/balances",
            {"network": self.network, "address": address, "tokens": tokens or []},
        )

    async def poll(self, signature: str) -> dict:
        return await self.client.post(
            "/chains/solana/poll", {"network": self.network, "signature": signature}
        )

    # ── acting as the vault's wallet ──────────────────────────────────────────
    #
    # Gateway holds the vault's delegate key, so "the wallet" is a
    # `walletAddress` like any other and its chokepoint wraps every call for the
    # delegate. What each of these decides is an amount; what none of them
    # decides is how to say it on the chain.

    async def withdraw(
        self, swig_account: str, destination: str, amount: str, mint: Optional[str] = None
    ) -> dict:
        """Move assets out of a private vault, signed by its delegate."""
        body = {
            "network": self.network,
            "swigAccount": swig_account,
            "destination": destination,
            "amount": amount,
        }
        if mint:
            body["mint"] = mint
        return await self.client.post(f"{self.BASE}/withdraw", body)

    async def burn(self, swig_account: str, amount: float) -> dict:
        """Burn some of the vault's own token, out of the wallet that holds it.

        The amount is in UI units and the wire format is Gateway's problem —
        which is the point: Condor decides how much a sweep burns, and nothing
        here writes an instruction.
        """
        return await self.client.post(
            f"{self.BASE}/burn",
            {
                "network": self.network,
                "swigAccount": swig_account,
                "amount": str(amount),
            },
        )

    async def fund_delegate(self, swig_account: str, lamports: int) -> dict:
        """Move SOL from the vault's wallet to its delegate, so it can pay for gas."""
        return await self.client.post(
            f"{self.BASE}/fund-delegate",
            {
                "network": self.network,
                "swigAccount": swig_account,
                "lamports": str(lamports),
            },
        )

    async def buy_on_curve(
        self,
        wallet_address: str,
        base_token: str,
        quote_token: str,
        amount: float,
        slippage_pct: float = 1.0,
    ) -> dict:
        """Buy the vault's token on its bonding curve, as the vault's wallet."""
        return await self.client.post(
            "/trading/launch/execute-swap",
            {
                "chainNetwork": f"solana/{self.network}",
                "connector": "meteora",
                "walletAddress": wallet_address,
                "baseToken": base_token,
                "quoteToken": quote_token,
                "amount": amount,
                "side": "BUY",
                "slippagePct": slippage_pct,
            },
        )

    async def buy_on_market(
        self,
        wallet_address: str,
        base_token: str,
        quote_token: str,
        amount: float,
        slippage_pct: float = 1.0,
    ) -> dict:
        """Buy the vault's token wherever it trades after graduation.

        Through the router rather than the migrated pool directly: after
        graduation the token is simply a token, and a sweep should pay the best
        price available rather than the one pool Condor happens to know about.
        """
        return await self.client.post(
            "/trading/router/execute-swap",
            {
                "chainNetwork": f"solana/{self.network}",
                "walletAddress": wallet_address,
                "baseToken": base_token,
                "quoteToken": quote_token,
                "amount": amount,
                "side": "BUY",
                "slippagePct": slippage_pct,
            },
        )
