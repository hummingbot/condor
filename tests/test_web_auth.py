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
