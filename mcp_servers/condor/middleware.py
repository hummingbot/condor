"""Error-handling middleware for MCP tool functions."""

import functools
import logging

from mcp_servers.condor.exceptions import APIError, ToolError

logger = logging.getLogger("condor.mcp")


def handle_errors(action_name: str):
    """Decorator that catches exceptions and returns error dicts."""

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except (ToolError, APIError):
                raise
            except Exception as e:
                logger.exception("MCP tool error in %s", action_name)
                return {"error": f"{action_name} failed: {e}"}

        return wrapper

    return decorator
