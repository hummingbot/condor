"""Telegram carries its conversation across a respawn (ARCH-101).

Telegram transcripts have always been recorded, but no Telegram call site ever
passed a ``conversation_id`` back in. Every respawn -- a bot restart, a
health-monitor reap, an LRU detach, a model change, a switch of interlocutor --
minted a brand-new conversation and handed the user an agent with no memory of
the chat they were in the middle of, while the real transcript stayed orphaned
on disk.

These tests drive the handlers with the runtime faked one level lower than
``tests/test_agent_binding_persisted.py`` does -- at ``runtime.create_session``
rather than at ``_create_tg_session`` -- so the recording of the conversation
onto the chat is exercised for real, and the assertions are on the
``SessionSpec`` each respawn is built from.
"""

import asyncio
from copy import deepcopy

import condor.agents.agent as agent_mod
import config_manager as cm_mod
from condor.runtime import SessionKey, SessionSpec, conversations
from condor.runtime import sessions as sessions_mod
from handlers import agents as agents_mod

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


class _FakeInfo:
    """What ``runtime.create_session`` hands back, reduced to what is read."""

    def __init__(self, spec, conversation_id):
        self.alive = True
        self.is_busy = False
        self.agent_key = spec.agent_key
        self.agent_slug = spec.agent_slug
        self.conversation_id = conversation_id
        self.label = spec.agent_slug or "Condor"


class _LiveSession:
    """A session already running for the chat, as ``get_info`` reports it."""

    def __init__(self, conversation_id="", slug="", alive=True, is_busy=False):
        self.alive = alive
        self.is_busy = is_busy
        self.agent_key = "claude-code"
        self.agent_slug = slug
        self.conversation_id = conversation_id
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


class _World:
    """The faked runtime: what was spawned, and what was written down."""

    def __init__(self):
        self.specs: list[SessionSpec] = []
        self.systems: list[tuple] = []
        self.minted = 0

    @property
    def last(self) -> SessionSpec:
        return self.specs[-1]

    def new_id(self) -> str:
        self.minted += 1
        return f"conv-new-{self.minted}"


def _install(monkeypatch, agents=(), *, live=None):
    """Wire the doubles; return the ``_World`` the handlers act on."""
    world = _World()
    _FakeStore.agents = list(agents)
    monkeypatch.setattr(agent_mod, "AgentStore", _FakeStore)

    async def fake_create_session(spec, *, permission_callback=None, user_data=None):
        world.specs.append(spec)
        # An empty id is what makes the runtime mint a fresh conversation; a
        # supplied one is resumed and handed straight back.
        return _FakeInfo(spec, spec.conversation_id or world.new_id())

    async def fake_get_info(key):
        return live

    async def fake_destroy(key):
        return True

    async def fake_prompt(key, request, **kw):
        return
        yield  # pragma: no cover

    async def fake_prompt_once(key, prompt):
        return "a summary of the chat"

    monkeypatch.setattr(agents_mod.runtime, "create_session", fake_create_session)
    monkeypatch.setattr(agents_mod.runtime, "get_info", fake_get_info)
    monkeypatch.setattr(agents_mod.runtime, "destroy", fake_destroy)
    monkeypatch.setattr(agents_mod.runtime, "prompt", fake_prompt)
    monkeypatch.setattr(agents_mod.runtime, "prompt_once", fake_prompt_once)
    monkeypatch.setattr(agents_mod, "_is_agent_available", lambda key: True)
    monkeypatch.setattr(
        agents_mod, "_tg_permission_callback", lambda bot, c, u: object()
    )
    monkeypatch.setattr(agents_mod, "TelegramStreamer", _Streamer)

    monkeypatch.setattr(
        conversations,
        "record_system",
        lambda uid, cid, text, kind="": world.systems.append((uid, cid, text, kind)),
    )

    monkeypatch.setattr(
        cm_mod,
        "get_config_manager",
        lambda: type(
            "_CM", (), {"get_user_role": lambda self, uid: cm_mod.UserRole.ADMIN}
        )(),
    )
    return world


def _send(ctx, monkeypatch, agents=(), *, live=None, text="hello"):
    """A plain message into the chat -- the auto-create hot path."""
    rendered: list[str] = []
    world = _install(monkeypatch, agents, live=live)
    asyncio.run(agents_mod.agent_message_handler(_Update(rendered, text=text), ctx))
    return rendered, world


# --- the respawn carries the conversation ------------------------------------


def test_a_restart_resumes_the_conversation_it_left(monkeypatch):
    """The subprocess dies, the chat does not: the next message resumes it."""
    ctx = _Context()
    _, world = _send(ctx, monkeypatch)
    first = world.last.conversation_id
    assert first == ""  # nothing to resume yet
    born = "conv-new-1"

    # A bot restart leaves the pickled user_data and nothing else.
    restarted = _Context(user_data=deepcopy(ctx.user_data))
    _, world = _send(restarted, monkeypatch)

    assert world.last.conversation_id == born


def test_changing_the_llm_keeps_the_conversation(monkeypatch):
    """Change LLM only destroys; the auto-create behind it must not orphan."""
    ctx = _Context()
    _send(ctx, monkeypatch)

    # _handle_set_llm and friends destroy without recreating -- the next
    # message is the respawn, and it is the one that has to carry the id.
    _, world = _send(ctx, monkeypatch)
    assert world.last.conversation_id == "conv-new-1"


