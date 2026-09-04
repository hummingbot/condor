"""A chat can be *raised* as a specialist, not only switched onto one.

The "Talk to" picker only hung off ``_active_session_keyboard``, so the one
thing a chat could not start as was a specialist: you booted the coordinator and
immediately switched away from it, paying for a subprocess nobody wanted. These
tests drive the two doors that now reach ``_raise_agent`` with no session
standing -- the button on the no-session menu, and ``/agent <name>`` -- and
assert on the ``agent_slug`` the spawn is created with.
"""

import asyncio

import pytest

import condor.agents.agent as agent_mod
from condor.preferences import get_chat_binding
from handlers import agents as agents_mod
from handlers.agents import menu as menu_mod

# --- doubles -----------------------------------------------------------------


class _FakeAgent:
    def __init__(self, slug, name="", agent_key="claude-code"):
        self.slug = slug
        self.name = name or slug
        self.agent_key = agent_key


class _FakeStore:
    agents: list = []

    def get(self, slug):
        return next((a for a in self.agents if a.slug == slug), None)

    def list_specialists(self):
        return list(self.agents)


class _FakeSession:
    def __init__(self, alive=True, agent_key="claude-code", slug="", conv="c1"):
        self.alive = alive
        self.is_busy = False
        self.agent_key = agent_key
        self.agent_slug = slug
        self.label = slug or "Condor"
        self.conversation_id = conv


class _Message:
    """Records everything rendered into it, edits included."""

    def __init__(self, rendered):
        self._rendered = rendered
        self.text = "/agent"
        self.message_id = 7
        self.keyboards: list = []

    async def edit_text(self, text, **kw):
        self._rendered.append(text)
        self.keyboards.append(kw.get("reply_markup"))
        return self

    async def reply_text(self, text, **kw):
        self._rendered.append(text)
        self.keyboards.append(kw.get("reply_markup"))
        return self


class _Chat:
    id = 4242
    type = "private"


class _User:
    id = 99


class _Update:
    def __init__(self, rendered, *, callback=False):
        self.message_obj = _Message(rendered)
        self.callback_query = (
            type("_Q", (), {"message": self.message_obj, "data": "agent:talk_to"})()
            if callback
            else None
        )
        self.message = None if callback else self.message_obj
        self.effective_chat = _Chat()
        self.effective_user = _User()


class _Context:
    def __init__(self, args=None, user_data=None):
        self.bot = object()
        self.args = args or []
        self.user_data = {} if user_data is None else user_data
        self.chat_data: dict = {}


def _install(monkeypatch, agents, *, session=None):
    """Wire the doubles; return the list of ``_create_tg_session`` kwargs."""
    _FakeStore.agents = agents
    monkeypatch.setattr(agent_mod, "AgentStore", _FakeStore)

    creates: list[dict] = []

    async def fake_create(**kw):
        creates.append(kw)
        return _FakeSession(
            agent_key=kw["agent_key"],
            slug=kw.get("agent_slug", ""),
            conv=kw.get("conversation_id") or "fresh",
        )

    async def fake_get_session(chat_id):
        return session

    async def fake_destroy(chat_id):
        return True

    async def fake_get_info(key):
        return session

    async def fake_conversation_for_session(key):
        return session.conversation_id if session else ""

    monkeypatch.setattr(agents_mod, "_create_tg_session", fake_create)
    monkeypatch.setattr(agents_mod, "get_session", fake_get_session)
    monkeypatch.setattr(agents_mod, "destroy_session", fake_destroy)
    monkeypatch.setattr(agents_mod, "_is_agent_available", lambda key: True)
    monkeypatch.setattr(
        agents_mod, "_tg_permission_callback", lambda bot, c, u: object()
    )
    monkeypatch.setattr(
        agents_mod.runtime, "conversation_for_session", fake_conversation_for_session
    )
    monkeypatch.setattr(agents_mod.runtime, "get_info", fake_get_info)
    monkeypatch.setattr(menu_mod.runtime, "get_info", fake_get_info)
    return creates


def _slash_agent(ctx, monkeypatch, agents, *, session=None):
    """Drive ``/agent <args>`` past the @restricted wrapper."""
    rendered: list[str] = []
    creates = _install(monkeypatch, agents, session=session)
    update = _Update(rendered)
    asyncio.run(agents_mod.agent_command.__wrapped__(update, ctx))
    return rendered, creates


# --- the argument resolver ---------------------------------------------------


@pytest.mark.parametrize(
    "typed",
    ["orca_lp_expert", "Orca_LP_Expert", "orca-lp-expert", "Orca LP Expert", "orca"],
)
def test_a_name_resolves_however_it_is_typed(monkeypatch, typed):
    """Slug, display name, dashes, spaces and a prefix all reach one agent."""
    _install(monkeypatch, [_FakeAgent("orca_lp_expert", "Orca LP Expert")])
    slug, label, error = agents_mod._resolve_agent_argument(typed)
    assert (slug, label, error) == ("orca_lp_expert", "Orca LP Expert", "")


