"""Transport-agnostic entry point to the session runtime.

Every caller outside ``condor/runtime/`` goes through this module — never
through ``condor.runtime.sessions`` — so that swapping the in-process registry
for an HTTP client is a config flip rather than a rewrite.

``CONDOR_RUNTIME_MODE`` selects the transport:

* ``local`` (default) — thin await-through to ``condor.runtime.sessions``.
* ``http`` — reserved for the runtime process split; not implemented yet.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, AsyncIterator

from condor.runtime.keys import SessionKey
from condor.runtime.models import PromptRequest, RuntimeEvent, SessionInfo, SessionSpec

if TYPE_CHECKING:  # pragma: no cover - typing only
    from condor.runtime.sessions import AgentSession

LOCAL = "local"
HTTP = "http"

MODE = os.environ.get("CONDOR_RUNTIME_MODE", LOCAL)


def _local():
    """Return the local registry module.

    Imported lazily: ``condor.runtime.sessions`` pulls in handler helpers that
    in turn import this module, so a module-level import would be circular.
    """
    if MODE != LOCAL:
        raise NotImplementedError(
            f"CONDOR_RUNTIME_MODE={MODE!r} is not implemented yet; only "
            f"{LOCAL!r} is supported."
        )
    from condor.runtime import sessions

    return sessions


async def create_session(
    spec: SessionSpec,
    *,
    permission_callback=None,
    user_data: dict | None = None,
) -> SessionInfo:
    """Provision (or reuse) the session described by ``spec``."""
    session = await _local().get_or_create_session(
        spec, permission_callback=permission_callback, user_data=user_data
    )
    return session.info()


async def get_info(key: SessionKey) -> SessionInfo | None:
    """Serializable view of a session, or None when it does not exist."""
    session = _local().get_session(key)
    return session.info() if session else None


async def list_sessions(user_id: int | None = None) -> list[SessionInfo]:
    """List sessions, optionally filtered by owning user."""
    return _local().list_sessions(user_id)


async def destroy(key: SessionKey) -> bool:
    """Destroy a session. Returns True if one existed."""
    return await _local().destroy_session(key)


async def destroy_all() -> None:
    """Destroy every session. Called on shutdown."""
    await _local().destroy_all_sessions()


async def prompt(key: SessionKey, req: PromptRequest) -> AsyncIterator[RuntimeEvent]:
    """Stream one user turn through the session.

    ``req.image_b64``/``req.image_mime`` are accepted but not yet forwarded:
    the underlying ACP and Pydantic AI clients are text-only today.
    """
    session = _local().get_session(key)
    if session is None:
        raise KeyError(f"No session for {key}")
    async for event in session.prompt_stream(req.text):
        yield event


async def abort(key: SessionKey) -> bool:
    """Abort the in-flight prompt. Returns True if a session existed."""
    session = _local().get_session(key)
    if session is None:
        return False
    session.abort()
    return True


def get_live(key: SessionKey) -> "AgentSession | None":
    """In-process escape hatch returning the live session object.

    Only valid in ``local`` mode. Streaming and context injection still reach
    for ``session.client`` directly; those call sites move behind ``prompt()``
    when the runtime gains an HTTP transport, and this function disappears
    with them.
    """
    return _local().get_session(key)
