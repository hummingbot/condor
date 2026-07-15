"""REST surface for native wallet onboarding — importing venue credentials.

The dashboard's "import a wallet" flow posts here. Secrets are sealed at rest by
``wallets.save_venue`` (AES-256-GCM) and are never returned. GET reports which
venues are configured (field NAMES only, never values)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from condor.executors import wallets
from condor.web.auth import get_current_user
from condor.web.models import WebUser

router = APIRouter(tags=["venues"])


class ImportVenueRequest(BaseModel):
    fields: dict


@router.get("/venues")
async def list_venues(user: WebUser = Depends(get_current_user)):
    """Configured venues and their stored field names (never secret values)."""
    return {"venues": wallets.configured_venues()}


@router.post("/venues/{name}")
async def import_venue(
    name: str,
    req: ImportVenueRequest,
    user: WebUser = Depends(get_current_user),
):
    """Import/update a venue's credentials (admin only). Secret fields are
    encrypted at rest; the response echoes only the stored field names."""
    if getattr(user, "role", None) != "admin":
        raise HTTPException(status_code=403, detail="admin role required to import wallets")
    try:
        entry = wallets.save_venue(name, req.fields or {})
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"venue": name, "fields": sorted(entry.keys())}
