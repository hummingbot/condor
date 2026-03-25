"""
Middleware decorators for common tool patterns.
"""
import functools
import logging
from typing import Any, Callable, Coroutine, TypeVar

from hummingbot_mcp.exceptions import MaxConnectionsAttemptError as HBConnectionError, ToolError

logger = logging.getLogger("hummingbot-mcp")

T = TypeVar("T")

GATEWAY_LOG_HINT = "\n\n💡 Check gateway logs for more details: manage_gateway_container(action='get_logs')"


def handle_errors(
    action_name: str,
    error_suffix: str = "",
) -> Callable[[Callable[..., Coroutine[Any, Any, T]]], Callable[..., Coroutine[Any, Any, T]]]:
    """
    Decorator for standardized error handling in tool functions.

    Catches exceptions and wraps them in ToolError with a descriptive message.
    Re-raises HBConnectionError and existing ToolError as-is.

    Args:
        action_name: Description of the action for error messages (e.g., "get prices")
        error_suffix: Optional string appended to error messages (e.g., GATEWAY_LOG_HINT)
    """
    def decorator(func: Callable[..., Coroutine[Any, Any, T]]) -> Callable[..., Coroutine[Any, Any, T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            try:
                return await func(*args, **kwargs)
            except HBConnectionError as e:
                raise ToolError(str(e))
            except ToolError:
                raise
            except Exception as e:
                logger.error(f"{action_name} failed: {str(e)}", exc_info=True)
                raise ToolError(f"Failed to {action_name}: {str(e)}{error_suffix}")
        return wrapper
    return decorator
