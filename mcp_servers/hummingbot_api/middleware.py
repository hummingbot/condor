"""
Middleware decorators for common tool patterns.
"""

import functools
import inspect
import logging
from typing import Any, Callable, Coroutine, TypeVar

from mcp_servers.hummingbot_api.exceptions import (
    MaxConnectionsAttemptError as HBConnectionError,
)
from mcp_servers.hummingbot_api.exceptions import (
    ToolError,
)
from mcp_servers.hummingbot_api.throttle import (
    OUTCOME_ERROR,
    OUTCOME_OK,
    OUTCOME_REFUSED,
    is_throttle_refusal,
    record_call,
    venue_of,
)

logger = logging.getLogger("hummingbot-mcp")

T = TypeVar("T")

GATEWAY_LOG_HINT = (
    "\n\n💡 Gateway's logs usually say why. They are in the Condor dashboard "
    "(Settings → Gateway → Logs); point the user there."
)


def handle_errors(
    action_name: str,
    error_suffix: str = "",
) -> Callable[
    [Callable[..., Coroutine[Any, Any, T]]], Callable[..., Coroutine[Any, Any, T]]
]:
    """
    Decorator for standardized error handling in tool functions.

    Catches exceptions and wraps them in ToolError with a descriptive message.
    Re-raises HBConnectionError and existing ToolError as-is.

    Args:
        action_name: Description of the action for error messages (e.g., "get prices")
        error_suffix: Optional string appended to error messages (e.g., GATEWAY_LOG_HINT)
    """

    def decorator(
        func: Callable[..., Coroutine[Any, Any, T]],
    ) -> Callable[..., Coroutine[Any, Any, T]]:
        # Read once per tool, not once per call: binding the arguments is how a
        # venue passed positionally or left at its default is still attributed
        # (FEAT-138), and ``signature`` is the expensive half of doing it.
        try:
            tool_signature: inspect.Signature | None = inspect.signature(func)
        except (TypeError, ValueError):  # pragma: no cover - builtins only
            tool_signature = None

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            # Who spent the call and where, read before it is made: a failing
            # call still has to be attributable (FEAT-138). ``_count`` swallows,
            # and every branch below raises exactly what it raised before —
            # counting a call may not change its outcome.
            venue = _venue(tool_signature, args, kwargs)
            try:
                result = await func(*args, **kwargs)
            except HBConnectionError as e:
                _count(func.__name__, venue, e)
                raise ToolError(str(e))
            except ToolError as e:
                _count(func.__name__, venue, e)
                raise
            except Exception as e:
                _count(func.__name__, venue, e)
                logger.error(f"{action_name} failed: {str(e)}", exc_info=True)
                raise ToolError(f"Failed to {action_name}: {str(e)}{error_suffix}")
            _count(func.__name__, venue, None)
            return result

        return wrapper

    return decorator


def _venue(
    tool_signature: inspect.Signature | None,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> str:
    """The connector this call names, never raising on odd arguments.

    The arguments are bound against the tool's own signature first, so a
    connector left at its default or passed positionally is attributed to the
    venue it actually names rather than to ``unknown``. A call the signature
    cannot bind (it will fail on its way in anyway) falls back to the keywords
    as given.
    """
    try:
        if tool_signature is not None:
            bound = tool_signature.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            return venue_of(dict(bound.arguments))
        return venue_of(kwargs)
    except Exception:  # noqa: BLE001
        try:
            return venue_of(kwargs)
        except Exception:  # noqa: BLE001
            from mcp_servers.hummingbot_api.throttle import VENUE_UNKNOWN

            return VENUE_UNKNOWN


def _count(tool: str, venue: str, error: BaseException | None) -> None:
    """Record one call's outcome against its venue and its caller.

    The accounting seam of FEAT-138, sitting here because ``handle_errors``
    decorates every tool on this server and is the one place that sees both the
    call and what came back from it. A venue refusal (a 429, or the prose a
    client left in place of one) is counted apart from every other failure,
    which is the distinction #276 found nobody could make: sampling the logs
    said a host was "intermittent", counting said it was consistently refused.

    Labels come off ``settings`` — stamped onto this process by its spawner —
    so nothing here reads the agent registry.
    """
    try:
        from mcp_servers.hummingbot_api.settings import settings

        if error is None:
            outcome, reason = OUTCOME_OK, ""
        else:
            reason = is_throttle_refusal(error)
            outcome = OUTCOME_REFUSED if reason else OUTCOME_ERROR
        record_call(
            tool,
            outcome,
            venue=venue,
            host=settings.server_name,
            agent=settings.agent_slug,
            agent_id=settings.caller_id,
            reason=reason,
        )
    except Exception:  # noqa: BLE001 - a counter may never fail a call
        return