def test_an_exact_name_beats_the_agent_that_merely_contains_it(monkeypatch):
    """ "orca" is a whole agent as well as a prefix of another -- it wins."""
    _install(
        monkeypatch,
        [_FakeAgent("orca", "Orca"), _FakeAgent("orca_lp_expert", "Orca LP Expert")],
    )
    assert agents_mod._resolve_agent_argument("orca")[0] == "orca"


def test_an_ambiguous_argument_is_refused_not_guessed(monkeypatch):
    """Two candidates raise neither: the wrong desk is silent once spawned."""
    _install(
        monkeypatch,
        [_FakeAgent("orca_lp_expert"), _FakeAgent("orca_swap_expert")],
    )
    slug, _, error = agents_mod._resolve_agent_argument("orca")
    assert slug == ""
    assert "more than one" in error
    assert "orca_lp_expert" in error and "orca_swap_expert" in error


def test_an_unknown_argument_names_the_roster(monkeypatch):
    _install(monkeypatch, [_FakeAgent("orca_lp_expert", "Orca LP Expert")])
    slug, _, error = agents_mod._resolve_agent_argument("brigado")
    assert slug == ""
    assert "Orca LP Expert" in error and "orca_lp_expert" in error


@pytest.mark.parametrize("typed", ["condor", "Condor", "coordinator", "-"])
def test_the_coordinator_is_reachable_by_name(monkeypatch, typed):
    """``/agent condor`` is the way back, so unbinding needs no picker either."""
    _install(monkeypatch, [_FakeAgent("orca_lp_expert")])
    assert agents_mod._resolve_agent_argument(typed) == ("", "Condor", "")


# --- /agent <name> -----------------------------------------------------------


def test_slash_agent_with_a_name_raises_that_specialist(monkeypatch):
    """No session, no menu: one command and the chat is the specialist's."""
    agents = [_FakeAgent("orca_lp_expert", "Orca LP Expert")]
    ctx = _Context(args=["orca_lp_expert"])

    rendered, creates = _slash_agent(ctx, monkeypatch, agents)

    assert creates[-1]["agent_slug"] == "orca_lp_expert"
    # Empty agent_key = the Agent's own configured model wins, exactly as the
    # picker does it.
    assert creates[-1]["agent_key"] == ""
    assert get_chat_binding(ctx.user_data).get("agent_slug") == "orca_lp_expert"
    # With nothing running the verb is "Starting", not "Switching to".
    assert any(r.startswith("Starting Orca LP Expert") for r in rendered)
    assert any("Now talking to Orca LP Expert" in r for r in rendered)


def test_slash_agent_with_a_name_switches_a_live_session(monkeypatch):
    """The same command mid-chat is a switch, and says so."""
    agents = [_FakeAgent("orca_lp_expert", "Orca LP Expert")]
    ctx = _Context(args=["orca"])
    live = _FakeSession(alive=True, conv="c1")

    rendered, creates = _slash_agent(ctx, monkeypatch, agents, session=live)

    assert creates[-1]["agent_slug"] == "orca_lp_expert"
    # The conversation is carried across the reap, as through the picker.
    assert creates[-1]["conversation_id"] == "c1"
    assert any(r.startswith("Switching to Orca LP Expert") for r in rendered)


def test_a_bad_name_spawns_nothing(monkeypatch):
    """A refused argument must not leave the chat bound or a session standing."""
    ctx = _Context(args=["nope"])
    rendered, creates = _slash_agent(
        ctx, monkeypatch, [_FakeAgent("orca_lp_expert", "Orca LP Expert")]
    )

    assert creates == []
    assert not get_chat_binding(ctx.user_data).get("agent_slug")
    assert any("No agent matches" in r for r in rendered)


def test_slash_agent_without_arguments_still_opens_the_menu(monkeypatch):
    """The shortcut is additive: the bare command is untouched."""
    ctx = _Context()
    rendered, creates = _slash_agent(ctx, monkeypatch, [_FakeAgent("orca_lp_expert")])

    assert creates == []
    assert any("No active session" in r for r in rendered)


# --- the button on the no-session menu ---------------------------------------


def test_the_picker_is_reachable_with_no_session(monkeypatch):
    """The gap this closes: "Talk to" was only on the live-session keyboard."""
    from handlers.agents.menu import _no_session_keyboard

    rows = _no_session_keyboard().inline_keyboard
    assert any(
        b.callback_data == "agent:talk_to" for row in rows for b in row
    ), "no-session menu must offer the agent picker"


def test_picking_from_the_no_session_menu_raises_the_agent(monkeypatch):
    """Tapping a specialist with nothing running spawns it directly."""
    agents = [_FakeAgent("orca_lp_expert", "Orca LP Expert")]
    ctx = _Context()
    rendered: list[str] = []
    creates = _install(monkeypatch, agents, session=None)

    asyncio.run(agents_mod._handle_talk_pick(_Update(rendered, callback=True), ctx, 0))

    assert creates[-1]["agent_slug"] == "orca_lp_expert"
    # Nothing to carry: a raise from cold starts a fresh conversation.
    assert creates[-1]["conversation_id"] == ""
    assert any(r.startswith("Starting Orca LP Expert") for r in rendered)
    assert not any("carried over" in r for r in rendered)
