"""Condor runs without Telegram (FEAT-049).

Two of these tests are the feature's safety controls and exist to fail loudly:

* **Mode is never inferred.** ``CONDOR_MODE`` unset with an empty
  ``TELEGRAM_TOKEN`` is a hard error naming ``make setup`` — an install that
  loses its token must stop, never quietly become an unauthenticated dashboard.
* **Local mode binds loopback only.** ``WEB_HOST`` is the one, explicit,
  documented way to expose a dashboard that has no login at all.

Everything else pins the seams: the local-login endpoint exists only in local
mode, the JWT it mints is an ordinary one (``get_current_user`` never learns
which mode it is in), the identity ``make setup`` writes actually resolves, and
PTB still runs a job queue on an Application that was never initialized.

Sync tests driving coroutines with ``asyncio.run``: ``pytest-asyncio`` is a dev
dependency but is not installed in this venv (same convention as
``tests/test_notifications.py``).
"""

import asyncio
import ipaddress

import pytest
from starlette.testclient import TestClient

from condor.web.app import create_app
from config_manager import ConfigManager, ServerPermission, UserRole
from utils import config as app_config
from utils.config import (
    ConfigError,
    check_startup_config,
    resolve_local_user_id,
    resolve_mode,
    resolve_web_host,
)

LOCAL_ID = 1


# ── Mode resolution: explicit, never inferred ──


@pytest.mark.parametrize(
    "env, expected",
    [
        ({}, "telegram"),
        ({"CONDOR_MODE": ""}, "telegram"),
        ({"CONDOR_MODE": "   "}, "telegram"),
        ({"CONDOR_MODE": "telegram"}, "telegram"),
        ({"CONDOR_MODE": "local"}, "local"),
        ({"CONDOR_MODE": "LOCAL"}, "local"),
        ({"CONDOR_MODE": " Local "}, "local"),
        # A typo must not land in local mode. Telegram is the fail-safe answer:
        # it then demands a token, which is the hard error below.
        ({"CONDOR_MODE": "locl"}, "telegram"),
        # The presence of a token says nothing about the mode, in either
        # direction: this is the inference that must never happen.
        ({"TELEGRAM_TOKEN": ""}, "telegram"),
        ({"CONDOR_MODE": "local", "TELEGRAM_TOKEN": "123:abc"}, "local"),
    ],
)
def test_resolve_mode(env, expected):
    assert resolve_mode(env) == expected


def test_no_mode_and_no_token_is_a_hard_error_naming_setup():
    """CONTROL (a). The failure this feature must never have.

    An install whose token went missing has to stop with an instruction, not
    fall through to a dashboard anybody can walk into.
    """
    with pytest.raises(ConfigError) as excinfo:
        check_startup_config({})

    message = str(excinfo.value)
    assert "make setup" in message
    assert "CONDOR_MODE" in message


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"TELEGRAM_TOKEN": ""},
        {"TELEGRAM_TOKEN": "   "},
        {"CONDOR_MODE": "telegram"},
        {"CONDOR_MODE": "telegram", "TELEGRAM_TOKEN": ""},
        # A mistyped mode is telegram, so it still needs a token.
        {"CONDOR_MODE": "locall", "TELEGRAM_TOKEN": ""},
    ],
)
def test_telegram_mode_without_a_token_refuses_to_start(env):
    """CONTROL (a), across the whole env matrix that could mean 'no token'."""
    with pytest.raises(ConfigError):
        check_startup_config(env)


@pytest.mark.parametrize(
    "env",
    [
        {"TELEGRAM_TOKEN": "123:abc"},
        {"CONDOR_MODE": "telegram", "TELEGRAM_TOKEN": "123:abc"},
        # Local mode is the only way to legitimately run with no token.
        {"CONDOR_MODE": "local"},
        {"CONDOR_MODE": "local", "TELEGRAM_TOKEN": ""},
    ],
)
def test_valid_configurations_start(env):
    check_startup_config(env)  # must not raise


# ── Bind address ──


