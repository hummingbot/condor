"""Under ACP, a tool the session was never named is a tool it cannot call.

MCP tools reach an ACP seat deferred: it sees only the names it has been told
about and must ``ToolSearch(query="select:...")`` before it can call one. The
preload line that carries those names used to be built inside
``build_initial_context`` — the branch a chat bound to a specialist Agent never
takes (CORR-272). So a specialist had every orchestration tool mounted and
authorized, and no way to discover one: asked to stop its own running loop it
read the repo instead of calling ``control_agent``.

These tests pin the fix at the seam: one list, reached from both branches, and
still withheld from the pydantic-ai seats that auto-discover their toolset.
"""

import pytest

from condor.runtime import binding
from condor.runtime.client import bound_agent_context
from condor.runtime.context import chat_tool_preload

ACP_KEY = "claude-acp:opus"
PYDANTIC_AI_KEY = "openai:gpt-4o"

# What the attended chat seat mounts and what an agent is told to reach for.
# `control_agent` is the one the live failure needed; the rest travel with it.
ORCHESTRATION_TOOLS = (
    "mcp__condor__control_agent",
    "mcp__condor__manage_agents",
    "mcp__condor__manage_strategies",
    "mcp__condor__get_available_models",
    "mcp__condor__consult",
    "mcp__condor__delegate",
    "mcp__condor__run_code",
)


def _specialist() -> binding.SessionBinding:
    return binding.SessionBinding(
        label="Test Agent",
        agent_slug="a-slug-that-does-not-exist",
        instructions="You are a test.",
        agent_key=ACP_KEY,
    )


def test_preload_names_the_orchestration_family():
    """A name missing here is a mounted tool the seat can never find."""
    line = chat_tool_preload(ACP_KEY)

    assert "ToolSearch" in line
    for tool in ORCHESTRATION_TOOLS:
        assert tool in line, f"{tool} is mounted for this seat but never named"


def test_pydantic_ai_seat_gets_no_preload():
    """Those seats auto-discover their toolset; the line would be noise."""
    assert chat_tool_preload(PYDANTIC_AI_KEY) == ""
    assert chat_tool_preload("") == ""
    assert chat_tool_preload(None) == ""


def test_bound_specialist_hears_which_tools_it_has():
    """The specialist branch skips build_initial_context; not this."""
    context = bound_agent_context(_specialist(), user_id=1, platform="web")

    assert "ToolSearch" in context
    for tool in ORCHESTRATION_TOOLS:
        assert tool in context
    # Identity still leads: the preload is appended, never a replacement.
    assert context.index("You are a test.") < context.index("ToolSearch")


def test_explicit_agent_key_overrides_the_binding():
    """A model picked in the UI beats the Agent's configured default."""
    bound = _specialist()
    bound.agent_key = PYDANTIC_AI_KEY

    assert "ToolSearch" not in bound_agent_context(bound, 1, "web")
    assert "ToolSearch" in bound_agent_context(bound, 1, "web", ACP_KEY)


@pytest.mark.parametrize("platform", ["web", "telegram"])
def test_bound_pydantic_ai_specialist_stays_clean(platform):
    """Unchanged behavior for the seats that never needed the hint."""
    bound = _specialist()
    bound.agent_key = PYDANTIC_AI_KEY

    assert "ToolSearch" not in bound_agent_context(bound, 1, platform)


def test_both_chat_branches_share_one_list():
    """Defined twice, the two branches drift and only one seat notices."""
    from pathlib import Path

    from condor.runtime import context as ctx

    source = Path(ctx.__file__).read_text()
    assert source.count("mcp__condor__control_agent") == 1
