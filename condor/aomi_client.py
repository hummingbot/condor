"""The one seam between Condor and the Aomi Pipeline API.

Every Condor surface that talks to Aomi — the shared ``aomi_*`` routines, the
``defi_positions`` provider — gets its client from :func:`get_pipeline_client`
and nowhere else, so a test monkeypatches exactly one name and a missing token
degrades every caller the same way (a helpful message, never a traceback).

The ``aomi`` import is deliberately lazy: the package is an editable path
dependency (``../aomi-python``) and the unit suite must keep running on a
checkout that does not have it.
"""

from __future__ import annotations

import os
from typing import Any

DEFAULT_AOMI_URL = "https://chat-staging.aomi.dev"

MISSING_TOKEN_MESSAGE = (
    "Aomi is not configured: set AOMI_TOKEN in .env (and AOMI_URL to point at "
    f"another origin; default {DEFAULT_AOMI_URL}). Mint a bearer with "
    "`python -m aomi.auth mint` from the aomi-python checkout."
)


def aomi_settings() -> tuple[str, str]:
    """``(base_url, token)`` read from the environment at call time.

    Goes through ``utils.config`` for its ``load_dotenv()`` side effect, the
    same idiom ``condor.runtime.toolsets`` uses for the Telegram token, so an
    ``AOMI_TOKEN`` in ``.env`` is seen whether or not ``main.py`` ran first.
    """
    import utils.config  # noqa: F401  (imported for load_dotenv())

    url = (os.environ.get("AOMI_URL") or "").strip() or DEFAULT_AOMI_URL
    token = (os.environ.get("AOMI_TOKEN") or "").strip()
    return url, token


def aomi_configured() -> bool:
    """Whether a bearer is present; says nothing about whether it is valid."""
    return bool(aomi_settings()[1])


def get_pipeline_client() -> Any | None:
    """A fresh ``PipelineClient``, or ``None`` when Aomi cannot be reached.

    ``None`` covers both "no ``AOMI_TOKEN``" and "``aomi`` is not installed";
    callers render :data:`MISSING_TOKEN_MESSAGE` for either. The caller owns
    the client and must ``await client.close()`` (or use ``async with``).
    """
    url, token = aomi_settings()
    if not token:
        return None
    try:
        from aomi.pipeline import PipelineClient
    except ImportError:
        return None
    return PipelineClient(url, token)