def test_talk_to_switches_inside_one_conversation(monkeypatch):
    """The specialist joins the chat rather than starting a new one."""
    agents = [_FakeAgent("funding_desk", "Funding Desk")]
    ctx = _Context()
    live = _LiveSession(conversation_id="conv-live")
    world = _install(monkeypatch, agents, live=live)

    rendered: list[str] = []
    asyncio.run(agents_mod._handle_talk_pick(_Update(rendered, callback=True), ctx, 0))

    assert world.last.conversation_id == "conv-live"
    assert world.last.agent_slug == "funding_desk"
    # The handover is a divider in the transcript, exactly as the dashboard's
    # switch action records it.
    assert world.systems == [(99, "conv-live", "Switched to Funding Desk", "switch")]
    # And the copy no longer claims a loss that does not happen.
    assert not any("not carried over" in r for r in rendered)
    assert any("carried over" in r for r in rendered)


def test_switching_back_to_condor_keeps_the_conversation(monkeypatch):
    """Unbinding changes the interlocutor, not the chat."""
    agents = [_FakeAgent("funding_desk", "Funding Desk")]
    ctx = _Context()
    _install(monkeypatch, agents, live=_LiveSession(conversation_id="conv-live"))
    asyncio.run(agents_mod._handle_talk_pick(_Update([], callback=True), ctx, 0))

    world = _install(
        monkeypatch, agents, live=_LiveSession(conversation_id="conv-live")
    )
    asyncio.run(agents_mod._handle_talk_pick(_Update([], callback=True), ctx, -1))

    assert world.last.agent_slug == ""
    assert world.last.conversation_id == "conv-live"

    # And a respawn after that still comes back to it.
    _, world = _send(_Context(user_data=deepcopy(ctx.user_data)), monkeypatch, agents)
    assert world.last.conversation_id == "conv-live"


def test_a_failed_switch_leaves_the_conversation_alone(monkeypatch):
    """No divider is written for a handover that never happened."""
    agents = [_FakeAgent("funding_desk", "Funding Desk")]
    ctx = _Context()
    world = _install(
        monkeypatch, agents, live=_LiveSession(conversation_id="conv-live")
    )

    async def boom(spec, **kw):
        raise RuntimeError("no CLI")

    monkeypatch.setattr(agents_mod.runtime, "create_session", boom)
    rendered: list[str] = []
    asyncio.run(agents_mod._handle_talk_pick(_Update(rendered, callback=True), ctx, 0))

    assert any("Could not switch" in r for r in rendered)
    assert world.systems == []


# --- the deliberate new-chat verbs still start clean --------------------------


def test_new_session_mints_a_fresh_conversation(monkeypatch):
    """ "New session" means a new transcript, not a new interlocutor."""
    ctx = _Context()
    _send(ctx, monkeypatch)

    world = _install(monkeypatch)
    asyncio.run(agents_mod._handle_start(_Update([], callback=True), ctx))
    assert world.last.conversation_id == ""

    # ...and the chat now belongs to that new one, not the abandoned one.
    _, world = _send(_Context(user_data=deepcopy(ctx.user_data)), monkeypatch)
    assert world.last.conversation_id == "conv-new-1"


def test_the_dash_reset_mints_a_fresh_conversation(monkeypatch):
    """ "-" is the typed form of the same verb."""
    ctx = _Context()
    _send(ctx, monkeypatch)

    _install(monkeypatch, live=_LiveSession(conversation_id="conv-new-1"))
    asyncio.run(agents_mod.agent_message_handler(_Update([], text="-"), ctx))

    _, world = _send(ctx, monkeypatch)
    assert world.last.conversation_id == ""


def test_compact_does_not_replay_what_it_just_summarized(monkeypatch):
    """Resuming here would re-inject the transcript the summary replaced."""
    ctx = _Context()
    _send(ctx, monkeypatch)

    world = _install(monkeypatch, live=_LiveSession(conversation_id="conv-new-1"))
    asyncio.run(agents_mod._handle_compact(_Update([], callback=True), ctx))

    assert world.last.conversation_id == ""

    # The compacted conversation is the one the chat carries from now on.
    _, world = _send(_Context(user_data=deepcopy(ctx.user_data)), monkeypatch)
    assert world.last.conversation_id == "conv-new-1"


# --- the runtime end of the contract ------------------------------------------


def test_a_conversation_deleted_since_does_not_strand_the_chat(monkeypatch):
    """A stored id can outlive its transcript; that is a new chat, not a crash."""
    minted = []

    monkeypatch.setattr(conversations, "update_meta", lambda *a, **kw: False)
    monkeypatch.setattr(
        conversations,
        "new_conversation",
        lambda uid, surface, **kw: minted.append(surface)
        or type("_M", (), {"id": "conv-fresh"})(),
    )
    monkeypatch.setattr(
        conversations, "replay_context", lambda *a, **kw: "should not be read"
    )

    conv_id, replay = sessions_mod._resolve_conversation(
        SessionSpec(
            key="tg:4242",
            agent_key="claude-code",
            user_id=99,
            conversation_id="conv-deleted",
        ),
        SessionKey.parse("tg:4242"),
        agent_key="claude-code",
        agent_slug="",
        server_name=None,
    )

    assert conv_id == "conv-fresh"
    assert replay == ""
    assert minted == ["tg"]
