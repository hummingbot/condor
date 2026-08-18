"""SEC-178: a caller-supplied chat id must not resolve someone else's server.

``POST /api/v1/sessions`` takes a whole ``SessionSpec`` off the wire. The route
forces ``spec.user_id`` to the caller, but ``spec.chat_id`` arrived untouched
and became the key for server resolution — and ``chat_defaults`` is a *global*
map keyed by chat id (config_manager.get_effective_server), so naming a chat you
do not own returned that chat's default server.

The name that comes out of that resolution is not a label: ``_shared.py`` looks
its credentials up and puts ``HUMMINGBOT_API_USERNAME``/``PASSWORD`` into the
spawned mcp-hummingbot subprocess's environment. So the bar is the one SEC-164
already set for ``TickEngine._resolve_server``: every candidate — the pinned
name, the chat default, the accessible fallback — must exist *and* be reachable
by the run's own principal.

What must NOT regress: a Telegram group chat's default is legitimately keyed by
chat id (``handlers/config/servers.py`` writes ``set_chat_default_server``), so
a member who *can* reach that server still resolves it.
"""

import asyncio

import pytest

import config_manager
from condor.runtime import SessionKey, SessionSpec
from condor.runtime import client as runtime
from condor.runtime import sessions as session_module

ATTACKER = 42
VICTIM_CHAT = 999
GROUP_CHAT = -100777
VICTIM_SERVER = "victim-prod"
OWN_SERVER = "mine"

SERVERS = {
    VICTIM_SERVER: {
        "host": "10.0.0.9",
        "port": 8000,
        "username": "victim-user",
        "password": "victim-p4ssword",
    },
    OWN_SERVER: {
        "host": "127.0.0.1",
        "port": 8000,
        "username": "my-user",
        "password": "my-p4ssword",
    },
}

# The global map the attack reads: the victim's chat defaults to their server,
# the group chat defaults to one the attacker legitimately reaches.
CHAT_DEFAULTS = {VICTIM_CHAT: VICTIM_SERVER, GROUP_CHAT: OWN_SERVER}


class _Cm:
    """Both servers exist; ``grants`` says which ones the subject may use."""

    def __init__(self, grants: dict[int, list[str]], accessible=None):
        self.grants = grants
        # Overridable so the fallback candidate can be made over-broad on
        # purpose — the gate must hold even if this list ever lies.
        self._accessible = accessible

    def get_server(self, name):
        return SERVERS.get(name)

    def has_server_access(self, user_id, name, *a, **kw):
        return name in self.grants.get(user_id, [])

    def get_accessible_servers(self, user_id):
        if self._accessible is not None:
            return list(self._accessible)
        return list(self.grants.get(user_id, []))

    def is_admin(self, user_id):
        return False


@pytest.fixture
def config(monkeypatch):
    """Install a stub ConfigManager + the global chat-defaults lookup."""

    def install(grants, accessible=None):
        cm = _Cm(grants, accessible)
        monkeypatch.setattr(config_manager, "get_config_manager", lambda: cm)
        # auth.py binds the getter at import time, so the web guard needs its
        # own patch to see the same stub.
        monkeypatch.setattr(
            "condor.web.auth.get_config_manager", lambda: cm, raising=False
        )
        monkeypatch.setattr(
            config_manager,
            "get_effective_server",
            lambda chat_id, user_data=None: CHAT_DEFAULTS.get(chat_id),
        )
        return cm

    return install


def _build(user_id, chat_id, **kwargs):
    from handlers.agents._shared import build_mcp_servers_for_session

    return build_mcp_servers_for_session(user_id, chat_id, **kwargs)


def _hummingbot(servers):
    return next((s for s in servers if s["name"] == "mcp-hummingbot"), None)


def _env_of(server):
    return {e["name"]: e["value"] for e in server.get("env", [])}


def _leaks_victim(servers) -> bool:
    """True if the victim's credentials reached the spawn config, anywhere."""
    blob = repr(servers)
    return any(
        SERVERS[VICTIM_SERVER][field] in blob for field in ("username", "password")
    )


# ── The credential handover: no grant, no mcp-hummingbot ──


def test_pinned_server_without_a_grant_never_yields_its_credentials(config):
    """Candidate 1: the explicitly pinned name."""
    config({ATTACKER: []})

    servers = _build(ATTACKER, ATTACKER, server_name=VICTIM_SERVER)

    assert _hummingbot(servers) is None
    assert not _leaks_victim(servers)


