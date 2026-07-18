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

FINISHING:
- If your strategy or session context defines a completion condition and it
  is MET, call complete_run(summary="...") — the run ends gracefully after
  this tick, before its tick budget. The summary is your final report to the
  user: what you did and the outcome, 1-3 sentences.
- Only for "job finished". Never call it on errors (journal those), and it
  can never extend a run.

ERROR RECOVERY:
- If manage_executors(action="create") fails, call manage_executors(executor_type="<type>") \
to fetch the full config schema, compare it against what you sent, fix the missing/wrong \
fields, and retry ONCE. Journal the error and fix as a learning.
"""

BASE_PROMPT_EXPERIMENT = """\
You are an autonomous trading agent running inside Condor in 🧪 EXPERIMENT mode.

RULES:
- This is a SIMULATION, observation only. Do NOT create or stop executors —
  any mutating call is cancelled at the gate.
- manage_executors is available for read-only queries (status/performance).
- Analyze the market and describe what you WOULD do, but take NO trading action.

REPORT — your response IS the report the user receives (nothing else is kept):
- Structure it for a human: your read of the market, the decision you would
  make, then the exact action(s) — executor type, pair, side, size, prices —
  and why.
- Use conditional language: "Would place grid..." not "Grid placed"
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
  create or edit them. Operational facts you learn go to record_learning.

MEMORY (about the user):
- [USER MEMORY] below is advisory, READ-ONLY context about the OWNER
  (preferences, profile), maintained by the chat. You cannot write it —
  anything durable YOU discover is operational and goes to record_learning
  (see RECORDING above).

NOTIFICATIONS:
- Use send_notification(text="...") to message the user.
"""

# Recording guidance (§7.1): the run's event stream is the one history — the
# engine records the tick (your response, tool calls, metrics) automatically.
# Durable operational learnings go to learnings.md via record_learning.
JOURNAL_SECTION_LIVE = """\
RECORDING:
- Your response and tool calls are recorded on the run automatically —
  state your decision and reasoning in ONE short line at the top of your
  response; no tool call needed for that.
- A genuinely NEW durable fact (market behavior, execution quirk) goes to
  learnings: record_learning(text="...") — factual, one line. [LEARNINGS]
  below is this list; do not re-record what is already there.
- To UPDATE or MERGE an existing learning (or when learnings are full),
  consolidate: record_learning(text="...", replaces="<distinctive substring
  of the entry to replace>"). Appends error when the list is full — never
  silently evict knowledge; consolidate instead.
"""

JOURNAL_SECTION_EXPERIMENT = """\
RECORDING:
- This is an experiment (dry run): NOTHING is persisted — no run record, no
  journal. Put ALL observations, reasoning, and would-be actions straight
  into your response; whatever you leave out is lost.
- A genuinely NEW durable fact discovered while testing still goes to
  learnings: record_learning(text="...") — factual, one line.
"""


def _build_tool_preload(*, is_experiment: bool) -> str:
    """ToolSearch preload line for the tick's ACP session.

    Experiments omit manage_executors (read-only) and complete_run (there
    is no run to complete — the single simulated tick ends by itself).
    """
    tools: list[str] = []
    if not is_experiment:
        tools.append("mcp__condor__manage_executors")
        tools.append("mcp__condor__complete_run")
    tools += [
        "mcp__condor__send_notification",
        "mcp__condor__record_learning",
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


def build_run_prompt_prefix(
    agent: Agent,
    config: dict[str, Any],
    cached_routines_section: str | None = None,
) -> str:
    """The FROZEN part of the tick prompt — constant for the whole run.

    Everything here derives from the frozen spec/config (§5.3), so the engine
    builds it once per run and persists it as the run's ``prompt.md``
    companion file. Anything that can change mid-run belongs in
    :func:`build_tick_prompt_suffix` instead.
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

    # The explicit goal (AGENT.md `goal:`): the stop condition the agent
    # judges every tick. Live runs wire it to complete_run; experiments
    # only assess it (there is no run to complete).
    if agent.goal.strip():
        if is_experiment:
            goal_wiring = (
                "This simulated tick has no run to complete — instead, state "
                "in your report whether the goal is ALREADY MET and what you "
                "would do about the gap."
            )
        else:
            goal_wiring = (
                "Evaluate this goal EVERY tick, after your actions have "
                "settled. The moment it is MET, call "
                'complete_run(summary="...") with your final report instead '
                "of acting further. The session context may tighten or "
                "parameterize the goal, never abandon it."
            )
        sections.append(f"[GOAL — when this is met, you are done]\n{agent.goal.strip()}\n\n{goal_wiring}")

    # Routine discovery is expensive and routines rarely change mid-run —
    # cached at run start, so it is part of the frozen prefix. Skills are
    # read fresh each tick and live in the suffix.
    routines_section = cached_routines_section
    if routines_section is None:
        try:
            routines_section = _build_routines_section(agent.slug)
        except Exception:
            routines_section = ""  # Don't fail the tick if discovery fails
    if routines_section:
        sections.append(f"[AVAILABLE ROUTINES]\n{routines_section}")

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

    return "\n\n".join(sections)


def build_tick_prompt_suffix(
    core_data: dict[str, str],
    learnings: str,
    summary: str,
    recent_decisions: str,
    risk_state: dict[str, Any],
    tick_number: int = 1,
    agent_id: str = "",
    user_memory: str = "",
    skills_index: str = "",
) -> str:
    """The PER-TICK part of the tick prompt.

    Everything here can differ between ticks. The engine records it inline on
    the tick's ``tick_started`` event (``prompt_suffix``) — the mutable
    context inputs (learnings / user memory / skills) additionally get
    ``context_changed`` events so their state at any tick is on the stream.
    """
    sections: list[str] = []

    # Tick identity. Condor-native executors are attributed automatically
    # (agent_slug/agent_id) — no attribution arg needed in tool calls.
    tick_info = f"[TICK INFO]\nThis is tick #{tick_number}. Use this number in notifications."
    if agent_id:
        tick_info += f"\nAgent ID: {agent_id}"
    sections.append(tick_info)

    # Skills are read fresh each tick (the agent may gain skills mid-session).
    if skills_index:
        sections.append(
            "[AVAILABLE SKILLS]\n"
            "SKILLS — playbooks (read before a known flow with "
            'manage_skill(action="read", name="..."); "→ routine:" links to an '
            "executable routine):\n"
            f"{skills_index}"
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

    # User memory -- what is known about the owner (read-only advisory
    # context from the global chat-curated store).
    if user_memory:
        sections.append(
            "[USER MEMORY — what is known about the owner; advisory, read-only]\n\n"
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
    """Full prompt for one agent tick: frozen prefix + per-tick suffix.

    The AGENT.md body IS the spec (§5.3 collapse): domain identity + the
    strategy tactic in one document (``agent.instructions``). The engine uses
    the two parts separately (prompt.md persistence / slim tick events); this
    joined form is the same text the model receives.
    """
    prefix = build_run_prompt_prefix(
        agent, config, cached_routines_section=cached_routines_section
    )
    suffix = build_tick_prompt_suffix(
        core_data,
        learnings,
        summary,
        recent_decisions,
        risk_state,
        tick_number=tick_number,
        agent_id=agent_id,
        user_memory=user_memory,
        skills_index=skills_index,
    )
    return f"{prefix}\n\n{suffix}"
