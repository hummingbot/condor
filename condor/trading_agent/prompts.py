"""Prompt builder for trading agent ticks.

Assembles the single prompt sent to a fresh ACP session each tick,
combining: base rules, strategy instructions, config, risk state,
pre-computed core data, and journal context (learnings + recent decisions).
"""

from __future__ import annotations

from typing import Any

from .risk import format_drawdown_display
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

ROUTINES:
- manage_routines(action="run", name="...", config={...}) for analysis scripts.
- manage_routines(action="list") to discover routines.
- Routines tagged "agent" are local to your strategy.

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
    'mcp__condor__manage_routines")\n'
    "Do this silently."
)


def _build_routines_section(strategy: Strategy) -> str:
    """Build an [AVAILABLE ROUTINES] section listing agent-local + global routines."""
    from routines.base import discover_routines, discover_routines_from_path

    lines = ["[AVAILABLE ROUTINES]"]
    lines.append(f'Call via: manage_routines(action="run", name="<name>", strategy_id="{strategy.id}", config={{...}})')
    lines.append("")

    # Agent-local routines first
    routines_dir = strategy.agent_dir / "routines"
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
) -> str:
    """Build the full prompt for one agent tick."""
    from condor.acp.pydantic_ai_client import is_pydantic_ai_model

    execution_mode = config.get("execution_mode", "loop")
    is_dry_run = execution_mode == "dry_run"
    agent_key = config.get("agent_key") or strategy.agent_key
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

    # Strategy instructions
    sections.append(f"[STRATEGY INSTRUCTIONS]\n{strategy.instructions}")

    # Available routines (use cached version if provided)
    if cached_routines_section:
        sections.append(cached_routines_section)
    else:
        try:
            sections.append(_build_routines_section(strategy))
        except Exception:
            pass  # Don't fail the tick if routine discovery fails

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
        "trading_context", "risk_limits",  # shown in dedicated sections
        "agent_key", "server_name", "frequency_sec", "execution_mode",  # noise / internal
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
    max_dd = rs.get('max_drawdown_pct', -1)
    dd_display = f"{rs.get('drawdown_pct', 0):.1f}% / {max_dd:.1f}% limit" if max_dd >= 0 else "disabled"
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


# ══════════════════════════════════════════════════════════════════════
# Managed provider prompts
#
# Persistent-session providers (e.g. Claude Managed Agents) split the
# prompt in two: a static system prompt set once on the hosted agent
# (build_managed_system_prompt) and a lean per-tick message carrying only
# dynamic state (build_managed_tick_prompt).
# ══════════════════════════════════════════════════════════════════════

# Config keys excluded from the managed tick prompt's [CURRENT CONFIG]
# (shown elsewhere or internal noise)
_MANAGED_CONFIG_EXCLUDE = {
    "trading_context", "risk_limits",  # shown in dedicated sections
    "agent_key", "server_name", "frequency_sec", "execution_mode", "model",
    "margin_quote", "leverage",  # shown in [SIZING]
}


def build_sizing_section(config: dict[str, Any]) -> str:
    """Render [SIZING] from margin_quote x leverage. Empty when margin unset."""
    try:
        margin = float(config.get("margin_quote", 0) or 0)
        leverage = max(int(config.get("leverage", 1) or 1), 1)
    except (TypeError, ValueError):
        return ""
    if margin <= 0:
        return ""
    notional = margin * leverage
    return "\n".join([
        "[SIZING]",
        f"Margin per trade: ${margin:,.2f}",
        f"Leverage: {leverage}x",
        f"Notional per trade: ${notional:,.2f} (margin x leverage)",
        f"Executor sizing: amount = {notional:.2f} / last_close (BASE units, 6 decimals); pass leverage={leverage}.",
        f"Price-move conversion: X% on margin = X/{leverage}% price move "
        f"(e.g. +10% on margin = {10 / leverage:.4f}% price).",
    ])


MANAGED_TOOLS_NOTE = """\
TOOLS:
- All trading tools (get_market_data, manage_executors, manage_routines, \
trading_agent_journal_write, send_notification, ...) are available natively. \
Call them directly by name.
- Tool calls are executed by Condor on the local machine and gated by its \
risk engine. A blocked call returns an error result — do not retry it; \
journal why instead.
"""

CURSOR_MEMORY_PROTOCOL = """\
MEMORY (filesystem):
- Long-term memory lives under your agent directory in ``memory/``:
  - memory/playbooks.md — setups that worked, with exact indicator values at entry.
  - memory/mistakes.md — every loss or error: state at entry, what happened, prevention rule.
  - memory/regimes.md — regime-specific behavior notes and SL calibration.
- Review memory/ at session start (read via manage_routines or journal context).
- Update memory/ after every closed position and at session end. Keep entries dated and numeric.
- Memory complements the journal: ALWAYS also write journal entries via trading_agent_journal_write.
- ``learnings.md`` is injected each tick for novelty filtering; curate durable patterns into memory/.
"""

MANAGED_MEMORY_PROTOCOL = """\
MEMORY (/mnt/memory):
- A persistent memory directory is mounted at /mnt/memory. It survives across \
sessions — it is YOUR long-term memory. Review it before your first trade \
decision of a session.
- Maintain these files (create them if missing):
  - playbooks.md — setups that work, entry/exit criteria, observed win rates.
  - mistakes.md — every loss or error: what happened, root cause, the rule \
that prevents a repeat.
  - regimes.md — regime-specific behavior notes (trend vs chop vs volatile).
- Update memory after every closed position and at the end of each session. \
Keep entries concise, factual, dated.
- Memory complements the journal, it does not replace it: ALWAYS also write \
journal entries via trading_agent_journal_write (local audit trail).
"""


