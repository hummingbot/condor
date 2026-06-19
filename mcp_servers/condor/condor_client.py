"""HTTP client for calling the Condor main-process web API."""

import re

import aiohttp

from mcp_servers.condor.exceptions import APIError
from mcp_servers.condor.settings import settings


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


def runkey_from_agent_id(agent_id: str) -> str:
    """Extract the ``"{agent_slug}.{strategy_slug}"`` run key from an agent_id.

    agent_id formats: ``'{agent_slug}.{strategy_slug}_{session_num}'`` or
    ``'..._e{num}'``. Slugs may contain underscores, so strip the trailing
    ``_N`` / ``_eN`` from the right.
    """
    m = re.match(r"^(.+?)_(?:e?\d+)$", agent_id)
    return m.group(1) if m else agent_id


def agent_strategy_from_agent_id(agent_id: str) -> tuple[str, str]:
    """Split an agent_id into ``(agent_slug, strategy_slug)``.

    The run key is ``"{agent_slug}.{strategy_slug}"``; slugs never contain a dot,
    so the first dot is the boundary. A legacy dotless key maps both parts to the
    same slug.
    """
    run_key = runkey_from_agent_id(agent_id)
    if "." in run_key:
        agent_slug, sslug = run_key.split(".", 1)
        return agent_slug, sslug
    return run_key, run_key


# Backwards-compatible alias (callers may still import the old name).
def slug_from_agent_id(agent_id: str) -> str:
    return runkey_from_agent_id(agent_id)
