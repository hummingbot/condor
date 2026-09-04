"""Which tools each seat mounts, as **names** (FEAT-066, FEAT-091).

A leaf module on purpose: strings and nothing else. ``server.py`` cannot be
imported to ask what it registers — importing it parses argv and builds a
``FastMCP`` singleton as an import side effect — so the web process, which has
to render a switch per tool, reads the tables from here instead.

``server.py`` resolves every name below in its own module namespace at import
and raises if one does not resolve, which is what keeps this table and the
functions provably in step: a renamed tool fails the server's import, not a
session three hours later.

The rings are the same ones FEAT-066 drew; read them narrowest first. Nothing
here changes a tool's behaviour — a seat simply cannot name what was never
registered.
"""

from __future__ import annotations

#: One line per tool, for the operator's switch in the brain panel. Prose, not
#: the docstring: the panel has one line of room and the docstring's first line
#: is written for the model.
TOOL_DESCRIPTIONS: dict[str, str] = {
    "consult": "Ask a specialist agent a question and get its answer",
    "delegate": "Hand a long task to a background agent instance",
    "send_notification": "Send the user a Telegram message",
    "manage_routines": "List, run, schedule and edit routines",
    "run_code": "Run a Python snippet inside Condor",
    "manage_servers": "List Hummingbot API servers and say where this seat points",
    "manage_memory": "Read and write what this agent remembers about the user",
    "manage_skill": "Read, write and refine playbooks",
    "trading_agent_journal_read": "Read the trading journal",
    "trading_agent_journal_write": "Write a line to the trading journal",
    "manage_agents": "Create and edit agent identities (AGENT.md)",
    "manage_strategies": "Create and edit strategies — the looping playbooks",
    "control_agent": "Start, stop, pause and resume live agent instances",
    "get_available_models": "List the models an agent can run on",
}

#: Everything a session needs whoever is sitting in it: its own memory and
#: playbooks, its routines, a scratch interpreter, the journal it writes each
#: tick, and the peers it may consult. ``consult``/``delegate`` stay even in the
#: narrowest profile — a peer consult and a background copy of oneself are
#: designed behaviour, and a worker already has ``delegate(action="start")``
#: refused in code rather than by omission.
COMMON_TOOLS: tuple[str, ...] = (
    "consult",
    "delegate",
    "send_notification",
    "manage_routines",
    "run_code",
    "manage_servers",
    "manage_memory",
    "manage_skill",
    "trading_agent_journal_read",
    "trading_agent_journal_write",
)

#: Orchestration: who exists, what loops they own, and which instances are
#: running. An *attended* specialist owns these — ``strategy_builder`` is a
#: shared playbook every agent inherits, and it tells the agent to author its own
#: strategy with ``manage_strategies``, pick a model from ``get_available_models``
#: and launch itself with ``control_agent``. A tick is the one seat that must
#: not: it is already running inside the very loop these tools start and stop,
#: and nothing in a tick playbook reaches for them.
ORCHESTRATION_TOOLS: tuple[str, ...] = (
    "manage_agents",
    "manage_strategies",
    "control_agent",
    "get_available_models",
)

#: profile name → the tools it registers. ``agent`` and ``full`` register the
#: same set today, and the name is kept distinct on purpose: it is the seat axis
#: the hummingbot server narrows (no Gateway config, no container control, no
#: repointing the API server), and keeping one profile vocabulary across both
#: servers means a seat is described once, in ``condor.runtime.toolsets``.
PROFILE_TOOLS: dict[str, tuple[str, ...]] = {
    "tick": COMMON_TOOLS,
    "agent": COMMON_TOOLS + ORCHESTRATION_TOOLS,
    "full": COMMON_TOOLS + ORCHESTRATION_TOOLS,
}
