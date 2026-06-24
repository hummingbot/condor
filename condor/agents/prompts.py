"""Prompt builder for trading agent ticks.

Assembles the single prompt sent to a fresh ACP session each tick,
combining: base rules, strategy instructions, config, risk state,
pre-computed core data, and journal context (learnings + recent decisions).
"""

from __future__ import annotations

from typing import Any

from .agent import Agent
from .strategy import Strategy

BASE_PROMPT_LIVE = """\
You are an autonomous trading agent running inside Condor.

RULES:
- Trade ONLY via manage_executors(action="create"). NEVER use place_order.
- Be conservative. When in doubt, hold and journal why.

ERROR RECOVERY:
- If manage_executors(action="create") fails, call manage_executors(executor_type="<type>") \
to fetch the full config schema, compare it against what you sent, fix the missing/wrong \
fields, and retry ONCE. Journal the error and fix as a learning.
"""

BASE_PROMPT_DRY_RUN = """\
You are an autonomous trading agent running inside Condor in 🧪 DRY RUN mode.

RULES:
- This is OBSERVATION ONLY. Do NOT create or stop executors.
- manage_executors is available for read-only queries (performance_report).
- Analyze the market and describe what you WOULD do, but take NO trading action.

DRY RUN MESSAGING:
- Use conditional language: "Would place grid..." not "Grid placed"
- Prefix actions with 🧪 to signal dry-run
- End with: "No executors were created (dry run)"
"""

BASE_PROMPT_COMMON = """\
GENERAL:
- The mcp-hummingbot server is pre-configured. Do NOT call configure_server.
- Keep tool chains short (1-5 calls per tick).
- Your executor state and positions are pre-loaded in [CORE DATA] below — no need to query them.

JOURNAL:
- Write ONE action entry per tick via trading_agent_journal_write(entry_type="action"). One line.
- Learnings must specify a category: "market" or "execution".
  trading_agent_journal_write(entry_type="learning", category="market|execution", text="...")
  - market: band behavior, volatility regimes, S/R patterns, routine observations.
  - execution: executor errors, schema issues, fill problems, timing.
- Keep learnings factual and short (1 line). No speculation.
- Only write a learning if it's genuinely NEW. Duplicates are auto-filtered.
- Do NOT call trading_agent_journal_read — context is already in this prompt.

SKILLS & ROUTINES:
- [AVAILABLE SKILLS & ROUTINES] below lists SKILLS (playbooks — know-how: when to
  act + steps) and ROUTINES (executable scripts).
- Before a known flow, read the relevant playbook with manage_skill(action="read",
  name="...") and follow it instead of re-deriving the procedure.
- A skill may reference a routine (shown as "→ routine: <name>"); run it with
  manage_routines(action="run", name="...", config={...}). manage_routines(action="list")
  to discover routines; routines tagged "agent" are local to your strategy.
- Skills are read-only playbooks shipped with this agent — follow them, you can't
  create or edit them. Operational facts you learn go to [LEARNINGS] (journal).

MEMORY (about the user, NOT operational learnings):
- [USER MEMORY] below is what is known about the OWNER (preferences, profile).
  This is distinct from [LEARNINGS] (market/execution), which go to the journal.
- Read detail with manage_memory(action="read", name="...").
- If you learn something new and stable about the USER (a standing preference,
  a profile fact, a correction), save it with manage_memory(action="write",
  name="short-name", description="one line", content="...", type="preference|fact").
  Operational/market learnings still go to trading_agent_journal_write, NOT here.

NOTIFICATIONS:
- Use send_notification(text="...") to message the user on Telegram.
"""

TOOL_PRELOAD_LIVE = (
    "IMPORTANT: At the very start, load ALL MCP tools in a single ToolSearch call:\n"
    'ToolSearch(query="select:mcp__mcp-hummingbot__get_market_data,'
    "mcp__mcp-hummingbot__manage_executors,"
    "mcp__mcp-hummingbot__search_history,"
    "mcp__mcp-hummingbot__explore_geckoterminal,"
    "mcp__condor__trading_agent_journal_write,"
    "mcp__condor__send_notification,"
    "mcp__condor__manage_memory,"
    "mcp__condor__manage_skill,"
    'mcp__condor__manage_routines")\n'
    "Do this silently."
)

