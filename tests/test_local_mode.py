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

The identity tests carry a second lesson. Local mode logs in as
``ADMIN_USER_ID`` and nothing else: a dedicated ``CONDOR_LOCAL_USER_ID`` was a
second knob for one identity, and the two disagreed the moment a Telegram
install flipped ``CONDOR_MODE=local``. Whether that identity resolves is now
settled at boot, where the .env is, rather than by a 500 from the browser.

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
    check_local_user,
    check_startup_config,
    resolve_admin_id,
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


# ── Bind address under Tailscale ──
#
# Three deployments, three answers: localhost for a local install, every
# interface for a VPS that authenticates, and this node's tailnet address when
# Tailscale is on -- reachable across the tailnet and on no public interface.
# The dashboard used to bind loopback and rely on `tailscale serve`, which
# needs the daemon socket and so refused unprivileged callers: the proxy never
# came up and the dashboard was reachable from nowhere at all.


@pytest.mark.parametrize("mode", ["local", "telegram"])
def test_tailscale_binds_this_nodes_tailnet_address(monkeypatch, mode):
    """CONTROL (b) under Tailscale: on the tailnet, and nowhere else."""
    import utils.tailscale as ts

    monkeypatch.setattr(ts, "tailnet_ip", lambda timeout=5: "100.101.1.5")
    env = {"CONDOR_MODE": mode, "USE_TAILSCALE": "true"}

    host = resolve_web_host(env)

    assert host == "100.101.1.5"
    assert not ipaddress.ip_address(host).is_loopback


def test_tailscale_fails_closed_when_the_address_is_unknown(monkeypatch):
    """CONTROL (b). Unresolvable must narrow to loopback, never widen to 0.0.0.0.

    The daemon may not be up yet, or the node may not be registered. Falling
    back to a public bind would invert the guarantee the setting exists for.
    """
    import utils.tailscale as ts

    monkeypatch.setattr(ts, "tailnet_ip", lambda timeout=5: None)

    host = resolve_web_host({"CONDOR_MODE": "telegram", "USE_TAILSCALE": "true"})

    assert ipaddress.ip_address(host).is_loopback


def test_web_host_still_overrides_tailscale(monkeypatch):
    """The documented opt-out outranks the tailnet address too."""
    import utils.tailscale as ts

    monkeypatch.setattr(ts, "tailnet_ip", lambda timeout=5: "100.101.1.5")
    env = {"CONDOR_MODE": "telegram", "USE_TAILSCALE": "true", "WEB_HOST": "0.0.0.0"}

    assert resolve_web_host(env) == "0.0.0.0"


@pytest.mark.parametrize(
    "addr,expected",
    [
        ("100.64.0.1", True),  # first address in the CGNAT range
        ("100.127.255.9", True),  # last
        ("100.128.0.1", False),  # just outside
        ("100.63.255.1", False),  # just below
        ("10.0.0.1", False),
        ("127.0.0.1", False),
    ],
)
def test_is_tailnet_ip_matches_the_cgnat_range(addr, expected):
    """Tailscale allocates from 100.64.0.0/10 -- the doctor keys off this."""
    from utils.tailscale import is_tailnet_ip

    assert is_tailnet_ip(addr) is expected


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
        ({"ADMIN_USER_ID": ""}, 1),
        ({"ADMIN_USER_ID": "  "}, 1),
        # The admin *is* the local user. There is no second knob to disagree
        # with this one.
        ({"ADMIN_USER_ID": "42"}, 42),
        ({"ADMIN_USER_ID": " 123456789 "}, 123456789),
        # Junk and falsy ids fall back to 1: several call sites read a falsy
        # user or chat id as "absent", so 0 would silently address nobody.
        # check_startup_config refuses to boot on these separately.
        ({"ADMIN_USER_ID": "nope"}, 1),
        ({"ADMIN_USER_ID": "0"}, 1),
        ({"ADMIN_USER_ID": "-5"}, 1),
    ],
)
def test_resolve_local_user_id(env, expected):
    assert resolve_local_user_id(env) == expected


def test_local_user_is_the_admin_when_a_telegram_install_flips_mode():
    """The regression this collapse exists for.

    A Telegram install that sets ``CONDOR_MODE=local`` keeps its Telegram
    ``ADMIN_USER_ID``. Local mode used to log in as a hardcoded ``1`` that
    ``config.yml`` had never heard of, so ``/auth/local-login`` 500'd on an
    install that was, by every other measure, correctly configured.
    """
    env = {"CONDOR_MODE": "local", "ADMIN_USER_ID": "123456789"}

    assert resolve_local_user_id(env) == 123456789
    assert resolve_local_user_id(env) == resolve_admin_id(env)


@pytest.mark.parametrize("raw", ["ralphsohandsome", "1.5", "0", "-5", "1,2"])
def test_an_unusable_admin_id_refuses_to_start(raw):
    """A typo'd ADMIN_USER_ID used to be swallowed, leaving *no admin at all*.

    No admin panel, no approvals, no boot notification, and in local mode no
    user to log in as — each of which surfaces later as unrelated breakage.
    """
    with pytest.raises(ConfigError) as excinfo:
        check_startup_config({"ADMIN_USER_ID": raw, "TELEGRAM_TOKEN": "123:abc"})

    assert "ADMIN_USER_ID" in str(excinfo.value)
    assert raw in str(excinfo.value)


@pytest.mark.parametrize("raw", ["1", "123456789", " 42 "])
def test_a_usable_admin_id_starts(raw):
    check_startup_config({"ADMIN_USER_ID": raw, "TELEGRAM_TOKEN": "123:abc"})


# ── Local mode's user is checked at boot, not at login ──


def test_local_mode_refuses_to_start_on_an_unconfigured_user():
    """Ralph's report: the failure has to land in ``make run``, not the browser.

    A dashboard that boots, opens, auto-logs-in and *then* 500s tells you
    nothing about the .env line that caused it.
    """
    with pytest.raises(ConfigError) as excinfo:
        check_local_user({"CONDOR_MODE": "local"}, get_role=lambda _uid: None)

    message = str(excinfo.value)
    assert "ADMIN_USER_ID" in message
    assert "make setup" in message


def test_local_mode_names_the_admin_id_it_actually_tried():
    """The message has to name the id, or it sends you looking in config.yml."""
    env = {"CONDOR_MODE": "local", "ADMIN_USER_ID": "999"}

    with pytest.raises(ConfigError) as excinfo:
        check_local_user(env, get_role=lambda _uid: None)

    assert "999" in str(excinfo.value)


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.USER])
def test_local_mode_starts_when_the_user_resolves(role):
    check_local_user({"CONDOR_MODE": "local"}, get_role=lambda _uid: role)


@pytest.mark.parametrize("role", [UserRole.PENDING, UserRole.BLOCKED, None])
def test_local_mode_refuses_a_user_who_cannot_use_the_dashboard(role):
    """Same bar as ``get_current_user``: pending and blocked are not sessions."""
    with pytest.raises(ConfigError):
        check_local_user({"CONDOR_MODE": "local"}, get_role=lambda _uid: role)


@pytest.mark.parametrize("env", [{}, {"CONDOR_MODE": "telegram"}, {"CONDOR_MODE": ""}])
def test_the_local_user_check_is_a_no_op_in_telegram_mode(env):
    """Telegram mode has its own auth; this check must never touch it."""

    def _explode(_uid):  # pragma: no cover - must not be called
        raise AssertionError("config.yml consulted outside local mode")

    check_local_user(env, get_role=_explode)


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
