"""The same Agent says the same thing about its memory, whichever door you use.

ARCH-099: the ``[DOMAIN MEMORY]`` / ``[DOMAIN SKILLS]`` block used to be written
out twice — once in ``binding.agent_identity_context`` (a chatted Agent) and once
in ``_shared.build_agent_context`` (a consulted one). Editing one copy silently
made the Agent behave differently depending on how it was reached. Both now
compose :func:`condor.memory.domain_context`, and these tests fail if a copy ever
comes back.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from condor.memory import domain_context
from condor.memory import paths as paths_module
from condor.memory.skills import SkillStore
from condor.memory.store import MemoryStore

_SLUG = "backpack_mm"
_USER_ID = 7

_PROJECT_ROOT = Path(__file__).parent.parent


@pytest.fixture
def stocked_agent(tmp_path, monkeypatch):
    """An agent with one memory and one skill, in stores under a tmp root."""
    monkeypatch.setattr(paths_module, "_PROJECT_ROOT", tmp_path)

    MemoryStore(_USER_ID, _SLUG).write(
        name="Funding window",
        content="Funding pays on the hour.",
        description="When funding settles",
    )
    SkillStore(_SLUG).create(
        name="Rebalance",
        description="Rebalance the book",
        when_to_use="Inventory is skewed",
        body="1. Measure skew.\n2. Quote against it.\n",
    )
    return tmp_path


def _domain_part(text: str) -> str:
    """The memory/skills block of a built prompt, stripped of what wraps it."""
    start = text.index("[DOMAIN MEMORY")
    end = text.find("[CONSULT REQUEST]")
    return text[start:] if end == -1 else text[start:end].rstrip()


def test_chatted_and_consulted_agents_get_identical_domain_sections(stocked_agent):
    from condor.runtime import binding
    from handlers.agents._shared import build_agent_context

    chatted = binding.agent_identity_context(
        _SLUG, user_id=_USER_ID, instructions="Domain knowledge.", label="Backpack MM"
    )
    consulted = build_agent_context(
        SimpleNamespace(slug=_SLUG, instructions="Domain knowledge."),
        user_id=_USER_ID,
        task="Why is the spread wide?",
    )

    # Byte-identical, not merely "both mention memory": the point of the shared
    # builder is that an edit to one instruction reaches both doors at once.
    assert _domain_part(chatted) == _domain_part(consulted)
    assert _domain_part(chatted) == "\n\n".join(domain_context(_SLUG, _USER_ID))


def test_both_builders_carry_the_memory_and_skills_indexes(stocked_agent):
    from condor.runtime import binding
    from handlers.agents._shared import build_agent_context

    chatted = binding.agent_identity_context(
        _SLUG, user_id=_USER_ID, instructions="Domain knowledge.", label="Backpack MM"
    )
    consulted = build_agent_context(
        SimpleNamespace(slug=_SLUG, instructions="Domain knowledge."),
        user_id=_USER_ID,
        task="Why is the spread wide?",
    )

    for built in (chatted, consulted):
        assert "[DOMAIN MEMORY — what you remember in this domain]" in built
        assert "When funding settles" in built
        assert "[DOMAIN SKILLS — playbooks you can follow]" in built
        assert "Inventory is skewed" in built

    # Each builder still owns its own wrapper.
    from condor.agents.agent import identity_header

    assert chatted.startswith(identity_header(_SLUG, "Backpack MM"))
    assert "[CONSULT REQUEST]\nWhy is the spread wide?" in consulted
    assert "[CONSULT REQUEST]" not in chatted


def test_an_empty_store_contributes_no_sections(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_module, "_PROJECT_ROOT", tmp_path)
    assert domain_context("nobody", _USER_ID) == []


def test_the_section_headers_are_written_in_exactly_one_module():
    """A reintroduced copy is caught here even if it never diverges."""
    headers = (
        "[DOMAIN MEMORY — what you remember in this domain]",
        "[DOMAIN SKILLS — playbooks you can follow]",
    )
    sources = [
        p
        for p in _PROJECT_ROOT.rglob("*.py")
        if ".venv" not in p.parts and "tests" not in p.parts
    ]

    for header in headers:
        holders = [
            p.relative_to(_PROJECT_ROOT).as_posix()
            for p in sources
            if header in p.read_text(encoding="utf-8", errors="ignore")
        ]
        assert holders == ["condor/memory/context.py"], (
            f"{header!r} should be written once, in the shared builder; found in "
            f"{holders}"
        )