TOOL_PRELOAD_DRY_RUN = (
    "IMPORTANT: At the very start, load ALL MCP tools in a single ToolSearch call:\n"
    'ToolSearch(query="select:mcp__mcp-hummingbot__get_market_data,'
    "mcp__mcp-hummingbot__search_history,"
    "mcp__mcp-hummingbot__explore_geckoterminal,"
    "mcp__condor__trading_agent_journal_write,"
    "mcp__condor__send_notification,"
    "mcp__condor__manage_memory,"
    "mcp__condor__manage_skill,"
    'mcp__condor__manage_routines")\n'
    "Do this silently."
)


def _build_routines_section(strategy: Strategy) -> str:
    """Build an [AVAILABLE ROUTINES] section listing strategy-local + global routines."""
    from routines.base import discover_routines, discover_routines_from_path

    lines = ["ROUTINES — executable analysis scripts:"]
    lines.append(
        f'Call via: manage_routines(action="run", name="<name>", strategy_id="{strategy.key}", config={{...}})'
    )
    lines.append("")

    # Agent-level routines first (shared across this agent's strategies)
    from routines.base import assistant_routines_dir

    routines_dir = assistant_routines_dir(strategy.agent_slug)
    if routines_dir.exists():
        from routines.base import discover_routines_from_path

        local = discover_routines_from_path(routines_dir)
        if local:
            lines.append("Agent-local:")
            for name, r in sorted(local.items()):
                lines.append(f"  - {name}: {r.description}")

    # Global routines
    global_routines = discover_routines(force_reload=False)
    if global_routines:
        lines.append("Global:")
        for name, r in sorted(global_routines.items()):
            lines.append(f"  - {name}: {r.description}")

    return "\n".join(lines)


