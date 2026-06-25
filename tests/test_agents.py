"""Unit tests for the unified Agent model: AgentStore + Strategy sub-resource.

Covers the FEAT-004 capabilities derived from a definition (consultable vs
loopeable), the strategy CRUD scoped under an Agent, the shared per-Agent skill
library, and the pydantic-ai tool allowlist.
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


# ── Agent discovery + derived capabilities ──


def test_agent_discovery_and_consultable_derived(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    # Consult-capable Agent: trigger + pydantic-ai model.
    _write_agent(
        tmp_path,
        "executor_manager",
        name="Executor Manager",
        description="Manages executors",
        when_to_consult="When deploying or tuning executors",
        agent_key="ollama:qwen3:32b",
        body="Body for executor_manager.",
    )
    # Loop-only Agent: no consult trigger => NOT consultable.
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
    assert em.consultable is True

    brig = store.get("brigado")
    assert brig is not None
    assert brig.consultable is False  # empty when_to_consult => not consultable

    index = store.list_consultable_index()
    assert "[executor_manager] When deploying or tuning executors" in index
    assert "brigado" not in index  # only consultable agents appear


def test_consultable_on_any_model(tmp_path, monkeypatch):
    """A consult trigger alone makes an agent consultable, regardless of model.

    An ACP key (claude-code) can't enforce the tools allowlist, but the consult
    still runs (unrestricted, mutations confirmation-gated) — so it IS consultable.
    """
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(
        tmp_path,
        "acp_consult",
        name="ACP",
        when_to_consult="whenever",
        agent_key="claude-code",  # ACP model
    )
    store = AgentStore()
    assert store.get("acp_consult").consultable is True
    assert "acp_consult" in store.list_consultable_index()


def test_missing_agent_returns_none(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    assert AgentStore().get("nope") is None
    assert AgentStore().get("") is None
    assert AgentStore().list_consultable_index() == ""


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


# ── MCP tool: manage_trading_agent agent CRUD (the AGENT.md identity) ──


def test_manage_trading_agent_agent_crud(tmp_path, monkeypatch):
    """create_agent/get_agent/update_agent/delete_agent through the MCP tool."""
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import trading_agent as ta

    _patch_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "user_id", 7, raising=False)

    created = asyncio.run(
        ta.manage_trading_agent(
            action="create_agent",
            name="Risk Sentry",
            description="watches drawdown",
            instructions="identity + domain knowledge",
            agent_key="ollama:qwen3:32b",
            when_to_consult="when sizing a position",
            tools=["get_market_data"],
        )
    )
    assert created["created"] is True
    assert created["agent_slug"] == "risk_sentry"
    assert created["consultable"] is True  # has a consult trigger

    got = asyncio.run(
        ta.manage_trading_agent(action="get_agent", agent_slug="risk_sentry")
    )
    assert got["instructions"].strip() == "identity + domain knowledge"
    assert got["tools"] == ["get_market_data"]

    updated = asyncio.run(
        ta.manage_trading_agent(
            action="update_agent",
            agent_slug="risk_sentry",
            instructions="new body",
            when_to_consult="",  # demote from consultable
        )
    )
    assert updated["updated"] is True
    assert updated["consultable"] is False
    assert (
        asyncio.run(
            ta.manage_trading_agent(action="get_agent", agent_slug="risk_sentry")
        )["instructions"].strip()
        == "new body"
    )

    listed = asyncio.run(ta.manage_trading_agent(action="list_agent_definitions"))[
        "agents"
    ]
    assert any(a["slug"] == "risk_sentry" for a in listed)

    assert asyncio.run(
        ta.manage_trading_agent(action="delete_agent", agent_slug="risk_sentry")
    ) == {"deleted": True}
    assert "error" in asyncio.run(
        ta.manage_trading_agent(action="get_agent", agent_slug="risk_sentry")
    )


def test_manage_trading_agent_delete_refuses_with_strategy(tmp_path, monkeypatch):
    """delete_agent must refuse while the agent still owns a strategy."""
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import trading_agent as ta

    _patch_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "user_id", 7, raising=False)

    asyncio.run(ta.manage_trading_agent(action="create_agent", name="Looper"))
    StrategyStore().create(agent_slug="looper", name="Tick", instructions="x")

    refused = asyncio.run(
        ta.manage_trading_agent(action="delete_agent", agent_slug="looper")
    )
    assert "error" in refused and "strateg" in refused["error"].lower()


def test_create_strategy_requires_existing_agent(tmp_path, monkeypatch):
    """A strategy can't be created under an agent that does not exist."""
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import trading_agent as ta

    _patch_roots(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "user_id", 7, raising=False)

    result = asyncio.run(
        ta.manage_trading_agent(
            action="create_strategy",
            agent_slug="ghost",
            name="S",
            instructions="x",
        )
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


def test_agent_skill_library_read_and_edit(tmp_path, monkeypatch):
    """An Agent's skills/<slug>/SKILL.md library is readable and editable."""
    from condor.memory import paths as paths_module
    from condor.memory.skills import SkillStore

    monkeypatch.setattr(paths_module, "_PROJECT_ROOT", tmp_path)
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


def test_assistant_routines_dir_layout():
    from routines.base import assistant_routines_dir

    assert assistant_routines_dir(None).parts[-3:] == (
        "assistants",
        "condor",
        "routines",
    )
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


def _run_create_session(monkeypatch, **kwargs):
    """Invoke get_or_create_session with the ACP client + context stubbed out."""
    from handlers.agents import session as session_module

    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr(session_module, "ACPClient", _FakeACPClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(
        session_module, "build_mcp_servers_for_session", lambda *a, **k: []
    )
    _FakeACPClient.last_extra_env = None
    asyncio.run(session_module.get_or_create_session(agent_key="claude-code", **kwargs))
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

    cmd, env = resolve_acp("claude-acp:opus")
    assert cmd == "claude-agent-acp"
    assert env == {"ANTHROPIC_MODEL": "opus"}

    _, env = resolve_acp("claude-acp:claude-opus-4-8")
    assert env == {"ANTHROPIC_MODEL": "claude-opus-4-8"}

    assert resolve_acp("claude-code") == ("claude-agent-acp", {})
    assert resolve_acp("claude-acp") == ("claude-agent-acp", {})

    cmd, env = resolve_acp("gemini")
    assert "gemini" in cmd and env == {}


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
        config_manager,
        "get_config_manager",
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
        config_manager,
        "get_config_manager",
        lambda: SimpleNamespace(has_server_access=lambda uid, name: True),
    )

    # Caller is 42 but tries to impersonate user 999.
    req = _consult_request(server_name="X", user_id=999)
    result = asyncio.run(agents_module.consult_agent("em", req, user=_web_user(42)))

    assert result["answer"] == "ok"
    assert seen["user_id"] == 42  # caller's id, not the 999 override
    assert seen["server_name"] == "X"
