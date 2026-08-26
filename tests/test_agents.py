"""Unit tests for the unified Agent model: AgentStore + Strategy sub-resource.

Covers the universal capabilities (every Agent is consultable, delegable and
loopable — no flags, no gating), the strategy CRUD scoped under an Agent, the
default playbook that makes a strategy-less Agent loopable, the shared per-Agent
skill library, and the pydantic-ai tool allowlist.
"""

import asyncio
from types import SimpleNamespace

from condor.acp.pydantic_ai_client import PydanticAIClient
from condor.agents import agent as agent_module
from condor.agents import strategy as strategy_module
from condor.agents.agent import AgentStore
from condor.agents.strategy import StrategyStore


def _write_agent(root, slug, *, body="Body.", **frontmatter):
    """Write an AGENT.md under root/<slug>/."""
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    fm = "\n".join(f"{k}: {v}" for k, v in frontmatter.items())
    (d / "AGENT.md").write_text(f"---\n{fm}\n---\n\n{body}\n")
    return d


def _patch_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(strategy_module, "_DATA_ROOT", tmp_path)


# ── Agent discovery + the index every Agent appears in ──


def test_agent_discovery_and_index(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(
        tmp_path,
        "executor_manager",
        name="Executor Manager",
        description="Manages executors",
        when_to_consult="When deploying or tuning executors",
        agent_key="ollama:qwen3:32b",
        body="Body for executor_manager.",
    )
    # No consult trigger — still a first-class agent, just described by its
    # description in the index. Filtering it out would make it unroutable.
    _write_agent(
        tmp_path,
        "brigado",
        name="Brigado",
        description="BRL market making",
        agent_key="claude-code",
    )

    store = AgentStore()
    em = store.get("executor_manager")
    assert em is not None
    assert em.slug == "executor_manager"
    assert em.agent_key == "ollama:qwen3:32b"
    assert em.instructions.strip().endswith("Body for executor_manager.")

    index = store.list_index()
    assert "[executor_manager] When deploying or tuning executors" in index
    assert "[brigado] BRL market making" in index


def test_consult_hint_falls_back(tmp_path, monkeypatch):
    """The hint degrades description -> name; it never gates consultability."""
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(
        tmp_path, "with_trigger", name="A", description="d", when_to_consult="t"
    )
    _write_agent(tmp_path, "with_desc", name="B", description="d")
    _write_agent(tmp_path, "bare", name="C")

    store = AgentStore()
    assert store.get("with_trigger").consult_hint == "t"
    assert store.get("with_desc").consult_hint == "d"
    assert store.get("bare").consult_hint == "C"
    assert "[bare] C" in store.list_index()


def test_missing_agent_returns_none(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    assert AgentStore().get("nope") is None
    assert AgentStore().get("") is None
    assert AgentStore().list_index() == ""


def test_agent_crud_roundtrip(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    store = AgentStore()
    a = store.create(
        name="River Maker",
        description="d",
        instructions="identity body",
        agent_key="ollama:x",
        when_to_consult="ask me",
    )
    assert a.slug == "river_maker"
    assert (tmp_path / "river_maker" / "AGENT.md").exists()

    reloaded = store.get("river_maker")
    assert reloaded.instructions.strip() == "identity body"
    assert reloaded.when_to_consult == "ask me"

    reloaded.description = "updated"
    store.update(reloaded)
    assert store.get("river_maker").description == "updated"

    assert store.delete("river_maker") is True
    assert store.get("river_maker") is None


# ── Strategy as an Agent sub-resource ──


def test_strategy_crud_under_agent(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "brigado", name="Brigado", agent_key="claude-code")

    store = StrategyStore()
    s = store.create(
        agent_slug="brigado",
        name="BRL MM",
        description="tactic",
        instructions="do the thing",
        default_config={"connector_name": "binance"},
    )
    assert s.slug == "brl_mm"
    assert s.key == "brigado.brl_mm"
    assert s.dir == tmp_path / "brigado" / "strategies" / "brl_mm"
    assert (s.dir / "strategy.md").exists()

    # get / get_by_key / list
    assert store.get("brigado", "brl_mm").instructions.strip() == "do the thing"
    assert store.get_by_key("brigado.brl_mm").name == "BRL MM"
    assert [x.slug for x in store.list("brigado")] == ["brl_mm"]

    # A second strategy under the same Agent (shares the brain).
    store.create(agent_slug="brigado", name="BRL Scalp", instructions="scalp")
    assert sorted(x.slug for x in store.list("brigado")) == ["brl_mm", "brl_scalp"]
    assert sorted(x.key for x in store.list_all()) == [
        "brigado.brl_mm",
        "brigado.brl_scalp",
    ]

    assert store.delete("brigado", "brl_scalp") is True
    assert [x.slug for x in store.list("brigado")] == ["brl_mm"]


def test_strategy_agent_key_override_optional(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "brigado", name="Brigado", agent_key="claude-code")
    store = StrategyStore()
    # No override => inherits the Agent's model (agent_key is None).
    s = store.create(agent_slug="brigado", name="Inherit", instructions="x")
    assert store.get_by_key(s.key).agent_key is None
    # Explicit override persists.
    s2 = store.create(
        agent_slug="brigado", name="Override", instructions="x", agent_key="ollama:z"
    )
    assert store.get_by_key(s2.key).agent_key == "ollama:z"


# ── Every Agent is loopable: the default playbook ──


def test_agent_with_no_strategy_is_still_loopable(tmp_path, monkeypatch):
    """An Agent that owns no playbook resolves to a default one, built on demand."""
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "brigado", name="Brigado", agent_key="claude-code")
    store = StrategyStore()
    assert store.list("brigado") == []

    resolved = store.resolve_for_loop("brigado")
    assert resolved is not None
    assert resolved.slug == strategy_module.DEFAULT_STRATEGY_SLUG
    assert resolved.key == "brigado.default"
    assert (resolved.dir / "strategy.md").exists()
    assert resolved.instructions.strip()  # a real playbook body, not empty

    # Idempotent: a second start reuses the same one instead of piling up.
    again = store.resolve_for_loop("brigado")
    assert again.key == resolved.key
    assert [x.slug for x in store.list("brigado")] == ["default"]


def test_resolve_for_loop_prefers_explicit_playbooks(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "brigado", name="Brigado", agent_key="claude-code")
    store = StrategyStore()
    store.create(agent_slug="brigado", name="BRL MM", instructions="do the thing")

    # A composite key is always honored verbatim.
    assert store.resolve_for_loop("brigado.brl_mm").key == "brigado.brl_mm"
    # A bare slug with exactly one playbook means that playbook — no default is
    # invented behind the user's back.
    assert store.resolve_for_loop("brigado").key == "brigado.brl_mm"
    assert [x.slug for x in store.list("brigado")] == ["brl_mm"]

    # With several, the bare slug falls back to the agent's own default loop.
    store.create(agent_slug="brigado", name="BRL Scalp", instructions="scalp")
    assert store.resolve_for_loop("brigado").slug == "default"


def test_resolve_for_loop_unknown_target(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    assert StrategyStore().resolve_for_loop("nope") is None
    assert StrategyStore().resolve_for_loop("nope.also_nope") is None


# ── MCP tool: manage_agents CRUD (the AGENT.md identity) ──


def test_manage_agents_crud(tmp_path, monkeypatch):
    """create/get/update/delete through the manage_agents tool."""
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import trading_agent as ta

    _patch_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "user_id", 7, raising=False)

    created = ta.manage_agents(
        action="create",
        name="Risk Sentry",
        description="watches drawdown",
        instructions="identity + domain knowledge",
        agent_key="ollama:qwen3:32b",
        when_to_consult="when sizing a position",
        tools=["get_market_data"],
    )
    assert created["created"] is True
    assert created["agent_slug"] == "risk_sentry"

    got = ta.manage_agents(action="get", agent_slug="risk_sentry")
    assert got["instructions"].strip() == "identity + domain knowledge"
    assert got["tools"] == ["get_market_data"]

    updated = ta.manage_agents(
        action="update",
        agent_slug="risk_sentry",
        instructions="new body",
        when_to_consult="",  # clearing the hint must NOT gate anything
    )
    assert updated["updated"] is True
    assert (
        ta.manage_agents(action="get", agent_slug="risk_sentry")["instructions"].strip()
        == "new body"
    )

    listed = ta.manage_agents(action="list")["agents"]
    entry = next(a for a in listed if a["slug"] == "risk_sentry")
    # The hint fell back to the description once the trigger was cleared.
    assert entry["when_to_consult"] == "watches drawdown"

    assert ta.manage_agents(action="delete", agent_slug="risk_sentry") == {
        "deleted": True
    }
    assert "error" in ta.manage_agents(action="get", agent_slug="risk_sentry")


def test_create_strategy_requires_existing_agent(tmp_path, monkeypatch):
    """A strategy can't be created under an agent that does not exist."""
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import trading_agent as ta

    _patch_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "user_id", 7, raising=False)

    result = ta.manage_strategies(
        action="create",
        agent_slug="ghost",
        name="S",
        instructions="x",
    )
    assert "error" in result and "not found" in result["error"].lower()


def test_routines_dir_resolves_bare_agent_slug(tmp_path, monkeypatch):
    """A bare agent slug (no strategy yet) resolves to its routines dir."""
    from mcp_servers.condor.tools import routines as routines_tool

    _patch_roots(monkeypatch, tmp_path)
    _write_agent(tmp_path, "soloist", name="Soloist", agent_key="ollama:x")
    monkeypatch.setattr(
        "routines.base.assistant_routines_dir", lambda slug: tmp_path / str(slug)
    )

    # Bare slug of an existing agent => that agent's dir.
    assert routines_tool._get_agent_routines_dir("soloist") == tmp_path / "soloist"
    # Unknown slug (not an agent, not a strategy) => None.
    assert routines_tool._get_agent_routines_dir("nope") is None


# ── Shared per-Agent skill library (FEAT-003 brain) ──


def test_agent_skill_library_read_and_edit(tmp_path):
    """An Agent's skills/<slug>/SKILL.md library is readable and editable."""
    from condor.memory.skills import SkillStore

    skill_dir = tmp_path / "agents" / "executor_manager" / "skills" / "size_grid"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: size_grid\ndescription: d\nwhen_to_use: before a grid\n"
        "source: builtin\n---\n\nSteps.\n"
    )

    store = SkillStore(agent_slug="executor_manager")
    assert "[size_grid] before a grid" in store.list_index()
    read = store.read("size_grid")
    assert read is not None and read["when_to_use"] == "before a grid"

    assert store.create("stop or widen", "d2", "when underwater", "steps")["saved"]
    assert "[stop_or_widen] when underwater" in store.list_index()
    assert store.edit("size_grid", description="updated")["description"] == "updated"
    assert store.delete("stop_or_widen") is True
    assert "stop_or_widen" not in store.list_index()