def build_tick_prompt(
    agent: Agent,
    strategy: Strategy,
    config: dict[str, Any],
    core_data: dict[str, str],
    learnings: str,
    summary: str,
    recent_decisions: str,
    risk_state: dict[str, Any],
    tick_number: int = 1,
    agent_id: str = "",
    cached_routines_section: str | None = None,
    user_memory: str = "",
    skills_index: str = "",
) -> str:
    """Build the full prompt for one agent tick.

    Composes the Agent's domain identity (``agent.instructions``) with the
    strategy's tactic (``strategy.instructions``): the Agent says *who you are and
    what you know*; the strategy says *what to do this tick*.
    """
    from condor.acp.pydantic_ai_client import is_pydantic_ai_model

    execution_mode = config.get("execution_mode", "loop")
    is_dry_run = execution_mode == "dry_run"
    agent_key = config.get("agent_key") or strategy.agent_key or agent.agent_key
    use_pydantic_ai = is_pydantic_ai_model(agent_key)

    # Select base prompt and tool preload based on mode
    base_prompt = BASE_PROMPT_DRY_RUN if is_dry_run else BASE_PROMPT_LIVE
    sections: list[str] = [base_prompt, BASE_PROMPT_COMMON]

    # Tool preload is ACP-specific (ToolSearch); pydantic-ai auto-discovers MCP tools
    if not use_pydantic_ai:
        tool_preload = TOOL_PRELOAD_DRY_RUN if is_dry_run else TOOL_PRELOAD_LIVE
        sections.append(tool_preload)
    else:
        sections.append(
            "TOOLS:\n"
            "All MCP tools are pre-loaded and available. Call them directly by name."
        )

    # Tick identity
    tick_info = f"[TICK INFO]\nThis is tick #{tick_number}. Use this number in journal entries and notifications."
    if agent_id:
        tick_info += f"\nAgent ID: {agent_id}"
        if not is_dry_run:
            tick_info += f'\nPass controller_id="{agent_id}" as a TOP-LEVEL arg to manage_executors (not inside executor_config).'
    sections.append(tick_info)

    # Run-once mode note
    if execution_mode == "run_once":
        sections.append(
            "[EXECUTION MODE — RUN ONCE]\n"
            "Single-tick session with LIVE execution. The engine will stop after this tick. "
            "Make your best move now — there will be no follow-up ticks."
        )

    # Server credentials are injected via env vars into the MCP process,
    # so no need to include them in the prompt or call configure_server.

    # Agent identity + domain knowledge (who you are), then the strategy tactic
    # (what to do this tick). The Agent body is shared across all its strategies.
    if agent.instructions.strip():
        sections.append(f"[AGENT — domain identity & knowledge]\n{agent.instructions}")
    sections.append(f"[STRATEGY INSTRUCTIONS]\n{strategy.instructions}")

    # Available skills (playbooks) + routines, unified under one header. Skills
    # are read fresh each tick (the agent may create its own mid-session), so
    # they arrive via skills_index; routine discovery is cached (it's expensive).
    routines_section = cached_routines_section
    if routines_section is None:
        try:
            routines_section = _build_routines_section(strategy)
        except Exception:
            routines_section = ""  # Don't fail the tick if discovery fails
    skills_routines = ["[AVAILABLE SKILLS & ROUTINES]"]
    if skills_index:
        skills_routines.append(
            "\nSKILLS — playbooks (read before a known flow with "
            'manage_skill(action="read", name="..."); "→ routine:" links to an '
            "executable routine):\n"
            f"{skills_index}"
        )
    if routines_section:
        skills_routines.append(f"\n{routines_section}")
    sections.append("\n".join(skills_routines))

    # Session trading context (natural language directives for this session)
    trading_context = config.get("trading_context", "")
    if trading_context:
        sections.append(
            "[SESSION CONTEXT]\n"
            "The user provided the following natural language context for this trading session. "
            "Use this to guide your market selection, risk appetite, and trading style:\n\n"
            f"{trading_context}"
        )

    # Current config (exclude keys shown elsewhere or not useful to the LLM)
    _CONFIG_EXCLUDE = {
        "trading_context",
        "risk_limits",  # shown in dedicated sections
        "agent_key",
        "server_name",
        "frequency_sec",
        "execution_mode",  # noise / internal
    }
    config_lines = [
        "[CURRENT CONFIG]",
        "These are the ACTIVE values for this session. If the strategy instructions mention different defaults, IGNORE them and use these values instead.",
    ]
    for k, v in config.items():
        if k in _CONFIG_EXCLUDE:
            continue
        config_lines.append(f"{k}: {v}")
    sections.append("\n".join(config_lines))

    # Controller mode: the agent steers a named bot's controllers instead of
    # spawning standalone executors. Triggered solely by a non-empty bot_name.
    bot_name = config.get("bot_name", "")
    if bot_name:
        sections.append(
            "[CONTROLLER MODE]\n"
            f"You operate the Hummingbot bot '{bot_name}'. Steer its controllers "
            "instead of creating standalone executors:\n"
            '- Check current state first: manage_bots(action="status").\n'
            "- Define/update controller config templates with manage_controllers.\n"
            f"- Apply them with manage_bots: deploy if '{bot_name}' is not running, "
            "otherwise update_config / start_controllers / stop_controllers.\n"
            "Do NOT create standalone executors unless the strategy instructions "
            "explicitly tell you to. The bot's PnL is attributed to you automatically."
        )

    # Risk state
    rs = risk_state
    max_dd = rs.get("max_drawdown_pct", -1)
    dd_display = (
        f"{rs.get('drawdown_pct', 0):.1f}% / {max_dd:.1f}% limit"
        if max_dd >= 0
        else "disabled"
    )
    risk_lines = [
        "[RISK STATE]",
        f"Position Size: ${rs.get('total_exposure', 0):.2f} / ${rs.get('max_position_size', 500):.2f} limit",
        f"Open Executors: {rs.get('executor_count', 0)} / {rs.get('max_open_executors', 5)} limit",
        f"Drawdown: {dd_display}",
        f"Status: {'BLOCKED - ' + rs.get('block_reason', '') if rs.get('is_blocked') else 'ACTIVE'}",
    ]
    sections.append("\n".join(risk_lines))

    # Core skill data (pre-computed)
    for name, data_summary in core_data.items():
        sections.append(f"[CORE DATA - {name}]\n{data_summary}")

    # User memory -- what is known about the owner (preferences/profile)
    if user_memory:
        sections.append(
            "[USER MEMORY — what is known about the owner; advisory]\n"
            'Read detail with manage_memory(action="read", name="...").\n\n'
            f"{user_memory}"
        )

    # Journal -- compact memory
    if learnings:
        sections.append(
            f"[LEARNINGS — do NOT repeat these, only add genuinely new insights]\n{learnings}"
        )
    if summary:
        sections.append(f"[CURRENT STATUS]\n{summary}")
    if recent_decisions:
        sections.append(f"[RECENT DECISIONS — last 3 snapshots]\n{recent_decisions}")

    return "\n\n".join(sections)
