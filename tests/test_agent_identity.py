"""A bound Agent is told, at system level, that it is not Condor (FEAT-025).

The bug these cover: a chat bound to ``backpack_mm`` answered "I'm Condor — the
backpack_mm agent is a specialist I can consult", which was a faithful reading
of its own instructions. Identity lives in two places now (the condor MCP
server's ``instructions`` — the only system-level channel ACP v1 gives us — and
the session's opening context) and both must say the same thing.
"""

import pytest

from condor.agents import agent as agent_module
from condor.agents import strategy as strategy_module
from condor.agents.agent import AgentStore, identity_header


def _write_agent(root, slug, *, body="Body.", **frontmatter):
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    fm = "\n".join(f"{k}: {v}" for k, v in frontmatter.items())
    (d / "AGENT.md").write_text(f"---\n{fm}\n---\n\n{body}\n")
    return d


def _patch_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(strategy_module, "_DATA_ROOT", tmp_path)


@pytest.fixture
def three_agents(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    _write_agent(
        tmp_path,
        "backpack_mm",
        name="Backpack MM",
        when_to_consult="Backpack market making",
    )
    _write_agent(
        tmp_path, "brigado", name="Brigado", when_to_consult="BRL market making"
    )
    _write_agent(
        tmp_path,
        "executor_manager",
        name="Executor Manager",
        when_to_consult="Deploy and tune executors",
    )
    return tmp_path


# ── list_index(exclude=) ──


def test_list_index_excludes_only_the_named_slug(three_agents):
    index = AgentStore().list_index(exclude="backpack_mm")
    assert "- [backpack_mm]" not in index
    # Every OTHER agent is still there: filtering for any other reason would be
    # the same as deleting them.
    assert "- [brigado] BRL market making" in index
    assert "- [executor_manager] Deploy and tune executors" in index


def test_list_index_default_lists_everyone(three_agents):
    index = AgentStore().list_index()
    for slug in ("backpack_mm", "brigado", "executor_manager"):
        assert f"- [{slug}]" in index


def test_list_index_unknown_exclude_is_a_no_op(three_agents):
    assert AgentStore().list_index(exclude="nope") == AgentStore().list_index()


# ── the shared identity line ──


def test_identity_header_asserts_first_person_and_not_condor():
    header = identity_header("backpack_mm", "Backpack MM")
    assert "Backpack MM" in header and "backpack_mm" in header
    assert "NOT Condor" in header
    assert "first person" in header


def test_identity_header_falls_back_to_the_slug():
    assert "nameless" in identity_header("nameless")


def test_agent_identity_context_leads_with_the_header(three_agents, monkeypatch):
    from condor.runtime import binding

    ctx = binding.agent_identity_context(
        "backpack_mm", user_id=42, instructions="Domain knowledge.", label="Backpack MM"
    )
    assert ctx.startswith(identity_header("backpack_mm", "Backpack MM"))
    assert "Domain knowledge." in ctx
    # None of the coordinator persona leaks into a bound Agent's opening context.
    assert "consultable **domain agents**" not in ctx


# ── _build_instructions(): one rule, three sections ──


def _instructions(monkeypatch, slug: str) -> str:
    from mcp_servers.condor import server
    from mcp_servers.condor.settings import settings

    monkeypatch.setattr(settings, "agent_slug", slug)
    return server._build_instructions()


def test_instructions_unbound_are_the_coordinator_text(three_agents, monkeypatch):
    text = _instructions(monkeypatch, "")
    assert text.startswith("Condor exposes reusable **skills**")
    assert "[AGENTS — consult for domain work]" in text
    # The full roster, self included — the coordinator has no self to exclude.
    for slug in ("backpack_mm", "brigado", "executor_manager"):
        assert f"- [{slug}]" in text


def test_instructions_bound_assert_identity_and_drop_self(three_agents, monkeypatch):
    text = _instructions(monkeypatch, "backpack_mm")
    assert identity_header("backpack_mm", "Backpack MM") in text
    assert "Condor exposes reusable **skills**" not in text
    assert "- [backpack_mm]" not in text
    assert "[PEER AGENTS — consult for work outside your domain]" in text
    assert "- [brigado] BRL market making" in text
    # FEAT-031: routine authoring stays with the agent — it inherits the cookbook.
    assert "ROUTINE AUTHORING IS YOURS" in text
    assert "routine_cookbook" in text
    # And never routed back out to a builder agent — that agent is gone (FEAT-032).
    assert "routine_builder" not in text


def test_chat_routes_authoring_to_a_background_condor(three_agents, monkeypatch):
    """FEAT-032: the chat hands authoring to a detached worker, not to an agent.

    It must not block on the cookbook (664 lines it would have to read) and it
    must not write the routine itself — but *running* one is still just a call.
    """
    text = _instructions(monkeypatch, "")
    assert 'delegate(action="start", agent="condor"' in text
    assert "routine_builder" not in text
    assert "RUNNING an existing routine is not authoring" in text


def test_unknown_slug_degrades_to_the_coordinator_text(three_agents, monkeypatch):
    """An agent that vanished from the store falls back to today's behavior."""
    text = _instructions(monkeypatch, "ghost")
    assert text.startswith("Condor exposes reusable **skills**")
    assert "[AGENTS — consult for domain work]" in text


# ── the ACP system-prompt channel ──
#
# `claude-agent-acp` reads `_meta.systemPrompt` on session/new and forwards it
# to the agent SDK. It is what decides who the model thinks it is: the MCP
# instructions above reach it and are followed, but read as tool-server guidance
# rather than identity, and everything else Condor sends is a user turn.


def _session_new_params(**client_kwargs) -> dict:
    """The params ACPClient actually puts on the wire for session/new."""
    from condor.acp.client import ACPClient

    return ACPClient(
        command="true", working_dir="/tmp", **client_kwargs
    )._session_new_params()


def test_session_new_appends_the_system_prompt():
    params = _session_new_params(system_prompt="You ARE the thing.")
    # `append`, never a bare string: replacing the preset would strip the host's
    # own tool discipline along with the coordinator framing.
    assert params["_meta"] == {"systemPrompt": {"append": "You ARE the thing."}}


def test_session_new_omits_meta_when_unbound():
    """The Condor chat sends exactly what it sends today — no `_meta` at all."""
    assert _session_new_params() == {"cwd": "/tmp", "mcpServers": []}
