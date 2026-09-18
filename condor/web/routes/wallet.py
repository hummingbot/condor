"""The browser wallet a runner signs with (plan M2).

Three jobs, and nothing else belongs here:

* **Attach** — a wallet proves itself once, with a signature over a one-time
  message, and becomes this user's runner identity (D13). Condor holds no key;
  the proof is all it keeps.
* **Sign and submit** — the browser signs what Gateway built, and hands the
  signed bytes back through here rather than to a chain of its own. The browser
  never holds an RPC URL (plan §6): Condor's backend reaches the chain through
  Gateway, and so does every transaction it helps land.
* **Detach** — refused while the user still runs a vault, because the attached
  wallet is the vault's runner on chain and forgetting it locally would only
  hide that.

Every route here is server-scoped: a signature is submitted through *some*
server's Gateway, and which one decides which chain it lands on.
"""

from __future__ import annotations

import base64
import binascii
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from condor import vault_store, wallet_store
from condor.gateway_client import GatewayClient
from condor.solana_keys import is_pubkey
from condor.web.auth import (
    get_current_user,
    require_server_access_query,
)
from condor.web.models import (
    WalletAttachRequest,
    WalletInfo,
    WalletNonceRequest,
    WalletNonceResponse,
    WalletPollRequest,
    WalletSubmitRequest,
    WebUser,
)
from config_manager import get_config_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wallet", tags=["wallet"])


async def _gateway(server: str) -> GatewayClient:
    return await GatewayClient.for_server(get_config_manager(), server)


def _decode_signature(value: str) -> bytes:
    """A 64-byte Ed25519 signature, base64. Rejected loudly, never coerced."""
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="signature is not base64")
    if len(raw) != 64:
        raise HTTPException(
            status_code=400,
            detail=f"signature is {len(raw)} bytes, expected 64",
        )
    return raw


# ── attach ────────────────────────────────────────────────────────────────────


@router.post("/nonce", response_model=WalletNonceResponse)
async def nonce(req: WalletNonceRequest, user: WebUser = Depends(get_current_user)):
    """A one-time message for ``req.address`` to sign.

    The server composes the message and the browser signs it verbatim. Building
    it on both sides would be two implementations of one string, which is how a
    wallet ends up signing something that no longer verifies.
    """
    try:
        return wallet_store.issue_nonce(user.id, req.address)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("", response_model=WalletInfo)
async def attach(req: WalletAttachRequest, user: WebUser = Depends(get_current_user)):
    signature = _decode_signature(req.signature)
    try:
        record = wallet_store.attach(user.id, req.address, signature, req.nonce)
    except wallet_store.AttachRefused as e:
        raise HTTPException(status_code=400, detail=str(e))
    return WalletInfo(address=record["address"], attached_at=record["attached_at"])


@router.get("", response_model=WalletInfo | None)
async def read(user: WebUser = Depends(get_current_user)):
    record = wallet_store.get_wallet(user.id)
    if record is None:
        return None
    return WalletInfo(
        address=record["address"], attached_at=record.get("attached_at", 0)
    )


@router.delete("")
async def detach(user: WebUser = Depends(get_current_user)):
    """Forget the attached wallet — refused while it still runs something.

    The wallet is a vault's runner *on chain*. Detaching would not revoke
    anything; it would only cost this user the ability to reach vaults they
    still own. A vault that is `Redeemable`, or a draft with no delegate, holds
    nothing back.
    """
    holding = vault_store.live_vault_labels(user.id)
    if holding:
        raise HTTPException(
            status_code=409,
            detail="this wallet still runs "
            + ", ".join(holding)
            + " — wind those down (or delete the drafts) before detaching",
        )
    wallet_store.detach(user.id)
    return {"detached": True}


# ── sign and submit ───────────────────────────────────────────────────────────


@router.post("/submit")
async def submit(
    req: WalletSubmitRequest,
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    """Broadcast a transaction the browser signed, through this server's Gateway.

    Gateway re-merges the build's ephemeral signatures and verifies every
    signature over the exact message before it broadcasts, so a message mutated
    after signing fails here with a reason instead of vanishing into the
    network.
    """
    gw = await _gateway(server)
    try:
        return await gw.post(
            "/chains/solana/submit",
            {
                "network": req.network,
                "signedTransaction": req.signed_transaction,
                "extraSigners": [s.model_dump() for s in req.extra_signers],
            },
        )
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"The Gateway on '{server}' refused the submit: {e}",
        )


@router.post("/poll")
async def poll(
    req: WalletPollRequest,
    server: str = Query(...),
    user: WebUser = Depends(require_server_access_query),
):
    gw = await _gateway(server)
    try:
        return await gw.post(
            "/chains/solana/poll",
            {"network": req.network, "signature": req.signature},
        )
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"The Gateway on '{server}' refused the poll: {e}",
        )
