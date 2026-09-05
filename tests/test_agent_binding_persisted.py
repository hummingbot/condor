"""A chat stays bound to its specialist across respawns (CORR-090).

Binding a Telegram chat to a domain Agent used to live only on the in-memory
session object. The moment that subprocess died -- bot restart, health-monitor
reap, LRU detach -- the implicit auto-create respawned with no slug at all, so
the chat silently went back to the coordinator: other identity, other tools,
other pinned server, other memory scope, and nothing on screen to say so.

These tests treat the binding the way the bug did: they bind, throw the live
session away, keep only what a pickle reload would have kept (``user_data``),
and assert on the ``agent_slug`` the respawn is created with.
"""

import asyncio
from copy import deepcopy

import pytest

import condor.agents.agent as agent_mod
import config_manager as cm_mod
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
    """Stands in for the on-disk agents/ directory."""

    agents: list = []

    def get(self, slug):
        return next((a for a in self.agents if a.slug == slug), None)

    def list_specialists(self):
        return list(self.agents)


class _FakeSession:
    def __init__(self, alive=True, is_busy=False, agent_key="claude-code", slug=""):
        self.alive = alive
        self.is_busy = is_busy
        self.agent_key = agent_key
        self.agent_slug = slug
        self.label = slug or "Condor"


class _Message:
    def __init__(self, rendered, text="hello"):
        self._rendered = rendered
        self.text = text
        self.message_id = 7

    async def edit_text(self, text, **kw):
        self._rendered.append(text)
        return self

    async def reply_text(self, text, **kw):
        self._rendered.append(text)
        return self


class _Chat:
    id = 4242
    type = "private"


class _User:
    id = 99


class _Update:
    def __init__(self, rendered, *, callback=False, text="hello"):
        message = _Message(rendered, text=text)
        self.callback_query = (
            type("_Q", (), {"message": message})() if callback else None
        )
        self.message = None if callback else message
        self.effective_chat = _Chat()
        self.effective_user = _User()


class _Context:
    def __init__(self, user_data=None):
        self.bot = object()
        self.user_data = {} if user_data is None else user_data
        self.chat_data: dict = {}


class _Streamer:
    def __init__(self, **kw):
        pass

    def start_edit_loop(self):
        return None

    async def process_event(self, event):
        pass

    async def finalize(self):
        pass


def _install(monkeypatch, agents, *, session=None):
    """Wire the doubles; return the list of ``_create_tg_session`` kwargs."""
    _FakeStore.agents = agents
    monkeypatch.setattr(agent_mod, "AgentStore", _FakeStore)

    creates: list[dict] = []

    async def fake_create(**kw):
        creates.append(kw)
        return _FakeSession(agent_key=kw["agent_key"], slug=kw.get("agent_slug", ""))

    async def fake_get_session(chat_id):
        return session

    async def fake_destroy(chat_id):
        return True

    async def fake_get_info(key):
        return session

    async def fake_prompt(key, request, **kw):
        # An answer with no events is enough: the assertions are on the spawn.
        return
        yield  # pragma: no cover

    monkeypatch.setattr(agents_mod, "_create_tg_session", fake_create)
    monkeypatch.setattr(agents_mod, "get_session", fake_get_session)
    monkeypatch.setattr(agents_mod, "destroy_session", fake_destroy)
    monkeypatch.setattr(agents_mod, "_is_agent_available", lambda key: True)
    monkeypatch.setattr(
        agents_mod, "_tg_permission_callback", lambda bot, c, u: object()
    )
    monkeypatch.setattr(agents_mod, "TelegramStreamer", _Streamer)
    monkeypatch.setattr(agents_mod.runtime, "prompt", fake_prompt)
    monkeypatch.setattr(agents_mod.runtime, "get_info", fake_get_info)
    monkeypatch.setattr(menu_mod.runtime, "get_info", fake_get_info)

    # The message handler gates on the caller's role before anything else.
    monkeypatch.setattr(
        cm_mod,
        "get_config_manager",
        lambda: type(
            "_CM", (), {"get_user_role": lambda self, uid: cm_mod.UserRole.ADMIN}
        )(),
    )
    return creates


def _pick(ctx, index, monkeypatch, agents):
    """Drive /agent -> Talk to -> <index>."""
    rendered: list[str] = []
    creates = _install(monkeypatch, agents)
    asyncio.run(
        agents_mod._handle_talk_pick(_Update(rendered, callback=True), ctx, index)
    )
    return rendered, creates


def _respawn(ctx, monkeypatch, agents):
    """Send a plain message into a chat whose session is gone."""
    rendered: list[str] = []
    creates = _install(monkeypatch, agents)
    asyncio.run(agents_mod.agent_message_handler(_Update(rendered), ctx))
    return rendered, creates


# --- tests -------------------------------------------------------------------


def test_the_binding_outlives_the_session_that_made_it(monkeypatch):
    """Bind, lose the subprocess, keep only user_data: still the specialist."""
    agents = [_FakeAgent("funding_desk", "Funding Desk")]

    ctx = _Context()
    _, creates = _pick(ctx, 0, monkeypatch, agents)
    assert creates[-1]["agent_slug"] == "funding_desk"

    # What a bot restart leaves behind is the pickled user_data, nothing else.
    restarted = _Context(user_data=deepcopy(ctx.user_data))
    rendered, creates = _respawn(restarted, monkeypatch, agents)

    assert creates[-1]["agent_slug"] == "funding_desk"
    # Empty agent_key = the Agent's own configured model wins, exactly as it
    # did when the binding was made. A pinned server rides on the same slug.
    assert creates[-1]["agent_key"] == ""
    assert not any("no longer exists" in r for r in rendered)