def build_managed_system_prompt(
    strategy: Strategy,
    config: dict[str, Any],
    routines_section: str = "",
    memory_protocol: str | None = None,
) -> str:
    """Build the static system prompt for a Claude Managed Agent.

    Unlike the per-tick ACP prompt, this is set once on the hosted agent and
    cached by the harness. Per-tick dynamic data goes through
    build_managed_tick_prompt() instead.
    """
    is_dry_run = config.get("execution_mode", "loop") == "dry_run"
    base_prompt = BASE_PROMPT_DRY_RUN if is_dry_run else BASE_PROMPT_LIVE

    sections: list[str] = [
        base_prompt,
        BASE_PROMPT_COMMON,
        MANAGED_TOOLS_NOTE,
        memory_protocol or MANAGED_MEMORY_PROTOCOL,
        f"[STRATEGY INSTRUCTIONS]\n{strategy.instructions}",
    ]
    if routines_section:
        sections.append(routines_section)

    trading_context = config.get("trading_context", "")
    if trading_context:
        sections.append(
            "[SESSION CONTEXT]\n"
            "The user provided the following natural language context for this "
            "trading session. Use this to guide your market selection, risk "
            f"appetite, and trading style:\n\n{trading_context}"
        )
    return "\n\n".join(sections)


def build_cursor_system_prompt(
    strategy: Strategy,
    config: dict[str, Any],
    routines_section: str = "",
) -> str:
    """Static system prompt for Cursor SDK agents."""
    return build_managed_system_prompt(
        strategy,
        config,
        routines_section=routines_section,
        memory_protocol=CURSOR_MEMORY_PROTOCOL,
    )


def _trim_learnings_for_managed(text: str, max_lines: int = 30) -> str:
    """Cap learnings injected into the lean per-tick managed prompt."""
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[-max_lines:])


def _trim_recent_decisions_for_managed(
    text: str,
    max_entries: int = 5,
    max_chars: int = 2000,
) -> str:
    """Keep only the last N recent-decision entries for the managed prompt."""
    if not text:
        return ""
    entries = [e for e in text.split("\n- ") if e.strip()]
    if entries and not entries[0].startswith("-"):
        entries[0] = entries[0].lstrip("- ")
    trimmed = entries[-max_entries:] if len(entries) > max_entries else entries
    joined = "\n- ".join(trimmed)
    if trimmed:
        joined = "- " + joined if not joined.startswith("-") else joined
    return joined[:max_chars]


def read_learnings_bootstrap(agent_dir: Any, max_lines: int = 40) -> str:
    """Last N lines of learnings.md for managed-agent memory bootstrap."""
    from pathlib import Path

    path = Path(agent_dir) / "learnings.md"
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    tail = "\n".join(lines[-max_lines:]).strip()
    return tail


def build_managed_tick_prompt(
    config: dict[str, Any],
    core_data: dict[str, str],
    learnings: str,
    risk_state: dict[str, Any],
    tick_number: int = 1,
    agent_id: str = "",
    summary: str = "",
    recent_decisions: str = "",
    extra_sections: list[str] | None = None,
) -> str:
    """Build the lean per-tick message for a Claude Managed Agent.

    The hosted session is persistent, so static content (rules, strategy,
    routines) lives in the system prompt and the conversation itself carries
    tick-to-tick context. Only the dynamic state goes here.
    """
    execution_mode = config.get("execution_mode", "loop")
    is_dry_run = execution_mode == "dry_run"

    tick_info = (
        f"[TICK INFO]\nThis is tick #{tick_number}. "
        "Use this number in journal entries and notifications."
    )
    if agent_id:
        tick_info += f"\nAgent ID: {agent_id}"
        if not is_dry_run:
            tick_info += (
                f'\nPass controller_id="{agent_id}" as a TOP-LEVEL arg to '
                "manage_executors (not inside executor_config)."
            )
    sections: list[str] = [tick_info]

    if execution_mode == "run_once":
        sections.append(
            "[EXECUTION MODE — RUN ONCE]\n"
            "Single-tick session with LIVE execution. The engine will stop after "
            "this tick. Make your best move now — there will be no follow-up ticks."
        )

    config_lines = ["[CURRENT CONFIG]"]
    for k, v in config.items():
        if k in _MANAGED_CONFIG_EXCLUDE:
            continue
        config_lines.append(f"{k}: {v}")
    sections.append("\n".join(config_lines))

    rs = risk_state
    dd_display = format_drawdown_display(rs)
    sections.append("\n".join([
        "[RISK STATE]",
        f"Position Size: ${rs.get('total_exposure', 0):.2f} / ${rs.get('max_position_size', 500):.2f} limit",
        f"Open Executors: {rs.get('executor_count', 0)} / {rs.get('max_open_executors', 5)} limit",
        f"Drawdown: {dd_display}",
        f"Status: {'BLOCKED - ' + rs.get('block_reason', '') if rs.get('is_blocked') else 'ACTIVE'}",
    ]))

    sizing = build_sizing_section(config)
    if sizing:
        sections.append(sizing)

    for name, data_summary in core_data.items():
        sections.append(f"[CORE DATA - {name}]\n{data_summary}")

    if extra_sections:
        sections.extend(extra_sections)

    learnings = _trim_learnings_for_managed(learnings)
    recent_decisions = _trim_recent_decisions_for_managed(recent_decisions)

    if learnings:
        sections.append(
            f"[LEARNINGS — do NOT repeat these, only add genuinely new insights]\n{learnings}"
        )
    if summary:
        sections.append(f"[CURRENT STATUS]\n{summary}")
    if recent_decisions:
        sections.append(f"[RECENT DECISIONS — last 5 entries]\n{recent_decisions}")

    return "\n\n".join(sections)
