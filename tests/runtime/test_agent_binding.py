"""Tests for agent-bound sessions (FEAT-011).

The load-bearing one is ``test_resolve_agent_sets_memory_scope``: binding a
session to an Agent must scope manage_memory/manage_skill to that Agent's own
store. Getting it wrong writes the user's domain notes into the chat
assistant's store, silently and irreversibly.
"""

import asyncio

import pytest

from condor.agents import agent as agent_module
from condor.agents.agent import AgentStore
from condor.memory.paths import store_root
from condor.runtime import SessionKey, SessionSpec, binding
from condor.runtime import client as runtime
from condor.runtime import sessions as session_module
from condor.runtime.binding import AGENT_SLUG_ENV, UnknownAgent


class _FakeClient:
    """Captures how the client was configured, without spawning anything."""

    last: "_FakeClient | None" = None

    def __init__(self, **kwargs):
        self.alive = True
        self.stop_calls = 0
        self.kwargs = kwargs
        self.prompts: list[str] = []
        type(self).last = self

    async def start(self):
        pass

    async def stop(self):
        self.stop_calls += 1
        self.alive = False

    async def prompt(self, text):
        self.prompts.append(text)
        return ""


@pytest.fixture
def agents_root(tmp_path, monkeypatch):
    """An agents/ tree with one pinned Agent and one serverless Agent."""
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    store = AgentStore()
    store.create(
        name="Executor Manager",
        description="Manages executors",
        instructions="You manage executors.",
        agent_key="ollama:qwen3:32b",
        tools=["get_market_data", "create_grid_executor"],
        when_to_consult="When deploying executors",
        server_required=False,
    )
    return tmp_path


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr("condor.acp.client.ACPClient", _FakeClient)
    monkeypatch.setattr("condor.acp.pydantic_ai_client.PydanticAIClient", _FakeClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "CHAT")
    # This file asserts the opening turn verbatim, so the conversation's
    # attribution block is blanked alongside the context builder above — it
    # is covered on its own in tests/runtime/test_conversation_attribution.py
    monkeypatch.setattr(session_module, "conversation_attribution", lambda *a, **k: "")
    _FakeClient.last = None
    return session_module


def _spec(**kwargs) -> SessionSpec:
    base = dict(
        key=str(SessionKey.telegram(42)),
        agent_key="",
        chat_id=42,
        user_id=42,
    )
    base.update(kwargs)
    return SessionSpec(**base)


# ── Resolution ──


def test_resolve_unbound_session_is_the_default_agent(monkeypatch):
    """No agent_slug resolves Condor — the default agent, not "no agent".

    Condor is an ordinary record under ``agents/`` now (FEAT-033), so the chat
    gets its instructions and store scope through the same path a specialist
    does. What it must NOT gain is specialist status: ``is_agent`` stays False
    (no separate identity asserted at system level, no server pin), and the
    slug that gets recorded and shown stays empty.
    """
    captured = {}

    def fake_session_servers(user_id, chat_id, user_data=None, **kw):
        captured["args"] = (user_id, chat_id, kw)
        return [{"name": "condor"}]

    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", fake_session_servers
    )

    bound = binding.resolve(_spec(agent_key="claude-code"))

    assert bound.is_agent is False
    assert bound.specialist_slug == ""
    assert bound.agent_slug == "condor"
    assert bound.label == "Condor"
    assert bound.agent_key == "claude-code"
    assert bound.tools == []
    # The subprocess still learns who it is: that is what scopes its stores.
    assert bound.mcp_env == {AGENT_SLUG_ENV: "condor"}
    assert captured["args"][2]["agent_slug"] == "condor"
    assert captured["args"][0] == 42


def test_ownerless_session_argv_matches_the_env_fallback(monkeypatch):
    """An ownerless spec reaches the subprocess as the chat id, never as 0.

    The id travels on two channels — ``CONDOR_USER_ID`` in the env (sessions.py)
    and ``--user-id`` on argv (here) — and argv is resolved first
    (mcp_servers/condor/settings.py), so an ``or 0`` fallback of our own would
    override the chat-id fallback the env channel computes and pinned by
    ``tests/test_agents.py::test_extra_env_falls_back_to_chat_id``. Down there
    the value is a principal: it mints the web JWT, subjects the server-access
    and admin checks, and roots the memory store — user 0 would be a phantom
    store shared by every ownerless session (SEC-180).
    """
    import config_manager

    class _NoServers:
        def get_accessible_servers(self, user_id):
            return []

        def get_server(self, name):
            return None

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _NoServers())
    monkeypatch.setattr(config_manager, "get_effective_server", lambda *a, **k: None)

    def argv_of(spec):
        bound = binding.resolve(spec)
        condor = next(s for s in bound.mcp_servers if s["name"] == "condor")
        return condor["args"]

    args = argv_of(_spec(agent_key="claude-code", user_id=None, chat_id=777))
    assert args[args.index("--user-id") + 1] == "777"
    assert args[args.index("--chat-id") + 1] == "777"
    assert args[args.index("--user-id") + 1] != "0"

    # And the owned case is untouched: the explicit ids pass straight through.
    args = argv_of(_spec(agent_key="claude-code", user_id=42, chat_id=555))
    assert args[args.index("--user-id") + 1] == "42"
    assert args[args.index("--chat-id") + 1] == "555"


