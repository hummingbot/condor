"""A stock Agent's ``tools:`` allowlist binds on every backend, so it must name
what the agent is actually told to call.

It used to bind only pydantic-ai seats. On a Claude seat it was decoration, and it
drifted unnoticed: four of the six stock lists omitted tools their own playbooks
call — ``solana_dex_lp_expert``'s shutdown notifies the owner with a tool its list never named. Now
the spawner never mounts what a list leaves out (``toolsets.seat_mutes``), so a
missing name is a step the agent cannot take. These pin every stock list against
the two sources that say what an agent needs.
"""

import re
from pathlib import Path

import pytest
import yaml

from mcp_servers.condor import profiles as condor_profiles
from mcp_servers.hummingbot_api import profiles as hummingbot_profiles

AGENTS = Path(__file__).resolve().parent.parent / "agents"

#: What an attended specialist can mount at all.
AGENT_RING = set(condor_profiles.PROFILE_TOOLS["agent"]) | set(
    hummingbot_profiles.PROFILE_TOOLS["agent"]
)

#: What the framework skills every agent inherits (``agent_framework``,
#: ``loop_builder``, ``operate_your_loop``, ``skill_authoring``,
#: ``self_improve``) and the tick's house prompt tell *any* agent to call.
FRAMEWORK = {
    "delegate",
    "send_notification",
    "run_code",
    "manage_memory",
    "manage_skill",
    "manage_routines",
    "manage_agent_controllers",
    "trading_agent_journal_read",
    "trading_agent_journal_write",
    "manage_agents",
    "manage_loops",
    "control_agent",
    "get_available_models",
}

#: Tool names an agent's own files mention without calling them.
NOT_CALLS: dict[str, set[str]] = {}


def _frontmatter(path: Path) -> dict:
    return yaml.safe_load(path.read_text().split("---")[1]) or {}


def _allowlisted_agents() -> list[str]:
    return sorted(
        path.parent.name
        for path in AGENTS.glob("*/AGENT.md")
        if _frontmatter(path).get("tools")
    )


def _allowlist(slug: str) -> set[str]:
    return set(_frontmatter(AGENTS / slug / "AGENT.md")["tools"])


def _named_in_own_files(slug: str) -> dict[str, list[str]]:
    """``{tool: [file, ...]}`` for every ring tool the agent's own markdown names."""
    home = AGENTS / slug
    found: dict[str, list[str]] = {}
    for path in sorted(home.rglob("*.md")):
        text = path.read_text()
        for tool in AGENT_RING:
            if re.search(rf"\b{tool}\b", text):
                found.setdefault(tool, []).append(str(path.relative_to(home)))
    return found


ALLOWLISTED = _allowlisted_agents()


def test_there_are_lists_to_check():
    assert ALLOWLISTED, "no stock agent names an allowlist any more"


@pytest.mark.parametrize("slug", ALLOWLISTED)
def test_the_list_names_only_tools_the_seat_can_mount(slug):
    """A typo, or a tool no ring mounts any more, is a name that grants nothing."""
    assert _allowlist(slug) <= AGENT_RING, sorted(_allowlist(slug) - AGENT_RING)


@pytest.mark.parametrize("slug", ALLOWLISTED)
def test_the_list_carries_the_framework_family(slug):
    missing = FRAMEWORK - _allowlist(slug)
    assert not missing, f"{slug} inherits playbooks that call {sorted(missing)}"


@pytest.mark.parametrize("slug", ALLOWLISTED)
def test_the_list_covers_what_the_agents_own_files_call(slug):
    exempt = NOT_CALLS.get(slug, set())
    missing = {
        tool: files
        for tool, files in _named_in_own_files(slug).items()
        if tool not in _allowlist(slug) and tool not in exempt
    }
    assert not missing, f"{slug}'s own files call tools its list leaves out: {missing}"


def test_every_exemption_is_still_needed():
    """An exemption whose mention is gone would hide the next real one."""
    for slug, exempt in NOT_CALLS.items():
        named = _named_in_own_files(slug)
        stale = {t for t in exempt if t not in named or t in _allowlist(slug)}
        assert not stale, f"drop {sorted(stale)} from NOT_CALLS[{slug!r}]"
