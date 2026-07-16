"""Prompt builder for trading agent ticks.

Assembles the single prompt sent to a fresh ACP session each tick,
combining: base rules, strategy instructions, config, risk state,
pre-computed core data, and journal context (learnings + recent decisions).
"""

from __future__ import annotations

from typing import Any

from .agent import Agent

BASE_PROMPT_LIVE = """\
You are an autonomous trading agent running inside Condor.

RULES:
- Trade ONLY via manage_executors(action="create").
- Be conservative. When in doubt, hold and say why in your response.

ERROR RECOVERY:
- If manage_executors(action="create") fails, call manage_executors(executor_type="<type>") \
to fetch the full config schema, compare it against what you sent, fix the missing/wrong \
fields, and retry ONCE. Journal the error and fix as a learning.
"""

BASE_PROMPT_EXPERIMENT = """\
You are an autonomous trading agent running inside Condor in 🧪 EXPERIMENT mode.

RULES:
- This is OBSERVATION ONLY. Do NOT create or stop executors.
- manage_executors is available for read-only queries (status/performance).
- Analyze the market and describe what you WOULD do, but take NO trading action.

EXPERIMENT MESSAGING:
- Use conditional language: "Would place grid..." not "Grid placed"
- Prefix actions with 🧪 to signal the experiment
- End with: "No executors were created (experiment)"
"""

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
  create or edit them. Operational facts you learn go to agent memory.

MEMORY (about the user, NOT operational learnings):
- [USER MEMORY] below is what is known about the OWNER (preferences, profile).
  This is distinct from operational/market learnings (agent memory).
- Read detail with manage_memory(action="read", name="...").
- If you learn something new and stable about the USER (a standing preference,
  a profile fact, a correction), save it with manage_memory(action="write",
  name="short-name", description="one line", content="...", type="preference|fact").
  Operational/market learnings go to agent memory (see RECORDING above), NOT here.

NOTIFICATIONS:
- Use send_notification(text="...") to message the user.
"""

# Recording guidance (§7.1): the run's event stream is the one history — the
# engine records the tick (your response, tool calls, metrics) automatically.
# Durable operational learnings go to agent memory (an explicit tool call).
JOURNAL_SECTION_LIVE = """\
RECORDING:
- Your response and tool calls are recorded on the run automatically —
  state your decision and reasoning in ONE short line at the top of your
  response; no tool call needed for that.
- A genuinely NEW durable fact (market behavior, execution quirk) goes to
  agent memory: manage_memory(action="write", ...) — factual, one line.
"""

JOURNAL_SECTION_EXPERIMENT = """\
RECORDING:
- This is an experiment (dry run): mutating actions are cancelled, and the
  whole tick is recorded automatically on the run's event stream.
- Put all observations, reasoning, and what you WOULD do straight into your
  response.
"""


def _build_tool_preload(*, is_experiment: bool) -> str:
    """ToolSearch preload line for the tick's ACP session.

    Experiments omit manage_executors (read-only).
    """
    tools: list[str] = []
    if not is_experiment:
        tools.append("mcp__condor__manage_executors")
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

    The AGENT.md body IS the spec (§5.3 collapse): domain identity + the
    strategy tactic in one document (``agent.instructions``).
    """
    execution_mode = config.get("execution_mode", "loop")
    is_experiment = execution_mode == "experiment"

    # Select base prompt and journal protocol based on mode
    base_prompt = BASE_PROMPT_EXPERIMENT if is_experiment else BASE_PROMPT_LIVE
    journal_section = (
        JOURNAL_SECTION_EXPERIMENT if is_experiment else JOURNAL_SECTION_LIVE
    )
    sections: list[str] = [base_prompt, journal_section, BASE_PROMPT_COMMON]

    # Tool preload is ACP-specific (ToolSearch)
    sections.append(_build_tool_preload(is_experiment=is_experiment))

    # Tick identity. Condor-native executors are attributed automatically
    # (agent_slug/agent_id) — no attribution arg needed in tool calls.
    tick_info = f"[TICK INFO]\nThis is tick #{tick_number}. Use this number in notifications."
    if agent_id:
        tick_info += f"\nAgent ID: {agent_id}"
    sections.append(tick_info)

    # Single-tick session note (run_once maps to max_ticks=1)
    if not is_experiment and config.get("max_ticks") == 1:
        sections.append(
            "[EXECUTION MODE — SINGLE TICK]\n"
            "Single-tick session with LIVE execution. The engine will stop after this tick. "
            "Make your best move now — there will be no follow-up ticks."
        )

    # The agent spec: identity + domain knowledge + strategy tactic, one
    # document (AGENT.md body — §5.3 collapse).
    if agent.instructions.strip():
        sections.append(f"[AGENT — identity, knowledge & strategy]\n{agent.instructions}")

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
        # "quote units", not "$": exposure is denominated in each session's
        # quote asset (SOL for a Solana-quoted agent, USD(C) for perp/pred).
        f"Position Size: {rs.get('total_exposure', 0):.2f} / "
        f"{rs.get('max_position_size', 500):.2f} quote units limit",
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
