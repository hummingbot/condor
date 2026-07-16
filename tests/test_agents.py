"""Unit tests for the unified Agent model (AGENT.md is the ONE spec, §5.3).

Covers the FEAT-004 capabilities derived from a definition (consultable vs
runnable), the risk-baseline requirement for trading agents, and the shared
per-Agent skill library.
"""

import asyncio

from condor.agents import agent as agent_module
from condor.agents.agent import AgentStore


def _write_agent(root, slug, *, body="Body.", **frontmatter):
    """Write an AGENT.md under root/<slug>/."""
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    fm = "\n".join(f"{k}: {v}" for k, v in frontmatter.items())
    (d / "AGENT.md").write_text(f"---\n{fm}\n---\n\n{body}\n")
    return d


def _patch_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        "condor.agents.service.agents_data_root", lambda: tmp_path
    )


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
        risk_limits={"max_position_size_quote": 500, "max_open_executors": 3},
        denomination="USDC",
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


def test_trading_agent_requires_risk_baseline(tmp_path, monkeypatch):
    """The AGENT.md defines what the agent does — an agent that can trade
    (declares manage_executors, or no tool scope at all) without a risk
    baseline is an incomplete definition and must not save. {0, 0} is the
    explicit read-only statement and is accepted."""
    import pytest

    _patch_roots(monkeypatch, tmp_path)
    store = AgentStore()

    # Empty tool list = unrestricted = trading — needs a baseline.
    with pytest.raises(ValueError, match="risk_limits"):
        store.create(name="No Budget", instructions="x")

    # Declaring manage_executors — needs a baseline.
    with pytest.raises(ValueError, match="risk_limits"):
        store.create(
            name="Trader", instructions="x", tools=["manage_executors"]
        )

    # Explicit read-only baseline is a complete definition.
    ro = store.create(
        name="Read Only",
        instructions="x",
        risk_limits={"max_position_size_quote": 0, "max_open_executors": 0},
        denomination="USD",
    )
    assert store.get(ro.slug).risk_limits["max_open_executors"] == 0

    # Specialists with a declared non-trading scope need no baseline.
    s = store.create(
        name="Specialist", instructions="x", tools=["manage_routines"]
    )
    assert store.get(s.slug).risk_limits == {}
    assert store.get(s.slug).can_trade is False

    # update() goes through the same check: stripping the baseline must fail.
    ro.risk_limits = {}
    with pytest.raises(ValueError, match="risk_limits"):
        store.update(ro)


def test_can_trade_derivation():
    from condor.agents.agent import Agent

    assert Agent(slug="a", name="a", tools=[]).can_trade is True  # unrestricted
    assert Agent(slug="a", name="a", tools=["manage_executors"]).can_trade is True
    assert (
        Agent(slug="a", name="a", tools=["mcp__condor__manage_executors"]).can_trade
        is True
    )
    assert Agent(slug="a", name="a", tools=["manage_routines"]).can_trade is False


# ── MCP tools: explicit agent CRUD (the AGENT.md identity, §8) ──


def _stub_control(monkeypatch):
    """Route the MCP tool's call_control through the REAL agent handlers
    (tool → handler → AgentService → store), no socket involved."""
    import inspect

    from condor.control.handlers import build_agent_handlers
    from mcp_servers.condor.tools import trading_agent as ta

    handlers = build_agent_handlers()

    async def fake_call_control(method, params=None, timeout=60):
        result = handlers[method](**(params or {}))
        if inspect.isawaitable(result):
            result = await result
        return result

    monkeypatch.setattr(ta, "call_control", fake_call_control)


