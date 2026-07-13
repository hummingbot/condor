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
- If your strategy deploys a controller-based bot, manage_bots(action="deploy")
  MUST include max_global_drawdown_quote within your risk limits — deploys
  without a declared loss cap are blocked by the risk engine.
- Be conservative. When in doubt, hold and journal why.

ERROR RECOVERY:
- If manage_executors(action="create") fails, call manage_executors(executor_type="<type>") \
to fetch the full config schema, compare it against what you sent, fix the missing/wrong \
fields, and retry ONCE. Journal the error and fix as a learning.
"""

BASE_PROMPT_EXPERIMENT = """\
You are an autonomous trading agent running inside Condor in 🧪 EXPERIMENT mode.

RULES:
- This is OBSERVATION ONLY. Do NOT create or stop executors, and do NOT deploy,
  stop, or update a controller-based bot (manage_bots with action="deploy",
  "stop_bot", "stop_controllers", "start_controllers", or "update_config").
- manage_executors and manage_bots are available for read-only queries
  (performance_report; status/logs/get_config).
- Analyze the market and describe what you WOULD do, but take NO trading action.

EXPERIMENT MESSAGING:
- Use conditional language: "Would place grid..." not "Grid placed"
- Prefix actions with 🧪 to signal the experiment
- End with: "No executors were created (experiment)"
"""

# The mcp-hummingbot line is added only for server-backed agents (see
# build_tick_prompt); serverless agents run condor-only and must not be told a
# hummingbot server exists — they'd otherwise reach for its tools.
HUMMINGBOT_PRECONFIGURED_LINE = (
    "- The mcp-hummingbot server is pre-configured. Do NOT call configure_server.\n"
)

BASE_PROMPT_COMMON = """\
GENERAL:
- Keep tool chains short (1-5 calls per tick).
- Your executor state and positions are pre-loaded in [CORE DATA] below — no need to query them.

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
  Operational/market learnings go to the journal (see JOURNAL above), NOT here.

NOTIFICATIONS:
- Use send_notification(text="...") to message the user on Telegram.
"""

# Journal guidance. In experiments the engine keeps NO journal — the whole
# tick is captured in an experiment snapshot instead — so the agent must not
# call trading_agent_journal_write (it would fail with "no journal available").
# Everything else (loop, incl. max_ticks=1 run-once sessions) gets the full
# journal protocol.
JOURNAL_SECTION_LIVE = """\
JOURNAL:
- Write ONE action entry per tick via trading_agent_journal_write(entry_type="action"). One line.
- Learnings must specify a category: "market" or "execution".
  trading_agent_journal_write(entry_type="learning", category="market|execution", text="...")
  - market: band behavior, volatility regimes, S/R patterns, routine observations.
  - execution: executor errors, schema issues, fill problems, timing.
- Keep learnings factual and short (1 line). No speculation.
- Only write a learning if it's genuinely NEW. Duplicates are auto-filtered.
- Do NOT call trading_agent_journal_read — context is already in this prompt.
"""

JOURNAL_SECTION_EXPERIMENT = """\
JOURNAL:
- This is an experiment: there is NO journal this tick.
- Do NOT call trading_agent_journal_write or trading_agent_journal_read — they are
  unavailable here and will error.
- Put all observations, reasoning, and what you WOULD record straight into your
  response. The full tick is saved automatically as an experiment snapshot.