def test_a_missing_default_record_still_starts_the_chat(monkeypatch, tmp_path):
    """An unreadable agents/condor/AGENT.md degrades; it does not fail closed."""
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **kw: []
    )

    bound = binding.resolve(_spec(agent_key="claude-code"))

    assert bound.label == "Condor"
    assert bound.instructions == ""
    assert bound.is_agent is False


def test_a_named_agent_that_does_not_exist_still_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    with pytest.raises(UnknownAgent):
        binding.resolve(_spec(agent_slug="ghost"))


def test_resolve_agent_sets_memory_scope(agents_root, monkeypatch):
    """A bound Agent scopes the MCP subprocess to its own store."""
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session",
        lambda *a, **kw: [{"name": "condor", "agent_slug": kw.get("agent_slug")}],
    )

    bound = binding.resolve(_spec(agent_slug="executor_manager"))

    assert bound.is_agent is True
    assert bound.agent_slug == "executor_manager"
    assert bound.label == "Executor Manager"
    # This is what makes manage_memory write to the Agent's store.
    assert bound.mcp_env[AGENT_SLUG_ENV] == "executor_manager"
    assert bound.mcp_servers[0]["agent_slug"] == "executor_manager"

    # And that env value is exactly the slug the store path is keyed on.
    scoped = store_root(42, bound.mcp_env[AGENT_SLUG_ENV])
    assert scoped.parts[-3:] == ("executor_manager", "store", "user_42")
    assert scoped != store_root(42, None)


def test_agent_key_precedence(agents_root, monkeypatch):
    """Explicit spec model > Agent's configured model."""
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **kw: []
    )

    inherited = binding.resolve(_spec(agent_slug="executor_manager"))
    assert inherited.agent_key == "ollama:qwen3:32b"

    overridden = binding.resolve(
        _spec(agent_slug="executor_manager", agent_key="claude-code")
    )
    assert overridden.agent_key == "claude-code"


def test_unknown_agent_slug(agents_root):
    """A bad slug fails loudly, before anything is spawned."""
    with pytest.raises(UnknownAgent) as exc:
        binding.resolve(_spec(agent_slug="does_not_exist"))
    assert "does_not_exist" in str(exc.value)


# ── Threading through spawn ──


def test_tool_allowlist_enforced(agents_root, registry, monkeypatch):
    """A bound pydantic-ai session is restricted to the Agent's tools."""
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **kw: []
    )

    info = asyncio.run(runtime.create_session(_spec(agent_slug="executor_manager")))

    assert _FakeClient.last.kwargs["allowed_tools"] == [
        "get_market_data",
        "create_grid_executor",
    ]
    # The Agent's own model was used, so the pydantic-ai client was selected.
    assert _FakeClient.last.kwargs["model"] == "ollama:qwen3:32b"
    assert info.agent_slug == "executor_manager"
    assert info.label == "Executor Manager"


def test_bound_session_opens_with_agent_identity(agents_root, registry, monkeypatch):
    """The Agent's own instructions replace the chat assistant's context."""
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **kw: []
    )

    asyncio.run(
        runtime.create_session(_spec(agent_slug="executor_manager", lazy_context=True))
    )
    session = registry.get_session(SessionKey.telegram(42))

    assert "You manage executors." in session.pending_context
    # The generic chat context (stubbed as "CHAT") must NOT be what it opens with.
    assert "CHAT" not in session.pending_context


def test_unbound_session_keeps_chat_context(registry, monkeypatch):
    """Without a binding, the assistant context is untouched."""
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **kw: []
    )

    asyncio.run(
        runtime.create_session(_spec(agent_key="claude-code", lazy_context=True))
    )
    session = registry.get_session(SessionKey.telegram(42))

    assert session.pending_context == "CHAT"
    assert session.agent_slug == ""
    assert session.label == "Condor"


def test_switch_destroys_and_respawns(agents_root, registry, monkeypatch):
    """Rebinding stops the old subprocess exactly once and reuses the key."""
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **kw: []
    )
    key = SessionKey.telegram(42)

    async def scenario():
        await runtime.create_session(_spec(agent_key="claude-code"))
        first_client = registry.get_session(key).client
        # lazy_context mirrors the real switch path (routes/sessions.py
        # _respawn): the new identity lands on the user's next prompt.
        await runtime.create_session(
            _spec(
                agent_slug="executor_manager",
                extra_context="Previously: hi",
                lazy_context=True,
            )
        )
        return first_client, registry.get_session(key)

    old_client, current = asyncio.run(scenario())

    assert old_client.stop_calls == 1
    assert current.client is not old_client
    assert current.agent_slug == "executor_manager"
    assert len(registry._sessions) == 1
    # The handoff note rides into the new session's opening context.
    assert "Previously: hi" in current.pending_context


def test_rebinding_to_same_agent_reuses_session(agents_root, registry, monkeypatch):
    """An empty agent_key must not look like a model change and respawn."""
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **kw: []
    )

    async def scenario():
        await runtime.create_session(_spec(agent_slug="executor_manager"))
        first = registry.get_session(SessionKey.telegram(42)).client
        await runtime.create_session(_spec(agent_slug="executor_manager"))
        return first, registry.get_session(SessionKey.telegram(42)).client

    first, second = asyncio.run(scenario())
    assert first is second
    assert first.stop_calls == 0