# ── pydantic-ai tool allowlist (enforced on consult) ──


def test_allowlist_filters_bare_and_namespaced_names():
    client = PydanticAIClient(
        model="ollama:x", allowed_tools=["manage_executors", "get_market_data"]
    )
    defs = [
        SimpleNamespace(name="manage_executors"),
        SimpleNamespace(name="mcp__condor__get_market_data"),
        SimpleNamespace(name="manage_bots"),
        SimpleNamespace(name="place_order"),
    ]
    kept = asyncio.run(client._prepare_tools(None, defs))
    assert sorted(d.name for d in kept) == [
        "manage_executors",
        "mcp__condor__get_market_data",
    ]


def test_no_allowlist_means_no_filter():
    assert PydanticAIClient(model="ollama:x").allowed_tools is None


# ── prompt_stream PromptDone sentinel (CORR-041) ──


class _FakeRun:
    """Minimal stand-in for pydantic-ai's agent run iterator."""

    def __init__(self, nodes):
        self._nodes = nodes
        self.result = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def __aiter__(self):
        for node in self._nodes:
            yield node


def _collect_prompt_stream(client):
    async def _run():
        return [event async for event in client.prompt_stream("hi")]

    return asyncio.run(_run())


def _prompt_done_reasons(events):
    from condor.acp.client import PromptDone

    return [e.stop_reason for e in events if isinstance(e, PromptDone)]


