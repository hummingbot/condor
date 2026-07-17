"""Phase 5 (§8): the MCP surface is explicit, narrowly typed tools — the
mega-dispatcher and its satellites are gone, and the tool list matches the
plan's enumeration (honesty about the count: the ~18 plus the two listing
verbs the run/approval flows need and the §7.1 learnings append)."""

import asyncio

EXPECTED_TOOLS = {
    # agent CRUD + lifecycle (explicit, §8)
    "create_agent",
    "update_agent",
    "delete_agent",
    "run_agent",
    "get_run",
    "get_agent",
    "list_agents",
    "list_runs",
    "control_run",
    "shutdown_agent",
    # retained verbs the other surfaces have
    "consult",
    "delegate",
    "resolve_approval",
    "list_approvals",
    "get_notifications",
    "manage_executors",
    "manage_memory",
    "record_learning",
    "manage_skill",
    "manage_routines",
    "send_notification",
}

RETIRED_TOOLS = {
    "manage_trading_agent",
    "trading_agent_journal_read",
    "trading_agent_journal_write",
    "manage_servers",
    "get_user_context",
    "manage_notes",
}


def test_tool_list_is_the_explicit_surface():
    import mcp_servers.condor.server as srv

    names = {t.name for t in asyncio.run(srv.mcp.list_tools())}
    assert names == EXPECTED_TOOLS
    assert not (names & RETIRED_TOOLS)
