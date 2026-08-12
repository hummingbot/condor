"""Which library does a `manage_skill` / `manage_routines` write land in?

Skills and routines are both keyed by the **agent** — there is no per-strategy
library — so the target parameter is `agent`. The old `strategy_id` spelling
stays accepted (older MCP hosts and playbooks name it) and resolves a composite
"agent_slug.strategy_slug" key to its *owning agent*.

These tests pin the resolution, because getting it wrong is silent: the skill
lands in Condor's own library and only the chat ever sees it.
"""

import asyncio
from dataclasses import dataclass

import pytest

from condor.agents import agent as agent_module
from condor.agents import strategy as strategy_module
from condor.memory import paths as paths_module
from mcp_servers.condor.settings import settings
from mcp_servers.condor.tools import skills as skills_tool

AGENT_SLUG = "market_making_expert"
STRATEGY_KEY = f"{AGENT_SLUG}.hip_3_mm_operator"


@dataclass
class _FakeStrategy:
    agent_slug: str


class _FakeAgentStore:
    """Only AGENT_SLUG exists."""

    def get(self, slug):
        return object() if slug == AGENT_SLUG else None


class _FakeStrategyStore:
    """Only STRATEGY_KEY exists, owned by AGENT_SLUG."""

    def get_by_key(self, key):
        return _FakeStrategy(AGENT_SLUG) if key == STRATEGY_KEY else None


@pytest.fixture
def chat(tmp_path, monkeypatch):
    """Run as the chat condor (no bound specialist), rooted at a tmp project."""
    monkeypatch.setattr(paths_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(agent_module, "AgentStore", _FakeAgentStore)
    monkeypatch.setattr(strategy_module, "StrategyStore", _FakeStrategyStore)
    monkeypatch.setattr(settings, "agent_slug", "")
    return tmp_path


def _create(**kwargs):
    return asyncio.run(
        skills_tool.manage_skill(
            "create",
            name="band_walk",
            description="d",
            when_to_use="w",
            body="Steps.",
            **kwargs,
        )
    )


def test_agent_targets_that_agents_library(chat):
    _create(agent=AGENT_SLUG)

    assert (chat / "agents" / AGENT_SLUG / "skills" / "band_walk" / "SKILL.md").exists()
    # Emphatically NOT the chat's own library — that is the wrong-agent bug.
    assert not (chat / "agents" / "condor" / "skills" / "band_walk").exists()


def test_without_a_target_the_chat_writes_its_own(chat):
    _create()

    assert (chat / "agents" / "condor" / "skills" / "band_walk" / "SKILL.md").exists()


def test_strategy_id_alias_resolves_to_the_owning_agent(chat):
    """The deprecated spelling still works — and a strategy key lands one level up."""
    _create(strategy_id=STRATEGY_KEY)

    assert (chat / "agents" / AGENT_SLUG / "skills" / "band_walk" / "SKILL.md").exists()


def test_agent_wins_over_the_deprecated_alias(chat):
    _create(agent=AGENT_SLUG, strategy_id="nonexistent.thing")

    assert (chat / "agents" / AGENT_SLUG / "skills" / "band_walk" / "SKILL.md").exists()


def test_unknown_target_errors_instead_of_writing_to_the_chat(chat):
    result = _create(agent="no_such_agent")

    assert "error" in result
    assert "no_such_agent" in result["error"]
    assert not (chat / "agents" / "condor" / "skills" / "band_walk").exists()


def test_a_launched_agent_writes_only_to_its_own_library(tmp_path, monkeypatch):
    """A specialist ignores nothing and targets nobody else: its slug is the scope."""
    monkeypatch.setattr(paths_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(settings, "agent_slug", AGENT_SLUG)

    _create()

    assert (
        tmp_path / "agents" / AGENT_SLUG / "skills" / "band_walk" / "SKILL.md"
    ).exists()


def test_an_agent_cannot_publish_to_every_agent(tmp_path, monkeypatch):
    """`shared` is honored only in Condor's library — an agent may not publish."""
    monkeypatch.setattr(paths_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(settings, "agent_slug", AGENT_SLUG)

    result = _create(shared=True)

    assert "shared" not in result
    body = (
        tmp_path / "agents" / AGENT_SLUG / "skills" / "band_walk" / "SKILL.md"
    ).read_text()
    assert "shared" not in body


def test_routines_resolve_the_same_way(chat):
    """`manage_routines` shares the rule: agent level, alias accepted."""
    from mcp_servers.condor.tools import routines as routines_tool

    # Routines resolve against the repo's own root, so pin the shape and the
    # equivalence of the two spellings rather than an absolute path.
    by_agent = routines_tool._get_agent_routines_dir(AGENT_SLUG)
    assert by_agent is not None
    assert by_agent.parts[-3:] == ("agents", AGENT_SLUG, "routines")
    assert routines_tool._get_agent_routines_dir(STRATEGY_KEY) == by_agent
    assert routines_tool._get_agent_routines_dir("no_such_agent") is None
