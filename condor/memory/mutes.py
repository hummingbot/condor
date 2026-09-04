"""Per-agent mute set — the libraries an operator switched off for one agent.

An agent's context is a budget. ``agents/_shared`` grows for everybody, and an
agent that only trades perps still pays for the LP playbooks on every tick. A
**mute** is the operator's curation control over that: an individual playbook,
routine (or tool — FEAT-091) is switched off *for one agent*, and switching it
off genuinely removes it from the run rather than merely hiding it from a list.

Nothing is deleted. A muted playbook stays editable and stays readable from the
panel; what changes is that the agent is never told about it and cannot reach
it. Switching it back on restores it.

**Why a file of its own** (``agents/<slug>/mutes.yml``) rather than a field in
``AGENT.md``:

1. A shared item is one file read by every agent, so the flag can never live in
   the item — it has to live on the agent side.
2. ``AgentStore._save`` re-renders the whole front matter from its dataclass, so
   a field it does not know about is dropped by the next unrelated write.
3. The agent can rewrite its own ``AGENT.md`` through ``manage_agents``. A
   curation control the curated party can revoke is not one — so the mute set
   lives in a file no agent-facing tool opens.

Like :mod:`condor.memory.paths` this is a leaf: ``yaml`` plus the path resolver,
nothing else, so it runs from the main process (prompt injection) and from the
MCP subprocess (the tools) alike.

File shape — an absent file means nothing is muted, which is every agent today::

    skills: [lp_rebalance]
    routines: [lp_scanner]
    tools: []

Names are stored exactly as the resolver writes them: skill **slugs**, routine
**names**, tool names.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from condor.fsutil import atomic_write_text

from .paths import agent_home

# The kinds a mute can address. ``tools`` is written and read here from the
# start so FEAT-091 adds a filter, not a file format.
KINDS = ("skills", "routines", "tools")


def mutes_path(agent_slug: str | None = None) -> Path:
    """``agents/<slug>/mutes.yml``; a falsy slug resolves the chat (Condor).

    Condor is an ordinary agent (FEAT-033), so ``None`` must resolve the *chat's*
    mutes rather than "no mutes" — otherwise the one assistant every unbound
    session talks to would be the one that cannot be curated.
    """
    return agent_home(agent_slug) / "mutes.yml"


def load_mutes(agent_slug: str | None = None) -> dict[str, set[str]]:
    """The muted names of each kind. Always all three keys, possibly empty.

    Absent, empty or unreadable file → nothing muted. A mute set that cannot be
    parsed must not blank a library: the failure mode of "the operator loses a
    curation" is a nuisance, "the agent loses its playbooks" is an outage.
    """
    empty: dict[str, set[str]] = {kind: set() for kind in KINDS}
    path = mutes_path(agent_slug)
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return empty
    if not isinstance(raw, dict):
        return empty
    for kind in KINDS:
        values = raw.get(kind) or []
        if isinstance(values, str):  # a one-item list written by hand
            values = [values]
        if isinstance(values, list):
            empty[kind] = {str(v) for v in values if v}
    return empty


def is_muted(agent_slug: str | None, kind: str, name: str) -> bool:
    """True if ``name`` of ``kind`` is switched off for this agent."""
    return name in load_mutes(agent_slug).get(_kind_key(kind), set())


def set_muted(agent_slug: str | None, kind: str, name: str, muted: bool) -> None:
    """Switch ``name`` off (``muted=True``) or back on for this agent.

    Empty lists are pruned and a file left with nothing in it is removed, so an
    agent nobody has curated keeps no ``mutes.yml`` at all — "absent file" stays
    the honest reading of "nothing muted" instead of degrading into "either".
    """
    key = _kind_key(kind)
    current = load_mutes(agent_slug)
    if muted:
        current[key].add(name)
    else:
        current[key].discard(name)

    path = mutes_path(agent_slug)
    data = {k: sorted(v) for k, v in current.items() if v}
    if not data:
        path.unlink(missing_ok=True)
        return
    atomic_write_text(path, yaml.safe_dump(data, default_flow_style=False))


def _kind_key(kind: str) -> str:
    """Accept the singular the API speaks (``"skill"``) as well as the plural."""
    key = (kind or "").strip().lower()
    key = key if key.endswith("s") else f"{key}s"
    if key not in KINDS:
        raise ValueError(f"unknown mute kind '{kind}' (expected one of {KINDS})")
    return key