def test_picking_the_coordinator_clears_the_binding(monkeypatch):
    """Talk to -> Condor is how you unbind; later respawns must stay unbound."""
    agents = [_FakeAgent("funding_desk", "Funding Desk")]

    ctx = _Context()
    _pick(ctx, 0, monkeypatch, agents)
    assert get_chat_binding(ctx.user_data).get("agent_slug") == "funding_desk"

    # Only the slug is cleared, not the record: the conversation stored beside
    # it survives the change of interlocutor (ARCH-101).
    _pick(ctx, -1, monkeypatch, agents)
    assert not get_chat_binding(ctx.user_data).get("agent_slug")

    restarted = _Context(user_data=deepcopy(ctx.user_data))
    _, creates = _respawn(restarted, monkeypatch, agents)
    assert creates[-1]["agent_slug"] == ""


def test_a_deleted_agent_is_announced_once_then_forgotten(monkeypatch):
    """A binding we cannot honour degrades explicitly, not silently."""
    agents = [_FakeAgent("funding_desk", "Funding Desk")]

    ctx = _Context()
    _pick(ctx, 0, monkeypatch, agents)

    # The agent directory is deleted while the chat is bound to it.
    restarted = _Context(user_data=deepcopy(ctx.user_data))
    rendered, creates = _respawn(restarted, monkeypatch, [])

    assert any("funding_desk" in r and "no longer exists" in r for r in rendered)
    assert creates[-1]["agent_slug"] == ""

    # Said once: the binding is gone, so the next message is plain Condor.
    rendered, creates = _respawn(restarted, monkeypatch, [])
    assert not any("no longer exists" in r for r in rendered)
    assert creates[-1]["agent_slug"] == ""


def test_a_failed_switch_does_not_bind_the_chat(monkeypatch):
    """A chat must never be left pointing at an agent that would not start."""
    agents = [_FakeAgent("funding_desk", "Funding Desk")]
    ctx = _Context()
    rendered: list[str] = []
    _install(monkeypatch, agents)

    async def boom(**kw):
        raise RuntimeError("no CLI")

    monkeypatch.setattr(agents_mod, "_create_tg_session", boom)
    asyncio.run(agents_mod._handle_talk_pick(_Update(rendered, callback=True), ctx, 0))

    # The verb follows what was actually attempted -- these doubles have no live
    # session, so the pick raised the agent rather than switching off another.
    assert any("Could not start Funding Desk" in r for r in rendered)
    assert get_chat_binding(ctx.user_data) == {}


def test_new_session_keeps_the_interlocutor(monkeypatch):
    """ "New session" means a new conversation, not a demotion to Condor."""
    agents = [_FakeAgent("funding_desk", "Funding Desk")]

    ctx = _Context()
    _pick(ctx, 0, monkeypatch, agents)

    rendered: list[str] = []
    creates = _install(monkeypatch, agents)
    asyncio.run(agents_mod._handle_start(_Update(rendered, callback=True), ctx))

    assert creates[-1]["agent_slug"] == "funding_desk"
    assert any("Funding Desk is ready" in r for r in rendered)


def test_compact_carries_the_identity_over(monkeypatch):
    """Compact respawns too: summarizing as the specialist and resuming as the
    coordinator would hand one agent's context to another."""
    session = _FakeSession(slug="funding_desk")
    creates = _install(monkeypatch, [_FakeAgent("funding_desk")], session=session)

    async def fake_prompt_once(key, prompt):
        return "one two three"

    monkeypatch.setattr(agents_mod.runtime, "prompt_once", fake_prompt_once)

    rendered: list[str] = []
    asyncio.run(
        agents_mod._handle_compact(_Update(rendered, callback=True), _Context())
    )

    assert creates[-1]["agent_slug"] == "funding_desk"


def test_the_menu_reports_the_binding_with_no_live_session(monkeypatch):
    """Between respawns the menu is the only place the real answerer shows."""
    agents = [_FakeAgent("funding_desk", "Funding Desk")]

    ctx = _Context()
    _pick(ctx, 0, monkeypatch, agents)

    rendered: list[str] = []
    _install(monkeypatch, agents)  # session=None
    asyncio.run(menu_mod.show_agent_menu(_Update(rendered), ctx))

    assert any("Talking to: Funding Desk" in r for r in rendered)


@pytest.mark.parametrize("field", ["conversation_id", "agent_slug"])
def test_the_binding_is_a_record_other_fields_can_join(field):
    """ARCH-101 adds the conversation id beside the slug; merging must not
    clobber whatever the other writer put there."""
    from condor.preferences import set_chat_binding

    user_data: dict = {}
    set_chat_binding(user_data, {"agent_slug": "funding_desk"})
    set_chat_binding(user_data, {"conversation_id": "conv-1"})

    stored = get_chat_binding(user_data)
    assert stored["agent_slug"] == "funding_desk"
    assert stored[field]
