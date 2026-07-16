"""Account-management REST surface (§6.2b, Phase 3 — the minimal UI's API).

Multi-account per venue over the structured store: list accounts (never
secrets), onboard (custody derived from credentials + read-only probe),
edit (custody identity revalidated), remove (cooperative warning), and pick
the default account. Mutations are gated by loopback posture only (§5.5,
single-operator model) — no per-request role check.

Busy guard: editing/removing an account is rejected (409) while any durable
nonterminal executor record exists for the venue — after a crash there is no
running Python object, but the record still needs its credentials for
adoption, so busy is a durable projection, not an in-memory check.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from condor.accounts import (
    AccountResolutionError,
    AccountStoreError,
    OnboardingError,
    derive_custody,
    onboard_account,
)
from condor.accounts.registry import UnknownVenueError, default_registry
from condor.executors import wallets
from condor.executors.log import ExecutorLog

router = APIRouter(tags=["venues"])

_REMOVAL_WARNING = (
    "Condor will no longer manage venue state held by this account — "
    "balances, positions, attributed holdings, resting orders, and triggers "
    "stay live on the venue, unmanaged. Re-adding the same address later "
    "restores management."
)


class OnboardAccountRequest(BaseModel):
    credentials: dict
    name: str = ""


class EditAccountRequest(BaseModel):
    credentials: Optional[dict] = None
    name: Optional[str] = None


class DefaultAccountRequest(BaseModel):
    account: str


def _venue_busy(venue_id: str) -> bool:
    """True while any durable nonterminal executor record targets the venue.

    Transitional per-venue account model: executor configs carry `venue`, not
    yet a resolved AccountRef — per-ACCOUNT busy matching lands when executor
    openers carry the bound AccountRef (§6.2b binding invariant).
    """
    for rec in ExecutorLog().load_non_terminal():
        if (rec.config or {}).get("venue") == venue_id:
            return True
    return False


def _reject_when_busy(venue_id: str) -> None:
    if _venue_busy(venue_id):
        raise HTTPException(
            status_code=409,
            detail=(
                f"venue {venue_id!r} has nonterminal executors — account "
                "edits/removal are unavailable while it is busy (stop them, "
                "or let recovery finish, then retry)"
            ),
        )


def _canonical_address(venue_id: str, address: str) -> str:
    try:
        return default_registry().account_ref(venue_id, address).custody_address
    except UnknownVenueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/venues")
async def list_venues():
    """Per-venue accounts: custody address, display name, default marker —
    never credential fields, sealed or otherwise."""
    data = wallets.account_store().load()
    venues = {}
    for venue_id, entry in data.items():
        if venue_id.startswith("_"):  # reserved non-account blocks (_services)
            continue
        default = entry.get("default_account")
        venues[venue_id] = {
            "default_account": default,
            "accounts": [
                {
                    "address": addr,
                    "name": acct.get("name", ""),
                    "is_default": addr == default,
                }
                for addr, acct in entry.get("accounts", {}).items()
            ],
        }
    return {"venues": venues}


@router.post("/venues/{venue_id}/accounts")
async def onboard_venue_account(
    venue_id: str,
    req: OnboardAccountRequest,
):
    """Onboard an account: custody derived FROM the credentials (a mismatched
    typed address is rejected), read-only probe must pass, secrets sealed."""
    try:
        ref = onboard_account(
            venue_id, req.credentials or {}, name=req.name, probe=True
        )
    except (OnboardingError, AccountStoreError, UnknownVenueError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    default = wallets.account_store().load()[venue_id].get("default_account")
    return {
        "venue_id": venue_id,
        "account": {
            "address": ref.custody_address,
            "name": req.name,
            "is_default": ref.custody_address == default,
        },
    }


@router.put("/venues/{venue_id}/accounts/{address}")
async def edit_venue_account(
    venue_id: str,
    address: str,
    req: EditAccountRequest,
):
    """Edit an account. Credential edits revalidate custody identity — the
    derived custody must EQUAL the address key (same derivation + probe as
    onboarding); a rename touches only the display name."""
    canonical = _canonical_address(venue_id, address)
    store = wallets.account_store()
    data = store.load()
    entry = data.get(venue_id, {}).get("accounts", {}).get(canonical)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{venue_id}: no account {address!r}")
    _reject_when_busy(venue_id)

    if req.credentials:
        try:
            derived = derive_custody(venue_id, req.credentials)
            if derived.custody_address != canonical:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"credentials derive custody {derived.custody_address!r} "
                        f"but this account is {canonical!r} — a different "
                        "custody identity is a NEW account, not an edit"
                    ),
                )
            name = req.name if req.name is not None else entry.get("name", "")
            onboard_account(venue_id, req.credentials, name=name, probe=True)
        except HTTPException:
            raise
        except (OnboardingError, AccountStoreError, ValueError) as e:
            raise HTTPException(status_code=422, detail=str(e))
    elif req.name is not None:
        entry["name"] = req.name
        try:
            store.save(data)  # duplicate display names rejected by validate
        except AccountStoreError as e:
            raise HTTPException(status_code=422, detail=str(e))
    else:
        raise HTTPException(status_code=422, detail="nothing to edit — pass credentials and/or name")

    updated = store.load()[venue_id]
    return {
        "venue_id": venue_id,
        "account": {
            "address": canonical,
            "name": updated["accounts"][canonical].get("name", ""),
            "is_default": updated.get("default_account") == canonical,
        },
    }


@router.delete("/venues/{venue_id}/accounts/{address}")
async def remove_venue_account(
    venue_id: str,
    address: str,
):
    """Remove an account (cooperative: warns, does not verify venue state).
    Removing the default just leaves the venue with no default."""
    canonical = _canonical_address(venue_id, address)
    _reject_when_busy(venue_id)
    if not wallets.account_store().remove_account(venue_id, canonical):
        raise HTTPException(status_code=404, detail=f"{venue_id}: no account {address!r}")
    return {"venue_id": venue_id, "removed": canonical, "warning": _REMOVAL_WARNING}


@router.post("/venues/{venue_id}/default")
async def set_default_account(
    venue_id: str,
    req: DefaultAccountRequest,
):
    """Point the venue's default_account at the resolved selector (address
    or display name)."""
    try:
        ref = wallets.account_store().set_default(venue_id, req.account)
    except AccountResolutionError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (AccountStoreError, UnknownVenueError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"venue_id": venue_id, "default_account": ref.custody_address}
