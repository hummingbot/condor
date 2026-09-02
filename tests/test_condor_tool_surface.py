"""The condor MCP surface — what is registered, and what is not.

FEAT-067 dropped four leftovers: ``manage_notes`` (a declared-dead alias over
``manage_memory``), ``get_user_context`` (folded into ``manage_servers``), the
``list_routines``/``run_routine`` actions on the agent tool (strict duplicates
of ``manage_routines``), and the ``strategy_id`` alias. FEAT-068 then split the
``manage_trading_agent`` funnel into ``manage_agents`` / ``manage_strategies`` /
``control_agent``. Every docstring costs context on every session, so the
surface is pinned here.
"""

import asyncio
import re
import subprocess
from pathlib import Path

import pytest

from mcp_servers.condor import server
from mcp_servers.condor.settings import settings
from mcp_servers.condor.tools import servers as servers_tool

EXPECTED_TOOLS = {
    "consult",
    "control_agent",
    "delegate",
    "get_available_models",
    "manage_agents",
    "manage_memory",
    "manage_routines",
    "manage_servers",
    "manage_skill",
    "manage_strategies",
    "run_code",
    "send_notification",
    "trading_agent_journal_read",
    "trading_agent_journal_write",
}


def _registered() -> set[str]:
    return {tool.name for tool in asyncio.run(server.mcp.list_tools())}


def test_the_server_registers_exactly_the_expected_tools():
    assert _registered() == EXPECTED_TOOLS


def test_the_dropped_tools_are_not_registered():
    """Unknown-tool at the host beats a live deprecated docstring in context."""
    assert (
        not {
            "manage_notes",
            "get_user_context",
            "manage_trading_agent",
        }
        & _registered()
    )


class _FakeRole:
    value = "admin"


class _FakeConfigManager:
    def get_accessible_servers(self, user_id):
        return ["local"]

    def get_server(self, name):
        return {"host": "127.0.0.1", "port": 8000}

    def get_server_permission(self, user_id, name):
        return _FakeRole()

    def get_chat_default_server(self, chat_id):
        return "local"

    def get_user_role(self, user_id):
        return _FakeRole()

    def is_admin(self, user_id):
        return True


@pytest.fixture
def fake_cm(monkeypatch):
    import config_manager

    monkeypatch.setattr(config_manager, "get_config_manager", _FakeConfigManager)
    monkeypatch.setattr(settings, "user_id", 1)
    monkeypatch.setattr(settings, "chat_id", 1)


def test_manage_servers_list_answers_who_and_where(fake_cm):
    """The context fields get_user_context used to carry now ride on `list`."""
    result = asyncio.run(servers_tool.manage_servers("list", None))

    assert result["active_server"] == "local"
    assert result["user_role"] == "admin"
    assert result["is_admin"] is True
    # The LLM identity half — a coordinator must never invent an agent_key.
    assert "active_agent_key" in result
    assert "custom_llm_endpoints" in result


def test_the_duplicate_routine_actions_are_gone():
    """manage_routines(agent=...) is the one way in; the alias no longer answers."""
    from mcp_servers.condor.tools import trading_agent

    for action in ("list_routines", "run_routine"):
        result = asyncio.run(trading_agent.control_agent(action))
        assert "Unknown action" in result.get("error", "")


def test_strategy_id_is_gone_from_the_routine_and_skill_signatures():
    import inspect

    for fn in (server.manage_routines, server.manage_skill):
        assert "strategy_id" not in inspect.signature(fn).parameters


REPO_ROOT = Path(__file__).resolve().parents[1]


