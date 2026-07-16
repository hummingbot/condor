"""Settings API routes.

The legacy exchange-connector settings surface (server CRUD, gateway
container management, exchange credentials) and the voice/transcription
preferences were removed in the simplification pass (§9.2). Account/venue
setup lives under the venues routes; nothing remains here today — the
router is kept so future non-server settings have a home.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])
