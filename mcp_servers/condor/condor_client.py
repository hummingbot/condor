"""HTTP client for calling the Condor main-process web API."""

import asyncio
import json
import re
import time
from urllib.parse import quote, unquote

import aiohttp

from mcp_servers.condor.exceptions import (
    APIError,
    APIHTTPError,
    APITimeout,
    APIUnreachable,
)
from mcp_servers.condor.settings import settings

# A refused connection usually means the main process is mid-restart, which is
# over in a second or two. Only an idempotent GET is retried: the request never
# ran, but a POST that *did* reach the route and then lost its connection must
# not be sent twice.
_RETRY_BACKOFF = (0.2, 0.6)

# How long a minted token is reused. Far under the token's own lifetime, so a
# cached one is never presented near its expiry.
_TOKEN_REUSE_SEC = 300.0

_session: aiohttp.ClientSession | None = None
_session_loop: asyncio.AbstractEventLoop | None = None
_token: tuple[int, float, str] | None = None


def _get_session() -> aiohttp.ClientSession:
    """The one session this subprocess talks to the main process through.

    A session per call paid a connector and a TCP handshake every time — 120 of
    them for one routine waited on for two minutes. A session belongs to the
    loop it was made on, so one made on a loop that has since gone (a test's
    ``asyncio.run``) is replaced rather than reused.
    """
    global _session, _session_loop
    loop = asyncio.get_running_loop()
    if _session is None or _session.closed or _session_loop is not loop:
        _session = aiohttp.ClientSession()
        _session_loop = loop
    return _session


def _auth_header() -> dict[str, str]:
    global _token
    from condor.web.auth import create_jwt

    now = time.monotonic()
    if (
        _token is None
        or _token[0] != settings.user_id
        or now - _token[1] > _TOKEN_REUSE_SEC
    ):
        _token = (settings.user_id, now, create_jwt(settings.user_id, role="user"))
    return {"Authorization": f"Bearer {_token[2]}"}


def _detail(status: int, text: str) -> str:
    """What an error response said, whether or not it was JSON."""
    try:
        data = json.loads(text)
    except ValueError:
        return text.strip()[:500] or f"HTTP {status} with an empty body"
    if isinstance(data, dict) and "detail" in data:
        detail = data["detail"]
        return detail if isinstance(detail, str) else json.dumps(detail)
    return str(data)


def path_segment(value: object) -> str:
    """``value`` as exactly one segment of a main-API path, or refuse.

    Ids reach these paths straight from tool arguments, i.e. from a model that
    may be reading injected text. Interpolated raw, ``../settings/servers/x``
    as an agent slug turned an agent delete into ``DELETE /settings/servers/x``:
    the HTTP client collapses dot segments before the request leaves. One
    segment means no separator and no dot segment; what is left is
    percent-encoded so nothing in it is read as path syntax.
    """
    text = str(value)
    if (
        not text
        or text in (".", "..")
        or ".." in text
        or any(ch in text for ch in ("/", "\\", "\0", "?", "#", "%"))
    ):
        raise APIError(f"Invalid id {text!r}")
    return quote(text, safe="")


def _refuse_traversal(path: str) -> None:
    """Refuse a path that would resolve to another route (the backstop).

    :func:`path_segment` is the fix at each call site; this catches the caller
    that forgot it. Dot segments are checked decoded as well, because ``%2e%2e``
    is normalised exactly as ``..`` is.
    """
    route = path.split("?", 1)[0]
    for candidate in (route, unquote(route)):
        if "\\" in candidate or any(seg in (".", "..") for seg in candidate.split("/")):
            raise APIError(f"Refusing a main-API path with a dot segment: {path!r}")


async def call_main_api(
    method: str, path: str, body: dict | None = None, timeout: float | None = None
) -> dict | list:
    """Call the Condor web API in the main process.

    The MCP server runs as a subprocess -- TickEngines must be created in the
    main process so they survive beyond the MCP subprocess lifecycle.

    ``timeout`` defaults to the shared policy (condor.runtime.timeouts) but
    callers that block on the main process (e.g. a routine run waited on inline)
    should pass a larger value.

    Raises APIError on failure instead of returning {"error": ...}, as one of
    three subclasses whose messages say what to do next (MC-11):

    * ``APIUnreachable`` -- nothing accepted the connection; the request did
      not run. An idempotent GET is retried briefly first.
    * ``APITimeout`` -- reached, no answer in time. The request may still be
      executing; the caller must check before retrying.
    * ``APIHTTPError`` -- an error status, with the route's ``detail`` (or the
      body text when the answer was not JSON).
    """
    from condor.runtime.timeouts import TIMEOUTS
    from utils.config import WEB_PORT

    _refuse_traversal(path)
    if timeout is None:
        timeout = TIMEOUTS.mcp_call
    url = f"http://127.0.0.1:{WEB_PORT}/api/v1{path}"
    backoff = _RETRY_BACKOFF if method.upper() == "GET" else ()

    for attempt in range(len(backoff) + 1):
        try:
            async with _get_session().request(
                method,
                url,
                json=body,
                headers=_auth_header(),
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                # Text first: a plain-text 500 or a proxy page is still an
                # answer, and decoding it as JSON ahead of the status check
                # reported it as the API being unreachable.
                text = await resp.text()
                status = resp.status
        except asyncio.TimeoutError:
            raise APITimeout(
                f"The main process did not answer {method} {path} within "
                f"{timeout:g}s. The request may still be executing there — "
                "check its outcome before retrying."
            ) from None
        except aiohttp.ClientConnectionError as e:
            if attempt < len(backoff):
                await asyncio.sleep(backoff[attempt])
                continue
            if isinstance(e, aiohttp.ClientConnectorError):
                raise APIUnreachable(
                    "Condor's main process is not accepting connections — it "
                    f"may be restarting. The request was not carried out ({e})."
                ) from None
            # Connected, then cut off: the route may have run. Not the same
            # advice as a refused connection, so not the same sentence.
            raise APIUnreachable(
                f"The connection to Condor's main process was lost during "
                f"{method} {path} — it may be restarting. The request may or "
                f"may not have been carried out; check before retrying ({e})."
            ) from None
        except Exception as e:
            raise APIError(f"Call to the main process failed: {e}") from None

        if status >= 400:
            raise APIHTTPError(status, _detail(status, text))
        try:
            return json.loads(text)
        except ValueError:
            raise APIError(
                f"The main process answered {method} {path} with a non-JSON "
                f"{status} response: {text.strip()[:200]}"
            ) from None
    raise AssertionError("unreachable")  # the loop returns or raises


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
