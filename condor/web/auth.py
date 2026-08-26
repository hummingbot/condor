from __future__ import annotations

import logging
import os
import secrets
import time
from typing import Optional

from fastapi import Depends, HTTPException, Query, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from condor.web.models import WebUser
from config_manager import ServerPermission, UserRole, get_config_manager

logger = logging.getLogger(__name__)

_ALGORITHM = "HS256"
_TOKEN_EXPIRE_SECONDS = 86400  # 24 hours
_LOGIN_TOKEN_TTL = 300  # one-time login tokens valid for 5 minutes

_bearer_scheme = HTTPBearer()

# In-memory store: token_str -> {user_id, username, first_name, created_at}
_pending_login_tokens: dict[str, dict] = {}


def _jwt_secret() -> str:
    """Return the secret used to sign/verify web session JWTs.

    Prefers the dedicated ``WEB_JWT_SECRET`` environment variable so the secret
    can be shared across instances or rotated on demand. When it is not set, a
    strong random secret is generated once and persisted to ``config.yml`` (via
    :class:`ConfigManager`), so the dashboard is secure by default with no
    operator configuration and web sessions survive restarts. The secret is
    never derived from ``TELEGRAM_TOKEN`` — that coupled the two trust domains
    and broke when the token was empty.
    """
    web_secret = os.getenv("WEB_JWT_SECRET")
    if web_secret:
        return web_secret
    return get_config_manager().get_or_create_web_jwt_secret()


# ── JWT helpers ──


def create_jwt(
    user_id: int, username: str = "", first_name: str = "", role: str = "user"
) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "first_name": first_name,
        "role": role,
        "exp": int(time.time()) + _TOKEN_EXPIRE_SECONDS,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=_ALGORITHM)


def decode_jwt(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=[_ALGORITHM])
    except JWTError:
        return None


# ── WebSocket auth (subprotocol header, query-param fallback) ──

# Sentinel subprotocol that marks a JWT-carrying handshake. The client offers
# ``[WS_AUTH_SUBPROTOCOL, <jwt>]`` as Sec-WebSocket-Protocol values so the token
# stays out of the URL (and thus out of proxy/access logs and browser history).
WS_AUTH_SUBPROTOCOL = "condor-jwt"


