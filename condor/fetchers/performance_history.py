"""The shared performance-history surface, read raw (FEAT-087).

``GET /performance/history`` serves **both** populations — controllers and
executors — in one normalized row shape, which is the whole reason Condor wants
it: a running executor gets a curve of its own instead of borrowing its
controller's, and the browser gets one data contract instead of two.

**Why raw rather than through the SDK.** ``hummingbot-api-client`` is pinned in
``pyproject.toml`` to a released PyPI version (``==1.5.9``) and has no
``performance`` router; every router's ``_get`` is protected, and the design
doc's alternative — add the router upstream-of-the-pin and move the pin — is
not available for a version pin at a package index. So the request goes to the
same authenticated session every other call already uses, by the same idiom
:func:`condor.backtesting.get_task` established: reach for the router's
``session`` and ``base_url``, and degrade rather than crash for a client shape
that exposes neither (the test doubles, a future client).

**Why a 404 is not an error.** The route is new and unreleased — it exists on a
server built from the branch behind hummingbot/hummingbot-api#226 and nowhere
else. Every other server, and this one after any ``docker compose pull``,
answers 404. That is the *normal* case, not a failure: Condor falls back to the
series it derives client-side and says so on the chart. Hence
:class:`PerformanceHistoryUnsupported`, which callers translate into a
capability answer rather than into an offline server.

Every other failure keeps its HTTP identity. In particular the route's own
**400** — a filter aimed at the wrong population, an unparseable timestamp — is
the caller's bad request and has to reach the caller as one, which is why this
module raises ``aiohttp.ClientResponseError`` with the upstream status and
detail attached: that is exactly what
:func:`condor.fetchers.executors.describe_executor_error` reads, so
``routes/_errors.upstream_error`` maps it without a second rule.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

#: The path, relative to the API root. One constant so the probe and the read
#: can never ask about different routes.
PERFORMANCE_HISTORY_PATH = "/performance/history"

#: The two populations the route serves. A subject is required by the route and
#: is the only thing that decides which table answers.
SUBJECT_CONTROLLER = "controller"
SUBJECT_EXECUTOR = "executor"

#: Filters that belong to exactly one population. Mirrored from the upstream
#: router so Condor can keep a caller's mistake local instead of spending a
#: round trip to be told 400. Upstream stays the authority — this never widens
#: what is accepted, it only declines earlier.
CONTROLLER_ONLY_FILTERS = ("bot_name",)
EXECUTOR_ONLY_FILTERS = (
    "executor_id",
    "executor_type",
    "account_name",
    "connector_name",
    "trading_pair",
)

#: Upstream's ``limit`` ceiling (``le=1000``). Asking for more is a 422, which
#: the controller history route learned the hard way (CORR-260).
MAX_PAGE_LIMIT = 1000


class PerformanceHistoryUnsupported(Exception):
    """This server has no ``/performance/history``.

    Not an error condition — the route is unreleased, so most servers answer
    404 and Condor's derived series is the right thing to draw. Distinct from
    every other failure precisely so a 404 can never be reported as a server
    that is down.
    """


def _endpoint(client) -> Optional[tuple[Any, str]]:
    """``(session, base_url)`` for a client that exposes them, else ``None``.

    Read off ``bot_orchestration`` because that is the router whose surface this
    one replaces; any router would do, they all hold the same session. A client
    that exposes neither is not broken — it is a double, or a client shape that
    changed — and the caller treats it as "cannot ask", which lands on the same
    fallback a 404 does.
    """
    router = getattr(client, "bot_orchestration", None)
    session = getattr(router, "session", None)
    base_url = getattr(router, "base_url", None)
    if session is None or not base_url:
        return None
    return session, str(base_url).rstrip("/")


async def _get(client, path: str, params: dict[str, Any], *, timeout=None) -> Any:
    """One raw authenticated GET, with upstream's status preserved on failure."""
    endpoint = _endpoint(client)
    if endpoint is None:
        raise PerformanceHistoryUnsupported(
            "this client cannot reach the performance routes"
        )
    session, base_url = endpoint

    kwargs: dict[str, Any] = {"params": params}
    if timeout is not None:
        kwargs["timeout"] = timeout
    async with session.get(f"{base_url}{path}", **kwargs) as response:
        if response.status == 404:
            raise PerformanceHistoryUnsupported(
                f"{path} is not served by this API version"
            )
        if not response.ok:
            raise aiohttp.ClientResponseError(
                response.request_info,
                response.history,
                status=response.status,
                message=await _detail(response),
                headers=response.headers,
            )
        return await response.json()


