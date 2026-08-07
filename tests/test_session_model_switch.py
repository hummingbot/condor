"""The model a chat runs on is inherited, deliberate, and remembered (FEAT-037).

Two halves, one rule: ``agent_key == ""`` on the wire means "ask whoever is
bound", so a non-empty key is by construction a user opening the picker and
choosing — which is what makes it worth persisting. Before this, every web
start path volunteered a concrete key and thereby claimed an override the user
never made, so a bound Agent silently ran on Condor's model.
"""

import asyncio
import json

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from condor.acp.client import PromptDone, TextChunk
from condor.agents import agent as agent_module
from condor.agents.agent import AgentStore
from condor.runtime import SessionKey, SessionSpec
from condor.runtime import client as runtime
from condor.runtime import conversations
from condor.runtime import sessions as session_module
from condor.runtime.binding import remember_model_choice
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import agents as agent_routes
from condor.web.routes import sessions as session_routes
from condor.web.routes.chat_ws import _handle_resume_conversation, _handle_start_session

USER = WebUser(id=777, username="u", first_name="U", role="user")

# Pinned rather than read from the repo's condor/AGENT.md: the assertions here
# are all "X, not the fallback", so a fixture Agent that happened to share the
# real default would make them pass for the wrong reason.
FALLBACK = "claude-code"
BRIGADO_MODEL = "claude-acp:sonnet"


class _FakeWS:
    """Collects what the handler would have sent to the browser."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def started(self) -> dict:
        events = [e for e in self.sent if e.get("event") == "session_started"]
        assert events, f"no session_started in {self.sent}"
        return events[-1]


class _Client:
    def __init__(self, **kwargs):
        self.alive = True

    async def start(self):
        pass

    async def stop(self):
        self.alive = False

    async def prompt(self, text):
        return "ok"

    async def prompt_stream(self, text):
        yield TextChunk(text="ok")
        yield PromptDone(stop_reason="end_turn")


class FakeConfigManager:
    # Read directly by ``get_effective_server`` on the session-spawn path.
    _data: dict = {}

    def is_admin(self, user_id):
        return False

    def get_accessible_servers(self, user_id):
        return ["local", "prod"]

    def has_server_access(self, user_id, server_name, *a, **k):
        return server_name in {"local", "prod"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A sandboxed agents/ tree, conversation store and runtime."""
    agents_root = tmp_path / "agents"
    agents_root.mkdir()
    monkeypatch.setattr(agent_module, "_DATA_ROOT", agents_root)
    monkeypatch.setattr(conversations, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(conversations, "_live_recorders", set())
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr(session_module, "ACPClient", _Client)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(
        "handlers.agents._shared.build_mcp_servers_for_session", lambda *a, **k: []
    )
    monkeypatch.setattr(
        "condor.runtime.binding.agent_identity_context", lambda *a, **k: ""
    )
    monkeypatch.setattr("condor.preferences.load_user_data_for", lambda *a, **k: {})
    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: FakeConfigManager()
    )
    monkeypatch.setattr(session_routes, "get_config_manager", FakeConfigManager)
    monkeypatch.setattr("handlers.agents._shared.DEFAULT_AGENT", FALLBACK)
    monkeypatch.setattr("condor.web.routes.chat_ws.DEFAULT_AGENT", FALLBACK)

    AgentStore().create(
        name="Brigado",
        description="BRL market making",
        instructions="Trade the BRL pairs and never widen past 40 bps.",
        agent_key=BRIGADO_MODEL,
        tools=["manage_bots"],
        when_to_consult="BRL market making",
        server_required=False,
    )
    return agents_root


def _prefs(monkeypatch) -> dict:
    """Capture what would have been written to the user's preferences."""
    written: dict = {}
    monkeypatch.setattr(
        "condor.preferences.load_user_data_for", lambda uid: {"_user_id": uid}
    )
    monkeypatch.setattr(
        "condor.preferences.set_active_agent_key",
        lambda user_data, key: written.update(key=key, user=user_data.get("_user_id")),
    )
    return written


def _sessions_client() -> TestClient:
    app = FastAPI()
    app.include_router(session_routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app)


def _agents_client() -> TestClient:
    app = FastAPI()
    app.include_router(agent_routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app)


