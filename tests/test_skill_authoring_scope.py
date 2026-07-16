"""§5.5: skill mutations derive their target from the session's invocation
context — a launched agent cannot write another agent's skills; the chat
coordinator may target explicitly. (Routines have the same guard, tested in
test_routine_authoring_scope.py.)"""

import asyncio

import pytest

from mcp_servers.condor.settings import settings
from mcp_servers.condor.tools import skills as skills_tool


@pytest.fixture
def agents(tmp_path, monkeypatch):
    import condor.agents.agent as agent_mod
    import condor.memory.paths as memory_paths

    # SkillStore resolves agents/{slug}/skills from paths._PROJECT_ROOT, not
    # the agent store root — patch BOTH or the test writes into the real
    # agents/ tree.
    agents_root = tmp_path / "agents"
    monkeypatch.setattr(agent_mod, "_DATA_ROOT", agents_root)
    monkeypatch.setattr(memory_paths, "_PROJECT_ROOT", tmp_path)
    for slug in ("owner_agent", "victim_agent"):
        d = agents_root / slug
        d.mkdir(parents=True)
        (d / "AGENT.md").write_text(f"---\nname: {slug}\n---\n\nBody.\n")
    return agents_root


def _create(agent_slug=None):
    return asyncio.run(
        skills_tool.manage_skill(
            action="create",
            name="probe",
            description="what and when",
            body="steps",
            agent_slug=agent_slug,
        )
    )


def test_cross_agent_skill_mutation_denied(agents, monkeypatch):
    monkeypatch.setattr(settings, "agent_slug", "owner_agent", raising=False)
    out = _create(agent_slug="victim_agent")
    assert "scoped to your own agent" in out.get("error", "")
    assert not (agents / "victim_agent" / "skills").exists()

    # delete/edit equally denied
    out = asyncio.run(
        skills_tool.manage_skill(
            action="delete", name="x", agent_slug="victim_agent"
        )
    )
    assert "scoped to your own agent" in out.get("error", "")


def test_own_agent_skill_mutation_allowed(agents, monkeypatch):
    monkeypatch.setattr(settings, "agent_slug", "owner_agent", raising=False)
    out = _create(agent_slug="owner_agent")
    assert "error" not in out


def test_chat_coordinator_may_target_explicitly(agents, monkeypatch):
    monkeypatch.setattr(settings, "agent_slug", "", raising=False)
    out = _create(agent_slug="victim_agent")
    assert "error" not in out


def test_reads_may_cross_agents(agents, monkeypatch):
    """Reads are not mutations: an agent may READ another agent's skill."""
    monkeypatch.setattr(settings, "agent_slug", "", raising=False)
    _create(agent_slug="victim_agent")
    monkeypatch.setattr(settings, "agent_slug", "owner_agent", raising=False)
    out = asyncio.run(
        skills_tool.manage_skill(
            action="read", name="probe", agent_slug="victim_agent"
        )
    )
    assert "error" not in out