def test_prompt_stream_success_emits_single_end_turn():
    client = PydanticAIClient(model="ollama:x")
    client._agent = SimpleNamespace(iter=lambda *a, **k: _FakeRun([]))
    client._request_semaphore = asyncio.Semaphore(1)

    events = _collect_prompt_stream(client)
    assert _prompt_done_reasons(events) == ["end_turn"]


def test_prompt_stream_error_emits_text_then_single_error():
    from condor.acp.client import PromptDone, TextChunk

    client = PydanticAIClient(model="ollama:x")

    def _boom(*a, **k):
        raise RuntimeError("kaboom")

    client._agent = SimpleNamespace(iter=_boom)
    client._request_semaphore = asyncio.Semaphore(1)

    events = _collect_prompt_stream(client)
    assert _prompt_done_reasons(events) == ["error"]
    # The error path emits a TextChunk before the PromptDone(error) sentinel.
    assert isinstance(events[0], TextChunk)
    assert isinstance(events[-1], PromptDone)


def test_prompt_stream_timeout_emits_single_timeout():
    client = PydanticAIClient(model="ollama:x")

    def _timeout(*a, **k):
        raise asyncio.TimeoutError()

    client._agent = SimpleNamespace(iter=_timeout)
    client._request_semaphore = asyncio.Semaphore(1)

    events = _collect_prompt_stream(client)
    assert _prompt_done_reasons(events) == ["timeout"]