def extract_ws_token(
    ws: WebSocket, query_token: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    """Extract the auth JWT from a WebSocket handshake.

    Prefers the ``Sec-WebSocket-Protocol`` subprotocol header: the client offers
    ``[WS_AUTH_SUBPROTOCOL, <jwt>]`` and we read the token from the second value.
    Falls back to the (deprecated, log-leaking) ``?token=`` query param so live
    sessions and older clients keep working during rollout.

    Returns ``(token, accept_subprotocol)``. ``accept_subprotocol`` is the
    sentinel (never the token) when the subprotocol path is used and must be
    echoed back in ``ws.accept(subprotocol=...)`` or the browser rejects the
    handshake; it is ``None`` for the query-param fallback.
    """
    subprotocols = ws.scope.get("subprotocols", [])
    if (
        subprotocols
        and subprotocols[0] == WS_AUTH_SUBPROTOCOL
        and len(subprotocols) >= 2
    ):
        return subprotocols[1], WS_AUTH_SUBPROTOCOL
    return query_token, None


# ── FastAPI dependency ──


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> WebUser:
    """FastAPI dependency that extracts and validates the JWT."""
    payload = decode_jwt(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )

    user_id = int(payload["sub"])
    cm = get_config_manager()
    role = cm.get_user_role(user_id)

    if role not in (UserRole.USER, UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )

    return WebUser(
        id=user_id,
        username=payload.get("username", ""),
        first_name=payload.get("first_name", ""),
        role=role.value,
    )


# ── Server-scoped access (SEC-147) ──


def check_server_access(user_id: int, server_name: str) -> None:
    """Raise ``403 No access`` unless ``user_id`` may use ``server_name``.

    Single implementation of the guard that used to be hand-copied into every
    server-scoped web endpoint. ``has_server_access`` defaults to
    :attr:`ServerPermission.TRADER`, which is the level every web call already
    required. Endpoints that need a *stronger* level (owner-only credential and
    server mutations in ``routes/settings.py``) still layer their own check on
    top of this one — this is the floor, never the ceiling.

    Endpoints whose server name arrives in the request **body** call this
    directly (a path/query dependency cannot see the body); endpoints that take
    it as a path or query parameter use the ``require_server_access*``
    dependencies below, which are thin wrappers over this function.
    """
    if not get_config_manager().has_server_access(user_id, server_name):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access")


def require_owner(cm, user_id: int, server_name: str) -> None:
    """Enforce the OWNER line on top of the TRADER floor every route already has.

    The rule (SEC-153, extended to the gateway by SEC-166, and to the gateway
    token list by SEC-207):

    * **Reading** a server's state — status, logs, wallet and network listings,
      configured connectors — needs TRADER. A shared trader has to be able to
      see what they are trading against, and to tell the owner when it is down.
    * **Trading** on a server needs TRADER. That is what the share is for.
    * **Mutating a server's configuration or its infrastructure** needs OWNER.
      Exchange credentials, the gateway container lifecycle, the private keys
      in its keystore, the RPC endpoints it dials and the entries on its token
      list are all the owner's machine, not a trading action — and each of them
      can break the owner's running bots for everyone else on the server.

    Admins keep the bypass they hold everywhere else in the web layer.

    Lives here rather than in ``routes/settings.py`` (its first home) because
    it is the ceiling to this module's floor, and a second hand-copied
    implementation in the next router that needs it is how the line drifts.
    """
    perm = cm.get_server_permission(user_id, server_name)
    if perm != ServerPermission.OWNER and not cm.is_admin(user_id):
        raise HTTPException(status_code=403, detail="Owner access required")


async def require_server_access(
    name: str, user: WebUser = Depends(get_current_user)
) -> WebUser:
    """Auth dependency for endpoints routed under ``/servers/{name}/...``.

    Drop-in replacement for ``Depends(get_current_user)``: it returns the same
    :class:`WebUser`, having first enforced access to the ``{name}`` path
    parameter. If the route has no ``{name}`` path parameter FastAPI treats it
    as a required *query* parameter and the request fails with 422 — the guard
    fails closed, never open.
    """
    check_server_access(user.id, name)
    return user


async def require_server_access_by_server_name(
    server_name: str, user: WebUser = Depends(get_current_user)
) -> WebUser:
    """Same as :func:`require_server_access` for a ``{server_name}`` path param."""
    check_server_access(user.id, server_name)
    return user


async def require_server_access_query(
    server: str = Query(...), user: WebUser = Depends(get_current_user)
) -> WebUser:
    """Same as :func:`require_server_access` for a ``?server=`` query param."""
    check_server_access(user.id, server)
    return user


# ── One-time login tokens (generated from Telegram /web command) ──


def create_login_token(user_id: int, username: str = "", first_name: str = "") -> str:
    """Create a one-time login token for a Telegram user."""
    # Clean up expired tokens
    now = time.time()
    expired = [
        k
        for k, v in _pending_login_tokens.items()
        if now - v["created_at"] > _LOGIN_TOKEN_TTL
    ]
    for k in expired:
        _pending_login_tokens.pop(k, None)

    token = secrets.token_urlsafe(32)
    _pending_login_tokens[token] = {
        "user_id": user_id,
        "username": username,
        "first_name": first_name,
        "created_at": now,
    }
    return token


def _gc_expired_login_tokens(now: float) -> None:
    """Remove expired one-time login tokens from the in-memory store."""
    expired = [
        k
        for k, v in _pending_login_tokens.items()
        if now - v["created_at"] > _LOGIN_TOKEN_TTL
    ]
    for k in expired:
        _pending_login_tokens.pop(k, None)


def redeem_login_token(token: str) -> Optional[dict]:
    """Redeem a one-time login token. Returns user info or None if invalid/expired.

    The security control is the token itself: 32 bytes of cryptographically
    random, single-use (popped on first lookup) data with a short TTL. A token
    cannot be brute-forced, and the user_id is only known *after* a valid token
    is presented, so there is no per-user threat to rate-limit. Garbage-collects
    expired tokens on every call (so unredeemed tokens do not leak memory).
    """
    now = time.time()
    # Sweep expired tokens up front so the store does not grow unbounded
    # even when tokens are never redeemed.
    _gc_expired_login_tokens(now)

    info = _pending_login_tokens.pop(token, None)
    if info is None:
        return None

    # Reject expired tokens (already popped above).
    if now - info["created_at"] > _LOGIN_TOKEN_TTL:
        return None

    return info