def _live_session(agent_slug: str = "", agent_key: str = FALLBACK):
    """A started web session on a fresh conversation. Returns (key, conv_id)."""
    conv = conversations.new_conversation(
        USER.id, "web", agent_key=agent_key, agent_slug=agent_slug
    )
    key = str(SessionKey.web(USER.id, conv.id))
    asyncio.run(
        runtime.create_session(
            SessionSpec(
                key=key,
                agent_key=agent_key,
                user_id=USER.id,
                agent_slug=agent_slug,
                conversation_id=conv.id,
                lazy_context=True,
            )
        )
    )
    return key, conv.id


# ── 1. "" means inherit ──


def test_binding_an_agent_starts_it_on_its_own_model(env):
    """The reported bug: the dashboard sent a key, so AGENT.md was ignored."""
    ws = _FakeWS()

    asyncio.run(_handle_start_session(ws, USER.id, {"agent_slug": "brigado"}))

    assert ws.started()["agent_key"] == BRIGADO_MODEL
    assert ws.started()["agent_slug"] == "brigado"


def test_an_unbound_chat_still_gets_the_default_model(env):
    ws = _FakeWS()

    asyncio.run(_handle_start_session(ws, USER.id, {}))

    assert ws.started()["agent_key"] == FALLBACK


def test_a_deliberate_pick_still_overrides_the_bound_agent(env, monkeypatch):
    _prefs(monkeypatch)
    ws = _FakeWS()

    asyncio.run(
        _handle_start_session(
            ws, USER.id, {"agent_slug": "brigado", "agent_key": "gemini"}
        )
    )

    assert ws.started()["agent_key"] == "gemini"


def test_resuming_a_bound_conversation_ignores_the_stale_recorded_key(env):
    """``conv.agent_key`` is a log of what answered, never a pin."""
    conv = conversations.new_conversation(
        USER.id, "web", agent_key="claude-code", agent_slug="brigado"
    )
    ws = _FakeWS()

    asyncio.run(_handle_resume_conversation(ws, USER.id, {"conversation_id": conv.id}))

    assert ws.started()["agent_key"] == BRIGADO_MODEL, "the Agent's own model"


def test_resuming_an_unbound_conversation_keeps_what_answered(env):
    conv = conversations.new_conversation(USER.id, "web", agent_key="gemini")
    ws = _FakeWS()

    asyncio.run(_handle_resume_conversation(ws, USER.id, {"conversation_id": conv.id}))

    assert ws.started()["agent_key"] == "gemini"


def test_a_server_only_switch_does_not_freeze_the_agents_model(env, monkeypatch):
    _prefs(monkeypatch)
    key, _ = _live_session(agent_slug="brigado", agent_key="claude-code")

    res = _sessions_client().post(
        f"/sessions/{key}/action", json={"action": "switch", "server_name": "prod"}
    )

    assert res.status_code == 200
    session = res.json()["session"]
    assert session["agent_key"] == BRIGADO_MODEL, "back to the Agent's own model"
    assert session["server_name"] == "prod"


def test_binding_a_different_agent_does_not_drag_the_outgoing_model(env, monkeypatch):
    _prefs(monkeypatch)
    key, _ = _live_session(agent_key="gemini")

    res = _sessions_client().post(
        f"/sessions/{key}/action", json={"action": "switch", "agent_slug": "brigado"}
    )

    assert res.json()["session"]["agent_key"] == BRIGADO_MODEL


# ── 2. A deliberate pick is remembered ──


def test_a_pick_on_a_bound_agent_becomes_its_default(env):
    remember_model_choice(USER.id, "brigado", "claude-acp:opus")

    assert AgentStore().get("brigado").agent_key == "claude-acp:opus"


def test_the_write_back_touches_only_the_model(env):
    before = AgentStore().get("brigado")

    remember_model_choice(USER.id, "brigado", "claude-acp:opus")

    after = AgentStore().get("brigado")
    assert after.instructions == before.instructions
    assert (after.name, after.description) == (before.name, before.description)
    assert after.tools == before.tools
    assert after.when_to_consult == before.when_to_consult
    assert after.server_required == before.server_required
    assert after.created_at == before.created_at


def test_switching_the_model_in_a_bound_chat_moves_the_agent(env):
    key, _ = _live_session(agent_slug="brigado")

    res = _sessions_client().post(
        f"/sessions/{key}/action",
        json={"action": "switch", "agent_key": "claude-acp:opus"},
    )

    assert res.json()["session"]["agent_key"] == "claude-acp:opus"
    assert (
        AgentStore().get("brigado").agent_key == "claude-acp:opus"
    ), "the next chat, consult, delegate or loop starts there too"


