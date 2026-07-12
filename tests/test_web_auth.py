"""Tests for one-time web login token redemption (condor/web/auth.py)."""

import time

import condor.web.auth as auth
from condor.web.auth import create_login_token, redeem_login_token


def _reset_store():
    auth._pending_login_tokens.clear()


def test_valid_token_redeems_once():
    """A fresh token redeems successfully exactly once (single-use)."""
    _reset_store()
    token = create_login_token(123, username="u", first_name="f")

    info = redeem_login_token(token)
    assert info is not None
    assert info["user_id"] == 123

    # Single-use: a second redemption of the same token fails.
    assert redeem_login_token(token) is None


def test_expired_token_is_rejected():
    """A token older than the TTL is rejected."""
    _reset_store()
    token = create_login_token(123)
    # Backdate creation beyond the TTL.
    auth._pending_login_tokens[token]["created_at"] = (
        time.time() - auth._LOGIN_TOKEN_TTL - 1
    )
    assert redeem_login_token(token) is None


def test_unknown_token_is_rejected():
    """A random/unknown token is rejected without error."""
    _reset_store()
    assert redeem_login_token("does-not-exist") is None


def test_expired_redemptions_do_not_block_later_valid_redemption():
    """CORR-031: repeated expired-link clicks must not lock out a valid login.

    A user who opens several stale /web links (expired tokens) must still be
    able to redeem a fresh, valid token afterwards for the same user_id.
    """
    _reset_store()
    user_id = 999

    # Simulate many expired-token redemptions in a row.
    for _ in range(10):
        stale = create_login_token(user_id)
        auth._pending_login_tokens[stale]["created_at"] = (
            time.time() - auth._LOGIN_TOKEN_TTL - 1
        )
        assert redeem_login_token(stale) is None

    # A subsequent valid token must still redeem.
    valid = create_login_token(user_id)
    info = redeem_login_token(valid)
    assert info is not None
    assert info["user_id"] == user_id


# ── CLI login tokens (`condor login-token`) ──


def test_cli_login_token_roundtrip(monkeypatch):
    """A CLI-minted token redeems to the right user via the stateless path."""
    monkeypatch.setenv("WEB_JWT_SECRET", "test-secret")
    token = auth.create_cli_login_token(456, username="feng")
    info = auth.redeem_cli_login_token(token)
    assert info is not None
    assert info["user_id"] == 456
    assert info["username"] == "feng"


def test_cli_login_token_rejected_as_session_bearer(monkeypatch):
    """Purpose-tagged tokens must never pass the session-JWT decoder,
    while ordinary session JWTs still do."""
    monkeypatch.setenv("WEB_JWT_SECRET", "test-secret")
    cli_token = auth.create_cli_login_token(456)
    assert auth.decode_jwt(cli_token) is None

    session = auth.create_jwt(456, username="feng")
    payload = auth.decode_jwt(session)
    assert payload is not None
    assert payload["sub"] == "456"


def test_session_jwt_not_redeemable_as_cli_login(monkeypatch):
    """The reverse direction is also closed: a session JWT (no purpose
    claim) is not a valid login token, and garbage is rejected."""
    monkeypatch.setenv("WEB_JWT_SECRET", "test-secret")
    session = auth.create_jwt(456)
    assert auth.redeem_cli_login_token(session) is None
    assert auth.redeem_cli_login_token("garbage") is None


def test_cli_login_token_expires(monkeypatch):
    """An expired CLI token is rejected."""
    monkeypatch.setenv("WEB_JWT_SECRET", "test-secret")
    monkeypatch.setattr(auth, "_CLI_LOGIN_TOKEN_TTL", -1)
    token = auth.create_cli_login_token(456)
    assert auth.redeem_cli_login_token(token) is None
