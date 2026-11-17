"""One mapping from a failed upstream call to a response that leaks nothing.

``str(exc)`` on an ``aiohttp`` client exception embeds the backend's own URL, so
handing it to a browser as an ``HTTPException`` detail publishes the internal
host and port to anyone who can provoke a backend blip — a timeout, a refused
connection, an upstream 5xx. SEC-116 established the remedy for the executor
mutations and SEC-126 extended it to the executor reads; this module is that
same remedy lifted out of ``routes/executors.py`` so every route module shares
one implementation instead of growing a second sanitizer.

The description itself still comes from :func:`condor.fetchers.describe_executor_error`,
which reads the safe pieces off the exception's attributes (``status`` is the
code the API answered with, ``message`` is the API's own ``detail``) and
collapses a transport failure — which has neither — to a generic line.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException

from condor.fetchers.executors import describe_executor_error

__all__ = ["offline_hint", "server_client", "upstream_error"]

logger = logging.getLogger(__name__)


def upstream_error(action: str, exc: Exception) -> HTTPException:
    """Map a failed backend call to an ``HTTPException`` that leaks nothing.

    An upstream 4xx is the caller's own bad request and stays a 400; anything
    else — an upstream 5xx, a timeout, a refused connection — is this gateway
    failing to reach its backend, so 502. The detail carries the API's message
    but never the raw exception.

    Callers log the exception before raising: the address belongs in the server
    log, where an operator needs it, not in the response, where a trader on a
    shared server would read it.
    """
    status, message = describe_executor_error(exc)
    code = 400 if status is not None and 400 <= status < 500 else 502
    return HTTPException(status_code=code, detail=f"{action}: {message}")


async def server_client(cm, name: str):
    """The API client for ``name``, or an ``HTTPException`` saying why not (WR-02).

    ``get_client`` raises before any route's own ``try`` is entered — a
    ``ConnectionError`` for the whole failure TTL after a failed connect, the
    transport error itself on the attempt that failed — and an unhandled one is
    a bare 500 with no JSON detail. This is the ``settings._get_client`` mapping
    for the routes that have no degraded shape to answer with: an unknown name
    is a 404, anything else goes through :func:`upstream_error`.

    ``cm`` is passed in rather than resolved, so a route keeps handing over the
    manager it already holds.
    """
    try:
        return await cm.get_client(name)
    except HTTPException:
        raise
    except ValueError:
        # The config manager's own "no such server". The name is the caller's.
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    except Exception as e:
        logger.warning("Cannot connect to server '%s': %s", name, e)
        raise upstream_error("Cannot connect to server", e)


def offline_hint(exc: BaseException) -> str:
    """The ``error_hint`` of a ``server_online: false`` answer (WR-09).

    The degraded responses are the other door the raw exception used to leave
    by: ``f"Connection error: {e}"`` on a transport failure names the backend's
    host and port just as an ``HTTPException`` detail would. Same description,
    same rule — the address stays in the log.
    """
    return f"Connection error: {describe_executor_error(exc)[1]}"
