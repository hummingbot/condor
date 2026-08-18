"""The ```chart fence is a contract between a prompt and a React component.

The agent is told the shape in `_WEB_FORMATTING`; `ChartBlock.tsx` is what
actually parses it. Nothing at runtime holds the two together: a renamed field
or a tightened cap on either side does not fail anything, it just makes charts
quietly stop appearing in the chat. These tests are that seam.

They also pin the second half of the feature: a chat bound to a specialist
Agent skips `build_initial_context`, so the formatting rules have to reach it
through `bound_agent_context` or that brain never learns the fence exists.
"""

import json
import re
from pathlib import Path

import pytest

from condor.runtime import binding
from condor.runtime.client import bound_agent_context
from handlers.agents._shared import (
    _TELEGRAM_FORMATTING,
    _WEB_FORMATTING,
    platform_formatting,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CHART_BLOCK_TSX = (
    REPO_ROOT / "frontend" / "src" / "components" / "chat" / "ChartBlock.tsx"
)


def _prompt_example() -> dict:
    """The one spec the prompt shows the agent, parsed."""
    # Anchored on the `{` so the prose that names the fence in passing
    # ("draw it with a ```chart fence") cannot match instead.
    fence = re.search(r"```chart\n(\s*\{.*?)\n\s*```", _WEB_FORMATTING, re.S)
    assert fence, "_WEB_FORMATTING no longer documents a ```chart fence"
    # The example is indented inside the bullet it belongs to.
    body = "\n".join(line.strip() for line in fence.group(1).splitlines())
    return json.loads(body)


def _renderer_types() -> set[str]:
    source = CHART_BLOCK_TSX.read_text()
    literal = re.search(r"const CHART_TYPES = \[(.*?)\] as const;", source, re.S)
    assert literal, "CHART_TYPES literal not found in ChartBlock.tsx"
    return set(re.findall(r'"([^"]+)"', literal.group(1)))


def _renderer_cap(name: str) -> int:
    source = CHART_BLOCK_TSX.read_text()
    match = re.search(rf"const {name} = (\d+);", source)
    assert match, f"{name} not found in ChartBlock.tsx"
    return int(match.group(1))


def test_web_prompt_documents_the_chart_fence():
    """A model that never sees the fence never draws one."""
    assert "```chart" in _WEB_FORMATTING
    assert "CHARTS:" in _WEB_FORMATTING


def test_prompt_example_is_what_the_renderer_accepts():
    """The example is the spec: if it would not render, nothing will."""
    spec = _prompt_example()

    assert spec["type"] in _renderer_types()
    assert isinstance(spec["x"], str) and spec["x"]
    assert spec["series"] and len(spec["series"]) <= _renderer_cap("MAX_SERIES")
    assert spec["data"] and len(spec["data"]) <= _renderer_cap("MAX_ROWS")

    # Every key the spec names must exist in every row, or the chart draws
    # axes around nothing.
    keys = {spec["x"]} | {s["key"] for s in spec["series"]}
    for row in spec["data"]:
        assert keys <= set(row), f"row {row} is missing {keys - set(row)}"


def test_prompt_documents_every_type_the_renderer_supports():
    """A supported type the agent is never told about is a dead branch."""
    for chart_type in _renderer_types():
        assert (
            f'"{chart_type}"' in _WEB_FORMATTING
        ), f"prompt never mentions {chart_type}"


def test_prompt_caps_match_the_renderer_caps():
    """The prompt asks for downsampling at exactly the point we slice."""
    assert str(_renderer_cap("MAX_ROWS")) in _WEB_FORMATTING
    assert str(_renderer_cap("MAX_SERIES")) in _WEB_FORMATTING


def test_named_colors_offered_to_the_agent_exist_in_the_renderer():
    """A color name the agent is told to use, and the renderer never heard of,
    silently paints the series with the wrong palette slot."""
    source = CHART_BLOCK_TSX.read_text()
    offer = re.search(r'"color":(.*?)or a hex', _WEB_FORMATTING, re.S)
    assert offer, "_WEB_FORMATTING no longer offers named series colors"
    named = set(re.findall(r'"(\w+)"', offer.group(1)))
    block = re.search(r"const NAMED_COLORS[^=]*= \{(.*?)\};", source, re.S)
    assert block, "NAMED_COLORS not found in ChartBlock.tsx"
    known = set(re.findall(r"^\s*(\w+):", block.group(1), re.M))
    assert named <= known, f"prompt offers colors the renderer ignores: {named - known}"


def test_telegram_prompt_stays_chartless():
    """Telegram renders PNGs, not fences — a chart fence there is noise."""
    assert "```chart" not in _TELEGRAM_FORMATTING
    assert platform_formatting("telegram") is _TELEGRAM_FORMATTING
    assert platform_formatting("web") is _WEB_FORMATTING


@pytest.mark.parametrize(
    "platform,expected",
    [("web", _WEB_FORMATTING), ("telegram", _TELEGRAM_FORMATTING)],
)
def test_bound_agent_opens_with_the_platform_formatting(platform, expected):
    """The specialist path skips build_initial_context; it must not skip this."""
    bound = binding.SessionBinding(
        label="Test Agent",
        agent_slug="a-slug-that-does-not-exist",
        instructions="You are a test.",
    )

    context = bound_agent_context(bound, user_id=1, platform=platform)

    assert expected in context
    # Identity still leads: the formatting is appended, never a replacement.
    assert context.index("You are a test.") < context.index(expected)