def test_local_mode_binds_loopback_only():
    """CONTROL (b). Local mode has no authentication; it must not be routable."""
    host = resolve_web_host({"CONDOR_MODE": "local"})

    assert ipaddress.ip_address(host).is_loopback
    assert host == "127.0.0.1"


def test_telegram_mode_still_binds_all_interfaces():
    """Telegram mode authenticates, and its binding is unchanged."""
    assert resolve_web_host({}) == "0.0.0.0"
    assert resolve_web_host({"CONDOR_MODE": "telegram"}) == "0.0.0.0"


@pytest.mark.parametrize("mode", ["local", "telegram", ""])
def test_web_host_is_the_explicit_opt_out(mode):
    """CONTROL (b)'s escape hatch: exposing it has to be a deliberate act."""
    env = {"CONDOR_MODE": mode, "WEB_HOST": "0.0.0.0"}
    assert resolve_web_host(env) == "0.0.0.0"

    env["WEB_HOST"] = "10.0.0.5"
    assert resolve_web_host(env) == "10.0.0.5"


def test_uvicorn_binds_the_resolved_host(monkeypatch):
    """CONTROL (b) is wired, not just computed: uvicorn gets WEB_HOST."""
    import main

    monkeypatch.setattr(main, "WEB_HOST", "127.0.0.1")
    assert ipaddress.ip_address(main._web_server_config(object()).host).is_loopback

    monkeypatch.setattr(main, "WEB_HOST", "0.0.0.0")
    assert main._web_server_config(object()).host == "0.0.0.0"


# ── Local identity ──


@pytest.mark.parametrize(
    "env, expected",
    [
        ({}, 1),
        ({"CONDOR_LOCAL_USER_ID": ""}, 1),
        ({"CONDOR_LOCAL_USER_ID": "42"}, 42),
        # Junk and falsy ids fall back to 1: several call sites read a falsy
        # user or chat id as "absent", so 0 would silently address nobody.
        ({"CONDOR_LOCAL_USER_ID": "nope"}, 1),
        ({"CONDOR_LOCAL_USER_ID": "0"}, 1),
        ({"CONDOR_LOCAL_USER_ID": "-5"}, 1),
    ],
)
def test_resolve_local_user_id(env, expected):
    assert resolve_local_user_id(env) == expected


def test_setup_template_gives_the_local_user_a_working_identity(tmp_path, monkeypatch):
    """What ``make setup`` writes in local mode has to actually resolve.

    ``ADMIN_USER_ID=1`` in ``.env`` plus ``admin_id: 1`` and
    ``server_access.local.owner_id: 1`` in ``config.yml`` is the whole identity
    story — no new concept, just the integer everything already keys on. If this
    breaks, ``/auth/local-login`` 500s and nothing else works.

    The ``.env`` half is load-bearing, not decoration: ``ConfigManager`` only
    materialises the admin's *user record* from the environment's
    ``ADMIN_USER_ID`` (``_get_admin_from_env``), so a ``config.yml`` naming
    ``admin_id: 1`` with an empty ``users:`` grants nothing on its own.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app_config, "ADMIN_USER_ID", LOCAL_ID)
    (tmp_path / "config.yml").write_text(
        """servers:
  local:
    host: localhost
    port: 8000
    username: admin
    password: admin

default_server: local

admin_id: 1

users: {}

server_access:
  local:
    owner_id: 1
    created_at: null
    shared_with: {}

chat_defaults:
    1: local

