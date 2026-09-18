"""Every tool a shipped agent claims must be a tool that exists.

An agent's ``tools:`` list is an *allowlist*, applied by subtraction: whatever
is not on it is muted. A name on it that no tool answers to is therefore
subtracted from nothing and silently ignored — which is how `cover_lp_agent`
carried `sweep_fees` and `vault_status` for as long as it did, telling the model
about two tools it could never call and, in one case, describing a mechanism the
design had deliberately removed.

Shipped agents only. A local agent is the operator's own file and may name
whatever they are building; this is an invariant of what Condor ships.
"""

from pathlib import Path

import pytest
import yaml

from condor.runtime.toolsets import _every_tool_name

AGENTS = Path(__file__).resolve().parent.parent / "agents"


def _shipped_agents() -> list[tuple[str, list[str]]]:
    out = []
    for directory in sorted(AGENTS.iterdir()):
        definition = directory / "AGENT.md"
        if not definition.exists():
            continue
        text = definition.read_text()
        if not text.startswith("---"):
            continue
        meta = yaml.safe_load(text.split("---", 2)[1]) or {}
        out.append((directory.name, [str(t) for t in (meta.get("tools") or [])]))
    return out


def test_the_library_is_not_empty():
    """A glob that matches nothing would make every assertion below vacuous."""
    assert _shipped_agents()


@pytest.mark.parametrize("slug,tools", _shipped_agents(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_named_tool_exists(slug, tools):
    known = _every_tool_name()
    # The list may carry fully-qualified MCP names; the allowlist compares on
    # the bare name, so this does too.
    missing = sorted({t for t in tools if t.rsplit("__", 1)[-1] not in known})
    assert not missing, (
        f"{slug} lists {missing}, which no tool answers to. The allowlist "
        "subtracts what is not named, so a name nothing matches is ignored "
        "rather than refused — and the model is told about a tool it cannot call."
    )