def test_prompt_stream_runs_without_semaphore_for_cloud_providers():
    # Cloud providers leave _request_semaphore None (PERF-038): prompt_stream
    # must still work, with the serialization guard acting as a no-op.
    client = PydanticAIClient(model="anthropic:claude-sonnet-4-6")
    client._agent = SimpleNamespace(iter=lambda *a, **k: _FakeRun([]))
    client._request_semaphore = None

    events = _collect_prompt_stream(client)
    assert _prompt_done_reasons(events) == ["end_turn"]


# ── per-server slot released during human confirmation (PERF-029) ──


def test_release_request_slot_frees_semaphore_during_wait():
    # PERF-029: while one session blocks on a human confirmation, the global
    # per-server slot must be free so a concurrent session on the same backend
    # can proceed instead of stalling for the whole confirmation timeout.
    async def _run():
        sem = asyncio.Semaphore(1)
        client = PydanticAIClient(model="ollama:x")
        client._request_semaphore = sem

        await sem.acquire()  # this session holds the only slot
        assert sem.locked()

        async with client._release_request_slot():
            # During the "human is deciding" window the slot is free…
            assert not sem.locked()
            # …and a concurrent session can grab it.
            await asyncio.wait_for(sem.acquire(), timeout=0.1)
            sem.release()
        # Re-acquired before returning, so model HTTP work stays serialized.
        assert sem.locked()

    asyncio.run(_run())


def test_release_request_slot_noop_for_cloud_providers():
    # PERF-038: cloud providers keep _request_semaphore None; the release helper
    # must be a no-op rather than crash on a None semaphore.
    async def _run():
        client = PydanticAIClient(model="anthropic:claude-sonnet-4-6")
        client._request_semaphore = None
        async with client._release_request_slot():
            pass

    asyncio.run(_run())


def test_resolve_base_url_distinguishes_cloud_from_local_backends():
    # PERF-038: only backends with a resolved base URL get serialized. Cloud
    # providers pydantic-ai resolves natively return None (no semaphore).
    from condor.acp.pydantic_ai_client import resolve_base_url

    # Cloud providers -> no base URL -> run concurrently (no semaphore).
    assert resolve_base_url("anthropic:claude-sonnet-4-6") is None
    assert resolve_base_url("groq:llama-3.3-70b-versatile") is None
    assert resolve_base_url("openai:gpt-4o") is None

    # Local / custom backends -> base URL -> stay serialized.
    assert resolve_base_url("ollama:llama3.1") == "http://localhost:11434/v1"
    assert resolve_base_url("lmstudio:qwen-14b") == "http://localhost:1234/v1"
    assert (
        resolve_base_url("openai:my-model", "http://localhost:8000/v1")
        == "http://localhost:8000/v1"
    )


def test_assistant_routines_dir_layout():
    from routines.base import assistant_routines_dir

    # The chat condor's home is the repo-root general library.
    assert assistant_routines_dir(None).parts[-1] == "routines"
    assert assistant_routines_dir("executor_manager").parts[-3:] == (
        "agents",
        "executor_manager",
        "routines",
    )


# ── MCP subprocess env (CONDOR_USER_ID injection) ──


class _FakeACPClient:
    """Captures the env passed to the ACP subprocess without launching it."""

    last_extra_env: dict | None = None

    def __init__(self, **kwargs):
        type(self).last_extra_env = kwargs.get("extra_env")
        self.alive = True

    async def start(self):
        pass

    async def stop(self):
        pass

    async def prompt(self, text):
        pass


