from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Optional

from fastapi import Depends, HTTPException, status
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

# Rate limiting for one-time login token redemption (per user_id, in-memory).
_LOGIN_REDEEM_MAX_FAILURES = 5  # max failed attempts within the window
_LOGIN_REDEEM_WINDOW = 300  # rolling window in seconds

_bearer_scheme = HTTPBearer()

# In-memory store: token_str -> {user_id, username, first_name, created_at}
_pending_login_tokens: dict[str, dict] = {}

# In-memory rate-limit store: user_id -> list[timestamp] of recent failed redeem attempts
_login_redeem_failures: dict[int, list[float]] = {}

# Flag so we only warn once per process about the missing dedicated secret.
_jwt_secret_warned = False


def _jwt_secret() -> str:
    """Return the secret used to sign/verify web session JWTs.

    Prefers the dedicated ``WEB_JWT_SECRET`` environment variable so the web
    session secret can be rotated independently of the Telegram bot token. If
    it is not set, falls back to deriving the secret from ``TELEGRAM_TOKEN``
    (the legacy behaviour) to avoid invalidating existing sessions, and logs a
    warning recommending that ``WEB_JWT_SECRET`` be configured.
    """
    global _jwt_secret_warned
    web_secret = os.getenv("WEB_JWT_SECRET")
    if web_secret:
        return web_secret
    if not _jwt_secret_warned:
        logger.warning(
            "WEB_JWT_SECRET is not set; deriving the JWT signing secret from "
            "TELEGRAM_TOKEN (legacy behaviour). Set WEB_JWT_SECRET to a dedicated "
            "random value so web sessions can be rotated independently of the bot token."
        )
        _jwt_secret_warned = True
    return hashlib.sha256(TELEGRAM_TOKEN.encode()).hexdigest()


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


def _is_rate_limited(user_id: int, now: float) -> bool:
    """Return True if user_id has too many recent failed redeem attempts."""
    attempts = _login_redeem_failures.get(user_id)
    if not attempts:
        return False
    recent = [t for t in attempts if now - t < _LOGIN_REDEEM_WINDOW]
    if recent:
        _login_redeem_failures[user_id] = recent
    else:
        _login_redeem_failures.pop(user_id, None)
    return len(recent) >= _LOGIN_REDEEM_MAX_FAILURES


def _record_redeem_failure(user_id: int, now: float) -> None:
    """Record a failed redeem attempt for a user_id (for rate limiting)."""
    _login_redeem_failures.setdefault(user_id, []).append(now)


def redeem_login_token(token: str) -> Optional[dict]:
    """Redeem a one-time login token. Returns user info or None if invalid/expired.

    Applies a best-effort in-memory rate limit per user_id and garbage-collects
    expired tokens on every call (so unredeemed tokens do not leak memory).
    """
    now = time.time()
    # Sweep expired tokens up front so the store does not grow unbounded
    # even when tokens are never redeemed.
    _gc_expired_login_tokens(now)

    info = _pending_login_tokens.pop(token, None)
    if info is None:
        return None

    user_id = info["user_id"]

    # Reject expired tokens (already popped above).
    if now - info["created_at"] > _LOGIN_TOKEN_TTL:
        _record_redeem_failure(user_id, now)
        return None

    # Rate-limit redemptions per user_id after repeated failures.
    if _is_rate_limited(user_id, now):
        _record_redeem_failure(user_id, now)
        return None

    # Successful redemption: clear any prior failure history for this user.
    _login_redeem_failures.pop(user_id, None)
    return info
