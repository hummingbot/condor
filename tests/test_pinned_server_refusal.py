"""CORR-729: a pinned server resolves to itself or to nothing — never to another.

GitHub issue #279: "in both, a missing or wrong setting kept working as
*something else* instead of failing". This is the server half.

``toolsets.build_mcp_servers_for_session`` and ``TickEngine._resolve_server``
each open-coded the same three-candidate chain — the setting, the chat default,
the first accessible server — and neither could tell *"nothing was chosen,
choose sensibly"* from *"this was chosen and it is not usable"*. So a session
pinned to a server that was down, renamed or revoked traded on a different
server's accounts behind one ``log.warning``.

The rule is one function now, ``config_manager.resolve_stored_server``, with the
distinction in its two flags rather than in each caller's reading of its own
setting:

* ``pinned`` — the name was chosen for this run. It resolves or raises.
* ``required`` — the seat cannot run without a server at all. An empty chain
  raises instead of handing back ``None`` for the caller to warn about.

Neither flag is the previous lenient chain, which is what keeps an *inherited*
name falling through and a serverless chat a valid outcome. That second property
is the one that makes this safe to ship: "fail hard on a pin" and "fail hard on
an absent pin" are different changes and only the first is wanted.
"""

import asyncio
from types import SimpleNamespace

import pytest

import config_manager
from condor.agents.engine import TickEngine
from condor.runtime import toolsets

OWNER = 7
STRANGER = 42
MINE = "mine"
REVOKED = "was-shared-with-me"

SERVERS = {
    MINE: {
        "host": "127.0.0.1",
        "port": 8000,
        "username": "my-user",
        "password": "my-p4ssword",
    },
    REVOKED: {
        "host": "10.0.0.9",
        "port": 8000,
        "username": "their-user",
        "password": "their-p4ssword",
    },
}


class _Cm:
    """``grants`` is who may reach what; ``servers`` is what exists at all."""

    def __init__(self, grants, servers=SERVERS):
        self.grants = grants
        self.servers = servers

    def get_server(self, name):
        return self.servers.get(name)

    def has_server_access(self, user_id, name, *a, **kw):
        return name in self.grants.get(user_id, [])

    def get_server_permission(self, user_id, name):
        from config_manager import ServerPermission

        return ServerPermission.OWNER if name in self.grants.get(user_id, []) else None

    def get_accessible_servers(self, user_id):
        return list(self.grants.get(user_id, []))

    def is_admin(self, user_id):
        return False

    async def get_client(self, server_name):
        return SimpleNamespace(server=server_name)


@pytest.fixture
def cm(monkeypatch):
    """Install a stub ConfigManager and a chat-default map that is never set."""

    def install(grants, servers=SERVERS, chat_defaults=None):
        stub = _Cm(grants, servers)
        monkeypatch.setattr(config_manager, "get_config_manager", lambda: stub)
        monkeypatch.setattr(
            config_manager,
            "get_effective_server",
            lambda chat_id, user_data=None: (chat_defaults or {}).get(chat_id),
        )
        return stub

    return install


def _hummingbot(servers):
    return next((s for s in servers if s["name"] == "mcp-hummingbot"), None)


class _Engine:
    """``_resolve_server`` and ``_get_client``, without ``__post_init__``."""

    _resolve_server = TickEngine._resolve_server
    _get_client = TickEngine._get_client

    def __init__(self, server_name, *, pinned, user_id=OWNER):
        self.config = {"server_name": server_name}
        self.chat_id = 0
        self.user_id = user_id
        self.agent_id = "brigado.scalp_1"
        self.server_pinned = pinned


# ── Criterion 1: a chosen server resolves to itself or refuses, by name ──


def test_a_pinned_server_that_is_not_usable_refuses_in_the_toolset(cm):
    """The toolset builder: the name decides whose credentials get spawned.

    ``REVOKED`` exists and ``MINE`` is reachable, so the old chain had somewhere
    to go: it dropped the pin and spawned mcp-hummingbot against ``MINE``.
    """
    cm({OWNER: [MINE]})

    with pytest.raises(config_manager.ServerUnavailable) as exc:
        toolsets.build_mcp_servers_for_session(
            OWNER, OWNER, server_name=REVOKED, server_pinned=True
        )

    assert REVOKED in str(exc.value), "the refusal must name the server"
    assert exc.value.server_name == REVOKED


def test_a_pinned_server_that_is_not_usable_refuses_in_the_engine(cm):
    """The same rule at the other call site, on the same inputs.

    ``_resolve_server`` is the one that keys the loop's API credentials, and it
    used to walk its own copy of the chain to ``MINE``.
    """
    cm({OWNER: [MINE]})

    with pytest.raises(config_manager.ServerUnavailable) as exc:
        _Engine(REVOKED, pinned=True)._resolve_server()

    assert REVOKED in str(exc.value)


def test_the_engine_does_not_swallow_the_refusal_into_no_client(cm):
    """``_get_client`` logs-and-returns-None for everything else, on purpose.

    A pin that cannot be used is the one failure here no retry fixes, and the
    whole defect was that it reached the operator as absence rather than as a
    reason.
    """
    cm({OWNER: [MINE]})

    with pytest.raises(config_manager.ServerUnavailable):
        asyncio.run(_Engine(REVOKED, pinned=True)._get_client())