def _run_create_session(monkeypatch, *, chat_id, user_id):
    """Invoke the runtime's session factory with the ACP client + context stubbed out."""
    from condor.runtime import SessionKey, SessionSpec
    from condor.runtime import sessions as session_module

    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr("condor.acp.client.ACPClient", _FakeACPClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    # Resolved through condor.runtime.binding now, so patch it at the source.
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **k: []
    )
    _FakeACPClient.last_extra_env = None
    spec = SessionSpec(
        key=str(SessionKey.telegram(chat_id)),
        agent_key="claude-code",
        chat_id=chat_id,
        user_id=user_id,
    )
    asyncio.run(session_module.get_or_create_session(spec))
    return _FakeACPClient.last_extra_env


def test_extra_env_uses_user_id(monkeypatch):
    """CONDOR_USER_ID is injected from the explicit user_id."""
    env = _run_create_session(monkeypatch, chat_id=555, user_id=42)
    assert env["CONDOR_USER_ID"] == "42"
    assert env["CONDOR_CHAT_ID"] == "555"


def test_extra_env_falls_back_to_chat_id(monkeypatch):
    """With no user_id, CONDOR_USER_ID falls back to the chat_id, not '0'."""
    env = _run_create_session(monkeypatch, chat_id=777, user_id=None)
    assert env["CONDOR_USER_ID"] == "777"
    assert env["CONDOR_USER_ID"] != "0"


def test_resolve_acp_model_suffix():
    from condor.acp.client import resolve_acp

    # Returns (command, env, model_pref). The suffix is surfaced as model_pref so
    # ACPClient can select it via session/set_model (the bridge ignores env).
    cmd, env, pref = resolve_acp("claude-acp:opus")
    assert cmd == "claude-agent-acp"
    assert env == {"ANTHROPIC_MODEL": "opus"}
    assert pref == "opus"

    _, env, pref = resolve_acp("claude-acp:claude-opus-4-8")
    assert env == {"ANTHROPIC_MODEL": "claude-opus-4-8"}
    assert pref == "claude-opus-4-8"

    assert resolve_acp("claude-code") == ("claude-agent-acp", {}, "")
    assert resolve_acp("claude-acp") == ("claude-agent-acp", {}, "")

    cmd, env, pref = resolve_acp("gemini")
    assert "gemini" in cmd and env == {} and pref == ""


def test_resolve_model_id_matching():
    from condor.acp.client import resolve_model_id

    models = [
        {"modelId": "claude-opus-4-6", "name": "Opus 4.6"},
        {"modelId": "claude-sonnet-4-6", "name": "Sonnet 4.6"},
        {"modelId": "claude-haiku-4-5", "name": "Haiku 4.5"},
    ]
    assert resolve_model_id("sonnet", models) == "claude-sonnet-4-6"
    assert resolve_model_id("opus", models) == "claude-opus-4-6"
    assert resolve_model_id("claude-sonnet-4-6", models) == "claude-sonnet-4-6"
    assert resolve_model_id("Sonnet 4.6", models) == "claude-sonnet-4-6"
    assert resolve_model_id("nonsense", models) is None
    assert resolve_model_id("", models) is None
    assert resolve_model_id("sonnet", []) is None


def test_claude_acp_takes_acp_path_not_pydantic_ai():
    from condor.acp.pydantic_ai_client import is_pydantic_ai_model

    assert is_pydantic_ai_model("claude-acp:opus") is False  # ACP subprocess path
    assert is_pydantic_ai_model("anthropic:claude-opus-4-8") is True  # API path
    assert is_pydantic_ai_model("ollama:qwen3:32b") is True


# ── consult endpoint authorization (SEC-035) ──


def _consult_request(**kw):
    from condor.web.routes.agents import ConsultRequest

    kw.setdefault("task", "what's my balance?")
    return ConsultRequest(**kw)


def _web_user(uid):
    return SimpleNamespace(id=uid, username="", first_name="", role="user")


def test_consult_denies_server_without_access(monkeypatch):
    """A user without access to server 'X' gets 403 and run_consult is not called."""
    import config_manager
    from condor.agents import consult as consult_module
    from condor.web.routes import agents as agents_module

    called = {"run": False}

    async def _fail_run_consult(**kw):  # pragma: no cover - must not be reached
        called["run"] = True
        return "should not run"

    monkeypatch.setattr(consult_module, "run_consult", _fail_run_consult)
    monkeypatch.setattr(
        "condor.web.auth.get_config_manager",
        lambda: SimpleNamespace(has_server_access=lambda uid, name: False),
    )

    from fastapi import HTTPException

    req = _consult_request(server_name="X", user_id=999)
    try:
        asyncio.run(agents_module.consult_agent("em", req, user=_web_user(42)))
        assert False, "expected 403"
    except HTTPException as exc:
        assert exc.status_code == 403
    assert called["run"] is False  # no MCP client built for X