"""


def _build_tool_preload(*, is_experiment: bool, uses_hummingbot: bool = True) -> str:
    """ToolSearch preload line for ACP sessions.

    Experiments omit manage_executors (read-only) and trading_agent_journal_write
    (experiments keep no journal). Serverless agents (``uses_hummingbot=False``)
    load the CONDOR-native manage_executors and NO mcp-hummingbot tools — wiring
    both would expose two manage_executors with incompatible schemas, and market
    data comes from the agent's own routines.
    """
    tools: list[str] = []
    if uses_hummingbot:
        tools.append("mcp__mcp-hummingbot__get_market_data")
        if not is_experiment:
            tools.append("mcp__mcp-hummingbot__manage_executors")
        tools += [
            "mcp__mcp-hummingbot__search_history",
            "mcp__mcp-hummingbot__explore_geckoterminal",
        ]
    elif not is_experiment:
        tools.append("mcp__condor__manage_executors")
    if not is_experiment:
        tools.append("mcp__condor__trading_agent_journal_write")
    tools += [
        "mcp__condor__send_notification",
        "mcp__condor__manage_memory",
        "mcp__condor__manage_skill",
        "mcp__condor__manage_routines",
    ]
    return (
        "IMPORTANT: At the very start, load ALL MCP tools in a single ToolSearch call:\n"
        f'ToolSearch(query="select:{",".join(tools)}")\n'
        "Do this silently."
    )


def _build_routines_section(agent_slug: str) -> str:
    """Build an [AVAILABLE ROUTINES] section listing this agent's own routines.

    Domain experts/trading agents are isolated: they see only their own routines
    (``agents/<slug>/routines``), never the chat's general library.
    """
    from routines.base import assistant_routines_dir, discover_routines_from_path

    lines = ["ROUTINES — executable analysis scripts:"]
    lines.append(
        f'Call via: manage_routines(action="run", name="<name>", agent_slug="{agent_slug}", config={{...}})'
    )
    lines.append("")

    # Agent-level routines (shared across this agent's strategies, isolated from
    # the chat's general library).
    routines_dir = assistant_routines_dir(agent_slug)
    local = discover_routines_from_path(routines_dir) if routines_dir.exists() else {}
    if local:
        for name, r in sorted(local.items()):
            lines.append(f"  - {name}: {r.description}")
    else:
        lines.append('  (none yet — create one with action="create_routine")')

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
    is_experiment = execution_mode == "experiment"
    agent_key = config.get("agent_key") or strategy.agent_key or agent.agent_key
    use_pydantic_ai = is_pydantic_ai_model(agent_key)
    # Serverless agents run condor-only (no mcp-hummingbot); the prompt must
    # match the actual wiring (condor/agents/run.py) or the model reaches for
    # tools that aren't there — or, worse, a second manage_executors that is.
    uses_hummingbot = getattr(agent, "server_required", True)

    # Select base prompt and journal protocol based on mode
    base_prompt = BASE_PROMPT_EXPERIMENT if is_experiment else BASE_PROMPT_LIVE
    journal_section = (
        JOURNAL_SECTION_EXPERIMENT if is_experiment else JOURNAL_SECTION_LIVE
    )
    common = BASE_PROMPT_COMMON
    if uses_hummingbot:
        common = common.replace(
            "GENERAL:\n", "GENERAL:\n" + HUMMINGBOT_PRECONFIGURED_LINE
        )
    sections: list[str] = [base_prompt, journal_section, common]

    # Tool preload is ACP-specific (ToolSearch); pydantic-ai auto-discovers MCP tools
    if not use_pydantic_ai:
        sections.append(
            _build_tool_preload(is_experiment=is_experiment, uses_hummingbot=uses_hummingbot)
        )
    else:
        sections.append(
            "TOOLS:\n"
            "All MCP tools are pre-loaded and available. Call them directly by name."
        )

    # Tick identity
    tick_info = f"[TICK INFO]\nThis is tick #{tick_number}. Use this number in journal entries and notifications."
    if agent_id:
        tick_info += f"\nAgent ID: {agent_id}"
        # controller_id is the hummingbot-executors attribution arg; condor-native
        # executors are attributed automatically (agent_slug/agent_id/strategy).
        if not is_experiment and uses_hummingbot:
            tick_info += f'\nPass controller_id="{agent_id}" as a TOP-LEVEL arg to manage_executors (not inside executor_config).'
    sections.append(tick_info)

    # Single-tick session note (run_once maps to max_ticks=1)
    if not is_experiment and config.get("max_ticks") == 1:
        sections.append(
            "[EXECUTION MODE — SINGLE TICK]\n"
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
            routines_section = _build_routines_section(agent.slug)
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
