from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from config_manager import UserRole, get_config_manager
from condor.web.models import WebUser
from utils.config import TELEGRAM_TOKEN

_ALGORITHM = "HS256"
_TOKEN_EXPIRE_SECONDS = 86400  # 24 hours
_AUTH_WINDOW_SECONDS = 86400  # accept auth_date within 24 hours
_LOGIN_TOKEN_TTL = 300  # one-time login tokens valid for 5 minutes

_bearer_scheme = HTTPBearer()

# In-memory store: token_str -> {user_id, username, first_name, created_at}
_pending_login_tokens: dict[str, dict] = {}


def _jwt_secret() -> str:
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


def create_jwt(user_id: int, username: str = "", first_name: str = "", role: str = "user") -> str:
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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user_id = int(payload["sub"])
    cm = get_config_manager()
    role = cm.get_user_role(user_id)

    if role not in (UserRole.USER, UserRole.ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

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
    expired = [k for k, v in _pending_login_tokens.items() if now - v["created_at"] > _LOGIN_TOKEN_TTL]
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


def redeem_login_token(token: str) -> Optional[dict]:
    """Redeem a one-time login token. Returns user info or None if invalid/expired."""
    info = _pending_login_tokens.pop(token, None)
    if info is None:
        return None
    if time.time() - info["created_at"] > _LOGIN_TOKEN_TTL:
        return None
    return info