version: 1
""",
        encoding="utf-8",
    )

    cm = ConfigManager(config_path=str(tmp_path / "config.yml"))

    assert cm.get_user_role(LOCAL_ID) == UserRole.ADMIN
    assert cm.has_server_access(LOCAL_ID, "local", ServerPermission.TRADER)


# ── The one mode-aware endpoint ──


@pytest.fixture
def client(monkeypatch):
    """A dashboard whose config manager knows exactly one user: the local admin."""
    monkeypatch.setenv("WEB_JWT_SECRET", "test-secret-for-local-mode")

    class _CM:
        def get_user_role(self, user_id):
            return UserRole.ADMIN if user_id == LOCAL_ID else None

    monkeypatch.setattr("condor.web.routes.auth.get_config_manager", lambda: _CM())
    monkeypatch.setattr("condor.web.auth.get_config_manager", lambda: _CM())
    monkeypatch.setattr(app_config, "LOCAL_USER_ID", LOCAL_ID)
    with TestClient(create_app()) as test_client:
        yield test_client


def _set_mode(monkeypatch, mode: str) -> None:
    monkeypatch.setattr(app_config, "CONDOR_MODE", mode)
    monkeypatch.setattr(app_config, "LOCAL_MODE", mode == "local")


def test_local_login_does_not_exist_in_telegram_mode(client, monkeypatch):
    """CONTROL, second half: no passwordless login exists where anyone can reach it."""
    _set_mode(monkeypatch, "telegram")
    assert client.post("/api/v1/auth/local-login").status_code == 404


def test_local_login_does_not_exist_when_mode_is_unset(client, monkeypatch):
    """An install that never heard of CONDOR_MODE has no local login."""
    _set_mode(monkeypatch, resolve_mode({}))
    assert client.post("/api/v1/auth/local-login").status_code == 404


def test_local_login_mints_a_session_the_shared_guard_accepts(client, monkeypatch):
    """The JWT is an ordinary one: ``get_current_user`` never learns the mode."""
    _set_mode(monkeypatch, "local")

    res = client.post("/api/v1/auth/local-login")
    assert res.status_code == 200
    body = res.json()
    assert body["user"]["id"] == LOCAL_ID
    assert body["user"]["role"] == "admin"
    assert res.headers["Referrer-Policy"] == "no-referrer"
    assert res.headers["Cache-Control"] == "no-store"

    # The unchanged guard, reached through an unchanged endpoint.
    me = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {body['token']}"}
    )
    assert me.status_code == 200
    assert me.json()["id"] == LOCAL_ID


def test_local_login_refuses_an_unconfigured_local_user(client, monkeypatch):
    """No config.yml entry means no session — not a session for a stranger."""
    _set_mode(monkeypatch, "local")
    monkeypatch.setattr(app_config, "LOCAL_USER_ID", 4242)

    res = client.post("/api/v1/auth/local-login")
    assert res.status_code == 500
    assert "make setup" in res.json()["detail"]


def test_auth_mode_reports_the_mode_without_a_session(client, monkeypatch):
    """The Login page has to know how to log in before it can."""
    _set_mode(monkeypatch, "local")
    assert client.get("/api/v1/auth/mode").json() == {"mode": "local"}

    _set_mode(monkeypatch, "telegram")
    assert client.get("/api/v1/auth/mode").json() == {"mode": "telegram"}


# ── Boot without Telegram ──


def test_job_queue_runs_on_an_uninitialized_application():
    """Local mode's whole boot rests on this PTB behaviour, so pin it.

    Scheduled routines, update checks and signals are all ``job_queue`` jobs.
    Local mode never calls ``initialize()``/``start()`` (nothing polls), it just
    starts the queue — a PTB release that breaks that must fail here rather than
    in someone's install.
    """
    from telegram.ext import Application

    async def scenario():
        application = Application.builder().token("0:local").build()
        fired = asyncio.Event()

        async def _job(_context):
            fired.set()

        # Scheduled *before* the queue starts, exactly as ``startup()`` does.
        application.job_queue.run_once(_job, 0)
        await application.job_queue.start()
        try:
            await asyncio.wait_for(fired.wait(), timeout=5)
        finally:
            await application.job_queue.stop()

    asyncio.run(scenario())


def test_outbound_messages_go_to_the_bell_in_local_mode(monkeypatch):
    """``context.bot`` is never None: with no Telegram it is the dashboard bell."""
    import main
    from condor.notifications import NotifyBot

    class _App:
        bot = object()

    monkeypatch.setattr(main, "LOCAL_MODE", True)
    assert isinstance(main._outbound_bot(_App()), NotifyBot)

    monkeypatch.setattr(main, "LOCAL_MODE", False)
    assert main._outbound_bot(_App()) is _App.bot