def test_a_pick_in_an_unbound_chat_writes_the_user_preference(env, monkeypatch):
    written = _prefs(monkeypatch)

    remember_model_choice(USER.id, "", "claude-acp:opus")

    assert written == {"key": "claude-acp:opus", "user": USER.id}


def test_an_unbound_pick_never_rewrites_condors_agent_md(env, monkeypatch):
    """``DEFAULT_AGENT`` is read at import and is *everyone's* default."""
    _prefs(monkeypatch)
    AgentStore()._save(
        agent_module.Agent(slug="condor", name="Condor", agent_key="claude-code")
    )

    remember_model_choice(USER.id, "condor", "claude-acp:opus")

    assert AgentStore().get("condor").agent_key == "claude-code", "untouched"


def test_picker_sentinels_are_never_written(env, monkeypatch):
    written = _prefs(monkeypatch)

    remember_model_choice(USER.id, "brigado", "openrouter:")
    remember_model_choice(USER.id, "", "custom:")

    assert AgentStore().get("brigado").agent_key == BRIGADO_MODEL
    assert written == {}


def test_an_empty_key_or_no_user_writes_nothing(env, monkeypatch):
    written = _prefs(monkeypatch)

    remember_model_choice(USER.id, "brigado", "")
    remember_model_choice(None, "", "gemini")

    assert AgentStore().get("brigado").agent_key == BRIGADO_MODEL
    assert written == {}


def test_a_no_op_pick_leaves_the_file_alone(env, monkeypatch):
    writes: list = []
    monkeypatch.setattr(AgentStore, "update", lambda self, a: writes.append(a))

    remember_model_choice(USER.id, "brigado", BRIGADO_MODEL)

    assert writes == [], "no spurious AGENT.md churn when nothing changed"


def test_a_failed_write_never_breaks_the_chat(env, monkeypatch):
    def boom(self, agent):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(AgentStore, "update", boom)

    remember_model_choice(USER.id, "brigado", "claude-acp:opus")  # does not raise


# ── 3. The options payload tells the picker the truth ──


def test_options_carry_each_agents_model(env, monkeypatch):
    monkeypatch.setattr("condor.preferences.get_active_agent_key", lambda uid: None)

    body = _sessions_client().get("/sessions/options").json()

    brigado = next(a for a in body["agent_bindings"] if a["slug"] == "brigado")
    assert brigado["agent_key"] == BRIGADO_MODEL


def test_the_default_model_follows_the_users_own_pick(env, monkeypatch):
    monkeypatch.setattr(
        "condor.preferences.get_active_agent_key", lambda uid: "claude-acp:opus"
    )

    body = _sessions_client().get("/sessions/options").json()

    assert body["default_agent"] == "claude-acp:opus"


def test_the_default_model_falls_back_to_condors(env, monkeypatch):
    monkeypatch.setattr("condor.preferences.get_active_agent_key", lambda uid: None)

    body = _sessions_client().get("/sessions/options").json()

    assert body["default_agent"] == FALLBACK


# ── 4. The Agent's page writes through the same door ──


def test_the_model_can_be_set_from_the_agents_page(env):
    res = _agents_client().patch(
        "/agents/brigado/config", json={"agent_key": "claude-acp:opus"}
    )

    assert res.status_code == 200
    assert res.json()["agent_key"] == "claude-acp:opus"
    assert AgentStore().get("brigado").agent_key == "claude-acp:opus"


def test_the_model_can_be_cleared_back_to_the_chat_default(env):
    res = _agents_client().patch("/agents/brigado/config", json={"agent_key": ""})

    assert res.status_code == 200
    assert AgentStore().get("brigado").agent_key == ""


def test_the_endpoint_refuses_a_picker_sentinel(env):
    res = _agents_client().patch(
        "/agents/brigado/config", json={"agent_key": "openrouter:"}
    )

    assert res.status_code == 400
    assert AgentStore().get("brigado").agent_key == BRIGADO_MODEL, "not written"


def test_setting_the_model_leaves_the_server_pin_alone(env):
    client = _agents_client()
    client.patch("/agents/brigado/config", json={"server_name": "prod"})

    client.patch("/agents/brigado/config", json={"agent_key": "gemini"})

    agent = AgentStore().get("brigado")
    assert (agent.server_name, agent.agent_key) == ("prod", "gemini")
