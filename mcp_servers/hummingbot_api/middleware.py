"""
Middleware decorators for common tool patterns.
"""

import functools
import inspect
import logging
import re
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


#: HTTP statuses a proxy answers with when the request went upstream and the
#: answer never came back. The write may have landed.
_UNKNOWN_OUTCOME_STATUSES = frozenset({502, 504})


def is_outcome_unknown(exc: BaseException) -> bool:
    """True when a request was (or may have been) SENT and no answer arrived.

    A timeout, a dropped connection mid-response, a truncated body, a proxy
    502/504. For a write that spends money this is not a failure: the backend
    may have acted. A connection that was never established
    (``ClientConnectorError``) is the opposite case — nothing was sent — and
    stays an ordinary error.
    """
    import asyncio

    import aiohttp

    if isinstance(exc, aiohttp.ClientConnectorError):
        return False
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    if isinstance(exc, aiohttp.ClientResponseError):
        return exc.status in _UNKNOWN_OUTCOME_STATUSES
    return isinstance(
        exc,
        (
            aiohttp.ServerDisconnectedError,
            aiohttp.ClientPayloadError,
            aiohttp.ClientOSError,
        ),
    )


#: A URL inside an error string. Only its scheme and authority are removed: the
#: path says which route failed and is safe to show.
_URL_AUTHORITY = re.compile(r"https?://[^/\s'\"<>)\]]+", re.IGNORECASE)


def scrub_urls(text: str) -> str:
    """Remove every backend scheme://host:port from ``text``."""
    return _URL_AUTHORITY.sub("<api>", text)


def describe_error(exc: BaseException) -> str:
    """What an exception may say to a model: never blank, never the backend's URL.

    ``str(exc)`` is wrong in two ways. aiohttp's total timeout raises a bare
    ``TimeoutError`` whose text is empty, which rendered as ``"Error creating
    position_executor: "``. And an ``aiohttp.ClientResponseError`` stringifies
    as ``429, message='Too Many Requests', url='http://10.0.0.7:8000/...'``, so
    the server owner's internal host and port went to the model and on into
    chat (the fetchers closed this in SEC-116; these tools had not).

    The safe pieces are attributes: ``status`` is the code the API answered
    with and ``message`` its own ``detail``. A transport failure has neither
    and its text is mostly the address, so it is named by type instead.
    """
    import asyncio

    import aiohttp

    if isinstance(exc, ToolError):
        return str(exc).strip() or type(exc).__name__
    status = getattr(exc, "status", None)
    if isinstance(exc, aiohttp.ClientResponseError) and isinstance(status, int):
        message = getattr(exc, "message", None)
        if isinstance(message, str) and message.strip():
            return f"HTTP {status}: {scrub_urls(message.strip())}"
        return f"the API returned HTTP {status}"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        from mcp_servers.hummingbot_api.settings import settings

        return (
            f"the API did not answer within {settings.connection_timeout:g}s "
            "(timeout)"
        )
    if isinstance(exc, aiohttp.ClientError):
        return f"the API is unreachable ({type(exc).__name__})"
    return scrub_urls(str(exc).strip()) or type(exc).__name__


def outcome_unknown_message(what: str, check: str, exc: BaseException) -> str:
    """The text for a write whose request was sent and never answered.

    ``what`` names the write ("the position_executor create"); ``check`` is the
    read that settles it. No spend path here has an idempotency key, so the
    message leads with the instruction that matters: do not send it again.
    """
    from mcp_servers.hummingbot_api.settings import settings

    if isinstance(exc, (TimeoutError,)):
        cause = f"no answer arrived within {settings.connection_timeout:g}s"
    else:
        cause = f"the connection failed before an answer arrived ({type(exc).__name__})"
    return (
        f"OUTCOME UNKNOWN: {what} was sent to the API but {cause}. It MAY HAVE "
        "SUCCEEDED — funds may already have moved. Do NOT retry this call: a "
        f"retry can execute it a second time. First {check}. Only if that shows "
        "nothing after a minute may it be sent again; if in doubt, tell the user."
    )


async def sent_write(awaitable: Any, *, what: str, check: str) -> Any:
    """Await a fund-moving request; turn "no answer" into OUTCOME UNKNOWN.

    Wrap only the request itself, after validation: everything raised before
    the send is an ordinary error and must stay one.
    """
    try:
        return await awaitable
    except Exception as exc:
        if is_outcome_unknown(exc):
            logger.error("%s: outcome unknown (%r)", what, exc)
            raise ToolError(outcome_unknown_message(what, check, exc)) from exc
        raise


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
                logger.error(f"{action_name} failed: {e!r}", exc_info=True)
                raise ToolError(
                    f"Failed to {action_name}: {describe_error(e)}{error_suffix}"
                )
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