async def _detail(response) -> str:
    """The API's own ``detail``, or a line that says only the status.

    Never the raw body: an upstream error page can be anything, and this string
    is handed to a browser. Trimmed for the same reason.
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


def reject_foreign_filters(subject: str, **supplied) -> Optional[str]:
    """The filters in ``supplied`` that belong to the *other* population.

    Returns a message naming them, or ``None`` when every filter fits. Upstream
    answers 400 for these, and forwarding that 400 is the contract; this exists
    so the common case does not need a round trip to find out, and so a proxy
    that never reaches the server still gets the rule right.
    """
    wrong = (
        EXECUTOR_ONLY_FILTERS
        if subject == SUBJECT_CONTROLLER
        else CONTROLLER_ONLY_FILTERS
    )
    offending = [name for name in wrong if supplied.get(name) is not None]
    if not offending:
        return None
    verb = "is" if len(offending) == 1 else "are"
    return f"{', '.join(offending)} {verb} not a valid filter for subject={subject}"


async def fetch_performance_history(
    client,
    *,
    subject: str,
    bot_name: Optional[str] = None,
    controller_id: Optional[str] = None,
    executor_id: Optional[str] = None,
    executor_type: Optional[str] = None,
    account_name: Optional[str] = None,
    connector_name: Optional[str] = None,
    trading_pair: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    interval: str = "5m",
    limit: int = MAX_PAGE_LIMIT,
    cursor: Optional[str] = None,
) -> dict:
    """One page of the shared history, as upstream's envelope.

    Returned unchanged — ``{"status", "data", "pagination"}`` — because the
    proxy above wants both the rows and the cursor, and the shared
    :func:`condor.fetchers._pagination.next_cursor` already reads the nested
    spelling this envelope uses.

    ``None`` filters are dropped rather than sent as empty strings: an empty
    ``controller_id`` is a filter for a controller literally named "", not the
    absence of one.
    """
    params: dict[str, Any] = {
        "subject": subject,
        "interval": interval,
        "limit": max(1, min(int(limit), MAX_PAGE_LIMIT)),
    }
    optional = {
        "bot_name": bot_name,
        "controller_id": controller_id,
        "executor_id": executor_id,
        "executor_type": executor_type,
        "account_name": account_name,
        "connector_name": connector_name,
        "trading_pair": trading_pair,
        "start_time": start_time,
        "end_time": end_time,
        "cursor": cursor,
    }
    params.update({k: v for k, v in optional.items() if v})

    result = await _get(client, PERFORMANCE_HISTORY_PATH, params)
    return result if isinstance(result, dict) else {}


def extract_rows(result: Any) -> list[dict]:
    """The row list out of the envelope, whichever spelling it arrives in.

    ``data`` is what this route returns; ``snapshots`` and a bare list are read
    too, so the same extractor survives the envelope being normalized upstream
    the way the controller route's has been more than once.
    """
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)]
    if not isinstance(result, dict):
        return []
    for field in ("data", "snapshots"):
        rows = result.get(field)
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    return []


async def probe_performance_history(client, **_kw) -> dict:
    """Whether this server serves ``/performance/history``. One request.

    Registered as an SDS fetch so the answer is cached per server and shared by
    every chart on the page: the capability is a property of the *server*, not
    of a scope, and asking once per chart would be one request per click on a
    tree. The cheapest possible question is asked — one controller row, the
    subject that is guaranteed to exist wherever the route does — because the
    answer is whether the route exists at all, not what it holds.

    Three answers, and the third is why this is not a boolean:

    * **supported** — the route answered.
    * **unsupported** — 404, or a client that cannot reach it. The published
      image is here, and the chart says the series is derived *because the API
      is older*, which is a different sentence from "there is no data".
    * **unknown** — the server did not answer at all. Reporting that as
      unsupported would pin a fallback to a server that is merely down and keep
      it there for the whole TTL after the server came back.
    """
    try:
        await fetch_performance_history(
            client, subject=SUBJECT_CONTROLLER, limit=1, interval="1d"
        )
    except PerformanceHistoryUnsupported as e:
        return {"supported": False, "detail": str(e)}
    except Exception as e:
        logger.debug("Performance-history probe did not complete: %s", e)
        return {
            "supported": False,
            "unknown": True,
            "detail": "the server did not answer",
        }
    return {"supported": True}
