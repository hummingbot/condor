"""HTTP client for calling the Condor main-process web API."""

import re

import aiohttp

from mcp_servers.condor.exceptions import APIError
from mcp_servers.condor.settings import ensure_identity, settings


async def call_main_api(
    method: str, path: str, body: dict | None = None, timeout: float = 15
) -> dict | list:
    """Call the Condor web API in the main process.

    The MCP server runs as a subprocess -- TickEngines must be created in the
    main process so they survive beyond the MCP subprocess lifecycle.

    ``timeout`` defaults to 15s but callers that block on the main process (e.g. a
    consult that awaits a user confirmation) should pass a larger value.

    Raises APIError on failure instead of returning {"error": ...}.
    """
    from condor.web.auth import create_jwt
    from utils.config import WEB_PORT

    # Without an identity the JWT below is minted for user 0, which is not a
    # registered account — the main process then 403s deep inside the call
    # (consult/delegate/lifecycle) with an opaque "Access denied". Try the
    # single-user auto-bind first (Tier A), then fail fast with the actual fix.
    if not settings.user_id and not ensure_identity():
        raise APIError(
            "Condor MCP server was started without an identity, and auto-bind "
            "could not resolve one (zero or multiple approved users in "
            "config.yml): set the CONDOR_USER_ID and CONDOR_CHAT_ID env vars "
            "(or pass --user-id/--chat-id args) to your registered Condor "
            "user id — the same id your Telegram bot / web dashboard login "
            "uses — then restart the MCP session."
        )

    url = f"http://127.0.0.1:{WEB_PORT}/api/v1{path}"
    token = create_jwt(settings.user_id, role="user")
    headers = {"Authorization": f"Bearer {token}"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method,
                url,
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                data = await resp.json()
                if resp.status >= 400:
                    detail = (
                        data.get("detail", str(data))
                        if isinstance(data, dict)
                        else str(data)
                    )
                    raise APIError(f"API error ({resp.status}): {detail}")
                return data
    except APIError:
        raise
    except Exception as e:
        raise APIError(f"Failed to reach main process API: {e}")


async def call_control(
    method: str, params: dict | None = None, timeout: float = 60
) -> dict | list:
    """Call the persistent Condor process over its unix control socket.

    Replaces call_main_api for live-runtime operations as the web REST app is
    retired. Raises APIError (mapping ControlError) so callers keep one error type.
    """
    from condor.control.client import ControlError
    from condor.control.client import call_control as _call

    try:
        return await _call(method, params, timeout=timeout)
    except ControlError as e:
        raise APIError(f"control error ({e.status}): {e.message}")


def slug_from_agent_id(agent_id: str) -> str:
    """Extract the agent slug from a session id.

    agent_id formats: ``'{agent_slug}_{session_num}'`` or
    ``'{agent_slug}_e{num}'``. Slugs may contain underscores, so strip the
    trailing ``_N`` / ``_eN`` from the right.
    """
    m = re.match(r"^(.+?)_(?:e?\d+)$", agent_id)
    return m.group(1) if m else agent_id