def test_a_pinned_server_that_is_usable_is_the_one_that_resolves(cm):
    """The gate refuses; it does not reroute a pin that is fine."""
    cm({OWNER: [MINE, REVOKED]})

    servers = toolsets.build_mcp_servers_for_session(
        OWNER, OWNER, server_name=REVOKED, server_pinned=True
    )

    env = {e["name"]: e["value"] for e in _hummingbot(servers)["env"]}
    assert env["HUMMINGBOT_API_USERNAME"] == SERVERS[REVOKED]["username"]
    assert _Engine(REVOKED, pinned=True)._resolve_server()[0] == REVOKED


# ── Criterion 2: an unpinned run still resolves through the default chain ──


def test_an_unpinned_run_with_no_setting_still_resolves(cm):
    """The naive version of this fix refuses every agent that never had a pin.

    Most agents never had one, so this is the criterion that makes the change
    shippable rather than the one that proves it works.
    """
    cm({OWNER: [MINE]})

    servers = toolsets.build_mcp_servers_for_session(OWNER, OWNER)

    assert _hummingbot(servers) is not None
    assert _Engine("", pinned=False)._resolve_server()[0] == MINE


def test_an_inherited_name_that_is_not_usable_still_falls_through(cm):
    """A strategy default is not a pin, and ``_start`` deliberately allows it.

    Same unusable name as the refusal tests above — only the flag differs, which
    is the distinction the fix adds.
    """
    cm({OWNER: [MINE]})

    servers = toolsets.build_mcp_servers_for_session(OWNER, OWNER, server_name=REVOKED)

    assert _hummingbot(servers) is not None
    assert _Engine(REVOKED, pinned=False)._resolve_server()[0] == MINE


def test_a_serverless_chat_is_still_a_valid_outcome(cm):
    """No grants at all, nothing pinned, nothing required: condor alone.

    Refusing here would take the chat away from a user who has not been shared
    a server yet — and the chat is where they go to get one.
    """
    cm({STRANGER: []})

    servers = toolsets.build_mcp_servers_for_session(STRANGER, STRANGER)

    assert [s["name"] for s in servers] == ["condor"]


# ── Criterion 3: a seat that needs a server refuses rather than start mute ──


def test_a_seat_that_requires_a_server_refuses_when_nothing_resolves(cm):
    """A trading agent with no trading tools used to start on a log line."""
    cm({STRANGER: []})

    with pytest.raises(config_manager.ServerUnavailable) as exc:
        toolsets.build_mcp_servers_for_session(STRANGER, STRANGER, server_required=True)

    assert "no accessible Hummingbot server" in str(exc.value)
    assert str(STRANGER) in str(exc.value), "the refusal must name the subject"


def test_the_tick_seat_requires_one(cm):
    """``build_gated_client`` is the loop's seat, and it declares it."""
    cm({STRANGER: []})

    engine = SimpleNamespace(
        user_id=STRANGER,
        chat_id=STRANGER,
        agent=SimpleNamespace(slug="brigado", tools=[]),
        config={"server_name": ""},
        server_pinned=False,
    )

    with pytest.raises(config_manager.ServerUnavailable):
        toolsets.build_mcp_servers_for_session(
            engine.user_id,
            engine.chat_id,
            server_name=engine.config.get("server_name"),
            agent_slug=engine.agent.slug,
            tick=True,
            server_pinned=engine.server_pinned,
            server_required=True,
        )


def test_a_tick_with_no_api_client_is_a_failure_not_a_quiet_return(cm):
    """``_tick`` journalled "No API client available" and returned cleanly.

    ``wait_first_tick`` then reported the run as answerable, so the start route
    and ``reconcile_boot`` both said "started" for a loop that could do nothing.
    """
    cm({STRANGER: []})

    engine = SimpleNamespace(
        _last_tick_at=0.0,
        journal=None,
        _get_client=lambda: _none(),
    )

    with pytest.raises(RuntimeError, match="No API client available"):
        asyncio.run(TickEngine._tick(engine))


async def _none():
    return None


# ── Criterion 4: no name that means "unset" ──


def test_agent_config_no_longer_defaults_to_a_server_name():
    """``"local"`` was a concrete name that then fell through the chain.

    So "no server configured" and "configured for the server called local" were
    one state, and an install whose server is named anything else resolved
    elsewhere while the config read as deliberate.
    """
    from condor.agents.config import AgentConfig

    assert AgentConfig().server_name == ""


def test_a_config_that_really_names_local_is_still_honoured(cm):
    """The other half: an install whose server *is* called local keeps working."""
    servers = {"local": SERVERS[MINE]}
    cm({OWNER: ["local"]}, servers=servers)

    assert _Engine("local", pinned=True)._resolve_server()[0] == "local"
    assert (
        _hummingbot(
            toolsets.build_mcp_servers_for_session(
                OWNER, OWNER, server_name="local", server_pinned=True
            )
        )
        is not None
    )


# ── Criterion 5: one rule, both call sites ──


@pytest.mark.parametrize("pinned", [False, True])
def test_both_call_sites_answer_the_same_way_on_the_same_inputs(cm, pinned):
    """The point of the shared rule: two copies of a chain are two answers.

    Run through both doors with identical inputs and compare the *outcome* —
    a refusal naming the server, or the same resolved name.
    """
    cm({OWNER: [MINE]})

    def via_toolsets():
        servers = toolsets.build_mcp_servers_for_session(
            OWNER, OWNER, server_name=REVOKED, server_pinned=pinned
        )
        hummingbot = _hummingbot(servers)
        return hummingbot["args"][hummingbot["args"].index("--server-name") + 1]

    def via_engine():
        return _Engine(REVOKED, pinned=pinned)._resolve_server()[0]

    if pinned:
        for door in (via_toolsets, via_engine):
            with pytest.raises(config_manager.ServerUnavailable):
                door()
    else:
        assert via_toolsets() == via_engine() == MINE
