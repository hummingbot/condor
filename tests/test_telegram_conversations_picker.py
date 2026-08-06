"""Telegram can list and resume a durable conversation (ARCH-102).

``condor/runtime/conversations.py`` is surface-agnostic, but only the dashboard
ever consumed it: Telegram recorded transcripts it could never get back into.
A chat held yesterday, one started on the web, or one interrupted by a restart
was readable everywhere except from the phone that produced it.

These tests drive the picker's handlers against a **real** conversation store on
a throwaway root, with the runtime faked only at ``runtime.create_session`` --
and that fake calls the runtime's own ``_resolve_conversation``, so the resume,
the replay and the meta update are the production ones. The delete assertions go
through the REST router the dashboard uses, because "gone" has to mean gone on
both surfaces.
"""

import asyncio
from copy import deepcopy

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.agents.agent as agent_mod
import condor.web.routes.conversations as conv_routes
from condor.runtime import SessionKey, SessionSpec, conversations
from condor.runtime import sessions as sessions_mod
from condor.runtime.conversations import REPLAY_HEADER, TurnEntry
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from handlers import agents as agents_mod
from handlers.agents import menu as menu_mod

CHAT_ID = 4242
USER_ID = 99


# --- doubles -----------------------------------------------------------------


class _FakeAgent:
    def __init__(self, slug, name=""):
        self.slug = slug
        self.name = name or slug
        self.agent_key = "claude-code"


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
    def __init__(self, conversation_id="", slug="", alive=True):
        self.alive = alive
        self.is_busy = False
        self.agent_key = "claude-code"
        self.agent_slug = slug
        self.conversation_id = conversation_id
        self.label = slug or "Condor"


class _Message:
    def __init__(self, rendered):
        self._rendered = rendered
        self.message_id = 7

    async def edit_text(self, text, **kw):
        self._rendered.append((text, kw.get("reply_markup")))
        return self


class _Update:
    def __init__(self, rendered):
        message = _Message(rendered)
        self.callback_query = type("_Q", (), {"message": message})()
        self.message = None
        self.effective_chat = type("_C", (), {"id": CHAT_ID, "type": "private"})()
        self.effective_user = type("_U", (), {"id": USER_ID})()


class _Context:
    def __init__(self, user_data=None):
        self.bot = object()
        self.user_data = {} if user_data is None else user_data
        self.chat_data: dict = {}


