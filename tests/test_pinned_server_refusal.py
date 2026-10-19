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
from condor.agents.engine import TickEngine, build_gated_client
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
    """``_resolve_server`` and ``_get_client``, without ``__post_init__``.

    Also carries what ``build_gated_client`` reads off a live engine, so the same
    stub can be driven through the tick seat itself (CORR-737) rather than only
    through ``build_mcp_servers_for_session``.
    """

    _resolve_server = TickEngine._resolve_server
    _get_client = TickEngine._get_client

    def __init__(self, server_name, *, pinned, user_id=OWNER, chat_id=0):
        self.config = {"server_name": server_name}
        self.chat_id = chat_id
        self.user_id = user_id
        self.agent_id = "brigado.scalp_1"
        self.server_pinned = pinned
        # The gate's inputs (SEC-631). Lazy to a callback, so stubs are enough.
        self.agent = SimpleNamespace(slug="brigado", tools=[])
        self.risk = SimpleNamespace()
        self.ledger = None
        self._refusals = None
        self._executor_owners = lambda: {}
        self._agent_key = lambda: "claude-code"


def _tick_seat_servers(monkeypatch, engine, execution_mode="loop"):
    """The mcp servers ``build_gated_client`` mounts for ``engine``.

    Drives the real seat — the thing production calls — and intercepts only the
    last step, the LLM client factory, which is where the built list lands.
    """
    from condor.runtime import llm_client

    captured = {}

    def fake_build_llm_client(agent_key, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(agent_key=agent_key)

    monkeypatch.setattr(llm_client, "build_llm_client", fake_build_llm_client)
    build_gated_client(engine, SimpleNamespace(), None, execution_mode)
    return captured["mcp_servers"]


def _seat_server_name(servers):
    """The ``--server-name`` the mcp-hummingbot subprocess would be spawned on."""
    args = _hummingbot(servers)["args"]
    return args[args.index("--server-name") + 1]


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


# ── CORR-737: one tick, one server — the data leg and the tools leg agree ──
#
# CORR-729 unified the *rule*; each call site kept its own *inputs*.
# ``_resolve_server`` substitutes ``chat_id=self.user_id`` (SEC-164 removed the
# request-body chat from the data leg), while ``build_gated_client`` used to hand
# the toolset builder the real ``engine.chat_id`` (SEC-178 deliberately kept the
# chat default for a chat *session*). Same rule, two subjects: for any loop that
# configures no server — which, after CORR-729 moved ``AgentConfig.server_name``
# to ``""``, is every shipped one — the prompt's balances, risk state and PnL came
# from fleet A while ``create_executor`` landed on fleet B.
#
# The tests above could not see it: they call ``build_mcp_servers_for_session``
# with ``chat_id == user_id`` by hand, against a fixture whose ``chat_defaults``
# map is empty. Those are exactly the two inputs production does not supply.

GROUP = -1003055388363
OWNERS_OWN = "brigado-2"
THE_GROUPS = "the-groups-box"

TWO_FLEETS = {
    OWNERS_OWN: {
        "host": "10.0.0.2",
        "port": 8000,
        "username": "brigado-2-user",
        "password": "brigado-2-p4ssword",
    },
    THE_GROUPS: {
        "host": "127.0.0.1",
        "port": 8000,
        "username": "the-groups-user",
        "password": "the-groups-p4ssword",
    },
}


@pytest.fixture
def two_fleets(cm):
    """Both servers reachable by the owner, and ``accessible[0]`` is the group's.

    So each of the three resolution candidates names a *different* fleet: the
    configured name (absent), the subject's own default (``OWNERS_OWN``) and the
    first accessible server (``THE_GROUPS``). A leg that reads the wrong subject
    cannot land on the right answer by luck.
    """
    return cm(
        {OWNER: [THE_GROUPS, OWNERS_OWN]},
        servers=TWO_FLEETS,
        chat_defaults={OWNER: OWNERS_OWN, GROUP: THE_GROUPS},
    )


@pytest.mark.parametrize(
    "chat_id",
    [
        pytest.param(GROUP, id="group-chat"),
        # The body the dashboard actually sends: ``startStrategy`` posts only
        # ``{config, trading_context}``, so ``StartStrategyRequest.chat_id`` keeps
        # its default ``0`` and reaches the engine as ``chat_id=0``. The tools leg
        # then found nothing under ``0`` and fell to ``accessible[0]``, while the
        # data leg never saw ``0`` at all.
        pytest.param(0, id="dashboard-launch"),
    ],
)
def test_the_tick_seat_trades_on_the_server_its_data_comes_from(
    two_fleets, monkeypatch, chat_id
):
    """The item itself: one run, one server, whatever chat it was started from.

    Asserted on both halves of what the name decides — the ``--server-name``
    argv that tells the subprocess which Condor server to act on, and the
    credentials injected beside it, which decide whose accounts it acts with.
    """
    engine = _Engine("", pinned=False, chat_id=chat_id)
    resolved = engine._resolve_server()[0]
    assert resolved == OWNERS_OWN, "the data leg reads the owner's own default"

    servers = _tick_seat_servers(monkeypatch, engine)

    assert _seat_server_name(servers) == resolved
    env = {e["name"]: e["value"] for e in _hummingbot(servers)["env"]}
    assert env["HUMMINGBOT_API_USERNAME"] == TWO_FLEETS[resolved]["username"]
    assert env["HUMMINGBOT_API_PASSWORD"] == TWO_FLEETS[resolved]["password"]


def test_the_chat_still_reaches_the_condor_subprocess(two_fleets, monkeypatch):
    """``chat_id`` is still passed down — it just no longer picks the venue.

    The fix is one argument wide on purpose: ``_condor_mcp_args`` consumes the
    same ``chat_id`` as the chat the condor MCP posts into and resolves its
    session from, so overriding it (the rejected alternative) would mis-target
    notifications to buy one resolution.
    """
    engine = _Engine("", pinned=False, chat_id=GROUP)

    servers = _tick_seat_servers(monkeypatch, engine)

    condor = next(s for s in servers if s["name"] == "condor")
    assert str(GROUP) in condor["args"]


def test_the_winddown_seat_acts_on_the_server_the_tick_read(two_fleets, monkeypatch):
    """``shutdown.py`` builds its LLM cleanup client through this same seat.

    A graceful stop cancels resting orders through ``_get_client()`` while the
    emergency winddown's own LLM pass goes through ``build_gated_client``
    (``condor/agents/shutdown.py:608``, ``execution_mode="shutdown"``). Two
    resolutions meant the floor and the nuance pass could act on two fleets.
    """
    engine = _Engine("", pinned=False, chat_id=GROUP)
    data_client = asyncio.run(engine._get_client())

    servers = _tick_seat_servers(monkeypatch, engine, execution_mode="shutdown")

    assert _seat_server_name(servers) == data_client.server


def test_a_handed_down_name_that_went_unusable_refuses_by_name(two_fleets, monkeypatch):
    """``server_pinned=bool(resolved)``: a name chosen for THIS run must stick.

    The loop is long-running and ``_resolve_server`` re-checks every tick, so the
    window is real — a share withdrawn or a server renamed between the two reads.
    Resolving to the *other* reachable fleet instead is the whole defect class
    CORR-729 closed for a configured pin; a handed-down name now gets the same
    bar.
    """
    engine = _Engine("", pinned=False, chat_id=GROUP)
    engine._resolve_server = lambda: ("renamed-out-from-under-us", {})

    with pytest.raises(config_manager.ServerUnavailable) as exc:
        _tick_seat_servers(monkeypatch, engine)

    assert "renamed-out-from-under-us" in str(exc.value)
    assert exc.value.server_name == "renamed-out-from-under-us"


def test_the_tick_seat_still_refuses_when_nothing_resolves_for_the_owner(
    cm, monkeypatch
):
    """CORR-729's ``server_required`` property, unchanged by handing the name down.

    ``resolved`` is ``None``, so nothing is pinned — and the chat default must not
    rescue the seat either: it is held to the owner's reach like every other
    candidate. A trading agent with no trading tools used to start on a log line.
    """
    cm({STRANGER: []}, servers=TWO_FLEETS, chat_defaults={GROUP: THE_GROUPS})
    engine = _Engine("", pinned=False, user_id=STRANGER, chat_id=GROUP)
    assert engine._resolve_server() == (None, None)

    with pytest.raises(config_manager.ServerUnavailable) as exc:
        _tick_seat_servers(monkeypatch, engine)

    assert "no accessible Hummingbot server" in str(exc.value)


def test_both_legs_agree_when_the_chat_is_not_the_subject(two_fleets, monkeypatch):
    """The sibling of ``test_both_call_sites_answer_the_same_way_on_the_same_inputs``.

    That test supplies ``chat_id == user_id`` and an empty ``chat_defaults`` map,
    which is where the two legs agree anyway. This one supplies the two inputs
    production does — a chat that is not the subject, and a default map where the
    two keys name different fleets — and compares the outcomes, not the rule.
    """
    engine = _Engine("", pinned=False, chat_id=GROUP)

    via_engine = engine._resolve_server()[0]
    via_tick_seat = _seat_server_name(_tick_seat_servers(monkeypatch, engine))
    via_chat_session = toolsets.build_mcp_servers_for_session(OWNER, GROUP)

    assert via_engine == via_tick_seat == OWNERS_OWN
    # And SEC-178 still holds where it was decided: an attended chat session in
    # that group legitimately follows the group's own default. What changed is
    # that an unattended loop is not a chat session.
    assert _seat_server_name(via_chat_session) == THE_GROUPS