def test_explicit_agent_crud_tools(tmp_path, monkeypatch):
    """create_agent/get_agent/update_agent/delete_agent explicit tools."""
    from mcp_servers.condor.settings import settings
    from mcp_servers.condor.tools import trading_agent as ta

    _patch_roots(monkeypatch, tmp_path)
    _stub_control(monkeypatch)

    created = asyncio.run(
        ta.create_agent(
            name="Risk Sentry",
            description="watches drawdown",
            instructions="identity + domain knowledge",
            agent_key="ollama:qwen3:32b",
            when_to_consult="when sizing a position",
            tools=["get_market_data"],
            risk_limits={"max_position_size_quote": 0, "max_open_executors": 0},
            denomination="USD",
        )
    )
    assert created["created"] is True
    assert created["agent_slug"] == "risk_sentry"
    assert created["consultable"] is True  # has a consult trigger

    got = asyncio.run(
        ta.get_agent("risk_sentry")
    )
    assert got["instructions"].strip() == "identity + domain knowledge"
    assert got["tools"] == ["get_market_data"]

    updated = asyncio.run(
        ta.update_agent(
            "risk_sentry",
            instructions="new body",
            when_to_consult="",  # demote from consultable
        )
    )
    assert updated["updated"] is True
    assert updated["consultable"] is False
    assert (
        asyncio.run(
            ta.get_agent("risk_sentry")
        )["instructions"].strip()
        == "new body"
    )

    listed = asyncio.run(ta.list_agents())["agents"]
    assert any(a["slug"] == "risk_sentry" for a in listed)

    deleted = asyncio.run(ta.delete_agent("risk_sentry"))
    assert deleted["deleted"] is True and deleted["tombstoned"] is True
    # Tombstone, not erase: history stays readable, but the agent leaves the
    # default listing and the slug is reserved.
    assert "instructions" in asyncio.run(
        ta.get_agent("risk_sentry")
    )
    listed_after = asyncio.run(ta.list_agents())["agents"]
    assert not any(a["slug"] == "risk_sentry" for a in listed_after)


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
    """An Agent's skills/<name>/SKILL.md library is readable and editable."""
    from condor.memory import paths as paths_module
    from condor.memory.skills import SkillStore

    monkeypatch.setattr(paths_module, "_PROJECT_ROOT", tmp_path)
    skill_dir = tmp_path / "agents" / "executor_manager" / "skills" / "size-grid"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        '---\nname: size-grid\ndescription: "Size a grid. Use when before a grid."\n'
        'metadata: {"condor-source": "builtin"}\n---\n\nSteps.\n'
    )

    store = SkillStore(agent_slug="executor_manager")
    assert "[size-grid] Size a grid. Use when before a grid." in store.list_index()
    read = store.read("size grid")
    assert read is not None and "Use when before a grid" in read["description"]

    assert store.create("stop or widen", "d2. Use when underwater.", "steps")["saved"]
    assert "[stop-or-widen] d2. Use when underwater." in store.list_index()
    assert store.edit("size-grid", description="updated")["description"] == "updated"
    assert store.delete("stop-or-widen")["deleted"] is True
    assert "stop-or-widen" not in store.list_index()


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


def _run_create_session(monkeypatch, **kwargs):
    """Invoke get_or_create_session with the ACP client + context stubbed out."""
    from condor.agents import chat_session as session_module

    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr(session_module, "ACPClient", _FakeACPClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    monkeypatch.setattr(
        session_module, "build_mcp_servers_for_session", lambda *a, **k: []
    )
    _FakeACPClient.last_extra_env = None
    asyncio.run(session_module.get_or_create_session(agent_key="claude-code", **kwargs))
    return _FakeACPClient.last_extra_env


def test_extra_env_carries_no_identity(monkeypatch):
    """§4.3: no CONDOR_USER_ID/CONDOR_CHAT_ID env is injected — identity is
    gone from the MCP subprocess wiring."""
    env = _run_create_session(monkeypatch, chat_id=555)
    assert "CONDOR_USER_ID" not in (env or {})
    assert "CONDOR_CHAT_ID" not in (env or {})


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


def test_condor_brain_loads_from_repo_root():
    """The chat brain is repo-root CONDOR.md (refactor-06): frontmatter + body."""
    from condor.agents.context import _load_condor

    meta, body = _load_condor()
    assert meta.get("label") == "Condor"
    assert "coordinator" in body or "Condor" in body


def test_session_mcp_servers_carry_agent_slug():
    """Agent runs must scope the condor MCP tools to the agent's own
    memory/skills via --agent-slug — without it, routine_builder-style agents
    silently read/write the CHAT's stores (e.g. 'routine_cookbook not found')."""
    from condor.agents.context import build_mcp_servers_for_session

    servers = build_mcp_servers_for_session(agent_slug="routine_builder")
    condor = next(s for s in servers if s["name"] == "condor")
    args = condor["args"]
    assert "--agent-slug" in args
    assert args[args.index("--agent-slug") + 1] == "routine_builder"

    # Chat sessions (no agent_slug) keep the chat scope: no --agent-slug arg.
    servers = build_mcp_servers_for_session()
    condor = next(s for s in servers if s["name"] == "condor")
    assert "--agent-slug" not in condor["args"]