def test_consult_forces_caller_user_id(monkeypatch):
    """An accessible-server consult runs, but user_id is forced to the caller's."""
    import config_manager
    from condor.agents import consult as consult_module
    from condor.web.routes import agents as agents_module

    seen = {}

    async def _capture_run_consult(**kw):
        seen.update(kw)
        return "ok"

    monkeypatch.setattr(consult_module, "run_consult", _capture_run_consult)
    monkeypatch.setattr(
        "condor.web.auth.get_config_manager",
        lambda: SimpleNamespace(has_server_access=lambda uid, name: True),
    )

    # Caller is 42 but tries to impersonate user 999.
    req = _consult_request(server_name="X", user_id=999)
    result = asyncio.run(agents_module.consult_agent("em", req, user=_web_user(42)))

    assert result["answer"] == "ok"
    assert seen["user_id"] == 42  # caller's id, not the 999 override
    assert seen["server_name"] == "X"


def test_session_mcp_servers_carry_agent_slug(monkeypatch):
    """Serverless agent runs (consult/tick without server_name) must scope the
    condor MCP tools to the agent's own memory/skills via --agent-slug —
    without it, an agent silently reads/writes the CHAT's stores (e.g. its
    routines land in the global library instead of its own dir)."""
    import config_manager
    from handlers.agents._shared import build_mcp_servers_for_session

    class _NoServers:
        def get_accessible_servers(self, user_id):
            return []

        def get_server(self, name):
            return None

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _NoServers())
    monkeypatch.setattr(config_manager, "get_effective_server", lambda *a, **k: None)

    servers = build_mcp_servers_for_session(42, 42, agent_slug="backpack_mm")
    condor = next(s for s in servers if s["name"] == "condor")
    args = condor["args"]
    assert "--agent-slug" in args
    assert args[args.index("--agent-slug") + 1] == "backpack_mm"

    # Chat sessions (no agent_slug) keep the chat scope: no --agent-slug arg.
    servers = build_mcp_servers_for_session(42, 42)
    condor = next(s for s in servers if s["name"] == "condor")
    assert "--agent-slug" not in condor["args"]


def test_numeric_credentials_reach_the_subprocess_as_strings(monkeypatch):
    """YAML ``password: 123`` loads as int; spawning an MCP subprocess needs str.

    The credentials ride the ``env`` channel rather than argv (SEC-095), so both
    channels are checked: an int anywhere in the args list or the env mapping
    trips pydantic-ai's StdioServerParameters validation, which is why this only
    ever surfaced on the lmstudio:/ollama:/openrouter: backends.
    """
    import config_manager
    from handlers.agents._shared import build_mcp_servers_for_session

    class _NumericPasswordServer:
        def get_accessible_servers(self, user_id):
            return ["local"]

        def has_server_access(self, user_id, name, permission=None):
            return True

        def get_server(self, name):
            return {
                "host": "localhost",
                "port": 8000,
                "username": 999,
                "password": 123,
            }

        def has_server_access(self, user_id, server_name, *args, **kwargs):
            # SEC-178: the resolver holds every candidate to reach, not just
            # existence. This double owns the server it hands out.
            return True

    monkeypatch.setattr(
        config_manager, "get_config_manager", lambda: _NumericPasswordServer()
    )
    monkeypatch.setattr(config_manager, "get_effective_server", lambda *a, **k: "local")

    servers = build_mcp_servers_for_session(42, 42)
    for server in servers:
        assert all(isinstance(a, str) for a in server["args"]), server["name"]
        for entry in server["env"]:
            assert isinstance(entry["name"], str)
            assert isinstance(entry["value"], str), f"{server['name']}/{entry['name']}"

    hb = next(s for s in servers if s["name"] == "mcp-hummingbot")
    env = {e["name"]: e["value"] for e in hb["env"]}
    assert env["HUMMINGBOT_API_USERNAME"] == "999"
    assert env["HUMMINGBOT_API_PASSWORD"] == "123"