class _World:
    """The faked runtime: what was spawned, and what it was handed to read."""

    def __init__(self):
        self.specs: list[SessionSpec] = []
        self.replays: list[str] = []
        self.destroyed = 0

    @property
    def last(self) -> SessionSpec:
        return self.specs[-1]


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A throwaway conversation root, shared by the handlers and the REST router."""
    monkeypatch.setattr(conversations, "_root", lambda: tmp_path / "conversations")
    return tmp_path


def _install(monkeypatch, agents=(), *, live=None):
    """Wire the doubles; return the ``_World`` the handlers act on."""
    world = _World()
    _FakeStore.agents = list(agents)
    monkeypatch.setattr(agent_mod, "AgentStore", _FakeStore)

    async def fake_create_session(spec, *, permission_callback=None, user_data=None):
        world.specs.append(spec)
        # The runtime's own resolver, against the real store: this is what makes
        # "resumed" mean the transcript was actually replayed, not just that an
        # id was passed along.
        conv_id, replay = sessions_mod._resolve_conversation(
            spec,
            SessionKey.parse(spec.key),
            agent_key=spec.agent_key,
            agent_slug=spec.agent_slug,
            server_name=None,
        )
        world.replays.append(replay)
        return _FakeInfo(spec, conv_id)

    async def fake_get_info(key):
        return live

    async def fake_destroy(key):
        world.destroyed += 1
        return True

    monkeypatch.setattr(agents_mod.runtime, "create_session", fake_create_session)
    monkeypatch.setattr(agents_mod.runtime, "get_info", fake_get_info)
    monkeypatch.setattr(agents_mod.runtime, "destroy", fake_destroy)
    monkeypatch.setattr(agents_mod, "_is_agent_available", lambda key: True)
    monkeypatch.setattr(
        agents_mod, "_tg_permission_callback", lambda bot, c, u: object()
    )
    return world


def _seed(surface="tg", *, agent_slug="", title="", said="hello"):
    """A conversation with real turns on disk, as either surface would leave it."""
    meta = conversations.new_conversation(
        USER_ID, surface, agent_key="claude-code", agent_slug=agent_slug
    )
    conversations.append_turn(
        USER_ID, meta.id, TurnEntry(role="user", text=title or said)
    )
    conversations.append_turn(
        USER_ID, meta.id, TurnEntry(role="assistant", text="sure, here you go")
    )
    return conversations.get_conversation(USER_ID, meta.id)


def _open_picker(ctx, monkeypatch, agents=(), *, live=None):
    rendered: list = []
    world = _install(monkeypatch, agents, live=live)
    asyncio.run(agents_mod._handle_conversations(_Update(rendered), ctx))
    return rendered, world


def _buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


def _token(meta):
    from handlers.agents._shared import conversation_token

    return conversation_token(meta.id)


# --- the list ----------------------------------------------------------------


def test_the_picker_lists_every_surfaces_conversations(store, monkeypatch):
    """One keyspace: a chat started on the dashboard is listed on the phone."""
    from_web = _seed("web", title="what is the SOL basis")
    from_tg = _seed("tg", title="check my portfolio")

    rendered, _ = _open_picker(_Context(), monkeypatch)
    text, markup = rendered[-1]
    labels = [b.text for b in _buttons(markup)]

    assert "dashboard" in text
    # Newest first, exactly as the REST listing orders them.
    assert any("check my portfolio" in label for label in labels)
    assert any("what is the SOL basis" in label for label in labels)
    picks = [
        b for b in _buttons(markup) if b.callback_data.startswith("agent:conv_pick:")
    ]
    assert [b.callback_data for b in picks] == [
        f"agent:conv_pick:{_token(from_tg)}",
        f"agent:conv_pick:{_token(from_web)}",
    ]


def test_each_row_says_which_agent_the_conversation_was_with(store, monkeypatch):
    """A flat log is only navigable if you can see who you were talking to."""
    _seed("tg", agent_slug="funding_desk", title="fund me")
    _seed("web", title="plain chat")

    rendered, _ = _open_picker(_Context(), monkeypatch)
    labels = [
        b.text
        for b in _buttons(rendered[-1][1])
        if b.callback_data.startswith("agent:conv_pick:")
    ]

    assert any("funding_desk" in label for label in labels)
    assert any("Condor" in label for label in labels)


def test_the_conversation_this_chat_is_in_is_marked(store, monkeypatch):
    """The one thing the shared list cannot tell you on its own."""
    _seed("tg", title="older one")
    here = _seed("tg", title="the live one")

    rendered, _ = _open_picker(
        _Context(), monkeypatch, live=_LiveSession(conversation_id=here.id)
    )
    marked = [
        b.text
        for b in _buttons(rendered[-1][1])
        if b.callback_data.startswith("agent:conv_pick:") and b.text.startswith("• ")
    ]

    assert len(marked) == 1
    assert "the live one" in marked[0]


def test_an_empty_store_offers_a_way_back(store, monkeypatch):
    rendered, _ = _open_picker(_Context(), monkeypatch)
    text, markup = rendered[-1]

    assert "No conversations yet" in text
    assert [b.callback_data for b in _buttons(markup)] == ["agent:menu"]


# --- resuming -----------------------------------------------------------------


def test_picking_a_conversation_replays_it_into_the_new_session(store, monkeypatch):
    """Criterion: the spawn carries the picked id and the agent reads the tail."""
    _seed("tg", title="the one I do not want")
    wanted = _seed("web", title="the one from the dashboard")

    ctx = _Context()
    world = _install(monkeypatch)
    asyncio.run(agents_mod._handle_conversation_pick(_Update([]), ctx, _token(wanted)))

    assert world.last.key == f"tg:{CHAT_ID}"
    assert world.last.conversation_id == wanted.id
    # Not just the id: the transcript really came back with it.
    assert REPLAY_HEADER in world.replays[-1]
    assert "the one from the dashboard" in world.replays[-1]


def test_resuming_comes_back_as_the_agent_it_was_held_with(store, monkeypatch):
    """Returning to a chat means returning to the interlocutor, not to Condor."""
    held_with = _seed("web", agent_slug="funding_desk", title="basis trade")

    ctx = _Context()
    world = _install(monkeypatch, [_FakeAgent("funding_desk", "Funding Desk")])
    asyncio.run(
        agents_mod._handle_conversation_pick(_Update([]), ctx, _token(held_with))
    )

    assert world.last.agent_slug == "funding_desk"
    # A bound Agent brings its own model.
    assert world.last.agent_key == ""
    # And the binding follows, or the next respawn silently swaps the identity.
    assert agents_mod.resolve_chat_binding(ctx.user_data)[0].slug == "funding_desk"


def test_resuming_a_conversation_whose_agent_is_gone_falls_back_to_condor(
    store, monkeypatch
):
    """The transcript outlived the agent; that is said out loud, not papered over."""
    orphan = _seed("tg", agent_slug="deleted_desk", title="old work")

    ctx = _Context()
    rendered: list = []
    world = _install(monkeypatch)  # no agents registered
    asyncio.run(
        agents_mod._handle_conversation_pick(_Update(rendered), ctx, _token(orphan))
    )

    assert world.last.agent_slug == ""
    assert world.last.conversation_id == orphan.id
    assert any("deleted_desk" in text for text, _ in rendered)


def test_the_resume_path_reattaches_after_a_restart(store, monkeypatch):
    """Criterion: create a conversation, restart the registry, resume the same id.

    The restart is the real one Telegram suffers: the session registry is empty
    and the only thing that survived is the pickled ``user_data``.
    """
    ctx = _Context()
    world = _install(monkeypatch, live=None)

    # A chat happens, on a conversation the runtime mints for it.
    asyncio.run(
        agents_mod._create_tg_session(
            chat_id=CHAT_ID,
            agent_key="claude-code",
            user_id=USER_ID,
            permission_callback=None,
            user_data=ctx.user_data,
        )
    )
    born = conversations.list_conversations(USER_ID)[0]
    assert world.last.conversation_id == ""  # nothing to resume yet
    conversations.append_turn(
        USER_ID, born.id, TurnEntry(role="user", text="remember the SOL basis")
    )

    # ...and the process dies. Nothing is live; only the pickle came back.
    restarted = _Context(user_data=deepcopy(ctx.user_data))
    world = _install(monkeypatch, live=None)
    rendered: list = []
    asyncio.run(agents_mod._handle_conversations(_Update(rendered), restarted))
    token = next(
        b.callback_data.split(":")[-1]
        for b in _buttons(rendered[-1][1])
        if b.callback_data.startswith("agent:conv_pick:")
    )
    asyncio.run(agents_mod._handle_conversation_pick(_Update([]), restarted, token))

    assert world.last.conversation_id == born.id
    assert "remember the SOL basis" in world.replays[-1]


def test_picking_the_conversation_already_open_does_not_respawn(store, monkeypatch):
    """Continuing the chat you are in is not a reason to reap its subprocess."""
    here = _seed("tg", title="right here")

    ctx = _Context()
    rendered: list = []
    world = _install(monkeypatch, live=_LiveSession(conversation_id=here.id))
    asyncio.run(
        agents_mod._handle_conversation_pick(_Update(rendered), ctx, _token(here))
    )

    assert world.specs == []
    assert world.destroyed == 0
    assert "already in that conversation" in rendered[-1][0]


def test_a_conversation_deleted_between_render_and_tap_is_not_an_error(
    store, monkeypatch
):
    """The list is shared with the dashboard, so a row can go stale mid-thought."""
    doomed = _seed("tg", title="about to go")
    token = _token(doomed)
    conversations.delete_conversation(USER_ID, doomed.id)

    ctx = _Context()
    rendered: list = []
    world = _install(monkeypatch)
    asyncio.run(agents_mod._handle_conversation_pick(_Update(rendered), ctx, token))

    assert world.specs == []
    assert "no longer there" in rendered[-1][0]


# --- deleting -----------------------------------------------------------------


def _api_client(user_id=USER_ID):
    app = FastAPI()
    app.include_router(conv_routes.router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(
        id=user_id, username="u", first_name="U", role="user"
    )
    return TestClient(app)


def test_deleting_from_telegram_removes_it_from_the_rest_listing(store, monkeypatch):
    """Criterion: gone from the phone means gone from ``GET /conversations``."""
    doomed = _seed("tg", title="delete me")
    kept = _seed("web", title="keep me")

    ctx = _Context()
    _install(monkeypatch)
    asyncio.run(
        agents_mod._handle_conversation_delete(_Update([]), ctx, _token(doomed))
    )

    listed = _api_client().get("/conversations").json()
    assert [c["id"] for c in listed] == [kept.id]


def test_deleting_asks_first(store, monkeypatch):
    """A transcript is the one thing here that cannot be recovered."""
    doomed = _seed("tg", title="delete me")

    ctx = _Context()
    rendered: list = []
    _install(monkeypatch)
    asyncio.run(
        agents_mod._handle_conversation_delete_confirm(
            _Update(rendered), ctx, _token(doomed)
        )
    )

    text, markup = rendered[-1]
    assert "cannot be recovered" in text
    assert conversations.get_conversation(USER_ID, doomed.id) is not None
    assert f"agent:conv_delok:{_token(doomed)}" in [
        b.callback_data for b in _buttons(markup)
    ]


def test_deleting_the_chats_own_conversation_lets_go_of_it(store, monkeypatch):
    """Mirrors the dashboard's DELETE, which also reaps the session answering it."""
    from handlers.agents._shared import stored_chat_conversation

    here = _seed("tg", title="the live one")
    ctx = _Context()
    world = _install(monkeypatch, live=_LiveSession(conversation_id=here.id))
    agents_mod.remember_chat_conversation(ctx.user_data, here.id)

    asyncio.run(agents_mod._handle_conversation_delete(_Update([]), ctx, _token(here)))

    assert world.destroyed == 1
    # Otherwise the next message spawns onto a transcript that is not there.
    assert stored_chat_conversation(ctx.user_data) == ""


