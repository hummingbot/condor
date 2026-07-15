"""Telegram-side re-exports of the agent core.

The agent core moved to ``condor.agents.context`` (prompt/context + MCP
config builders) and ``condor.agents.gating`` (tool classification) —
simplification plan §5.1. This module keeps the Telegram handlers' import
paths working until ``handlers/`` is deleted (Phase 6). Nothing under
``condor/`` may import from here.
"""

from condor.agents.context import (  # noqa: F401
    AGENT_OPTIONS,
    COMPACT_CONTEXT_TEMPLATE,
    COMPACT_PROMPT_AUTO,
    COMPACT_PROMPT_CUSTOM_TEMPLATE,
    DEFAULT_AGENT,
    _build_system_prompt,
    _condor_mcp_args,
    _default_agent,
    _load_condor,
    build_agent_context,
    build_initial_context,
    build_mcp_servers_for_agent,
    build_mcp_servers_for_session,
    get_project_dir,
)
from condor.agents.gating import (  # noqa: F401
    BLOCKED_TOOLS,
    DANGEROUS_BOT_ACTIONS,
    DANGEROUS_CLMM_ACTIONS,
    DANGEROUS_EXECUTOR_ACTIONS,
    DANGEROUS_SWAP_ACTIONS,
    DANGEROUS_TOOLS,
    SYSTEM_MUTATION_ACTIONS,
    is_dangerous_tool_call,
    system_mutation_tool,
)
