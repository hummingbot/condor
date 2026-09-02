"""The house rules reach every session, from one file (FEAT-095).

The rules an operator wants to hold for *all* agents ("read the playbook before
you act on it") used to have twelve places to live: each AGENT.md, plus a Python
constant for ticks and three hand-written base strings for the chat, consult and
worker seats. This pins the one file instead — that it is read at both prompt
surfaces, that an agent may shadow it, and that its absence is silent rather
than a broken tick.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from condor.agents import agent as agent_module
from condor.agents.prompts import (
    CORE_RULES_HEADER,
    build_tick_prompt,
    core_rules_section,
    load_core_rules,
)
from condor.paths import agents_root

RULES = "- Always read the playbook first."
OVERRIDE = "- Brigado answers only in BRL."


@pytest.fixture
def defaults():
    """An empty registry with the shared rulebook in it."""
    root = agents_root()
    (root / "_defaults").mkdir(parents=True, exist_ok=True)
    (root / "_defaults" / "core_rules.md").write_text(RULES, "utf-8")
    return root


def _write_agent(root, slug: str) -> None:
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "AGENT.md").write_text(f"---\nname: {slug}\n---\n\nBody.\n", "utf-8")


# ── The loader ──


def test_the_rules_come_from_the_shared_file(defaults):
    assert load_core_rules() == RULES
    assert load_core_rules("brigado") == RULES


def test_an_agent_can_override_the_house_rules(defaults):
    (defaults / "brigado").mkdir(parents=True, exist_ok=True)
    (defaults / "brigado" / "core_rules.md").write_text(OVERRIDE, "utf-8")

    assert load_core_rules("brigado") == OVERRIDE
    # Its neighbours, and the chat seat, still read the default.
    assert load_core_rules("backpack_mm") == RULES
    assert load_core_rules() == RULES


def test_frontmatter_is_stripped_off_the_body(defaults):
    (defaults / "_defaults" / "core_rules.md").write_text(
        f"---\n# an operator note\n---\n{RULES}\n", "utf-8"
    )

    assert load_core_rules() == RULES


def test_no_rulebook_is_silent_not_a_crash():
    # No file was planted: the isolated agents root is empty.
    assert load_core_rules() == ""
    assert load_core_rules("brigado") == ""
    assert core_rules_section() == ""


def test_an_unreadable_rulebook_is_silent_too(defaults):
    path = defaults / "_defaults" / "core_rules.md"
    path.unlink()
    path.mkdir()  # a directory where the file should be: read_text raises

    assert load_core_rules() == ""


# ── Surface 1: the loop tick ──


def _tick_prompt(slug: str = "brigado") -> str:
    agent = SimpleNamespace(instructions="", agent_key="claude-code", slug=slug)
    strategy = SimpleNamespace(
        instructions="Do the thing.",
        agent_key="claude-code",
        slug="grid",
        agent_slug=slug,
        dir=None,
    )
    return build_tick_prompt(
        agent=agent,
        strategy=strategy,
        config={"execution_mode": "loop"},
        core_data={},
        learnings="",
        summary="",
        recent_decisions="",
        risk_state={},
        cached_routines_section="",
    )


def test_the_tick_prompt_carries_the_rules(defaults):
    prompt = _tick_prompt()

    assert CORE_RULES_HEADER in prompt
    assert RULES in prompt


def test_the_tick_prompt_carries_the_agents_own_rules(defaults):
    (defaults / "brigado").mkdir(parents=True, exist_ok=True)
    (defaults / "brigado" / "core_rules.md").write_text(OVERRIDE, "utf-8")

    prompt = _tick_prompt()
    assert OVERRIDE in prompt
    assert RULES not in prompt


def test_the_rules_outrank_the_agents_own_identity(defaults):
    """Position is the point: an agent cannot talk its way out of the rules."""
    prompt = _tick_prompt()

    assert prompt.index(CORE_RULES_HEADER) < prompt.index("[STRATEGY INSTRUCTIONS]")


def test_a_tick_without_a_rulebook_still_builds():
    assert CORE_RULES_HEADER not in _tick_prompt()


# ── Surface 2: chat / consult / worker ──


def _instructions(monkeypatch, slug: str) -> str:
    from mcp_servers.condor import server
    from mcp_servers.condor.settings import settings

    monkeypatch.setattr(agent_module, "_DATA_ROOT", agents_root())
    monkeypatch.setattr(settings, "agent_slug", slug)
    return server._build_instructions()


def test_the_chat_seat_carries_the_rules(defaults, monkeypatch):
    text = _instructions(monkeypatch, "")

    assert CORE_RULES_HEADER in text
    assert RULES in text


def test_a_specialist_seat_carries_its_own_rules(defaults, monkeypatch):
    _write_agent(defaults, "brigado")
    (defaults / "brigado" / "core_rules.md").write_text(OVERRIDE, "utf-8")

    text = _instructions(monkeypatch, "brigado")
    assert OVERRIDE in text
    assert RULES not in text


def test_instructions_build_without_a_rulebook(monkeypatch):
    assert CORE_RULES_HEADER not in _instructions(monkeypatch, "")


# ── The rulebook that actually ships ──


def test_the_shipped_rulebook_is_real():
    """The repo's own file, not the tmp one every other test here plants."""
    from pathlib import Path

    import condor

    path = (
        Path(condor.__file__).parent.parent / "agents" / "_defaults" / "core_rules.md"
    )
    body = path.read_text("utf-8")

    assert "manage_skill" in body
    assert not body.strip().endswith("---"), "frontmatter only, no body"
