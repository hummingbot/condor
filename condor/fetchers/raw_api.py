"""One authenticated request to a hummingbot-api route the pinned client does not wrap.

``hummingbot-api-client`` is pinned in ``pyproject.toml`` to a released PyPI version
(``==1.5.9``). Routes that landed after it — ``/performance/*`` (FEAT-087),
``/system/info`` and the bot client defaults (FEAT-121) — have no method on that client,
every router's ``_get`` is protected, and moving the pin is not available for a version
that is not on the index. So the request goes to the *same* authenticated session every
other call already uses, reached off any router's ``session`` and ``base_url``, by the
idiom :func:`condor.backtesting.get_task` established.

This module exists so that idiom lives in exactly one place. The reason is
:func:`detail`: it is the rule that an upstream failure reaches a browser as the API's
own ``detail`` string and never as a raw response body, which can be anything — an nginx
error page, a stack trace, a page naming the backend host. A second copy of a redaction
rule is how one copy drifts, so there is one.

**A 404 is a capability answer, not a failure.** These routes are new, and a server that
predates them answers 404 to a perfectly well-formed request. That is normal: the caller
should say "this server's API is too old for this" and degrade, not "the server is down".
Hence :class:`ApiRouteUnsupported`, distinct from every other failure precisely so a 404
can never be reported as an outage.

Every other failure keeps its HTTP identity, raised as ``aiohttp.ClientResponseError``
with the upstream status and detail attached — which is what
:func:`condor.fetchers.executors.describe_executor_error` reads, so
:func:`condor.web.routes._errors.upstream_error` maps it with no second rule.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)


class ApiRouteUnsupported(Exception):
    """This server does not serve the route, or this client cannot reach it.

    Raised for a 404 and for a client shape that exposes no session (a test double, a
    future client). Both land a caller on the same branch — "cannot ask this server" —
    which is a capability answer, never an offline one.
    """


def endpoint(client) -> Optional[tuple[Any, str]]:
    """``(session, base_url)`` for a client that exposes them, else ``None``.

    Read off ``bot_orchestration`` because every router on the client holds the *same*
    ``aiohttp.ClientSession``, built once in ``HummingbotAPIClient.init()`` with the
    server's basic auth and timeout. A client exposing neither is not broken — it is a
    double, or a client shape that changed — and the caller treats it as "cannot ask".
    """
    router = getattr(client, "bot_orchestration", None)
    session = getattr(router, "session", None)
    base_url = getattr(router, "base_url", None)
    if session is None or not base_url:
        return None
    return session, str(base_url).rstrip("/")


async def detail(response) -> str:
    """The API's own ``detail``, or a line that says only the status.

    Never the raw body: an upstream error page can be anything, and this string is handed
    to a browser. Trimmed for the same reason.
    """
    try:
        body = await response.json()
    except Exception:
        return f"the trading API returned HTTP {response.status}"
    if isinstance(body, dict):
        for field in ("detail", "message", "error"):
            value = body.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()[:300]
    return f"the trading API returned HTTP {response.status}"


async def request(
    client,
    method: str,
    path: str,
    *,
    params: Optional[dict[str, Any]] = None,
    json: Optional[dict[str, Any]] = None,
    timeout: Optional[Any] = None,
    unsupported: type[Exception] = ApiRouteUnsupported,
) -> Any:
    """One raw authenticated request, with upstream's status preserved on failure.

    Args:
        client: A ``HummingbotAPIClient`` for the target server.
        method: ``"get"``, ``"put"``, ``"post"`` — any verb the session exposes as a
            method of that name.
        path: Absolute path on the API, e.g. ``"/system/info"``.
        params: Query string, passed through to aiohttp.
        json: Request body, for the verbs that take one.
        timeout: Per-request override; the session's own timeout applies when omitted.
        unsupported: Exception class raised for a 404 or an unreachable client shape.
            Callers with their own capability exception pass it so their existing
            ``except`` clauses keep working.

    Raises:
        unsupported: The route is not served here, or this client cannot reach it.
        aiohttp.ClientResponseError: Any other non-2xx, carrying upstream's status and
            redacted detail.
    """
    reachable = endpoint(client)
    if reachable is None:
        raise unsupported(f"this client cannot reach {path}")
    session, base_url = reachable

    kwargs: dict[str, Any] = {}
    if params is not None:
        kwargs["params"] = params
    if json is not None:
        kwargs["json"] = json
    if timeout is not None:
        kwargs["timeout"] = timeout

    # The verb method rather than ``session.request(...)``: it is the shape every
    # existing caller and every test double already speaks.
    send = getattr(session, method.lower())
    async with send(f"{base_url}{path}", **kwargs) as response:
        if response.status == 404:
            raise unsupported(f"{path} is not served by this API version")
        if not response.ok:
            raise aiohttp.ClientResponseError(
                response.request_info,
                response.history,
                status=response.status,
                message=await detail(response),
                headers=response.headers,
            )
        return await response.json()
