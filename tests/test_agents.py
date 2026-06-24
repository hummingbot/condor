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
    # Loop-only Agent: no trigger, ACP model => NOT consultable.
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
    assert brig.consultable is False  # ACP model + empty when_to_consult

    index = store.list_consultable_index()
    assert "[executor_manager] When deploying or tuning executors" in index
    assert "brigado" not in index  # only consultable agents appear


def test_consultable_requires_pydantic_ai(tmp_path, monkeypatch):
    """A trigger alone isn't enough — the model must be pydantic-ai."""
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(
        tmp_path,
        "acp_consult",
        name="ACP",
        when_to_consult="whenever",
        agent_key="claude-code",  # ACP => allowlist can't be enforced
    )
    assert AgentStore().get("acp_consult").consultable is False


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


# ── Shared per-Agent skill library (FEAT-003 brain) ──


def test_agent_skill_library_read_and_edit(tmp_path, monkeypatch):
    """An Agent's skills/<slug>/SKILL.md library is readable and editable."""
    from condor.memory import paths as paths_module
    from condor.memory.skills import SkillStore

    monkeypatch.setattr(paths_module, "_PROJECT_ROOT", tmp_path)
    skill_dir = (
        tmp_path / "agents" / "executor_manager" / "skills" / "size_grid"
    )
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
    asyncio.run(
        session_module.get_or_create_session(agent_key="claude-code", **kwargs)
    )
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
