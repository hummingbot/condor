"""Native Solana chain connector — sign / send / poll / simulate / balance.

Replaces Hummingbot Gateway for Solana execution. Deliberately thin: it owns
only the chain primitives (versioned-tx signing, blockhash lifetime, priority is
left to the tx builder, confirmation polling, balance + fill parsing). Protocol
logic (Jupiter routing) lives in ``jupiter.py`` on top of this.

No fallbacks: every RPC error raises. The wallet key is held here and never
leaves the process (see ``wallets.py`` for how it is loaded).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.rpc.core import TokenAccountOpts
from solana.rpc.models import TxOpts
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

logger = logging.getLogger(__name__)

# Canonical mints — the on-chain identity of these tokens (not tunable "values").
WSOL_MINT = "So11111111111111111111111111111111111111112"
DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
LAMPORTS_PER_SOL = Decimal(10) ** 9


@dataclass
class SimResult:
    err: Optional[str]
    logs: list[str]
    units_consumed: Optional[int]


@dataclass
class TxResult:
    signature: str
    confirmed: bool
    err: Optional[str] = None
    fee_lamports: int = 0
    slot: int = 0
    # UI-amount balance change for the wallet owner, keyed by SPL mint.
    token_deltas: dict[str, Decimal] = field(default_factory=dict)
    # Native SOL balance change for the wallet (lamports, fee-inclusive).
    sol_delta_lamports: int = 0


class SolanaConnector:
    """One wallet's signing + RPC surface. Construct with the loaded keypair."""

    def __init__(
        self,
        keypair: Keypair,
        rpc_url: str = DEFAULT_RPC,
        commitment=Confirmed,
    ):
        self.keypair = keypair
        self.rpc_url = rpc_url
        self._client = AsyncClient(rpc_url, commitment=commitment)
        self._decimals: dict[str, int] = {}

    @property
    def wallet_address(self) -> str:
        return str(self.keypair.pubkey())

    async def close(self) -> None:
        await self._client.close()

    # -- primitives ----------------------------------------------------------

    async def recent_blockhash(self) -> tuple[str, int]:
        resp = await self._client.get_latest_blockhash()
        v = resp.value
        return str(v.blockhash), int(v.last_valid_block_height)

    def _sign(self, tx_b64: str) -> VersionedTransaction:
        """Deserialize a builder's unsigned v0 tx and sign it with our key.

        Jupiter (and any protocol client) returns a fully-formed VersionedTransaction
        whose fee payer is this wallet; re-constructing with our keypair produces the
        signature the message's required signers demand, leaving routing/ALTs intact.
        """
        raw = base64.b64decode(tx_b64)
        unsigned = VersionedTransaction.from_bytes(raw)
        return VersionedTransaction(unsigned.message, [self.keypair])

    async def sign_and_send(self, tx_b64: str, *, skip_preflight: bool = True) -> str:
        signed = self._sign(tx_b64)
        opts = TxOpts(skip_preflight=skip_preflight, preflight_commitment=Confirmed, max_retries=3)
        resp = await self._client.send_raw_transaction(bytes(signed), opts)
        return str(resp.value)

    async def simulate(self, tx_b64: str) -> SimResult:
        signed = self._sign(tx_b64)
        resp = await self._client.simulate_transaction(signed)
        v = resp.value
        return SimResult(
            err=str(v.err) if v.err else None,
            logs=list(v.logs or []),
            units_consumed=getattr(v, "units_consumed", None),
        )

    async def poll(self, signature: str, *, timeout_s: float = 90.0, interval: float = 2.0) -> TxResult:
        """Wait for confirmation, then parse the landed tx for fee + fills.

        Raises TimeoutError if the tx neither confirms nor errors within the
        window — the caller decides whether that means retry or manual check.
        """
        sig = Signature.from_string(signature)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            resp = await self._client.get_signature_statuses([sig])
            st = resp.value[0]
            if st is not None:
                if st.err is not None:
                    return TxResult(signature=signature, confirmed=False, err=str(st.err))
                status = str(st.confirmation_status) if st.confirmation_status else ""
                if "finalized" in status.lower() or "confirmed" in status.lower():
                    return await self.get_transaction(signature)
            await asyncio.sleep(interval)
        raise TimeoutError(f"solana tx {signature} not confirmed within {timeout_s}s")

    async def status(self, signature: str) -> int:
        """Non-blocking confirmation check: 1 confirmed, 0 pending, -1 failed."""
        sig = Signature.from_string(signature)
        resp = await self._client.get_signature_statuses([sig])
        st = resp.value[0]
        if st is None:
            return 0
        if st.err is not None:
            return -1
        status = str(st.confirmation_status).lower() if st.confirmation_status else ""
        return 1 if ("finalized" in status or "confirmed" in status) else 0

    async def get_transaction(self, signature: str) -> TxResult:
        """Fetch a landed tx and extract fee + this wallet's balance deltas."""
        sig = Signature.from_string(signature)
        resp = await self._client.get_transaction(
            sig, encoding="jsonParsed", max_supported_transaction_version=0
        )
        if resp.value is None:
            raise RuntimeError(f"tx {signature} not found on-chain")
        # solders returns typed objects; go through JSON for stable field access.
        import json

        tx = json.loads(resp.value.to_json())
        meta = tx.get("meta") or {}
        err = meta.get("err")
        fee = int(meta.get("fee", 0))
        slot = int(tx.get("slot", 0))
        result = TxResult(
            signature=signature,
            confirmed=err is None,
            err=json.dumps(err) if err else None,
            fee_lamports=fee,
            slot=slot,
        )
        owner = self.wallet_address
        pre_tok = {b["mint"]: b for b in (meta.get("preTokenBalances") or []) if b.get("owner") == owner}
        post_tok = {b["mint"]: b for b in (meta.get("postTokenBalances") or []) if b.get("owner") == owner}
        for mint in set(pre_tok) | set(post_tok):
            pre_amt = Decimal(str((pre_tok.get(mint, {}).get("uiTokenAmount") or {}).get("uiAmountString") or "0"))
            post_amt = Decimal(str((post_tok.get(mint, {}).get("uiTokenAmount") or {}).get("uiAmountString") or "0"))
            result.token_deltas[mint] = post_amt - pre_amt
        # Native SOL delta for the fee payer (account index 0).
        keys = tx.get("transaction", {}).get("message", {}).get("accountKeys") or []
        idx = next((i for i, k in enumerate(keys)
                    if (k.get("pubkey") if isinstance(k, dict) else k) == owner), None)
        if idx is not None:
            pre_bal = (meta.get("preBalances") or [])
            post_bal = (meta.get("postBalances") or [])
            if idx < len(pre_bal) and idx < len(post_bal):
                result.sol_delta_lamports = int(post_bal[idx]) - int(pre_bal[idx])
        return result

    # -- balances ------------------------------------------------------------

    async def balance(self, mint: Optional[str] = None) -> Decimal:
        """SOL balance (mint None or WSOL) or SPL token UI balance for a mint."""
        if mint is None or mint == WSOL_MINT:
            resp = await self._client.get_balance(self.keypair.pubkey())
            return Decimal(resp.value) / LAMPORTS_PER_SOL
        owner = self.keypair.pubkey()
        mint_pk = Pubkey.from_string(mint)
        resp = await self._client.get_token_accounts_by_owner_json_parsed(
            owner, TokenAccountOpts(mint=mint_pk)
        )
        total = Decimal(0)
        for acc in resp.value:
            info = acc.account.data.parsed["info"]
            total += Decimal(str(info["tokenAmount"]["uiAmountString"]))
        return total

    async def token_decimals(self, mint: str) -> int:
        """On-chain decimals for a mint (cached). SOL/WSOL is always 9."""
        if mint == WSOL_MINT:
            return 9
        if mint in self._decimals:
            return self._decimals[mint]
        resp = await self._client.get_account_info_json_parsed(Pubkey.from_string(mint))
        if resp.value is None:
            raise RuntimeError(f"mint {mint} not found on-chain")
        decimals = int(resp.value.data.parsed["info"]["decimals"])
        self._decimals[mint] = decimals
        return decimals
