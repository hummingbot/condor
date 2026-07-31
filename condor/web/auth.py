from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Optional

from fastapi import Depends, HTTPException, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from condor.web.models import WebUser
from config_manager import UserRole, get_config_manager
from utils.config import TELEGRAM_TOKEN

logger = logging.getLogger(__name__)

_ALGORITHM = "HS256"
_TOKEN_EXPIRE_SECONDS = 86400  # 24 hours
_AUTH_WINDOW_SECONDS = 86400  # accept auth_date within 24 hours
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


# ── Telegram Login Widget verification ──


def verify_telegram_login(data: dict) -> bool:
    """Verify data from the Telegram Login Widget using HMAC-SHA256."""
    check_hash = data.get("hash", "")
    auth_date = data.get("auth_date", 0)

    # Check auth_date freshness
    if abs(time.time() - int(auth_date)) > _AUTH_WINDOW_SECONDS:
        return False

    # Build check string (alphabetically sorted key=value, excluding hash)
    filtered = {k: v for k, v in data.items() if k != "hash"}
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(filtered.items()))

    secret_key = hashlib.sha256(TELEGRAM_TOKEN.encode()).digest()
    computed = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

    return hmac.compare_digest(computed, check_hash)


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
