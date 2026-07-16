"""Loopback-only network posture (§5.5) — mechanical, not auth.

Single local user; the control socket is 0600 (OS-enforced). Removing auth
is only safe when the HTTP surface is MECHANICALLY local:

- **Bind**: 127.0.0.1/::1 only — a non-loopback bind host is rejected at
  startup (a remote dashboard comes back WITH auth, never by loosening
  this).
- **Host / WS Origin**: validated against a loopback allowlist (mitigates
  DNS rebinding and hostile browser pages driving the local API).
- **Unsafe methods** (POST/PUT/PATCH/DELETE) additionally require a
  loopback ``Origin``/``Referer`` OR the per-process ``X-Condor-Token``: a
  hostile page can submit an ordinary cross-origin form POST to 127.0.0.1
  with a valid loopback Host, and CORS does not stop the write. The
  dashboard fetches the token from ``GET /api/v1/security/token`` (readable
  cross-origin only under CORS, which allows loopback origins only).

``X-Condor-Token`` (dashboard CSRF) and ``store/.direct-token``
(condor-direct execution bootstrap, §6.2) are SEPARATE, independently
generated secrets — the CSRF token is exposed to browser code and must
never bootstrap execution authority; neither endpoint accepts the other's
token.
"""

from __future__ import annotations

import ipaddress
import logging
import secrets
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.websockets import WebSocket

log = logging.getLogger(__name__)

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1", "[::1]"}

# Per-process CSRF token: minted at import, rotated every restart, delivered
# to the dashboard over a same-origin GET.
CSRF_TOKEN = secrets.token_urlsafe(32)


def _is_loopback_hostname(hostname: str) -> bool:
    if not hostname:
        return False
    host = hostname.strip().lower().strip("[]")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_bind_host(host: str) -> None:
    """Startup gate: reject any non-loopback bind host (§5.5)."""
    if not _is_loopback_hostname(host):
        raise SystemExit(
            f"refusing to bind non-loopback host {host!r} — the dashboard is "
            "loopback-only (no auth); a remote deployment must come back WITH "
            "auth, not by loosening the bind"
        )


def _host_header_is_loopback(host_header: str) -> bool:
    """Host header (host[:port]) against the loopback allowlist."""
    if not host_header:
        return False
    # urlsplit handles [::1]:8000 correctly with a scheme prefix.
    try:
        hostname = urlsplit(f"//{host_header}").hostname or ""
    except ValueError:
        return False
    return _is_loopback_hostname(hostname)


def _origin_is_loopback(origin: str) -> bool:
    """An Origin/Referer URL whose host is loopback."""
    if not origin:
        return False
    try:
        hostname = urlsplit(origin).hostname or ""
    except ValueError:
        return False
    return _is_loopback_hostname(hostname)


class LoopbackPostureMiddleware(BaseHTTPMiddleware):
    """Host validation for every request + CSRF posture for unsafe methods."""

    async def dispatch(self, request: Request, call_next):
        host_header = request.headers.get("host", "")
        if not _host_header_is_loopback(host_header):
            log.warning("rejected request with non-loopback Host %r", host_header)
            return JSONResponse(
                {"detail": "non-loopback Host rejected"}, status_code=400
            )

        if request.method in UNSAFE_METHODS:
            origin = request.headers.get("origin", "")
            referer = request.headers.get("referer", "")
            token = request.headers.get("x-condor-token", "")
            allowed = (
                (origin and _origin_is_loopback(origin))
                or (not origin and referer and _origin_is_loopback(referer))
                or (token and secrets.compare_digest(token, CSRF_TOKEN))
            )
            if not allowed:
                log.warning(
                    "rejected unsafe %s %s: origin=%r referer=%r token=%s",
                    request.method,
                    request.url.path,
                    origin,
                    referer,
                    "present" if token else "absent",
                )
                return JSONResponse(
                    {
                        "detail": "unsafe method requires a loopback "
                        "Origin/Referer or X-Condor-Token"
                    },
                    status_code=403,
                )

        return await call_next(request)


def websocket_origin_allowed(ws: WebSocket) -> bool:
    """WS upgrades: loopback Host AND (no Origin, or loopback Origin).

    Browsers always send Origin on WS; non-browser clients (tests, CLIs)
    send none and are local by construction (loopback bind).
    """
    host_header = ws.headers.get("host", "")
    if not _host_header_is_loopback(host_header):
        return False
    origin = ws.headers.get("origin", "")
    if origin and not _origin_is_loopback(origin):
        return False
    return True