def test_foreign_chat_default_never_yields_its_credentials(config):
    """Candidate 2: the chat default — this is the SEC-178 attack itself.

    The attacker names the victim's chat id. ``chat_defaults[999]`` is the
    victim's server, and before the fix its username/password went straight into
    the subprocess env.
    """
    config({ATTACKER: []})

    servers = _build(ATTACKER, VICTIM_CHAT)

    assert _hummingbot(servers) is None, "victim's server was handed to the subprocess"
    assert not _leaks_victim(servers)
    # The condor MCP server is still spawned: a serverless session is a valid
    # outcome, refusing to resolve is not the same as refusing to run.
    assert [s["name"] for s in servers] == ["condor"]


def test_fallback_candidate_is_held_to_the_same_bar(config):
    """Candidate 3: the accessible fallback, made over-broad on purpose.

    ``get_accessible_servers`` is scoped by construction, so this pins the gate
    rather than the list: whatever name comes out of resolution passed
    ``has_server_access``, with no path around it.
    """
    config({ATTACKER: []}, accessible=[VICTIM_SERVER])

    servers = _build(ATTACKER, ATTACKER)

    assert _hummingbot(servers) is None
    assert not _leaks_victim(servers)


def test_a_granted_server_still_spawns_with_its_credentials(config):
    """The gate denies, it does not break the ordinary path."""
    config({ATTACKER: [OWN_SERVER]})

    servers = _build(ATTACKER, ATTACKER, server_name=OWN_SERVER)

    env = _env_of(_hummingbot(servers))
    assert env["HUMMINGBOT_API_USERNAME"] == SERVERS[OWN_SERVER]["username"]
    assert env["HUMMINGBOT_API_PASSWORD"] == SERVERS[OWN_SERVER]["password"]


def test_group_chat_default_is_not_regressed(config):
    """A member who *can* reach the group's default still resolves it.

    The fix must not do what engine.py did and drop ``chat_id`` from
    resolution: a Telegram group's default server is legitimately keyed by chat
    id, and every member of it is entitled to that server.
    """
    config({ATTACKER: [OWN_SERVER]})

    servers = _build(ATTACKER, GROUP_CHAT)

    hummingbot = _hummingbot(servers)
    assert hummingbot is not None
    assert OWN_SERVER in hummingbot["args"]
    assert _env_of(hummingbot)["HUMMINGBOT_API_PASSWORD"] == (
        SERVERS[OWN_SERVER]["password"]
    )


# ── The session the spawn is recorded as ──


class _FakeClient:
    """Stands in for the ACP subprocess."""

    def __init__(self, **kwargs):
        self.alive = True
        self.kwargs = kwargs

    async def start(self):
        pass

    async def stop(self):
        self.alive = False

    async def prompt(self, text):
        return ""


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr(session_module, "ACPClient", _FakeClient)
    monkeypatch.setattr(session_module, "PydanticAIClient", _FakeClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "handlers.agents._shared.build_mcp_servers_for_session", lambda *a, **k: []
    )
    return session_module


def test_session_does_not_record_a_server_the_owner_cannot_reach(config, registry):
    """The spec's chat id is not a licence for the session's own server name."""
    config({ATTACKER: []})

    info = asyncio.run(
        runtime.create_session(
            SessionSpec(
                key=str(SessionKey.web(ATTACKER, "s1")),
                agent_key="claude-code",
                user_id=ATTACKER,
                chat_id=VICTIM_CHAT,
                platform="web",
                lazy_context=True,
            )
        )
    )

    assert info.server_name != VICTIM_SERVER
    assert not info.server_name


def test_session_keeps_a_reachable_chat_default(config, registry):
    """Same call, with the grant in place, still resolves the chat's server."""
    config({ATTACKER: [OWN_SERVER]})

    info = asyncio.run(
        runtime.create_session(
            SessionSpec(
                key=str(SessionKey.telegram(GROUP_CHAT)),
                agent_key="claude-code",
                user_id=ATTACKER,
                chat_id=GROUP_CHAT,
                lazy_context=True,
            )
        )
    )

    assert info.server_name == OWN_SERVER


# ── The route ──


def test_route_refuses_a_pinned_server_the_caller_cannot_reach(config):
    """``create_session`` gates the body's server name like ``_respawn`` does."""
    from fastapi import HTTPException

    from condor.web.models import WebUser
    from condor.web.routes import sessions as routes

    config({ATTACKER: []})

    spec = SessionSpec(
        key=str(SessionKey.web(ATTACKER, "s1")),
        agent_key="claude-code",
        user_id=ATTACKER,
        server_name=VICTIM_SERVER,
        platform="web",
    )
    user = WebUser(id=ATTACKER, username="a", first_name="A", role="user")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.create_session(spec, user=user))

    assert exc.value.status_code == 403