def _tracked(*patterns: str) -> list[Path]:
    """The files git actually tracks under ``patterns``.

    Deliberately ``git ls-files`` and not a filesystem glob: some agent
    directories are locally excluded, and their markdown is nobody's contract.
    """
    listed = subprocess.run(
        ["git", "ls-files", *patterns],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [REPO_ROOT / line for line in listed.stdout.split()]


def _committed_playbooks() -> list[Path]:
    """The AGENT.md / strategy.md files git actually tracks."""
    return _tracked("agents/*/AGENT.md", "agents/*/strategies/*/strategy.md")


def _manage_routines_calls(text: str) -> list[str]:
    """Every ``manage_routines(...)`` argument list in a playbook."""
    calls = []
    for match in re.finditer(r"manage_routines\(", text):
        start = match.end() - 1
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    calls.append(text[start : i + 1])
                    break
        else:  # unbalanced — an abbreviated snippet; scan what there is
            calls.append(text[start:])
    return calls


def test_no_committed_playbook_targets_a_routine_by_strategy_id():
    """Routines are per-agent: playbooks must say ``agent=``, never the dead alias.

    FastMCP's argument model has no ``extra="forbid"``, so a stray
    ``strategy_id=`` is dropped in silence — the owning agent's own tick still
    resolves via CONDOR_AGENT_SLUG while every other seat gets 'routine not
    found'. Nothing but this guard notices the drift.
    """
    playbooks = _committed_playbooks()
    assert playbooks, "found no committed playbooks — the guard would pass vacuously"
    offenders = [
        f"{path.relative_to(REPO_ROOT)}: {call}"
        for path in playbooks
        for call in _manage_routines_calls(path.read_text(encoding="utf-8"))
        if "strategy_id" in call
    ]
    assert not offenders, "manage_routines takes agent=<slug>: " + "; ".join(offenders)


# ── FEAT-068: the three families, and the routing help between them ──


@pytest.mark.parametrize(
    "action",
    ["list", "create", "get", "update", "delete"],
)
def test_every_agent_action_reaches_manage_agents(action):
    """Each family action answers on its own tool — no "unknown action"."""
    from mcp_servers.condor.tools import trading_agent

    result = trading_agent.manage_agents(action)
    assert "Unknown action" not in result.get("error", "")


@pytest.mark.parametrize("action", ["list", "get", "create", "update", "delete"])
def test_every_strategy_action_reaches_manage_strategies(action):
    from mcp_servers.condor.tools import trading_agent

    result = trading_agent.manage_strategies(action)
    assert "Unknown action" not in result.get("error", "")


@pytest.mark.parametrize(
    "action",
    ["stop", "pause", "resume", "shutdown", "get_state", "set_state", "start"],
)
def test_every_control_action_reaches_control_agent(action):
    """Called with no ids, each one asks for its id rather than rejecting the verb."""
    from mcp_servers.condor.tools import trading_agent

    result = asyncio.run(trading_agent.control_agent(action))
    assert "is required" in result.get("error", "")


@pytest.mark.parametrize(
    "action,sibling",
    [
        ("start_agent", "control_agent"),
        ("list_agents", "control_agent"),
        ("create_strategy", "manage_strategies"),
        ("agent_tracker", "trading_agent_journal_read"),
    ],
)
def test_a_misrouted_action_names_the_sibling_tool(action, sibling):
    """A funnel-era action gets the call that works, not a bare error."""
    from mcp_servers.condor.tools import trading_agent

    result = trading_agent.manage_agents(action)
    assert sibling in result["error"]


def test_the_legacy_prefixed_names_still_resolve_in_their_own_family():
    """Prompts in the wild say "create_agent"; the family that owns it answers."""
    from mcp_servers.condor.tools import trading_agent

    result = trading_agent.manage_agents("get_agent")
    assert "belongs to" not in result.get("error", "")
    assert "Unknown action" not in result.get("error", "")


def test_journal_read_tracker_section_replaces_agent_tracker(monkeypatch):
    """agent_tracker's payload now rides on the read tool as a section."""
    from mcp_servers.condor.tools import trading_agent

    class _JM:
        def read_full(self):
            return "# tracker"

        def get_summary_dict(self):
            return {"ticks": 3}

    monkeypatch.setattr(
        trading_agent, "_resolve_journal_manager", lambda agent_id: (_JM(), None)
    )
    result = trading_agent.journal_read("some_agent", section="tracker")
    assert result == {"tracker_md": "# tracker", "summary": {"ticks": 3}}


def test_set_state_carries_its_payload_outside_config(monkeypatch):
    """`config` means start-overrides only; the state value has its own param."""
    from mcp_servers.condor.tools import trading_agent

    calls = []

    async def _fake_call(method, path, body=None):
        calls.append((method, path, body))
        return {"ok": True}

    monkeypatch.setattr(trading_agent, "call_main_api", _fake_call)
    asyncio.run(
        trading_agent.control_agent(
            "set_state", agent_id="mm.grid_abc123", key="cursor", value=42
        )
    )

    method, path, body = calls[0]
    assert (method, path) == ("POST", "/agents/mm/strategies/grid_abc123/state")
    assert body == {"key": "cursor", "value": 42, "expires_in": None, "clear": False}


# ── a routed skill names only tools that exist (CORR-306) ────────────────────


def _committed_condor_skills() -> list[Path]:
    """The playbooks the interactive Condor seat owns and routes work through."""
    return _tracked("agents/condor/skills/*/SKILL.md")


def _every_registered_tool() -> set[str]:
    """Both MCP servers' full surface — what a skill is allowed to name."""
    from mcp_servers.hummingbot_api import server as hb_server

    return _registered() | {
        tool.name for tool in asyncio.run(hb_server.mcp.list_tools())
    }


def _called_names(text: str) -> set[str]:
    """Every ``name(`` call shape in a playbook, fenced blocks included.

    These docs are prose plus tool-call snippets; a lowercase identifier written
    as a call is a tool the model is being told to invoke.
    """
    return set(re.findall(r"\b([a-z_][a-z0-9_]*)\(", text))


def test_the_agent_builder_skill_calls_only_registered_tools():
    """CORR-306: the routing rule sends every agent-building request here.

    A skill naming a dead TOOL fails harder than READ-290's dead *parameter*:
    FastMCP silently drops an unknown kwarg, but an unknown tool name is an
    error at the host. The skill was migrated to ``manage_agents`` /
    ``manage_strategies`` for create/list/start, while its edit-and-repair
    paragraph still said ``update_agent`` / ``delete_agent`` / ``get_agent`` /
    ``delete_strategy`` — funnel-era ACTION names that are not tools. So the
    Step 2 "the persona is off, fix the AGENT.md" path blew up at exactly the
    moment it was repairing a broken agent.
    """
    skill = REPO_ROOT / "agents/condor/skills/agent_builder/SKILL.md"
    called = _called_names(skill.read_text(encoding="utf-8"))
    assert called, "parsed no calls out of agent_builder — did the file move?"

    phantom = called - _every_registered_tool()
    assert not phantom, (
        f"agent_builder/SKILL.md tells Condor to call tool(s) no server "
        f"registers: {sorted(phantom)}"
    )


def test_no_condor_skill_names_a_funnel_era_action_as_a_tool():
    """FEAT-068 split ``manage_trading_agent`` into three tools; its action
    vocabulary survives only as ``action=`` strings, never as callables.

    ``_ACTION_OWNER`` is that vocabulary, so the guard tracks the split itself
    rather than a hand-copied deny-list that would rot the same way the skill
    did. Bare mentions count too: prose saying "``delete_agent`` refuses while
    the agent owns strategies" reads as a tool name even without parentheses.
    """
    from mcp_servers.condor.tools.trading_agent import _ACTION_OWNER

    dead = set(_ACTION_OWNER) - _every_registered_tool()
    assert dead, "the compatibility map named no dead actions — did it move?"

    skills = _committed_condor_skills()
    assert skills, "found no committed condor skills — the guard would pass vacuously"

    pattern = re.compile(r"`[^`]*\b(" + "|".join(sorted(dead)) + r")\b[^`]*`")
    offenders = [
        f"{path.relative_to(REPO_ROOT)}: {match.group(0)}"
        for path in skills
        for match in pattern.finditer(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        "these are actions on manage_agents/manage_strategies/control_agent, "
        "not tools: " + "; ".join(offenders)
    )
