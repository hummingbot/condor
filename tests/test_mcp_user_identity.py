"""An ownerless MCP launch resolves to the admin, not to user ``0`` (CORR-229).

The checked-in ``.mcp.json`` starts ``python -m mcp_servers.condor`` with no
``--user-id`` and an empty ``env``. That used to leave ``settings.user_id`` at
``0``, mint a JWT for a user ``config.yml`` has never heard of, and turn every
main-process tool into an opaque ``403 Access denied``. These tests pin the
resolution chain — argv → ``CONDOR_USER_ID`` → ``ADMIN_USER_ID`` → ``0`` — and
the warning that makes the last rung legible.
"""

import logging
import sys

import pytest


@pytest.fixture
def clean_env(monkeypatch):
    """No identity on any channel, and no argv but the program name."""
    monkeypatch.setattr(sys, "argv", ["prog"])
    monkeypatch.delenv("CONDOR_USER_ID", raising=False)
    monkeypatch.delenv("ADMIN_USER_ID", raising=False)


def _user_id() -> int:
    from mcp_servers.condor import settings as settings_module

    return settings_module._parse_settings().user_id


def test_ownerless_launch_resolves_to_the_admin(clean_env, monkeypatch):
    """The `.mcp.json` case: no argv, no CONDOR_USER_ID, .env has the admin."""
    monkeypatch.setenv("ADMIN_USER_ID", "8675309")
    assert _user_id() == 8675309


def test_argv_still_wins_over_both_env_vars(clean_env, monkeypatch):
    """The SEC-180 contract is unchanged: an explicit --user-id is the owner."""
    monkeypatch.setattr(sys, "argv", ["prog", "--user-id", "42"])
    monkeypatch.setenv("CONDOR_USER_ID", "777")
    monkeypatch.setenv("ADMIN_USER_ID", "8675309")
    assert _user_id() == 42


def test_condor_user_id_wins_over_admin_user_id(clean_env, monkeypatch):
    """A spawned session acts as its own user, never as the installation admin."""
    monkeypatch.setenv("CONDOR_USER_ID", "777")
    monkeypatch.setenv("ADMIN_USER_ID", "8675309")
    assert _user_id() == 777


@pytest.mark.parametrize("raw", ["", "   ", "0", "-1", "not-an-id"])
def test_unusable_condor_user_id_falls_through_to_the_admin(
    clean_env, monkeypatch, raw
):
    """A falsy or junk id addresses nobody, so it must not shadow the admin.

    Junk used to raise ValueError at import, which is the same dead server with
    a worse error.
    """
    monkeypatch.setenv("CONDOR_USER_ID", raw)
    monkeypatch.setenv("ADMIN_USER_ID", "8675309")
    assert _user_id() == 8675309


def test_no_identity_anywhere_warns_and_names_the_env_var(clean_env, caplog):
    """The last rung is still ``0``, but it says so instead of 403ing silently."""
    with caplog.at_level(logging.WARNING, logger="condor.mcp"):
        assert _user_id() == 0

    assert "CONDOR_USER_ID" in caplog.text
    assert "ADMIN_USER_ID" in caplog.text


def test_a_junk_admin_user_id_does_not_crash_the_server(clean_env, monkeypatch, caplog):
    """resolve_admin_id() treats junk as absent; the parse must survive it."""
    monkeypatch.setenv("ADMIN_USER_ID", "not-an-id")
    with caplog.at_level(logging.WARNING, logger="condor.mcp"):
        assert _user_id() == 0

    assert "CONDOR_USER_ID" in caplog.text