def test_deleting_someone_elses_row_leaves_the_chat_alone(store, monkeypatch):
    """Only the conversation this chat is in costs it its session."""
    from handlers.agents._shared import stored_chat_conversation

    here = _seed("tg", title="the live one")
    other = _seed("web", title="an old one")
    ctx = _Context()
    world = _install(monkeypatch, live=_LiveSession(conversation_id=here.id))
    agents_mod.remember_chat_conversation(ctx.user_data, here.id)

    asyncio.run(agents_mod._handle_conversation_delete(_Update([]), ctx, _token(other)))

    assert world.destroyed == 0
    assert stored_chat_conversation(ctx.user_data) == here.id


# --- callback_data ------------------------------------------------------------


def test_every_payload_fits_telegrams_cap_whatever_the_id(store):
    """Criterion: 64 bytes, regardless of conversation id or title length."""

    class _Meta:
        def __init__(self, conv_id, title):
            self.id = conv_id
            self.title = title
            self.last_snippet = ""
            self.agent_slug = "a" * 120
            self.turn_count = 3
            self.updated_at = conversations._utcnow()

    metas = [_Meta("c" * 400, "t" * 500) for _ in range(20)]

    markup = menu_mod._conversations_keyboard(metas, page=1, current_id=metas[0].id)
    payloads = [b.callback_data for b in _buttons(markup)]
    payloads += [
        b.callback_data
        for b in _buttons(menu_mod._conversation_delete_keyboard(metas[0]))
    ]

    assert payloads  # the assertion below is worthless against an empty list
    assert all(len(p.encode("utf-8")) <= 64 for p in payloads)
    assert all(b.text for b in _buttons(markup))


def test_a_row_is_addressed_by_content_not_by_position(store, monkeypatch):
    """The list reorders on every write; a position tapped after one is a trap."""
    first = _seed("tg", title="first")
    second = _seed("tg", title="second")
    token = _token(first)

    # Touching `first` floats it to the top of a list sorted by last write.
    conversations.append_turn(
        USER_ID, first.id, TurnEntry(role="user", text="one more thing")
    )
    assert conversations.list_conversations(USER_ID)[0].id == first.id

    ctx = _Context()
    world = _install(monkeypatch)
    asyncio.run(agents_mod._handle_conversation_pick(_Update([]), ctx, token))

    assert world.last.conversation_id == first.id
    assert world.last.conversation_id != second.id
